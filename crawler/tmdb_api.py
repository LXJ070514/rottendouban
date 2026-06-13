"""
TMDB API 数据获取模块
=====================
- 使用 TMDB v3 API 获取电影详情、海报、演员、编剧等
- 免费注册: https://www.themoviedb.org/settings/api
- 环境变量 TMDB_API_KEY 或 TMDB_BEARER_TOKEN
"""
import os
import re
import json
import ssl
import time
import random
import logging
import urllib.parse
import urllib.request
import urllib.error

logger = logging.getLogger("tmdb")

# SSL context
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# TMDB API 配置
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
TMDB_BEARER_TOKEN = os.environ.get("TMDB_BEARER_TOKEN", "")
TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"

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
    if not TMDB_API_KEY and not TMDB_BEARER_TOKEN:
        return None

    _rate_limit()

    url = f"{TMDB_BASE_URL}{endpoint}"
    params = params or {}

    if TMDB_BEARER_TOKEN:
        headers = {
            "Authorization": f"Bearer {TMDB_BEARER_TOKEN}",
            "Content-Type": "application/json",
        }
    else:
        params["api_key"] = TMDB_API_KEY
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
    """TMDB API 是否可用"""
    return bool(TMDB_API_KEY or TMDB_BEARER_TOKEN)


def search_movie(title, year=None):
    """搜索电影，返回最佳匹配的 TMDB ID"""
    params = {
        "query": title,
        "language": "en-US",
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

    # 模糊匹配
    for r in results:
        r_title = (r.get("title") or "").lower()
        r_original = (r.get("original_title") or "").lower()
        if title_lower in r_title or title_lower in r_original:
            return r["id"]
        if r_title in title_lower or r_original in title_lower:
            return r["id"]

    # 返回第一个结果
    return results[0]["id"] if results else None


def get_movie_details(movie_id):
    """获取电影详情 + credits"""
    data = _tmdb_request(
        f"/movie/{movie_id}",
        {"language": "en-US", "append_to_response": "credits"}
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
        "poster_local": "",
        "runtime": runtime_str,
        "release_date": release_date,
        "category": "豆瓣Top250",
    }
