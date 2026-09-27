"""
烂番茄数据获取 — 纯 Algolia 搜索 API，无浏览器依赖
================================================
- Algolia `content` 索引提供评分 / 类型 / 演职员 / 海报
- 匹配采用"精确标题 + 年份必须吻合"，宁缺毋滥
"""
import os
import re
import json
import time
import logging
import unicodedata
import urllib.request
import urllib.error

from crawler.config import RT_BASE_URL, build_ssl_context

logger = logging.getLogger("rotten_tomatoes")

_SSL_CTX = build_ssl_context()

# ==================== Algolia 搜索 API ====================
# APP_ID / API_KEY 是 RT 网页前端自己使用的搜索凭据：浏览器在
# rottentomatoes.com 上每次搜索都会带上它，不是破解或泄露物。
# RT 官方 API 面向合作方商业授权，个人项目拿不到，故走这条与页面同源的路。
# 两者都可用环境变量覆盖（键若轮换，改环境变量即可，不必改代码）。
ALGOLIA_APP_ID = os.environ.get("ALGOLIA_APP_ID", "79FRDP12PN")
ALGOLIA_API_KEY = os.environ.get("ALGOLIA_API_KEY", "175588f6e5f8319b27702e4cc4013561")
ALGOLIA_INDEX = os.environ.get("ALGOLIA_INDEX", "content")
ALGOLIA_URL = f"https://{ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/{ALGOLIA_INDEX}/query"

ALGOLIA_HEADERS = {
    "X-Algolia-Application-Id": ALGOLIA_APP_ID,
    "X-Algolia-API-Key": ALGOLIA_API_KEY,
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


def _algolia_search(query, filters="type:movie", hits_per_page=20, page=0):
    """使用 Algolia 搜索 API 查询电影数据"""
    body = json.dumps({
        "query": query,
        "hitsPerPage": hits_per_page,
        "filters": filters,
        "page": page,
    })

    for attempt in range(3):
        try:
            req = urllib.request.Request(ALGOLIA_URL, data=body.encode('utf-8'),
                                         headers=ALGOLIA_HEADERS)
            with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            return data.get('hits', [])
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 ** attempt)
                continue
            break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            if attempt < 2:
                time.sleep(1)
                continue
            break

    logger.error(f"Algolia搜索失败 [{query[:30]}]")
    return []



def _strip_accents(title):
    folded = unicodedata.normalize("NFKD", title or "")
    return folded.encode("ascii", "ignore").decode("ascii")


def _query_variants(title):
    """同一部影片在 Algolia 索引里的多种可能检索词，按可信度排序。

    RT 索引对重音符号敏感，且部分影片收录在别名之下
    （《Léon: The Professional》挂在 1994 年的 《The Professional》 条目下）。
    """
    variants = []

    def emit(query):
        query = (query or "").strip()
        if query and query.lower() not in [v.lower() for v in variants]:
            variants.append(query)

    emit(title)
    emit(_strip_accents(title))
    if ":" in title:
        head, tail = title.split(":", 1)
        emit(tail)
        emit(_strip_accents(tail))
        emit(head)
    return variants[:4]


def _normalize_title(title):
    """比对用标题：去重音、去标点、折叠空白，使 'Amélie' 与 'Amelie' 等价。"""
    folded = _strip_accents((title or "").lower())
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", folded)).strip()


def _has_score(hit):
    rt = hit.get("rottenTomatoes") or {}
    return rt.get("criticsScore") is not None or rt.get("audienceScore") is not None


def _algolia_best_match(hits, search_title, year=None):
    """从 Algolia 结果中挑出确实是本片的那一条。

    只接受规范化后完全相同的标题，且上映年份必须吻合（±1 容忍发行跨年和索引年份误差；
    索引未记年份时放宽）。RT 的 content 索引含大量同名翻拍片与重映活动，
    宁缺毋滥 —— 宁可前端不显示番茄分，也不能把 2018 年同名片当成 1997 年的泰坦尼克。
    """
    query = _normalize_title(search_title)
    candidates = [h for h in (hits or []) if _normalize_title(h.get("title")) == query]
    if not candidates:
        return None

    if year:
        # 索引缺年份时放宽；年份明确但不符的一律否掉（同名翻拍片全靠这条区分）
        year_ok = [
            h for h in candidates
            if not h.get("releaseYear") or abs(h["releaseYear"] - year) <= 1
        ]
        if not year_ok:
            return None
        candidates = year_ok

    return max(candidates, key=lambda h: (_has_score(h), h.get("releaseYear") or 0))


def _algolia_to_movie_data(hit):
    """将 Algolia hit 转换为电影数据字典"""
    rt_data = hit.get('rottenTomatoes', {})

    tomatometer = rt_data.get('criticsScore', '')
    audience_score = rt_data.get('audienceScore', '')

    genres = hit.get('genres', [])
    genre = ', '.join(genres) if genres else ''

    cast_list = hit.get('cast', [])
    cast = ', '.join([c.get('name', '') for c in cast_list[:8] if c.get('name')]) if cast_list else ''

    crew_list = hit.get('crew', [])
    # 先按 role 过滤再截断：原实现先切 crew[:3]，前三条不是导演就取不到导演
    directors = ', '.join([c.get('name', '') for c in crew_list
                           if c.get('name') and c.get('role', '').lower() == 'director'][:3])
    writers = ', '.join([c.get('name', '') for c in crew_list
                         if c.get('name') and c.get('role', '').lower() in ('screenwriter', 'writer')][:5])
    if not directors:
        cast_crew = hit.get('castCrew', '')
        if cast_crew:
            dir_match = re.search(r'Director[s]*:\s*([^|]+)', cast_crew)
            if dir_match:
                directors = dir_match.group(1).strip()
            wr_match = re.search(r'Screenwriter[s]*:\s*([^|]+)', cast_crew)
            if wr_match:
                writers = wr_match.group(1).strip()

    vanity = hit.get('vanity', '')

    return {
        "rt_url": f"{RT_BASE_URL}/m/{vanity}" if vanity else '',
        "title": hit.get('title', ''),
        "original_title": hit.get('title', ''),
        "year": hit.get('releaseYear'),
        "rating": hit.get('rating', ''),
        "tomatometer": f"{tomatometer}%" if tomatometer else '',
        "audience_score": f"{audience_score}%" if audience_score else '',
        "genre": genre,
        "director": directors,
        "writers": writers,
        "cast": cast,
        "synopsis": hit.get('description', ''),
        "poster_url": hit.get('posterImageUrl', ''),
        "runtime": f"{hit.get('runTime', '')} minutes" if hit.get('runTime') else '',
        "release_date": '',
    }


# ==================== 主类 ====================
class RottenTomatoesCrawler:
    """烂番茄数据获取 — 纯 Algolia API，无浏览器依赖"""

    def __init__(self):
        logger.info("烂番茄爬虫初始化: Algolia API 模式 (无浏览器)")

    def search_movie(self, title, year=None):
        """搜索单部电影的 RT 数据 — 通过 Algolia API

        逐个尝试标题变体，比对时用当次的检索词（别名查询命中《The Professional》
        这类条目时，原始标题反而对不上）。
        """
        for query in _query_variants(title):
            best = _algolia_best_match(_algolia_search(query, hits_per_page=10), query, year)
            if best:
                return _algolia_to_movie_data(best)
        return None

    def close(self):
        """无资源需要关闭"""
        pass
