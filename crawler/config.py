"""项目配置文件 — 纯API模式，无浏览器依赖"""
import os
import logging

# ===== 项目基础配置 =====
PROJECT_NAME = "RottenDouban"
PROJECT_VERSION = "5.0.0"
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_DIR, "data")
POSTERS_DIR = os.path.join(DATA_DIR, "posters")
SITE_DIR = os.path.join(PROJECT_DIR, "site")
DB_PATH = os.path.join(DATA_DIR, "movies.db")

# 确保目录存在
for d in [DATA_DIR, POSTERS_DIR, os.path.join(SITE_DIR, "data")]:
    os.makedirs(d, exist_ok=True)

# ===== 烂番茄 API 配置 =====
RT_BASE_URL = "https://www.rottentomatoes.com"
RT_CATEGORIES = [
    {"name": "影院热映", "url": "https://www.rottentomatoes.com/browse/movies_in_theaters"},
    {"name": "即将上映", "url": "https://www.rottentomatoes.com/browse/movies_coming_soon"},
    {"name": "家庭观影", "url": "https://www.rottentomatoes.com/browse/movies_at_home"},
]
RT_MAX_MOVIES = int(os.environ.get("RT_MAX_MOVIES", 50))

# ===== TMDB API 配置 =====
# 免费注册: https://www.themoviedb.org/settings/api
# 设置环境变量 TMDB_API_KEY 或 TMDB_BEARER_TOKEN
# TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")  # 在 tmdb_api.py 中读取

# ===== 豆瓣 API 配置 =====
DOUBAN_BASE_URL = "https://www.douban.com"
DOUBAN_SEARCH_URL = "https://search.douban.com/movie/subject_search"

# ===== 评分权重 =====
SCORE_WEIGHTS = {
    "tomatometer": 0.3,
    "audience": 0.3,
    "douban": 0.4,
}

# ===== 图片下载 =====
IMAGE_DOWNLOAD_TIMEOUT = 30
IMAGE_RETRY_MAX = 3
IMAGE_RETRY_BACKOFF_FACTOR = 2
IMAGE_THREAD_POOL_SIZE = 5

# ===== 数据库 =====
DB_BATCH_SIZE = 5

# ===== 字段限制 =====
MAX_DIRECTOR_LENGTH = 500
MAX_CAST_LENGTH = 1000
MAX_SYNOPSIS_LENGTH = 2000

# ===== 日志 =====
LOG_LEVEL = logging.INFO
LOG_FILE = os.path.join(DATA_DIR, "crawler.log")
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# ===== 评分历史 =====
SCORE_HISTORY_ENABLED = True
