"""
豆瓣电影爬虫模块 v5.0
============================
- 搜索API优先 (search.douban.com), 无需浏览器, 海外IP可用
- 豆瓣数据缓存机制: 只爬新电影, 已有数据直接使用 (只爬一次)
- 从搜索结果页面提取嵌入的 JSON 数据 (window.__DATA__)
- 返回: 评分、评分人数、类型、海报、中文名、豆瓣URL
- Selenium 仅作为本地回退方案 (driver 可用时自动使用)
"""
import os
import re
import json
import time
import random
import logging
import ssl
import urllib.parse
import urllib.request
import urllib.error
from typing import Optional, List, Dict

from crawler.config import (
    DOUBAN_BASE_URL, DOUBAN_SEARCH_URL,
    IMAGE_RETRY_MAX, IMAGE_RETRY_BACKOFF_FACTOR,
    DATA_DIR,
)

logger = logging.getLogger("douban")

# 模块级 SSL context 单例
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# 豆瓣缓存文件路径 (存储已匹配的豆瓣数据, 只爬一次)
DOUBAN_CACHE_PATH = os.path.join(DATA_DIR, "douban_cache.json")

# 豆瓣搜索 API 请求头 (不要设置 Accept-Encoding, urllib不自动解压gzip)
_SEARCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.douban.com/",
}


class DoubanMatcher:
    """豆瓣电影匹配器 v5.0 — 缓存优先, 搜索API次之, Selenium回退"""

    def __init__(self, driver=None, use_cache=True):
        self.driver = driver
        self._use_selenium = driver is not None
        self._use_cache = use_cache
        self._cache = {}  # 内存缓存 (title -> douban_data)
        # CI 环境减少延迟
        ci_env = os.environ.get("CI_ENV", "").lower() in ("true", "1", "yes")
        if ci_env:
            self._search_delay = (0.3, 0.6)
            self._detail_delay = (0.8, 1.5)
        else:
            self._search_delay = (1.5, 3.0)
            self._detail_delay = (2.0, 4.0)
        # 启动时加载持久缓存
        if self._use_cache:
            self._load_cache()

    # ==================== 缓存机制 (豆瓣只爬一次) ====================

    def _load_cache(self):
        """从 douban_cache.json 加载持久缓存"""
        try:
            if os.path.exists(DOUBAN_CACHE_PATH):
                with open(DOUBAN_CACHE_PATH, 'r', encoding='utf-8') as f:
                    cached = json.load(f)
                self._cache.update(cached)
                logger.info(f"豆瓣缓存加载完成: {len(cached)} 条记录")
            else:
                logger.info("豆瓣缓存文件不存在, 将创建新缓存")
        except Exception as e:
            logger.warning(f"豆瓣缓存加载失败: {e}, 使用空缓存")

    def _save_cache(self):
        """将缓存保存到 douban_cache.json"""
        try:
            os.makedirs(os.path.dirname(DOUBAN_CACHE_PATH), exist_ok=True)
            with open(DOUBAN_CACHE_PATH, 'w', encoding='utf-8') as f:
                json.dump(self._cache, f, ensure_ascii=False, indent=2)
            logger.info(f"豆瓣缓存保存完成: {len(self._cache)} 条记录")
        except Exception as e:
            logger.error(f"豆瓣缓存保存失败: {e}")

    def _check_cache(self, title):
        """检查缓存中是否已有该电影的豆瓣数据"""
        if not self._use_cache:
            return None
        # 用标题作为缓存 key (支持英文原名和中文标题)
        cache_key = f"{title.strip().lower()}"
        if cache_key in self._cache:
            cached_data = self._cache[cache_key]
            if cached_data:  # 有数据 (非空匹配)
                logger.info(f"  豆瓣缓存命中: {title[:25]} -> "
                            f"{cached_data.get('title', 'N/A')[:25]} "
                            f"评分:{cached_data.get('score', '-')}")
                return cached_data
        return None

    def _update_cache(self, title, douban_data):
        """更新缓存"""
        if not self._use_cache:
            return
        cache_key = f"{title.strip().lower()}"
        if douban_data:
            self._cache[cache_key] = douban_data

    # ==================== 搜索 API 方式 (无需浏览器) ====================

    def _extract_json_from_html(self, content):
        """从 HTML 中提取 window.__DATA__ 的 JSON 数据
        使用字符串状态追踪, 正确处理 JSON 字符串内的括号
        """
        data_start = content.find('window.__DATA__')
        if data_start < 0:
            return None

        eq_pos = content.find('=', data_start)
        # 找到等号后第一个 { 的位置
        json_start = eq_pos + 1
        while json_start < len(content) and content[json_start] in (' ', '\n', '\r', '\t'):
            json_start += 1
        if json_start >= len(content) or content[json_start] != '{':
            return None

        # 用字符串状态追踪提取完整 JSON (正确处理字符串内的 {} )
        bracket_count = 0
        in_string = False
        escape_next = False
        json_end = -1

        for i in range(json_start, len(content)):
            c = content[i]
            if escape_next:
                escape_next = False
                continue
            if c == '\\' and in_string:
                escape_next = True
                continue
            if c == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == '{':
                bracket_count += 1
            elif c == '}':
                bracket_count -= 1
                if bracket_count == 0:
                    json_end = i + 1
                    break

        if json_end < 0:
            return None

        raw_json = content[json_start:json_end]
        try:
            return json.loads(raw_json)
        except json.JSONDecodeError:
            return None

    def _api_search(self, title: str) -> List[Dict]:
        """使用豆瓣搜索 API 获取电影数据 (无需 Selenium)"""
        search_url = (f'https://search.douban.com/movie/subject_search?'
                      f'search_text={urllib.parse.quote(title.strip())}')

        try:
            req = urllib.request.Request(search_url, headers=_SEARCH_HEADERS)
            with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
                content = resp.read().decode('utf-8', errors='replace')
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
            logger.debug(f"豆瓣搜索API请求失败 [{title[:30]}]: {e}")
            return []

        # 提取 window.__DATA__ 中的 JSON
        data = self._extract_json_from_html(content)
        if data is None:
            logger.debug(f"豆瓣搜索无 __DATA__ [{title[:30]}]")
            return []

        items = data.get('items', [])
        results = []
        for item in items:
            rating = item.get('rating', {})
            # 只保留有评分且评分>0的结果
            score_value = rating.get('value', 0)
            if score_value > 0:
                results.append({
                    'url': item.get('url', ''),
                    'id': item.get('id', ''),
                    'title': item.get('title', ''),
                    'score': str(score_value),
                    'vote_count': str(rating.get('count', 0)),
                    'chinese_title': item.get('title', ''),
                    'genre': self._parse_genre_from_abstract(item.get('abstract', '')),
                    'poster': item.get('cover_url', ''),
                    'abstract': item.get('abstract', ''),
                })

        logger.info(f"  豆瓣API搜索: {title[:25]} -> {len(results)}条有评分结果")
        return results

    def _parse_genre_from_abstract(self, abstract):
        """从 abstract 字段提取类型信息
        abstract 格式: '美国 / 英国 / 剧情 / 科幻 / 悬疑 / 冒险 / ...'
        """
        if not abstract:
            return ''
        parts = abstract.split('/')
        genres = []
        for p in parts:
            p = p.strip()
            # 类型关键词通常是中文且长度2-4
            if p and len(p) >= 2 and len(p) <= 8 and not p[0].isdigit():
                # 排除国家/地区/时长等
                if not re.match(r'^[\d]+分钟$', p) and not re.match(r'^\d{4}$', p):
                    genres.append(p)
        # 常见类型关键词筛选
        genre_keywords = {'剧情', '喜剧', '动作', '爱情', '科幻', '动画', '悬疑',
                          '惊悚', '恐怖', '纪录片', '短片', '冒险', '奇幻', '犯罪',
                          '战争', '历史', '传记', '音乐', '歌舞', '家庭', '西部',
                          '武侠', '古装', '运动', '黑色电影', '实验电影', '情色'}
        filtered = [g for g in genres if g in genre_keywords]
        return ', '.join(filtered) if filtered else ', '.join(genres[:3])

    def _best_match(self, results: List[Dict], search_title: str) -> Dict:
        """从搜索结果中选择最佳匹配 — 取第一个包含搜索词的结果"""
        if not results:
            return {}

        search_lower = search_title.lower().strip()
        for item in results:
            title_lower = item.get('title', '').lower()
            # 搜索词出现在标题中 -> 高优先级, 立即返回第一个
            if search_lower in title_lower or title_lower in search_lower:
                return item

        # 没有好的标题匹配, 返回评分最高的第一个结果
        return results[0]

    # ==================== Selenium 方式 (回退, 需 driver) ====================

    def _selenium_full_fetch(self, title, release_date=''):
        """Selenium 搜索 + 抓取完整详情 (回退方案)"""
        from selenium.webdriver.common.by import By

        try:
            search_url = (f'https://movie.douban.com/subject_search?'
                          f'search_text={urllib.parse.quote(title.strip())}')
            self.driver.get(search_url)
            time.sleep(random.uniform(*self._search_delay))

            link_elems = self.driver.find_elements(
                By.XPATH,
                '//div[@id="root"]//a[contains(@href,"/subject/")]'
            )
            url = ''
            if link_elems:
                url = link_elems[0].get_attribute('href')

            if not url:
                link_elems = self.driver.find_elements(
                    By.XPATH,
                    '//a[contains(@href,"/subject/")]'
                )
                if link_elems:
                    url = link_elems[0].get_attribute('href')

            if not url:
                return {}

            self.driver.get(url)
            time.sleep(random.uniform(*self._detail_delay))

            data = {'url': url, 'title': title}
            data['score'] = self._selenium_extract_score()
            data['vote_count'] = self._selenium_extract_vote_count()
            data['chinese_title'] = self._selenium_safe_text(
                '//span[@property="v:itemreviewed"]')
            data['genre'] = self._selenium_extract_list(
                '//span[@property="v:genre"]')
            data['director'] = self._selenium_extract_list(
                '//a[@rel="v:directedBy"]')
            data['actors'] = self._selenium_extract_list(
                '//a[@rel="v:starring"]', limit=6)
            data['synopsis'] = self._selenium_safe_text(
                '//span[@property="v:summary"]')
            data['poster'] = self._selenium_safe_attr(
                '//img[@rel="v:image"]', attr='src')

            logger.info(f"  豆瓣Selenium: {title[:25]} -> "
                        f"评分:{data.get('score', '-')} "
                        f"类型:{data.get('genre', '')[:30]}")
            return data
        except Exception as err:
            logger.debug(f"Selenium豆瓣搜索失败: {err}")
            return {}

    def _selenium_extract_score(self):
        try:
            from selenium.webdriver.common.by import By
            elems = self.driver.find_elements(By.XPATH,
                '//strong[contains(@class,"ll")]|//span[@property="v:average"]')
            for e in elems:
                t = e.text.strip()
                if t and re.match(r'\d+\.?\d*', t):
                    return t
            try:
                meta = self.driver.find_element(By.XPATH, '//meta[@property="video:rating"]')
                v = meta.get_attribute('content') or ''
                if v:
                    return v
            except Exception:
                pass
        except Exception:
            pass
        return ''

    def _selenium_extract_vote_count(self):
        try:
            from selenium.webdriver.common.by import By
            elem = self.driver.find_element(By.XPATH,
                '//span[@property="v:votes"]|//span[contains(@class,"rating_people")]//span')
            t = elem.text.strip().replace(',', '').replace(' ', '')
            if t.isdigit():
                return t
        except Exception:
            pass
        return ''

    def _selenium_extract_list(self, xpath, limit=None):
        try:
            from selenium.webdriver.common.by import By
            elems = self.driver.find_elements(By.XPATH, xpath)
            items = [e.text.strip() for e in elems if e.text.strip()]
            if limit:
                items = items[:limit]
            return ', '.join(items)
        except Exception:
            return ''

    def _selenium_safe_text(self, xpath):
        try:
            from selenium.webdriver.common.by import By
            elem = self.driver.find_element(By.XPATH, xpath)
            return elem.text.strip()
        except Exception:
            return ''

    def _selenium_safe_attr(self, xpath, attr='src'):
        try:
            from selenium.webdriver.common.by import By
            elem = self.driver.find_element(By.XPATH, xpath)
            return elem.get_attribute(attr) or ''
        except Exception:
            return ''

    # ==================== 对外接口 ====================

    def find_movie(self, title: str, release_date: str = '') -> Dict:
        """匹配豆瓣电影 — 缓存优先, 搜索API次之, Selenium回退"""
        # 1. 先查缓存 (豆瓣只爬一次)
        cached = self._check_cache(title)
        if cached is not None:
            return cached

        result = {}
        try:
            # 2. 搜索API (快速, 无需浏览器)
            api_results = self._api_search(title)
            if api_results:
                result = self._best_match(api_results, title)
                logger.info(f"  豆瓣匹配(API): {title[:25]} -> "
                            f"{result.get('title', 'N/A')[:25]} "
                            f"评分:{result.get('score', '-')}")

            # 3. 如果API没找到且有driver, 回退Selenium
            if not result and self._use_selenium:
                result = self._selenium_full_fetch(title, release_date)
        except Exception as err:
            logger.debug(f"豆瓣匹配异常 [{title[:30]}]: {err}")

        # 更新缓存
        self._update_cache(title, result)
        return result

    def match_and_fetch(self, title: str, original_title: Optional[str] = None, timeout: int = 60) -> Dict:
        """自动匹配豆瓣并抓取完整数据 (适配 main.py)
        搜索API模式下 timeout 参数主要影响重试次数
        缓存优先: 如果已有数据直接返回, 不再重复爬取
        """
        # 优先用英文原名搜索
        search_title = original_title or title
        douban_info = self.find_movie(search_title, '')

        # 回退: 用标题搜索
        if not douban_info and original_title and original_title != title:
            douban_info = self.find_movie(title, '')

        if douban_info:
            douban_id = douban_info.get('id', '')
            if not douban_id:
                id_match = re.search(r'/subject/(\d+)/', douban_info.get('url', ''))
                if id_match:
                    douban_id = id_match.group(1)

            return {
                "douban_id": douban_id,
                "douban_url": douban_info.get("url", ""),
                "douban_score": douban_info.get("score", ""),
                "douban_vote_count": douban_info.get("vote_count", ""),
                "douban_title": douban_info.get("chinese_title", ""),
                "douban_genre": douban_info.get("genre", ""),
                "douban_director": douban_info.get("director", ""),
                "douban_cast": douban_info.get("actors", ""),
                "douban_synopsis": douban_info.get("synopsis", ""),
                "douban_poster": douban_info.get("poster", ""),
            }

        return {
            "douban_id": "",
            "douban_url": "",
            "douban_score": "",
            "douban_vote_count": "",
            "douban_title": "",
            "douban_genre": "",
            "douban_director": "",
            "douban_cast": "",
            "douban_synopsis": "",
            "douban_poster": "",
        }
