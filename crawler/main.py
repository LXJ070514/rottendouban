"""
RottenDouban 数据获取主入口 v6.0
====================================
- 以豆瓣 Top 250 电影列表为基础
- TMDB API 获取电影详情 (可选)
- RT Algolia API 获取烂番茄评分
- 豆瓣搜索 API 获取中文数据
- 支持3种模式: full / douban_only / site_only
"""
import os
import sys
import logging
import time
import traceback
from datetime import datetime

from crawler.config import (
    PROJECT_DIR, DATA_DIR, POSTERS_DIR, SITE_DIR,
    DB_PATH, SCORE_WEIGHTS, SCORE_HISTORY_ENABLED,
    LOG_LEVEL, LOG_FILE, LOG_FORMAT, LOG_DATE_FORMAT,
)
from crawler.database import Database
from crawler.site_generator import generate_site_data


def setup_logging():
    """配置日志"""
    os.makedirs(DATA_DIR, exist_ok=True)
    if sys.platform == 'win32':
        try:
            sys.stdout.reconfigure(encoding='utf-8')
            sys.stderr.reconfigure(encoding='utf-8')
        except Exception:
            pass

    root_logger = logging.getLogger()
    root_logger.setLevel(LOG_LEVEL)

    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setLevel(LOG_LEVEL)
    fh.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
    root_logger.addHandler(fh)

    ch = logging.StreamHandler(
        open(sys.stdout.fileno(), mode='w', encoding='utf-8', closefd=False)
    )
    ch.setLevel(LOG_LEVEL)
    ch.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
    root_logger.addHandler(ch)

    logger = logging.getLogger("main")
    logger.info("=" * 60)
    logger.info(f"RottenDouban v6.0 — 豆瓣Top250模式 — {datetime.now()}")
    logger.info(f"模式: {os.environ.get('CRAWLER_MODE', 'full')}")
    logger.info(f"TMDB: {'已配置' if os.environ.get('TMDB_API_KEY') or os.environ.get('TMDB_BEARER_TOKEN') else '未配置(将仅用RT Algolia)'}")
    logger.info("=" * 60)
    return logger


# ==================== 加权评分 ====================
def _parse_score(value):
    if not value:
        return None
    raw = str(value).replace('%', '').strip()
    try:
        num = float(raw)
    except (ValueError, TypeError):
        return None
    return num / 10.0 if num <= 10 else num / 100.0


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


def process_movies_pipeline(movies_list, db, logger, skip_posters=False):
    """处理流水线: 评分计算 → 入库"""
    for movie in movies_list:
        ws = calc_weighted_score(
            movie.get("tomatometer", ""),
            movie.get("audience_score", ""),
            movie.get("douban_score", ""),
        )
        movie["weighted_score"] = f"{ws:.2f}" if ws is not None else ""

    success_count = db.batch_insert_movies(movies_list)
    logger.info(f"入库完成: 成功 {success_count}/{len(movies_list)}")
    return success_count


# ==================== 从电影列表获取数据 ====================
def fetch_from_movie_list(db, logger):
    """从豆瓣 Top 250 电影列表获取数据 — TMDB + RT Algolia"""
    from crawler.movie_list import DOUBAN_TOP_250
    from crawler.tmdb_api import is_available as tmdb_available, search_and_get_details
    from crawler.rotten_tomatoes import RottenTomatoesCrawler

    use_tmdb = tmdb_available()
    rt_crawler = RottenTomatoesCrawler()
    movies_list = []
    total = len(DOUBAN_TOP_250)

    logger.info(f"===== 从电影列表获取数据 (共 {total} 部) =====")
    logger.info(f"数据源: TMDB={'ON' if use_tmdb else 'OFF'} | RT Algolia=ON")

    for i, entry in enumerate(DOUBAN_TOP_250):
        title_en = entry["title_en"]
        title_cn = entry["title_cn"]
        year = entry.get("year")
        label = f"{title_en} ({year})" if year else title_en

        logger.info(f"[{i+1}/{total}] {label} / {title_cn}")

        movie_data = {
            "rt_url": f"https://www.rottentomatoes.com/unknown/{title_en.replace(' ', '_').replace(':', '').lower()}",
            "title": title_en,
            "original_title": title_en,
            "year": year,
            "category": "豆瓣Top250",
        }

        # 1. TMDB (如果可用)
        if use_tmdb:
            try:
                tmdb_data = search_and_get_details(title_en, year)
                if tmdb_data:
                    # TMDB 数据为基础
                    movie_data.update(tmdb_data)
                    logger.info(f"  TMDB: ✓ {tmdb_data.get('title', '')[:30]} | "
                                f"poster={'✓' if tmdb_data.get('poster_url') else '✗'} | "
                                f"synopsis={'✓' if tmdb_data.get('synopsis') else '✗'}")
                else:
                    logger.info(f"  TMDB: ✗ 未找到")
            except Exception as e:
                logger.warning(f"  TMDB 异常: {e}")

        # 2. RT Algolia — 补充/覆盖 RT 评分
        try:
            rt_data = rt_crawler.search_movie(title_en, year)
            if rt_data:
                # RT 评分覆盖
                for key in ["tomatometer", "audience_score", "critics_consensus", "rt_url"]:
                    if rt_data.get(key):
                        movie_data[key] = rt_data[key]

                # 填补 TMDB 缺失的字段
                for key in ["title", "original_title", "year", "genre", "director",
                            "writers", "cast", "synopsis", "poster_url", "runtime",
                            "rating", "release_date"]:
                    if not movie_data.get(key) and rt_data.get(key):
                        movie_data[key] = rt_data[key]

                logger.info(f"  RT: 🍅{rt_data.get('tomatometer', '-')} "
                            f"🍿{rt_data.get('audience_score', '-')} "
                            f"| {'✓' if rt_data.get('critics_consensus') else '✗'} consensus")
            else:
                logger.info(f"  RT: ✗ 未找到")
        except Exception as e:
            logger.warning(f"  RT 异常: {e}")

        # 保存中文名到 douban_title (豆瓣匹配时可能覆盖)
        if title_cn and not movie_data.get("douban_title"):
            movie_data["douban_title"] = title_cn

        movie_data["category"] = "豆瓣Top250"
        movies_list.append(movie_data)

    rt_crawler.close()
    logger.info(f"数据获取完成: {len(movies_list)} 部电影")
    return movies_list


# ==================== 豆瓣匹配 ====================
def match_douban(movies_list, db, logger):
    """豆瓣匹配 — 缓存优先，搜索API次之，优先用中文名搜索"""
    from crawler.douban import DoubanMatcher

    douban_matcher = DoubanMatcher(use_cache=True)
    logger.info(f"豆瓣缓存: {len(douban_matcher._cache)} 条")

    logger.info("===== 豆瓣匹配 =====")
    matched = 0
    cached = 0

    for i, movie in enumerate(movies_list):
        try:
            start = time.time()
            title_en = movie.get("title", "")
            title_cn = movie.get("douban_title", "") or movie.get("original_title", "")

            # 优先用中文名搜索豆瓣 (更精准)
            douban_data = douban_matcher.match_and_fetch(title_cn)

            # 如果中文名没匹配到，用英文名
            if not douban_data.get("douban_url") and title_en != title_cn:
                douban_data = douban_matcher.match_and_fetch(title_en)

            for key, value in douban_data.items():
                if value:  # 只覆盖非空值
                    movie[key] = value

            elapsed = time.time() - start
            if elapsed < 0.1:
                cached += 1
            else:
                matched += 1
            logger.info(f"  [{i+1}/{len(movies_list)}] {title_en[:25]} → "
                        f"豆瓣={douban_data.get('douban_title', '未匹配')} ({elapsed:.1f}s)")
        except Exception as e:
            logger.error(f"豆瓣匹配失败: {movie.get('title')} - {e}")

    douban_matcher._save_cache()
    logger.info(f"豆瓣匹配完成: API {matched} / 缓存 {cached}")
    return movies_list


# ==================== 主流程 ====================
def main():
    logger = setup_logging()
    start_time = time.time()
    mode = os.environ.get("CRAWLER_MODE", "full").lower()

    db = Database()
    os.makedirs(POSTERS_DIR, exist_ok=True)

    try:
        if mode == "site_only":
            logger.info("===== 仅生成网站 =====")
            generate_site_data(db, SITE_DIR)

        elif mode == "douban_only":
            logger.info("===== 仅豆瓣匹配 =====")
            existing = db.get_all_movies()
            if not existing:
                logger.error("数据库无数据")
                return
            movies_list = [dict(row) for row in existing]
            movies_list = match_douban(movies_list, db, logger)
            process_movies_pipeline(movies_list, db, logger)
            generate_site_data(db, SITE_DIR)

        elif mode == "full":
            # 1. 从电影列表获取 TMDB + RT 数据
            movies_list = fetch_from_movie_list(db, logger)
            if not movies_list:
                logger.error("无电影数据")
                return

            # 2. 豆瓣匹配
            movies_list = match_douban(movies_list, db, logger)

            # 3. 处理流水线
            logger.info("===== 处理流水线 =====")
            process_movies_pipeline(movies_list, db, logger)

            # 4. 评分历史
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

            # 5. 生成网站
            logger.info("===== 生成网站数据 =====")
            generate_site_data(db, SITE_DIR)

        # 统计
        stats = db.get_statistics()
        logger.info("===== 统计 =====")
        logger.info(f"电影: {stats.get('total_movies', 0)} | "
                    f"平均分: {stats.get('avg_weighted', 0):.1f} | "
                    f"豆瓣匹配: {stats.get('matched_douban', 0)}")
        logger.info(f"总耗时: {time.time() - start_time:.1f}s")

    except Exception as e:
        logger.critical(f"主流程异常: {e}")
        logger.critical(traceback.format_exc())
    finally:
        db.close()


if __name__ == "__main__":
    main()
