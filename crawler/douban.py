"""
豆瓣电影匹配模块 v6.0 — 纯API方式，无浏览器依赖
====================================
- 搜索API优先 (search.douban.com)，无需浏览器
- 数据缓存机制: 只爬新电影，已有数据直接使用
- 从搜索结果页面提取嵌入的 JSON 数据 (window.__DATA__)
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

from crawler.config import DOUBAN_SEARCH_URL, DATA_DIR

logger = logging.getLogger("douban")

# SSL context
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# 缓存文件路径
DOUBAN_CACHE_PATH = os.path.join(DATA_DIR, "douban_cache.json")

# 请求头
_SEARCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.douban.com/",
}


class DoubanMatcher:
    """豆瓣电影匹配器 — 缓存优先，搜索API次之"""

    def __init__(self, use_cache=True):
        self._use_cache = use_cache
        self._cache = {}
        self._search_delay = (0.3, 0.6)
        if self._use_cache:
            self._load_cache()

    # ==================== 缓存 ====================

    def _load_cache(self):
        try:
            if os.path.exists(DOUBAN_CACHE_PATH):
                with open(DOUBAN_CACHE_PATH, 'r', encoding='utf-8') as f:
                    cached = json.load(f)
                self._cache.update(cached)
                logger.info(f"豆瓣缓存加载: {len(cached)} 条")
        except Exception as e:
            logger.warning(f"缓存加载失败: {e}")

    def _save_cache(self):
        try:
            os.makedirs(os.path.dirname(DOUBAN_CACHE_PATH), exist_ok=True)
            with open(DOUBAN_CACHE_PATH, 'w', encoding='utf-8') as f:
                json.dump(self._cache, f, ensure_ascii=False, indent=2)
            logger.info(f"豆瓣缓存保存: {len(self._cache)} 条")
        except Exception as e:
            logger.error(f"缓存保存失败: {e}")

    def _check_cache(self, title):
        if not self._use_cache:
            return None
        key = title.strip().lower()
        if key in self._cache:
            return self._cache[key]
        return None

    def _update_cache(self, title, data):
        if not self._use_cache:
            return
        key = title.strip().lower()
        if data:
            self._cache[key] = data

    # ==================== 搜索 API ====================

    def _extract_json_from_html(self, content):
        """从 HTML 中提取 window.__DATA__"""
        start = content.find('window.__DATA__')
        if start < 0:
            return None
        eq = content.find('=', start)
        json_start = eq + 1
        while json_start < len(content) and content[json_start] in ' \n\r\t':
            json_start += 1
        if json_start >= len(content) or content[json_start] != '{':
            return None

        bracket_count = 0
        in_string = False
        escape_next = False
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
                    try:
                        return json.loads(content[json_start:i+1])
                    except json.JSONDecodeError:
                        return None
        return None

    def _api_search(self, title: str) -> List[Dict]:
        """使用豆瓣搜索 API"""
        url = f'{DOUBAN_SEARCH_URL}?search_text={urllib.parse.quote(title.strip())}'
        try:
            req = urllib.request.Request(url, headers=_SEARCH_HEADERS)
            with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
                content = resp.read().decode('utf-8', errors='replace')
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
            logger.debug(f"搜索API失败 [{title[:30]}]: {e}")
            return []

        data = self._extract_json_from_html(content)
        if data is None:
            return []

        results = []
        for item in data.get('items', []):
            rating = item.get('rating', {})
            score = rating.get('value', 0)
            if score > 0:
                results.append({
                    'url': item.get('url', ''),
                    'id': item.get('id', ''),
                    'title': item.get('title', ''),
                    'score': str(score),
                    'vote_count': str(rating.get('count', 0)),
                    'chinese_title': item.get('title', ''),
                    'genre': self._parse_genre(item.get('abstract', '')),
                    'poster': item.get('cover_url', ''),
                })

        logger.info(f"  豆瓣搜索: {title[:25]} -> {len(results)}条")
        return results

    def _parse_genre(self, abstract):
        if not abstract:
            return ''
        keywords = {'剧情', '喜剧', '动作', '爱情', '科幻', '动画', '悬疑',
                    '惊悚', '恐怖', '纪录片', '短片', '冒险', '奇幻', '犯罪',
                    '战争', '历史', '传记', '音乐', '歌舞', '家庭', '西部',
                    '武侠', '古装', '运动'}
        parts = abstract.split('/')
        genres = [p.strip() for p in parts if p.strip() in keywords]
        return ', '.join(genres) if genres else ''

    def _best_match(self, results, search_title):
        if not results:
            return {}
        lower = search_title.lower().strip()
        for item in results:
            if lower in item.get('title', '').lower():
                return item
        return results[0]

    # ==================== 对外接口 ====================

    def find_movie(self, title: str) -> Dict:
        """匹配豆瓣电影 — 缓存优先，搜索API次之"""
        cached = self._check_cache(title)
        if cached is not None:
            return cached

        result = {}
        try:
            api_results = self._api_search(title)
            if api_results:
                result = self._best_match(api_results, title)
        except Exception as e:
            logger.debug(f"匹配异常 [{title[:30]}]: {e}")

        self._update_cache(title, result)
        return result

    def match_and_fetch(self, title: str, original_title: Optional[str] = None, timeout: int = 60) -> Dict:
        """自动匹配豆瓣并抓取数据"""
        search_title = original_title or title
        info = self.find_movie(search_title)

        if not info and original_title and original_title != title:
            info = self.find_movie(title)

        if info:
            douban_id = info.get('id', '')
            if not douban_id:
                m = re.search(r'/subject/(\d+)/', info.get('url', ''))
                if m:
                    douban_id = m.group(1)

            return {
                "douban_id": douban_id,
                "douban_url": info.get("url", ""),
                "douban_score": info.get("score", ""),
                "douban_vote_count": info.get("vote_count", ""),
                "douban_title": info.get("chinese_title", ""),
                "douban_genre": info.get("genre", ""),
                "douban_director": "",
                "douban_cast": "",
                "douban_synopsis": "",
                "douban_poster": info.get("poster", ""),
            }

        return {
            "douban_id": "", "douban_url": "", "douban_score": "",
            "douban_vote_count": "", "douban_title": "", "douban_genre": "",
            "douban_director": "", "douban_cast": "",
            "douban_synopsis": "", "douban_poster": "",
        }
