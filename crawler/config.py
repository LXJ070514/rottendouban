"""项目配置文件"""
import os
import logging

# ===== 项目基础配置 =====
PROJECT_NAME = "RottenDouban"
PROJECT_VERSION = "1.0.0"
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_DIR, "data")
POSTERS_DIR = os.path.join(DATA_DIR, "posters")
SITE_DIR = os.path.join(PROJECT_DIR, "site")
DB_PATH = os.path.join(DATA_DIR, "movies.db")
JSON_OUTPUT = os.path.join(SITE_DIR, "data", "movies.json")
CSV_OUTPUT = os.path.join(SITE_DIR, "data", "movies.csv")

# 确保目录存在
for d in [DATA_DIR, POSTERS_DIR, os.path.join(SITE_DIR, "data")]:
    os.makedirs(d, exist_ok=True)

# ===== 烂番茄爬虫配置 =====
RT_BASE_URL = "https://www.rottentomatoes.com"
RT_CATEGORIES = [
    {"name": "影院热映", "url": "https://www.rottentomatoes.com/browse/movies_in_theaters"},
    {"name": "即将上映", "url": "https://www.rottentomatoes.com/browse/movies_coming_soon"},
    {"name": "家庭观影", "url": "https://www.rottentomatoes.com/browse/movies_at_home"},
]
RT_MAX_MOVIES = int(os.environ.get("RT_MAX_MOVIES", 50))  # 每个分类最大抓取数 (可通过环境变量覆盖)

# ===== 豆瓣爬虫配置 =====
DOUBAN_BASE_URL = "https://www.douban.com"
DOUBAN_SEARCH_URL = "https://search.douban.com/movie/subject_search"
DOUBAN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Referer": "https://www.douban.com/",
}

# ===== 评分权重配置 =====
SCORE_WEIGHTS = {
    "tomatometer": 0.3,    # 烂番茄影评人评分权重
    "audience": 0.3,       # 烂番茄观众评分权重
    "douban": 0.4,         # 豆瓣评分权重
}

# ===== 图片下载配置 =====
IMAGE_DOWNLOAD_TIMEOUT = 30
IMAGE_RETRY_MAX = 3
IMAGE_RETRY_BACKOFF_FACTOR = 2  # 指数退避因子
IMAGE_THREAD_POOL_SIZE = 5

# ===== 数据库批量提交 =====
DB_BATCH_SIZE = 5

# ===== 字段宽度限制 =====
MAX_DIRECTOR_LENGTH = 500
MAX_CAST_LENGTH = 1000
MAX_SYNOPSIS_LENGTH = 2000

# ===== 日志配置 =====
LOG_LEVEL = logging.INFO
LOG_FILE = os.path.join(DATA_DIR, "crawler.log")
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# ===== 人类行为模拟配置 =====
HUMAN_DELAY_MIN = 1.0
HUMAN_DELAY_MAX = 4.0
HUMAN_SCROLL_PAUSE_MIN = 0.5
HUMAN_SCROLL_PAUSE_MAX = 1.5

# ===== 评分历史追踪 =====
SCORE_HISTORY_ENABLED = True

# ===== ChromeDriver 配置 =====
CHROME_OPTIONS = {
    "headless": True,
    "no_sandbox": True,
    "disable_gpu": True,
    "window_size": "1920,1080",
    "disable_blink_features": "AutomationControlled",
}