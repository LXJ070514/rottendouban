"""
烂番茄数据获取 v5.0 — 纯API方式，无浏览器依赖
====================================
- 使用 RT 内部 Algolia 搜索 API 获取电影数据 (评分/类型/演员/海报)
- 使用 JSON-LD 从浏览页获取当前上映电影列表
- 完全不依赖 Selenium/Chrome，CI 环境完美运行
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

from crawler.config import RT_BASE_URL, RT_CATEGORIES, RT_MAX_MOVIES

logger = logging.getLogger("rotten_tomatoes")

# SSL context (不验证证书，爬虫场景无敏感数据)
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# ==================== Algolia 搜索 API ====================
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

PAGE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _fetch_url_with_retry(url, headers=None, timeout=15, max_retries=3, backoff_factor=1.0):
    """带指数退避重试的 URL 获取"""
    headers = headers or PAGE_HEADERS
    last_error = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                return resp.read().decode('utf-8', errors='replace')
        except urllib.error.HTTPError as e:
            last_error = e
            if e.code in (403, 429):
                wait = backoff_factor * (2 ** attempt) + random.uniform(0, 1)
                logger.warning(f"HTTP {e.code}, 重试 {attempt+1}/{max_retries}, 等待 {wait:.1f}s")
                time.sleep(wait)
            else:
                break
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_error = e
            if attempt < max_retries - 1:
                wait = backoff_factor * (2 ** attempt)
                logger.debug(f"网络错误, 重试 {attempt+1}/{max_retries}: {e}")
                time.sleep(wait)

    logger.debug(f"URL获取失败 [{url[:60]}]: {last_error}")
    return ''


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


def _extract_json_ld_movies(html):
    """从页面 HTML 中提取 JSON-LD 电影列表"""
    movies = []
    pattern = r'<script\s+type="application/ld\+json"[^>]*>(.*?)</script>'
    matches = re.findall(pattern, html, re.DOTALL)

    for match in matches:
        try:
            data = json.loads(match.strip())
        except json.JSONDecodeError:
            continue

        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get('@type') == 'ItemList':
                for elem in item.get('itemListElement', []):
                    if isinstance(elem, str):
                        slug = elem.rstrip('/').split('/m/')[-1]
                        name = slug.replace('_', ' ').replace('-', ' ').title()
                        movies.append({
                            'name': name,
                            'url': elem if elem.startswith('http') else f"{RT_BASE_URL}/m/{slug}",
                            'image': '',
                            'dateCreated': '',
                        })
                    elif isinstance(elem, dict):
                        movie_obj = elem.get('item', elem)
                        if isinstance(movie_obj, str):
                            slug = movie_obj.rstrip('/').split('/m/')[-1]
                            name = slug.replace('_', ' ').replace('-', ' ').title()
                            movies.append({
                                'name': name,
                                'url': movie_obj if movie_obj.startswith('http') else f"{RT_BASE_URL}/m/{slug}",
                                'image': '',
                                'dateCreated': '',
                            })
                        elif isinstance(movie_obj, dict):
                            if movie_obj.get('@type') == 'Movie' or movie_obj.get('name'):
                                movies.append({
                                    'name': movie_obj.get('name', ''),
                                    'url': movie_obj.get('url', ''),
                                    'image': movie_obj.get('image', ''),
                                    'dateCreated': movie_obj.get('dateCreated', ''),
                                })
            elif item.get('@type') == 'Movie':
                movies.append({
                    'name': item.get('name', ''),
                    'url': item.get('url', ''),
                    'image': item.get('image', ''),
                    'dateCreated': item.get('dateCreated', ''),
                })

    # 备用：从页面链接提取
    if not movies:
        link_pattern = r'href="(https://www\.rottentomatoes\.com/m/[^"]+)"'
        links = re.findall(link_pattern, html)
        seen = set()
        for link in links:
            if link not in seen:
                seen.add(link)
                slug = link.rstrip('/').split('/m/')[-1]
                name = slug.replace('_', ' ').title()
                movies.append({'name': name, 'url': link, 'image': '', 'dateCreated': ''})

    return movies


def _algolia_best_match(hits, search_title):
    """从 Algolia 搜索结果中选择最佳匹配"""
    search_lower = search_title.lower().strip()
    for hit in hits:
        title_lower = hit.get('title', '').lower()
        vanity = hit.get('vanity', '').lower()
        if search_lower in title_lower or title_lower in search_lower:
            return hit
        if search_lower.replace(' ', '_').replace('-', '_') in vanity:
            return hit
    return hits[0] if hits else None


def _algolia_to_movie_data(hit, browse_info=None):
    """将 Algolia hit 转换为电影数据字典"""
    rt_data = hit.get('rottenTomatoes', {})

    tomatometer = rt_data.get('criticsScore', '')
    audience_score = rt_data.get('audienceScore', '')

    genres = hit.get('genres', [])
    genre = ', '.join(genres) if genres else ''

    cast_list = hit.get('cast', [])
    cast = ', '.join([c.get('name', '') for c in cast_list[:8] if c.get('name')]) if cast_list else ''

    crew_list = hit.get('crew', [])
    directors = ', '.join([c.get('name', '') for c in crew_list[:3]
                          if c.get('name') and c.get('role', '').lower() == 'director'])
    # Screenwriters
    writers = ', '.join([c.get('name', '') for c in crew_list[:5]
                        if c.get('name') and c.get('role', '').lower() in ('screenwriter', 'writer')])
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
    rt_url = f"{RT_BASE_URL}/m/{vanity}" if vanity else ''

    if browse_info and not rt_url:
        rt_url = browse_info.get('url', '')

    poster_url = hit.get('posterImageUrl', '')
    if browse_info and not poster_url:
        poster_url = browse_info.get('image', '')

    return {
        "rt_url": rt_url,
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
        "poster_url": poster_url,
        "poster_local": "",
        "category": browse_info.get('category', '') if browse_info else '',
        "runtime": f"{hit.get('runTime', '')} minutes" if hit.get('runTime') else '',
        "release_date": '',
        "critics_consensus": '',
    }


# ==================== 主类 ====================
class RottenTomatoesCrawler:
    """烂番茄数据获取 — 纯 Algolia API + JSON-LD，无浏览器依赖"""

    def __init__(self):
        logger.info("烂番茄爬虫初始化: Algolia API 模式 (无浏览器)")

    def crawl_all(self):
        """爬取所有分类"""
        all_movies = []
        no = 0

        for category in RT_CATEGORIES:
            cat_name = category["name"]
            url = category["url"]
            logger.info(f"分类: {cat_name} | {url}")

            # 获取浏览页
            html = _fetch_url_with_retry(url)
            if not html:
                logger.error(f"浏览页获取失败: {cat_name}")
                continue

            # 提取电影列表
            movies_list = _extract_json_ld_movies(html)
            logger.info(f"JSON-LD找到 {len(movies_list)} 部电影")

            # 用 Algolia 获取详情
            for movie_info in movies_list[:RT_MAX_MOVIES]:
                title = movie_info.get('name', '')
                if not title:
                    continue

                algolia_hits = _algolia_search(title, hits_per_page=5)
                if algolia_hits:
                    best_hit = _algolia_best_match(algolia_hits, title)
                    if best_hit:
                        detailed = _algolia_to_movie_data(best_hit, movie_info)
                        detailed['category'] = cat_name
                        all_movies.append(detailed)
                        no += 1
                        logger.info(f"[{no}] {title[:40]} | 🍅{detailed.get('tomatometer','-')} 👥{detailed.get('audience_score','-')}")
                        continue

                # Algolia 未匹配，保留基本信息
                all_movies.append({
                    "rt_url": movie_info.get('url', ''),
                    "title": title,
                    "original_title": title,
                    "year": None, "rating": '',
                    "tomatometer": '', "audience_score": '',
                    "genre": '', "director": '', "cast": '',
                    "synopsis": '', "poster_url": movie_info.get('image', ''),
                    "poster_local": "", "category": cat_name,
                    "runtime": '', "release_date": '', "critics_consensus": '',
                })
                no += 1
                logger.warning(f"[{no}] Algolia未匹配: {title[:40]}")

            logger.info(f"分类 [{cat_name}] 完成: {len(movies_list)} 部电影")

        logger.info(f"爬取结束! 共 {len(all_movies)} 部电影")
        return all_movies

    def search_movie(self, title, year=None):
        """搜索单部电影的 RT 数据 — 通过 Algolia API"""
        # 尝试带年份搜索
        if year:
            hits = _algolia_search(title, hits_per_page=5)
            if hits:
                # 优先匹配年份
                for h in hits:
                    ry = h.get('releaseYear')
                    if ry and int(ry) == year:
                        best = _algolia_best_match(hits, title)
                        if best:
                            return _algolia_to_movie_data(best)
                # 年份不匹配则取最佳
                best = _algolia_best_match(hits, title)
                if best:
                    return _algolia_to_movie_data(best)

        # 不带年份搜索
        hits = _algolia_search(title, hits_per_page=5)
        if hits:
            best = _algolia_best_match(hits, title)
            if best:
                return _algolia_to_movie_data(best)

        return None

    def close(self):
        """无资源需要关闭"""
        pass
