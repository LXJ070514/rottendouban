"""主入口 v3.0 - 评分加权计算 + 海报并发下载 + 日志系统 + 数据导出 + 网站生成
参考 rottentomatoes_spider.py 优化: _parse_score 加权评分、图片下载 urllib+SSL 回退
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
from crawler.rotten_tomatoes import RottenTomatoesCrawler
from crawler.douban import DoubanMatcher


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
    logger.info(f"RottenDouban 爬虫 v3.0 启动 - {datetime.now()}")
    logger.info("=" * 60)
    return logger


# ==================== 加权评分 (参考 rottentomatoes_spider.py) ====================
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
    # 判断尺度：豆瓣分数通常 ≤10, 烂番茄是百分比
    if num <= 10:
        return num / 10.0    # 8.5 → 0.85
    else:
        return num / 100.0   # 85 → 0.85


def calc_weighted_score(tomato_raw, audience_raw, douban_raw):
    """
    计算加权评分 (0-100)
    权重: 番茄0.3 + 观众0.3 + 豆瓣0.4
    缺失的评分按比例重新分配权重，最终输出 0-100 分值
    """
    scores = {
        'tomatometer': _parse_score(tomato_raw),
        'audience': _parse_score(audience_raw),
        'douban': _parse_score(douban_raw),
    }
    weights = dict(SCORE_WEIGHTS)

    available = {k: v for k, v in scores.items() if v is not None}

    if not available:
        return None

    # 重新分配权重
    total_weight = sum(weights[k] for k in available)
    if total_weight == 0:
        return None

    weighted = sum(scores[k] * (weights[k] / total_weight) for k in available)
    return round(weighted * 100, 2)


# ==================== 图片下载 (requests 优先, urllib+SSL 回退) ====================
def download_image(url, filename, retries=None):
    """带指数退避重试的图片下载 (参考 rottentomatoes_spider.py)"""
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
            # 方案1: requests (SSL 处理最佳)
            if REQUESTS_AVAILABLE:
                resp = _requests_lib.get(
                    url,
                    timeout=IMAGE_DOWNLOAD_TIMEOUT,
                    allow_redirects=True,
                    verify=False  # 解决 SSL ASN1
                )
                resp.raise_for_status()
                with open(filepath, 'wb') as f:
                    f.write(resp.content)
                logging.getLogger("main").info(f"图片下载成功: {filename}")
                return filepath

            # 方案2: urllib + 自定义 SSL context (解决 ASN1 NOT_ENOUGH_DATA)
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
            # 构造文件名
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

    # 导出 JSON
    json_data = db.export_json()
    json_dir = os.path.join(output_dir, "data")
    os.makedirs(json_dir, exist_ok=True)
    json_path = os.path.join(json_dir, "movies.json")
    with open(json_path, 'w', encoding='utf-8') as f:
        f.write(json_data)
    logger.info(f"JSON 数据导出完成: {json_path}")

    # 导出 CSV
    csv_data = db.export_csv()
    csv_path = os.path.join(json_dir, "movies.csv")
    with open(csv_path, 'w', encoding='utf-8', newline='') as f:
        f.write(csv_data)
    logger.info(f"CSV 数据导出完成: {csv_path}")

    # 导出统计数据
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


# ==================== 主流程 ====================
def main():
    """爬虫主入口"""
    logger = setup_logging()
    start_time = time.time()

    # 初始化数据库
    db = Database()
    logger.info("数据库初始化完成")

    # 初始化烂番茄爬虫
    rt_crawler = RottenTomatoesCrawler()
    logger.info("烂番茄爬虫初始化完成")

    # 豆瓣匹配器共用同一个 driver
    douban_matcher = DoubanMatcher(driver=rt_crawler.driver)
    logger.info("豆瓣匹配器初始化完成 (共用浏览器)")

    # 创建目录
    for path in [POSTERS_DIR, SITE_DIR]:
        os.makedirs(path, exist_ok=True)

    try:
        # 1. 爬取烂番茄数据
        logger.info("===== 开始爬取烂番茄 =====")
        movies_list = rt_crawler.crawl_all()
        logger.info(f"烂番茄数据收集完成: {len(movies_list)} 部电影")

        # 2. 豆瓣匹配和抓取
        logger.info("===== 开始豆瓣匹配 =====")
        for movie in movies_list:
            try:
                douban_data = douban_matcher.match_and_fetch(
                    movie.get("title", ""),
                    movie.get("original_title", "")
                )
                # 合并豆瓣数据
                for key, value in douban_data.items():
                    movie[key] = value
                logger.info(f"豆瓣匹配完成: {movie.get('title')} → "
                             f"豆瓣={douban_data.get('douban_title', '未匹配')}")
            except Exception as e:
                logger.error(f"豆瓣匹配失败: {movie.get('title')} - {e}")
                db.log_error(movie.get("rt_url", ""), "douban_match", str(e),
                             traceback.format_exc())

        # 3. 计算加权评分 (使用参考代码的 _parse_score)
        logger.info("===== 计算加权评分 =====")
        for movie in movies_list:
            mWeightedScore = calc_weighted_score(
                movie.get("tomatometer", ""),
                movie.get("audience_score", ""),
                movie.get("douban_score", ""),
            )
            if mWeightedScore is not None:
                movie["weighted_score"] = f"{mWeightedScore:.2f}"
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

        # 8. 统计报告
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
        rt_crawler.close()
        db.close()


if __name__ == "__main__":
    main()