"""
TMDB API 数据获取模块
=====================
- 使用 TMDB v3 API 获取电影详情、海报、演员、编剧等
- 免费注册: https://www.themoviedb.org/settings/api
- 环境变量 TMDB_API_KEY 或 TMDB_BEARER_TOKEN
"""
import os
import json
import time
import random
import logging
import urllib.parse
import urllib.request
import urllib.error

from crawler.config import build_ssl_context

logger = logging.getLogger("tmdb")

_SSL_CTX = build_ssl_context()

TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"

# 密钥走 getter 而非模块常量：常量会在 import 时固化成空串，
# 让 run.py / 测试里后设的环境变量静默失效。


def _credentials():
    return os.environ.get("TMDB_API_KEY", ""), os.environ.get("TMDB_BEARER_TOKEN", "")


# 速率控制: TMDB 限制 40 requests / 10 seconds
_MIN_REQUEST_INTERVAL = 0.3  # seconds between requests
_last_request_time = 0


def _rate_limit():
    """简单的速率限制"""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < _MIN_REQUEST_INTERVAL:
        time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
    _last_request_time = time.time()


def _tmdb_request(endpoint, params=None, timeout=10):
    """发送 TMDB API 请求"""
    api_key, bearer_token = _credentials()
    if not api_key and not bearer_token:
        return None

    _rate_limit()

    url = f"{TMDB_BASE_URL}{endpoint}"
    params = dict(params or {})

    if bearer_token:
        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
        }
    else:
        # v3 只认 query 里的 api_key；密钥因此会出现在 URL 中，优先用 bearer_token
        params["api_key"] = api_key
        headers = {"Content-Type": "application/json"}

    if params:
        url += "?" + urllib.parse.urlencode(params)

    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 2 ** attempt + random.uniform(0, 1)
                logger.warning(f"TMDB 429 rate limit, waiting {wait:.1f}s")
                time.sleep(wait)
                continue
            if e.code == 401:
                logger.error("TMDB API key invalid")
                return None
            logger.debug(f"TMDB HTTP {e.code}: {endpoint}")
            return None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if attempt < 2:
                time.sleep(1)
                continue
            logger.debug(f"TMDB request failed: {e}")
            return None

    return None


def is_available():
    """TMDB API 是否配置了密钥"""
    return any(_credentials())


def _search_language(query):
    """检索语言跟着查询语言走，让"精确匹配"那一级能真正生效。

    注：反查本身**不依赖**这一点 —— run 36267829360 里用 en-US 搜中文片名
    也拿到了 153/250 的反查结果（TMDB 会索引译名，结果落在 results[0]）。
    改 zh-CN 是为了把命中从"兜底取首条"抬到"标题精确匹配"，
    少走后面那道纯靠年份的模糊判断。上一版注释把覆盖率的锅算在这里是错的：
    当时那次低覆盖率来自 1413fc5，那个版本还没有反查代码。
    """
    return "en-US" if (query or "").isascii() else "zh-CN"


def _year_of(release_date):
    try:
        return int((release_date or "")[:4])
    except (TypeError, ValueError):
        return None


def search_movie(title, year=None):
    """搜索电影，返回最佳匹配的 TMDB ID"""
    params = {
        "query": title,
        "language": _search_language(title),
        "page": 1,
        "include_adult": "false",
    }
    if year:
        params["primary_release_year"] = str(year)

    data = _tmdb_request("/search/movie", params)
    if not data or not data.get("results"):
        # 尝试不带年份搜索
        if year:
            params.pop("primary_release_year", None)
            data = _tmdb_request("/search/movie", params)
        if not data or not data.get("results"):
            return None

    results = data["results"]
    # 最佳匹配：标题完全匹配 + 年份匹配
    title_lower = title.lower().strip()
    for r in results:
        r_title = (r.get("title") or "").lower().strip()
        r_original = (r.get("original_title") or "").lower().strip()
        if r_title == title_lower or r_original == title_lower:
            return r["id"]

    # 模糊匹配。改用 zh-CN 检索后这一级才真正有被触发的机会
    # （en-US 下 r_title 是英文，对中文查询几乎不可能构成子串关系），
    # 而子串关系是双向的 —— "证人" 是 "控方证人" 的子串，反过来也成立，
    # 光靠标题就能把无关影片选进来。故有年份时要求年份也吻合。
    for r in results:
        r_title = (r.get("title") or "").lower()
        r_original = (r.get("original_title") or "").lower()
        fuzzy = (title_lower in r_title or title_lower in r_original
                 or r_title in title_lower or r_original in title_lower)
        if not fuzzy:
            continue
        r_year = _year_of(r.get("release_date"))
        if year and r_year and abs(r_year - year) > 2:
            continue
        return r["id"]

    # 兜底取首个结果 —— TMDB 自己的相关度排序，用 en-US 检索时正是靠这一级
    # 拿到 153/250 的英文名（run 36267829360），是经验证有效的路径，不动它。
    return results[0]["id"] if results else None


def get_movie_details(movie_id):
    """获取电影详情 + credits"""
    data = _tmdb_request(
        f"/movie/{movie_id}",
        {"language": "en-US", "append_to_response": "credits,release_dates"}
    )
    return data


def search_and_get_details(title, year=None):
    """搜索电影并获取完整详情，返回标准化的电影数据字典"""
    movie_id = search_movie(title, year)
    if not movie_id:
        logger.debug(f"TMDB 未找到: {title} ({year})")
        return None

    data = get_movie_details(movie_id)
    if not data:
        return None

    # 提取导演
    directors = []
    writers = []
    if data.get("credits", {}).get("crew"):
        for person in data["credits"]["crew"]:
            job = (person.get("job") or "").lower()
            if job == "director":
                directors.append(person.get("name", ""))
            elif job in ("writer", "screenplay", "story"):
                writers.append(person.get("name", ""))

    # 去重保持顺序
    directors = list(dict.fromkeys(directors))[:5]
    writers = list(dict.fromkeys(writers))[:5]

    # 提取演员 (前8位)
    cast_list = []
    if data.get("credits", {}).get("cast"):
        for person in data["credits"]["cast"][:8]:
            cast_list.append(person.get("name", ""))

    # 提取类型
    genres = [g.get("name", "") for g in (data.get("genres") or []) if g.get("name")]

    # 海报
    poster_path = data.get("poster_path", "")
    poster_url = f"{TMDB_IMAGE_BASE}{poster_path}" if poster_path else ""

    # 年份
    release_date = data.get("release_date", "")
    movie_year = None
    if release_date:
        try:
            movie_year = int(release_date[:4])
        except (ValueError, IndexError):
            pass

    # 运行时间
    runtime = data.get("runtime")
    runtime_str = f"{runtime} minutes" if runtime else ""

    # MPAA 评级
    rating = ""
    for rd in (data.get("release_dates", {}).get("results") or []):
        if rd.get("iso_3166_1") == "US":
            for rd_item in (rd.get("release_dates") or []):
                cert = rd_item.get("certification", "")
                if cert:
                    rating = cert
                    break
            break

    return {
        "title": data.get("title", ""),
        "original_title": data.get("original_title", data.get("title", "")),
        "year": movie_year,
        "rating": rating,
        "genre": ", ".join(genres),
        "director": ", ".join(directors),
        "writers": ", ".join(writers),
        "cast": ", ".join(cast_list),
        "synopsis": data.get("overview", ""),
        "poster_url": poster_url,
        "runtime": runtime_str,
        "release_date": release_date,
        "category": "豆瓣Top250",
    }
