"""主入口 v4.0 - 爬虫架构重构
============================
- 烂番茄和豆瓣爬取分离
- 豆瓣数据缓存 (只爬一次, 新电影才搜索)
- 支持3种运行模式:
  * full: RT爬取 + 豆瓣匹配 + 生成网站 (默认)
  * douban_only: 仅豆瓣匹配 (RT数据从已有数据库读取)
  * site_only: 仅生成网站 (从已有数据库)
- CI环境下豆瓣匹配不依赖浏览器 (只用API)
- RT爬虫失败时仍可用已有数据继续
"""
import os
import sys
import re
import ssl
import json
import csv
import io
import shutil
import logging
import signal
import time
import traceback
import urllib.request
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import requests as _requests_lib
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

from crawler.config import (
    PROJECT_DIR, DATA_DIR, POSTERS_DIR, SITE_DIR,
    DB_PATH, JSON_OUTPUT, CSV_OUTPUT,
    SCORE_WEIGHTS, IMAGE_DOWNLOAD_TIMEOUT, IMAGE_RETRY_MAX,
    IMAGE_RETRY_BACKOFF_FACTOR, IMAGE_THREAD_POOL_SIZE,
    DB_BATCH_SIZE, SCORE_HISTORY_ENABLED,
    LOG_LEVEL, LOG_FILE, LOG_FORMAT, LOG_DATE_FORMAT,
)
from crawler.database import Database


def setup_logging():
    """配置日志系统 - 替代 print，修复 Windows 中文编码"""
    os.makedirs(DATA_DIR, exist_ok=True)

    # 修复 Windows 控制台 UTF-8 输出
    if sys.platform == 'win32':
        try:
            sys.stdout.reconfigure(encoding='utf-8')
            sys.stderr.reconfigure(encoding='utf-8')
        except Exception:
            pass

    root_logger = logging.getLogger()
    root_logger.setLevel(LOG_LEVEL)

    # 文件日志
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setLevel(LOG_LEVEL)
    fh.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
    root_logger.addHandler(fh)

    # 控制台日志 (强制 UTF-8)
    ch = logging.StreamHandler(
        open(sys.stdout.fileno(), mode='w', encoding='utf-8', closefd=False)
    )
    ch.setLevel(LOG_LEVEL)
    ch.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
    root_logger.addHandler(ch)

    logger = logging.getLogger("main")
    logger.info("=" * 60)
    logger.info(f"RottenDouban 爬虫 v4.0 启动 - {datetime.now()}")
    logger.info(f"运行模式: {os.environ.get('CRAWLER_MODE', 'full')}")
    logger.info("=" * 60)
    return logger


# ==================== SIGTERM 信号处理 (CI超时保存部分数据) ====================
_partial_movies = []
_db_instance = None
_rt_crawler_instance = None

def _sigterm_handler(signum, frame):
    """收到 SIGTERM 时保存已有数据 (GitHub Actions 超时前约10秒触发)"""
    logger = logging.getLogger("main")
    logger.warning(f"收到 SIGTERM 信号! 尝试保存部分数据 ({len(_partial_movies)} 部电影)")

    global _db_instance, _rt_crawler_instance
    if _partial_movies and _db_instance:
        try:
            # 计算加权评分
            for movie in _partial_movies:
                ws = calc_weighted_score(
                    movie.get("tomatometer", ""),
                    movie.get("audience_score", ""),
                    movie.get("douban_score", ""),
                )
                movie["weighted_score"] = f"{ws:.2f}" if ws is not None else ""

            # 下载海报
            poster_results = download_posters_concurrent(_partial_movies)
            for movie in _partial_movies:
                title = movie.get("title", "")
                if title in poster_results:
                    _, filename = poster_results[title]
                    movie["poster_local"] = filename
                else:
                    movie["poster_local"] = ""

            # 入库
            success_count = _db_instance.batch_insert_movies(_partial_movies)
            logger.warning(f"部分数据入库完成: {success_count}/{len(_partial_movies)}")

            # 生成网站数据
            generate_site_data(_db_instance, SITE_DIR)
            logger.warning("部分数据网站生成完成!")
        except Exception as e:
            logger.error(f"保存部分数据失败: {e}")

    # 关闭浏览器
    if _rt_crawler_instance:
        try:
            _rt_crawler_instance.close()
        except Exception:
            pass
    if _db_instance:
        try:
            _db_instance.close()
        except Exception:
            pass

    logger.warning("SIGTERM 处理完成, 退出")
    sys.exit(0)

# 注册信号处理
try:
    signal.signal(signal.SIGTERM, _sigterm_handler)
except (OSError, ValueError):
    pass  # Windows 不支持 SIGTERM


# ==================== 加权评分 ====================
def _parse_score(value):
    """
    解析评分字符串为 0-1 标准化浮点数
    - "85%" → 0.85
    - "7.8" (豆瓣0-10) → 0.78
    - "78" (>10的裸数, 0-100) → 0.78
    失败返回 None
    """
    if not value:
        return None
    raw = str(value).replace('%', '').strip()
    try:
        num = float(raw)
    except (ValueError, TypeError):
        return None
    if num <= 10:
        return num / 10.0
    else:
        return num / 100.0


def calc_weighted_score(tomato_raw, audience_raw, douban_raw):
    """计算加权评分 (0-100), 权重: 番茄0.3 + 观众0.3 + 豆瓣0.4"""
    scores = {
        'tomatometer': _parse_score(tomato_raw),
        'audience': _parse_score(audience_raw),
        'douban': _parse_score(douban_raw),
    }
    weights = dict(SCORE_WEIGHTS)
    available = {k: v for k, v in scores.items() if v is not None}
    if not available:
        return None
    total_weight = sum(weights[k] for k in available)
    if total_weight == 0:
        return None
    weighted = sum(scores[k] * (weights[k] / total_weight) for k in available)
    return round(weighted * 100, 2)


# ==================== 图片下载 ====================
def download_image(url, filename, retries=None):
    """带指数退避重试的图片下载"""
    if not url:
        return
    if retries is None:
        retries = IMAGE_RETRY_MAX
    filepath = os.path.join(POSTERS_DIR, filename)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    if os.path.exists(filepath):
        return filepath

    for attempt in range(retries):
        try:
            if REQUESTS_AVAILABLE:
                resp = _requests_lib.get(
                    url, timeout=IMAGE_DOWNLOAD_TIMEOUT,
                    allow_redirects=True, verify=False
                )
                resp.raise_for_status()
                with open(filepath, 'wb') as f:
                    f.write(resp.content)
                logging.getLogger("main").info(f"图片下载成功: {filename}")
                return filepath

            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(url)
            data = urllib.request.urlopen(
                req, timeout=IMAGE_DOWNLOAD_TIMEOUT, context=ssl_ctx
            ).read()
            with open(filepath, 'wb') as f:
                f.write(data)
            logging.getLogger("main").info(f"图片下载成功: {filename}")
            return filepath

        except Exception as err:
            if attempt < retries - 1:
                wait = 2 ** attempt
                logging.getLogger("main").warning(
                    f"图片重试 {attempt+1}/{retries}: {filename}, 等待{wait}s")
                time.sleep(wait)
            else:
                logging.getLogger("main").error(
                    f"图片下载最终失败 {filename}: {err}")
    return None


def download_posters_concurrent(movies_list):
    """线程池并发下载海报"""
    logger = logging.getLogger("main")
    logger.info(f"开始并发下载海报: {len(movies_list)} 部电影")
    executor = ThreadPoolExecutor(max_workers=IMAGE_THREAD_POOL_SIZE)
    results = {}

    futures = {}
    for movie in movies_list:
        poster_url = movie.get("poster_url") or movie.get("douban_poster")
        if poster_url:
            title = movie.get("title", "unknown")
            idx = movies_list.index(movie) + 1
            no_str = str(idx).zfill(6)
            ext = '.jpg'
            if poster_url:
                match = re.search(r'\.(jpg|jpeg|png|webp)(?:\?|$)', poster_url, re.I)
                if match:
                    ext = match.group(0).split('?')[0]
            filename = f"{no_str}{ext}"
            future = executor.submit(download_image, poster_url, filename)
            futures[future] = (title, filename)

    for future in as_completed(futures):
        title, filename = futures[future]
        try:
            local_path = future.result()
            if local_path:
                results[title] = (local_path, filename)
        except Exception as e:
            logger.error(f"海报下载线程异常: {title} - {e}")

    executor.shutdown(wait=True)
    logger.info(f"海报下载完成: 成功 {len(results)}/{len(movies_list)}")
    return results


# ==================== 网站数据生成 ====================
def generate_site_data(db, output_dir):
    """生成网站所需的 JSON 和 CSV 数据文件"""
    logger = logging.getLogger("main")

    json_data = db.export_json()
    json_dir = os.path.join(output_dir, "data")
    os.makedirs(json_dir, exist_ok=True)
    json_path = os.path.join(json_dir, "movies.json")
    with open(json_path, 'w', encoding='utf-8') as f:
        f.write(json_data)
    logger.info(f"JSON 数据导出完成: {json_path}")

    csv_data = db.export_csv()
    csv_path = os.path.join(json_dir, "movies.csv")
    with open(csv_path, 'w', encoding='utf-8', newline='') as f:
        f.write(csv_data)
    logger.info(f"CSV 数据导出完成: {csv_path}")

    stats = db.get_statistics()
    stats_path = os.path.join(json_dir, "stats.json")
    with open(stats_path, 'w', encoding='utf-8') as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    logger.info(f"统计数据导出完成: {stats_path}")

    # 复制海报到网站目录
    poster_src = POSTERS_DIR
    poster_dst = os.path.join(output_dir, "posters")
    if os.path.exists(poster_src):
        os.makedirs(poster_dst, exist_ok=True)
        for f_name in os.listdir(poster_src):
            src = os.path.join(poster_src, f_name)
            dst = os.path.join(poster_dst, f_name)
            if os.path.isfile(src) and f_name.lower().endswith(
                ('.jpg', '.jpeg', '.png', '.webp')):
                shutil.copy2(src, dst)
        logger.info(f"海报复制到网站目录: {poster_dst}")

    return json_path


# ==================== 烂番茄爬取 (独立步骤) ====================
def crawl_rotten_tomatoes(db, logger):
    """爬取烂番茄数据 — 独立步骤, 失败时返回已有数据库中的电影"""
    from crawler.rotten_tomatoes import RottenTomatoesCrawler

    global _rt_crawler_instance

    rt_crawler = None
    movies_list = []

    try:
        rt_crawler = RottenTomatoesCrawler()
        _rt_crawler_instance = rt_crawler
        logger.info("烂番茄爬虫初始化完成")

        logger.info("===== 开始爬取烂番茄 =====")
        movies_list = rt_crawler.crawl_all()
        logger.info(f"烂番茄数据收集完成: {len(movies_list)} 部电影")

        # 关闭浏览器 (RT爬取完成后就不需要了)
        rt_crawler.close()
        _rt_crawler_instance = None
        logger.info("烂番茄浏览器已关闭")

    except Exception as e:
        logger.error(f"烂番茄爬取失败: {e}")
        logger.error(traceback.format_exc())

        # 关闭浏览器 (如果还开着)
        if rt_crawler:
            try:
                rt_crawler.close()
            except Exception:
                pass
        _rt_crawler_instance = None

        # 爬取失败时, 尝试从已有数据库读取数据继续
        existing = db.get_all_movies()
        if existing:
            logger.info(f"烂番茄爬取失败, 使用数据库已有数据: {len(existing)} 部电影")
            movies_list = [dict(row) for row in existing]
        else:
            logger.error("烂番茄爬取失败且数据库无数据, 无法继续")
            return []

    return movies_list


# ==================== 豆瓣匹配 (独立步骤, 不依赖浏览器) ====================
def match_douban(movies_list, db, logger):
    """豆瓣匹配 — 独立步骤, 使用API+缓存, 不依赖浏览器"""
    from crawler.douban import DoubanMatcher

    # CI环境下不传driver (只用API, 不需要浏览器)
    ci_env = os.environ.get("CI_ENV", "").lower() in ("true", "1", "yes")

    if ci_env:
        logger.info("CI环境: 豆瓣匹配使用搜索API (无需浏览器)")
        douban_matcher = DoubanMatcher(driver=None, use_cache=True)
    else:
        # 本地: 尝试API优先, 搜索API足够时不需要driver
        douban_matcher = DoubanMatcher(driver=None, use_cache=True)
        logger.info("豆瓣匹配器初始化完成 (搜索API模式 + 缓存)")

    logger.info(f"豆瓣缓存已有: {len(douban_matcher._cache)} 条记录")

    logger.info("===== 开始豆瓣匹配 =====")
    douban_timeout = int(os.environ.get("DOUBAN_PER_MOVIE_TIMEOUT", "60"))
    matched_count = 0
    cached_count = 0

    for i, movie in enumerate(movies_list):
        try:
            start_match = time.time()
            douban_data = douban_matcher.match_and_fetch(
                movie.get("title", ""),
                movie.get("original_title", ""),
                timeout=douban_timeout,
            )

            # 合并豆瓣数据
            for key, value in douban_data.items():
                movie[key] = value

            elapsed = time.time() - start_match
            source = "缓存" if elapsed < 0.1 else "API"
            if source == "缓存":
                cached_count += 1
            else:
                matched_count += 1

            logger.info(f"豆瓣匹配完成 [{i+1}/{len(movies_list)}]: "
                         f"{movie.get('title')} → "
                         f"豆瓣={douban_data.get('douban_title', '未匹配')} "
                         f"({elapsed:.1f}s, {source})")

            # 超过限定时间则跳过后续
            if elapsed > douban_timeout:
                logger.warning(f"单部电影匹配超时 ({elapsed:.1f}s>{douban_timeout}s), "
                               f"跳过剩余豆瓣匹配")
                break
        except Exception as e:
            logger.error(f"豆瓣匹配失败: {movie.get('title')} - {e}")
            db.log_error(movie.get("rt_url", ""), "douban_match", str(e),
                         traceback.format_exc())

        # 更新部分数据列表 (供 SIGTERM 使用)
        _partial_movies = movies_list[:i+1]

    # 保存豆瓣缓存 (只爬一次, 下次直接使用)
    douban_matcher._save_cache()
    logger.info(f"豆瓣匹配完成: API匹配 {matched_count} 部, "
                 f"缓存命中 {cached_count} 部, "
                 f"总缓存 {len(douban_matcher._cache)} 条")

    return movies_list


# ==================== 独立豆瓣匹配模式 ====================
def douban_only_mode(db, logger):
    """仅豆瓣匹配模式: 从已有数据库读取RT数据, 补充豆瓣信息"""
    existing = db.get_all_movies()
    if not existing:
        logger.error("数据库无数据, 无法进行豆瓣匹配")
        return False

    movies_list = [dict(row) for row in existing]
    logger.info(f"从数据库读取 {len(movies_list)} 部电影, 开始豆瓣匹配")

    movies_list = match_douban(movies_list, db, logger)

    # 计算加权评分
    for movie in movies_list:
        ws = calc_weighted_score(
            movie.get("tomatometer", ""),
            movie.get("audience_score", ""),
            movie.get("douban_score", ""),
        )
        if ws is not None:
            movie["weighted_score"] = f"{ws:.2f}"
        else:
            movie["weighted_score"] = ""

    # 下载海报
    poster_results = download_posters_concurrent(movies_list)
    for movie in movies_list:
        title = movie.get("title", "")
        if title in poster_results:
            _, filename = poster_results[title]
            movie["poster_local"] = filename
        else:
            movie["poster_local"] = ""

    # 更新数据库
    success_count = db.batch_insert_movies(movies_list)
    logger.info(f"豆瓣匹配数据入库完成: {success_count}/{len(movies_list)}")

    return True


# ==================== 主流程 ====================
def main():
    """爬虫主入口 — 支持三种模式"""
    global _db_instance, _partial_movies

    logger = setup_logging()
    start_time = time.time()

    # 确定运行模式
    mode = os.environ.get("CRAWLER_MODE", "full").lower()
    logger.info(f"运行模式: {mode}")

    # 初始化数据库
    db = Database()
    _db_instance = db
    logger.info("数据库初始化完成")

    # 创建目录
    for path in [POSTERS_DIR, SITE_DIR]:
        os.makedirs(path, exist_ok=True)

    try:
        if mode == "site_only":
            # 仅生成网站 (从已有数据库)
            logger.info("===== 仅生成网站模式 =====")
            stats = db.get_statistics()
            if stats.get('total_movies', 0) == 0:
                logger.error("数据库无数据, 无法生成网站")
                return
            generate_site_data(db, SITE_DIR)

        elif mode == "douban_only":
            # 仅豆瓣匹配 (从已有数据库)
            logger.info("===== 仅豆瓣匹配模式 =====")
            if not douban_only_mode(db, logger):
                return
            generate_site_data(db, SITE_DIR)

        elif mode == "full":
            # 完整模式: RT爬取 → 豆瓣匹配 → 入库 → 生成网站
            # 1. 爬取烂番茄 (独立步骤, 失败时使用已有数据)
            movies_list = crawl_rotten_tomatoes(db, logger)
            if not movies_list:
                logger.error("无电影数据, 无法继续")
                return

            # 2. 豆瓣匹配 (独立步骤, 不依赖浏览器)
            movies_list = match_douban(movies_list, db, logger)

            # 3. 计算加权评分
            logger.info("===== 计算加权评分 =====")
            for movie in movies_list:
                ws = calc_weighted_score(
                    movie.get("tomatometer", ""),
                    movie.get("audience_score", ""),
                    movie.get("douban_score", ""),
                )
                if ws is not None:
                    movie["weighted_score"] = f"{ws:.2f}"
                else:
                    movie["weighted_score"] = ""

            # 4. 并发下载海报
            logger.info("===== 并发下载海报 =====")
            poster_results = download_posters_concurrent(movies_list)
            for movie in movies_list:
                title = movie.get("title", "")
                if title in poster_results:
                    _, filename = poster_results[title]
                    movie["poster_local"] = filename
                else:
                    movie["poster_local"] = ""

            # 5. 批量入库
            logger.info("===== 批量入库 =====")
            success_count = db.batch_insert_movies(movies_list)
            logger.info(f"入库完成: 成功 {success_count}/{len(movies_list)}")

            # 6. 记录评分历史
            if SCORE_HISTORY_ENABLED:
                logger.info("===== 记录评分历史 =====")
                for movie in movies_list:
                    row = db.get_movie_by_rt_url(movie.get("rt_url", ""))
                    if row:
                        db.record_score_history(
                            row["id"],
                            movie.get("tomatometer", -1),
                            movie.get("audience_score", -1),
                            movie.get("douban_score", -1),
                            movie.get("weighted_score", -1),
                        )

            # 7. 生成网站数据
            logger.info("===== 生成网站数据 =====")
            generate_site_data(db, SITE_DIR)

        # 统计报告
        stats = db.get_statistics()
        logger.info("===== 运行统计 =====")
        logger.info(f"总电影数: {stats.get('total_movies', 0)}")
        logger.info(f"平均加权分: {stats.get('avg_weighted', 0):.1f}")
        logger.info(f"豆瓣匹配数: {stats.get('matched_douban', 0)}")
        logger.info(f"历史记录数: {stats.get('history_records', 0)}")

        logger.info("=" * 60)
        logger.info(f"RottenDouban 爬虫运行完成! 总耗时: "
                     f"{time.time() - start_time:.2f}s")
        logger.info("=" * 60)

    except Exception as e:
        logger.critical(f"主流程异常: {e}")
        logger.critical(traceback.format_exc())
    finally:
        # 关闭资源
        if _rt_crawler_instance:
            try:
                _rt_crawler_instance.close()
            except Exception:
                pass
        db.close()


if __name__ == "__main__":
    main()
