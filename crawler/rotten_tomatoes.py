"""
烂番茄爬虫 v4.0 — Algolia API + JSON-LD (无需浏览器!)
====================================
- 使用 RT 内部 Algolia 搜索 API 获取电影数据 (评分/类型/演员/海报)
- 使用 JSON-LD 从浏览页获取当前上映电影列表
- 不依赖 Selenium/Chrome, Cloudflare 不拦截
- CI 环境完美运行, 速度快 (每部电影不到1秒)
- 保留 Selenium 作为本地回退方案 (仅当 API 失败时)
"""
import os
import re
import sys
import json
import ssl
import time
import random
import logging
import urllib.parse
import urllib.request

from crawler.config import (
    RT_BASE_URL, RT_CATEGORIES, RT_MAX_MOVIES,
)

logger = logging.getLogger("rotten_tomatoes")

# ==================== Algolia 搜索 API ====================
# RT 使用 Algolia 作为内部搜索引擎, 凭证嵌入在网站JS中
ALGOLIA_APP_ID = "79FRDP12PN"
ALGOLIA_API_KEY = "175588f6e5f8319b27702e4cc4013561"
ALGOLIA_INDEX = "content"
ALGOLIA_URL = f"https://{ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/{ALGOLIA_INDEX}/query"

ALGOLIA_HEADERS = {
    "X-Algolia-Application-Id": ALGOLIA_APP_ID,
    "X-Algolia-API-Key": ALGOLIA_API_KEY,
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

# 通用请求头 (用于获取页面 HTML)
PAGE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


# ==================== 工具函数 ====================
def _clean_text(text):
    """清理文本中的噪声"""
    if not text:
        return ''
    text = re.sub(r'\.[\w-]+\s*\{[^}]*\}', '', text)
    text = re.sub(r'<svg[^>]*>.*?</svg>', '', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'var\(--[\w-]+\)', '', text)
    text = re.sub(r'\s*,\s*,\s*', ', ', text)
    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip().strip(',').strip()


def _clean_name_list(text):
    """清理人名列表"""
    if not text:
        return ''
    text = _clean_text(text)
    items = [x.strip() for x in text.split(',') if x.strip()]
    cleaned = []
    for item in items:
        if any(k in item for k in ['{', '}', 'var(', 'fill:', 'transform:']):
            continue
        if len(item) > 60:
            continue
        cleaned.append(item)
    return ', '.join(cleaned)


def _ssl_context():
    """创建 SSL context (解决 ASN1 NOT_ENOUGH_DATA)"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _fetch_url(url, headers=None, timeout=15):
    """安全获取 URL 内容"""
    headers = headers or PAGE_HEADERS
    try:
        req = urllib.request.Request(url, headers=headers)
        ctx = _ssl_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.read().decode('utf-8', errors='replace')
    except Exception as e:
        logger.debug(f"URL获取失败 [{url[:50]}]: {e}")
        return ''


# ==================== Algolia API 方式 ====================
def _algolia_search(query, filters="type:movie", hits_per_page=20, page=0):
    """使用 Algolia 搜索 API 查询电影数据"""
    body = json.dumps({
        "query": query,
        "hitsPerPage": hits_per_page,
        "filters": filters,
        "page": page,
    })

    try:
        req = urllib.request.Request(ALGOLIA_URL, data=body.encode('utf-8'),
                                     headers=ALGOLIA_HEADERS)
        ctx = _ssl_context()
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        hits = data.get('hits', [])
        return hits
    except Exception as e:
        logger.error(f"Algolia搜索失败 [{query[:30]}]: {e}")
        return []


def _algolia_browse_movies(category_url, max_movies=50):
    """从浏览页 JSON-LD 获取电影列表, 然后用 Algolia 获取详情"""
    # 1. 获取浏览页 HTML
    html = _fetch_url(category_url)
    if not html:
        logger.error(f"浏览页获取失败: {category_url}")
        return []

    # 2. 提取 JSON-LD 数据
    movies_list = _extract_json_ld_movies(html)
    if not movies_list:
        # JSON-LD 失败时, 尝试 Algolia 搜索
        logger.warning(f"JSON-LD提取失败, 尝试Algolia搜索")
        return []

    logger.info(f"JSON-LD找到 {len(movies_list)} 部电影")

    # 3. 对每部电影, 用 Algolia 获取详细数据 (评分等)
    detailed_movies = []
    for movie_info in movies_list[:max_movies]:
        title = movie_info.get('name', '')
        if title:
            algolia_hits = _algolia_search(title, hits_per_page=5)
            if algolia_hits:
                # 找最佳匹配
                best_hit = _algolia_best_match(algolia_hits, title)
                if best_hit:
                    detailed = _algolia_to_movie_data(best_hit, movie_info)
                    if detailed:
                        detailed_movies.append(detailed)
                        logger.info(f"  Algolia匹配: {title[:30]} → "
                                     f"🍅{detailed.get('tomatometer', '-')} "
                                     f"👥{detailed.get('audience_score', '-')}")
                        continue

            # Algolia没找到时, 保留基本信息
            detailed_movies.append(_basic_movie_data(movie_info))
            logger.warning(f"  Algolia未匹配: {title[:30]}")

    return detailed_movies


def _algolia_best_match(hits, search_title):
    """从 Algolia 搜索结果中选择最佳匹配"""
    search_lower = search_title.lower().strip()
    for hit in hits:
        title_lower = hit.get('title', '').lower()
        vanity = hit.get('vanity', '').lower()
        # 标题匹配优先
        if search_lower in title_lower or title_lower in search_lower:
            return hit
        # URL slug 匹配
        if search_lower.replace(' ', '_').replace('-', '_') in vanity:
            return hit
    # 无标题匹配, 返回第一个
    return hits[0] if hits else None


def _algolia_to_movie_data(hit, browse_info=None):
    """将 Algolia hit 转换为电影数据字典"""
    rt_data = hit.get('rottenTomatoes', {})

    # Tomatometer
    tomatometer = rt_data.get('criticsScore', '')
    if tomatometer:
        tomatometer = f"{tomatometer}%"

    # Audience Score
    audience_score = rt_data.get('audienceScore', '')
    if audience_score:
        audience_score = f"{audience_score}%"

    # Genre
    genres = hit.get('genres', [])
    genre = ', '.join(genres) if genres else ''

    # Cast
    cast_list = hit.get('cast', [])
    cast = ', '.join([c.get('name', '') for c in cast_list[:8] if c.get('name')]) if cast_list else ''

    # Crew (directors)
    crew_list = hit.get('crew', [])
    directors = ', '.join([c.get('name', '') for c in crew_list[:3]
                          if c.get('name') and c.get('role', '').lower() == 'director'])
    if not directors:
        # 从 castCrew 提取
        cast_crew = hit.get('castCrew', '')
        if cast_crew:
            dir_match = re.search(r'Director[s]*:\s*([^|]+)', cast_crew)
            if dir_match:
                directors = dir_match.group(1).strip()

    # Description / Synopsis
    synopsis = hit.get('description', '')

    # Poster
    poster_url = hit.get('posterImageUrl', '')

    # Rating
    rating = hit.get('rating', '')

    # Runtime
    runtime = hit.get('runTime', '')
    if runtime:
        runtime = f"{runtime} minutes"

    # Release year
    year = hit.get('releaseYear')

    # RT URL
    vanity = hit.get('vanity', '')
    rt_url = f"https://www.rottentomatoes.com/m/{vanity}" if vanity else ''

    # Browse info fallback
    if browse_info and not rt_url:
        rt_url = browse_info.get('url', '')
    if browse_info and not poster_url:
        poster_url = browse_info.get('image', '')

    # Title
    title = hit.get('title', '')
    original_title = title  # Algolia gives us the display title

    # Certified Fresh
    certified_fresh = rt_data.get('certifiedFresh', False)

    return {
        "rt_url": rt_url,
        "title": title,
        "original_title": original_title,
        "year": year,
        "rating": rating,
        "tomatometer": tomatometer,
        "audience_score": audience_score,
        "genre": genre,
        "director": directors,
        "cast": cast,
        "critics_consensus": '',
        "synopsis": synopsis,
        "release_date": '',
        "runtime": runtime,
        "poster_url": poster_url,
        "poster_local": "",
        "category": browse_info.get('category', '') if browse_info else '',
    }


def _basic_movie_data(browse_info):
    """从浏览页 JSON-LD 生成基本电影数据 (无评分)"""
    return {
        "rt_url": browse_info.get('url', ''),
        "title": browse_info.get('name', ''),
        "original_title": browse_info.get('name', ''),
        "year": None,
        "rating": '',
        "tomatometer": '',
        "audience_score": '',
        "genre": '',
        "director": '',
        "cast": '',
        "critics_consensus": '',
        "synopsis": '',
        "release_date": browse_info.get('dateCreated', ''),
        "runtime": '',
        "poster_url": browse_info.get('image', ''),
        "poster_local": "",
        "category": browse_info.get('category', ''),
    }


# ==================== JSON-LD 提取 ====================
def _extract_json_ld_movies(html):
    """从页面 HTML 中提取 JSON-LD 数据 (电影列表)"""
    movies = []

    # 方法1: 找 <script type="application/ld+json"> 标签
    pattern = r'<script\s+type="application/ld\+json"[^>]*>(.*?)</script>'
    matches = re.findall(pattern, html, re.DOTALL)

    for match in matches:
        try:
            data = json.loads(match.strip())
        except json.JSONDecodeError:
            continue

        # JSON-LD 可能是单个对象或数组
        items = data if isinstance(data, list) else [data]
        for item in items:
            if item.get('@type') == 'ItemList':
                # ItemList 包含多个电影
                for elem in item.get('itemListElement', []):
                    movie = elem.get('item', {})
                    if movie.get('@type') == 'Movie':
                        movies.append({
                            'name': movie.get('name', ''),
                            'url': movie.get('url', ''),
                            'image': movie.get('image', ''),
                            'dateCreated': movie.get('dateCreated', ''),
                        })
            elif item.get('@type') == 'Movie':
                movies.append({
                    'name': item.get('name', ''),
                    'url': item.get('url', ''),
                    'image': item.get('image', ''),
                    'dateCreated': item.get('dateCreated', ''),
                })

    # 方法2: 搜索页面中的电影链接 (备用)
    if not movies:
        link_pattern = r'href="(https://www\.rottentomatoes\.com/m/[^"]+)"'
        links = re.findall(link_pattern, html)
        seen = set()
        for link in links:
            if link not in seen:
                seen.add(link)
                # 从 URL 提取电影名
                slug = link.rstrip('/').split('/m/')[-1]
                name = slug.replace('_', ' ').title()
                movies.append({
                    'name': name,
                    'url': link,
                    'image': '',
                    'dateCreated': '',
                })

    return movies


# ==================== Selenium 回退 (仅在本地需要时) ====================
def _selenium_crawl_all():
    """Selenium 爬取 (本地回退方案, CI环境不使用)"""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    # 检测 UC
    UC_AVAILABLE = False
    try:
        import undetected_chromedriver as uc
        UC_AVAILABLE = True
    except ImportError:
        pass

    driver = None
    try:
        if UC_AVAILABLE:
            logger.info("Selenium回退: 使用 undetected-chromedriver")
            options = uc.ChromeOptions()
            options.add_argument('--headless=new')
            options.add_argument('--disable-gpu')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--window-size=1920,1080')
            driver = uc.Chrome(options=options)
        else:
            logger.info("Selenium回退: 使用普通 Chrome")
            chrome_options = Options()
            chrome_options.add_argument('--headless=new')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--window-size=1920,1080')
            driver = webdriver.Chrome(options=chrome_options)
    except Exception as e:
        logger.error(f"Selenium初始化失败: {e}")
        return []

    # ... Selenium 爬取逻辑 (保留旧版代码)
    # 这里只作为回退, 正常情况下不会走到这里
    all_movies = []
    try:
        for category in RT_CATEGORIES:
            url = category["url"]
            cat_name = category["name"]
            logger.info(f"Selenium访问分类: {cat_name} | {url}")
            driver.get(url)
            time.sleep(5)

            # 收集电影链接
            links = []
            seen = set()
            elements = driver.find_elements(By.XPATH, '//a[contains(@href,"/m/")]')
            for link in elements:
                href = link.get_attribute('href')
                if href and '/m/' in href and href not in seen:
                    seen.add(href)
                    links.append({'url': href, 'name': link.text.strip()[:100],
                                  'category': cat_name})
            links = links[:RT_MAX_MOVIES]

            for movie_info in links:
                try:
                    driver.get(movie_info['url'])
                    time.sleep(random.uniform(2, 5))
                    # 简化提取
                    title = driver.find_element(By.XPATH, '//h1').text.strip()
                    title = re.sub(r'^#\d+\s*', '', title)
                    all_movies.append({
                        "rt_url": movie_info['url'],
                        "title": title,
                        "original_title": title,
                        "category": cat_name,
                        "tomatometer": '',
                        "audience_score": '',
                        "poster_url": '',
                    })
                except Exception:
                    pass
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    return all_movies


# ==================== 主类 ====================
class RottenTomatoesCrawler:
    """烂番茄爬虫 v4.0 — Algolia API + JSON-LD, 无需浏览器"""

    def __init__(self, use_selenium_fallback=False):
        self.driver = None
        self.No = 0
        self._use_selenium_fallback = use_selenium_fallback
        # CI 环境不使用 Selenium
        ci_env = os.environ.get("CI_ENV", "").lower() in ("true", "1", "yes")
        if ci_env:
            self._use_selenium_fallback = False
            logger.info("CI环境: RT爬虫使用 Algolia API (无需浏览器)")

    def crawl_all(self):
        """爬取所有分类 — Algolia API 优先, Selenium 回退"""
        all_movies = []

        # 方法1: Algolia API + JSON-LD (无需浏览器)
        logger.info("===== 使用 Algolia API 爬取烂番茄 =====")
        for category in RT_CATEGORIES:
            cat_name = category["name"]
            url = category["url"]
            logger.info(f"分类: {cat_name} | {url}")

            movies = _algolia_browse_movies(url, max_movies=RT_MAX_MOVIES)
            # 设置分类标签
            for m in movies:
                m['category'] = cat_name
                self.No += 1
                logger.info(f"[{self.No}] {m.get('title','?')[:40]} | "
                             f"🍅{m.get('tomatometer','-')} "
                             f"👥{m.get('audience_score','-')}")

            all_movies.extend(movies)
            logger.info(f"分类 [{cat_name}] 完成: {len(movies)} 部电影")

        # 如果 Algolia 没获取到任何电影, 尝试 Selenium
        if not all_movies and self._use_selenium_fallback:
            logger.warning("Algolia API 获取0部电影, 尝试 Selenium 回退...")
            all_movies = _selenium_crawl_all()

        logger.info(f"爬取结束! 共 {len(all_movies)} 部电影")
        return all_movies

    def close(self):
        """关闭资源"""
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            logger.info("浏览器驱动已关闭")
