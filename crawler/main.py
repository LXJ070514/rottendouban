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
import logging
import signal
import time
import traceback
from datetime import datetime
from typing import Optional, Dict, List, Tuple

from crawler.config import (
    PROJECT_DIR, DATA_DIR, POSTERS_DIR, SITE_DIR,
    DB_PATH, JSON_OUTPUT, CSV_OUTPUT,
    SCORE_WEIGHTS, IMAGE_DOWNLOAD_TIMEOUT, IMAGE_RETRY_MAX,
    IMAGE_RETRY_BACKOFF_FACTOR, IMAGE_THREAD_POOL_SIZE,
    DB_BATCH_SIZE, SCORE_HISTORY_ENABLED,
    LOG_LEVEL, LOG_FILE, LOG_FORMAT, LOG_DATE_FORMAT,
)
from crawler.database import Database
from crawler.downloader import download_image, download_posters_concurrent
from crawler.site_generator import generate_site_data


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
_partial_movies: List[Dict] = []
_db_instance = None
_rt_crawler_instance = None


def _sigterm_handler(signum, frame):
    """收到 SIGTERM 时保存已有数据 (GitHub Actions 超时前约10秒触发)"""
    logger = logging.getLogger("main")
    logger.warning(f"收到 SIGTERM 信号! 尝试保存部分数据 ({len(_partial_movies)} 部电影)")

    global _db_instance, _rt_crawler_instance
    if _partial_movies and _db_instance:
        try:
            process_movies_pipeline(_partial_movies, _db_instance, logger, skip_posters=True)
            generate_site_data(_db_instance, SITE_DIR)
            logger.warning("部分数据保存完成!")
        except Exception as e:
            logger.error(f"保存部分数据失败: {e}")

    # 关闭资源
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
def _parse_score(value: str) -> Optional[float]:
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


def calc_weighted_score(tomato_raw: str, audience_raw: str, douban_raw: str) -> Optional[float]:
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


# ==================== 通用电影处理流水线 ====================
def process_movies_pipeline(movies_list: List[Dict], db, logger, skip_posters: bool = False) -> int:
    """通用电影处理流水线: 评分计算 → 海报下载 → 批量入库"""
    # 1. 计算加权评分
    for movie in movies_list:
        ws = calc_weighted_score(
            movie.get("tomatometer", ""),
            movie.get("audience_score", ""),
            movie.get("douban_score", ""),
        )
        movie["weighted_score"] = f"{ws:.2f}" if ws is not None else ""

    # 2. 并发下载海报 (SIGTERM时可跳过)
    if not skip_posters:
        poster_results = download_posters_concurrent(movies_list)
        for movie in movies_list:
            title = movie.get("title", "")
            if title in poster_results:
                _, filename = poster_results[title]
                movie["poster_local"] = filename
            else:
                movie["poster_local"] = ""

    # 3. 批量入库
    success_count = db.batch_insert_movies(movies_list)
    logger.info(f"入库完成: 成功 {success_count}/{len(movies_list)}")
    return success_count


# ==================== 烂番茄爬取 (独立步骤) ====================
def crawl_rotten_tomatoes(db, logger):
    """爬取烂番茄数据 — Algolia API优先 (无需浏览器), Selenium回退"""
    from crawler.rotten_tomatoes import RottenTomatoesCrawler

    global _rt_crawler_instance

    rt_crawler = None
    movies_list = []

    try:
        # CI环境: 只用API (无需Chrome)
        # 本地: API优先, Selenium回退
        ci_env = os.environ.get("CI_ENV", "").lower() in ("true", "1", "yes")
        use_selenium = not ci_env  # 本地允许Selenium回退
        rt_crawler = RottenTomatoesCrawler(use_selenium_fallback=use_selenium)
        _rt_crawler_instance = rt_crawler
        logger.info(f"烂番茄爬虫初始化完成 (Selenium回退: {use_selenium})")

        logger.info("===== 开始爬取烂番茄 =====")
        movies_list = rt_crawler.crawl_all()
        logger.info(f"烂番茄数据收集完成: {len(movies_list)} 部电影")

        # 关闭资源 (如果开了浏览器)
        rt_crawler.close()
        _rt_crawler_instance = None

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

    global _partial_movies

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

    # 统一调用处理流水线
    process_movies_pipeline(movies_list, db, logger)

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

            # 3. 处理流水线: 评分计算 + 海报下载 + 入库
            logger.info("===== 处理流水线: 评分 + 海报 + 入库 =====")
            process_movies_pipeline(movies_list, db, logger)

            # 4. 记录评分历史
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

            # 5. 生成网站数据
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
