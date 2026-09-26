"""
豆瓣电影匹配模块 — 纯 API 方式，无浏览器依赖
====================================
- 豆瓣搜索页把结果内联在 window.__DATA__ 里，直接解析，无需浏览器
- 匹配采用"标题必须包含检索词 + 年份必须吻合"，宁缺毋滥
- 命中结果写入 douban_cache.json，CI 回传到仓库以摊薄后续抓取
"""
import os
import re
import json
import time
import logging
import urllib.parse
import urllib.request
import urllib.error
from typing import Optional, List, Dict

from crawler.config import DOUBAN_SEARCH_URL, DATA_DIR, build_ssl_context

logger = logging.getLogger("douban")

_SSL_CTX = build_ssl_context()

# 缓存文件路径
DOUBAN_CACHE_PATH = os.path.join(DATA_DIR, "douban_cache.json")

# 软封判定：至少这么多实时样本，且空结果占比达到该阈值
BLOCK_MIN_SAMPLES = int(os.environ.get("DOUBAN_BLOCK_MIN_SAMPLES", 12))
BLOCK_EMPTY_RATIO = float(os.environ.get("DOUBAN_BLOCK_EMPTY_RATIO", 0.85))


def _as_int(value, default=0):
    try:
        return int(str(value).replace(",", "").strip() or default)
    except (ValueError, TypeError):
        return default

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
        # 实时检索的空结果统计，用于识别豆瓣对数据中心 IP 的软封
        self.live_lookups = 0
        self.empty_lookups = 0
        if self._use_cache:
            self._load_cache()

    @property
    def blocked(self) -> bool:
        """样本足够且几乎全是空结果 —— 判定为被限流，上层应停止实时检索。

        豆瓣的软封有两种表现，且都返回 HTTP 200：页面里没有 window.__DATA__，
        或有 __DATA__ 而 items 为空。CI 实测后者才是主流（95 次检索 87 次空），
        所以按空结果比例判，而不是只看页面结构。片单里的《教父2》《美丽人生》
        不可能真的查无此片。
        """
        if self.live_lookups < BLOCK_MIN_SAMPLES:
            return False
        return self.empty_lookups / self.live_lookups >= BLOCK_EMPTY_RATIO

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

    def _cache_key(self, title, year):
        return f"{title.strip().lower()}|{year or ''}"

    def _check_cache(self, title, year=None):
        """先查带年份的新键，再回退到历史的裸片名键。

        缓存键在 6.1 从 title 改成 title|year，直接把仓库里已有的条目全作废了，
        逼着每次运行都去敲豆瓣接口 —— 而豆瓣对数据中心 IP 会软封（返回 200 但页面里
        没有 window.__DATA__）。回退时仍用条目自身标题里的年份做校验，不放严格性。
        """
        if not self._use_cache:
            return None

        hit = self._cache.get(self._cache_key(title, year))
        if hit is not None:
            return hit

        legacy = self._cache.get(title.strip().lower())
        if not legacy:
            return None
        if year:
            cached_year = self._parse_year(legacy.get("title", ""))
            if cached_year and abs(cached_year - year) > 1:
                return None
        return legacy

    def _update_cache(self, title, data, year=None):
        if not self._use_cache or not data:
            return
        self._cache[self._cache_key(title, year)] = data

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
        """使用豆瓣搜索 API

        豆瓣对数据中心 IP 的软封一律返回 HTTP 200，有两种形态：页面里没有
        window.__DATA__，或有 __DATA__ 而 items 为空。两者都计入空结果统计，
        由 blocked 按比例判定后让上层熔断，避免整轮白敲。
        """
        url = f'{DOUBAN_SEARCH_URL}?search_text={urllib.parse.quote(title.strip())}'
        self.live_lookups += 1

        try:
            req = urllib.request.Request(url, headers=_SEARCH_HEADERS)
            with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
                content = resp.read().decode('utf-8', errors='replace')
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
            logger.warning(f"搜索API失败 [{title[:30]}]: {e}")
            self.empty_lookups += 1
            return []

        data = self._extract_json_from_html(content)
        if data is None:
            self.empty_lookups += 1
            logger.warning(f"页面无 window.__DATA__ [{title[:30]}]")
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

        if not results:
            self.empty_lookups += 1
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

    def _parse_year(self, title):
        match = re.search(r"\((\d{4})\)", title or "")
        return int(match.group(1)) if match else None

    def _best_match(self, results, search_title, year=None):
        """豆瓣检索结果里挑真正的那一条。

        条目形如 "肖申克的救赎 The Shawshank Redemption (1994)"，标题带年份，
        足以否掉同名剧集与重映条目；对不上年份时宁可判为未匹配。
        """
        if not results:
            return {}

        needle = (search_title or "").strip().lower()
        candidates = [
            item for item in results
            if needle and needle in (item.get("title") or "").strip().lower()
        ]
        if not candidates:
            return {}

        if year:
            same_year = [
                item for item in candidates
                if (got := self._parse_year(item.get("title"))) is None
                or abs(got - year) <= 1
            ]
            candidates = same_year or [
                item for item in candidates if self._parse_year(item.get("title")) is None
            ]
            if not candidates:
                return {}

        return max(candidates, key=lambda item: _as_int(item.get("vote_count")))

    # ==================== 对外接口 ====================

    def find_movie(self, title: str, year: Optional[int] = None) -> Dict:
        """匹配豆瓣电影 — 缓存优先，搜索 API 次之"""
        cached = self._check_cache(title, year)
        if cached is not None:
            return cached

        result = {}
        try:
            api_results = self._api_search(title)
            if api_results:
                result = self._best_match(api_results, title, year)
        except Exception as e:
            logger.debug(f"匹配异常 [{title[:30]}]: {e}")

        self._update_cache(title, result, year)
        return result

    EMPTY = {
        "douban_id": "", "douban_url": "", "douban_score": "",
        "douban_vote_count": "", "douban_title": "", "douban_genre": "",
        "douban_poster": "",
    }

    def cached_only(self, title: str, year: Optional[int] = None) -> Dict:
        """只读缓存、不敲接口 —— 判定被限流后仍要把已有数据用上。"""
        info = self._check_cache(title, year)
        return self._to_fields(info) if info else dict(self.EMPTY)

    def match_and_fetch(self, title: str, year: Optional[int] = None) -> Dict:
        """匹配豆瓣并返回标准化的 douban_* 字段；未匹配到时返回空字段"""
        info = self.find_movie(title, year)
        return self._to_fields(info) if info else dict(self.EMPTY)

    @staticmethod
    def _to_fields(info: Dict) -> Dict:
        douban_id = info.get("id", "")
        if not douban_id:
            match = re.search(r"/subject/(\d+)/", info.get("url", ""))
            douban_id = match.group(1) if match else ""

        return {
            "douban_id": douban_id,
            "douban_url": info.get("url", ""),
            "douban_score": info.get("score", ""),
            "douban_vote_count": info.get("vote_count", ""),
            "douban_title": info.get("chinese_title", ""),
            "douban_genre": info.get("genre", ""),
            "douban_poster": info.get("poster", ""),
        }
