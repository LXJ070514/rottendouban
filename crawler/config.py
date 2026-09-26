"""项目配置 — 纯 API 模式，无浏览器依赖"""
import os
import ssl
import logging

# ===== 项目基础配置 =====
PROJECT_NAME = "RottenDouban"
PROJECT_VERSION = "6.1.0"

CRAWLER_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(CRAWLER_DIR)

DATA_DIR = os.path.join(CRAWLER_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "movies.db")
LOG_FILE = os.path.join(DATA_DIR, "crawler.log")

# 部署目录是仓库根的 site/ —— 曾经误指向 crawler/site，
# 生成的数据落在 .gitignore 忽略的路径里，Pages 永远上传旧数据
SITE_DIR = os.path.join(ROOT_DIR, "site")


def ensure_dirs():
    for path in (DATA_DIR, os.path.join(SITE_DIR, "data")):
        os.makedirs(path, exist_ok=True)


# ===== 烂番茄 Algolia API =====
RT_BASE_URL = "https://www.rottentomatoes.com"

# ===== TMDB API =====
# 免费注册: https://www.themoviedb.org/settings/api
# 未配置时爬虫仅用 RT Algolia + 豆瓣；密钥经环境变量注入，勿写入仓库
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
TMDB_BEARER_TOKEN = os.environ.get("TMDB_BEARER_TOKEN", "")
TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"

# ===== 豆瓣 =====
DOUBAN_BASE_URL = "https://www.douban.com"
DOUBAN_SEARCH_URL = "https://search.douban.com/movie/subject_search"

# ===== TLS 校验 =====
# 默认校验证书。仅在本地代理做 HTTPS 中间人导致握手失败时设 CRAWLER_INSECURE_SSL=1
# 临时绕过；该开关不影响 CI（GitHub Runner 上无需设置），且只放宽到本项目的数据源请求。
INSECURE_SSL = os.environ.get("CRAWLER_INSECURE_SSL", "") == "1"


def build_ssl_context():
    context = ssl.create_default_context()
    if INSECURE_SSL:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


# ===== 评分权重（三源均为 0–100 折算后加权，缺源时权重按比例重分配）=====
SCORE_WEIGHTS = {
    "tomatometer": 0.3,
    "audience": 0.3,
    "douban": 0.4,
}

# ===== 字段限制 =====
MAX_DIRECTOR_LENGTH = 500
MAX_CAST_LENGTH = 1000
MAX_SYNOPSIS_LENGTH = 2000

# ===== 日志 =====
LOG_LEVEL = logging.INFO
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# ===== 评分历史 =====
SCORE_HISTORY_ENABLED = True
