"""
RottenDouban 数据获取主入口
============================
- 以豆瓣电影列表为基础数据源
- TMDB API 获取详情（可选，需 TMDB_API_KEY / TMDB_BEARER_TOKEN）
- RT Algolia API 获取烂番茄评分
- 豆瓣搜索 API 获取中文数据
- 三种模式: full / douban_only / site_only
"""
import os
import sys
import logging
import time
import traceback
from datetime import datetime

from crawler.config import (
    PROJECT_VERSION, SITE_DIR,
    SCORE_WEIGHTS, SCORE_HISTORY_ENABLED,
    LOG_LEVEL, LOG_FILE, LOG_FORMAT, LOG_DATE_FORMAT,
    ensure_dirs,
)
from crawler.database import Database
from crawler.site_generator import generate_site_data


def setup_logging():
    """配置日志"""
    ensure_dirs()
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
    logger.info(f"RottenDouban v{PROJECT_VERSION} — {datetime.now()}")
    logger.info(f"模式: {os.environ.get('CRAWLER_MODE', 'full')}")
    logger.info(f"站点输出: {SITE_DIR}")
    logger.info(f"TMDB: {'已配置' if os.environ.get('TMDB_API_KEY') or os.environ.get('TMDB_BEARER_TOKEN') else '未配置(仅用RT Algolia)'}")
    logger.info("=" * 60)
    return logger


# ==================== 加权评分 ====================
def _as_float(value):
    """解析评分原始值；空值与负数一律视为缺失（-1 是数据库里的"无评分"哨兵）。"""
    if value is None or value == "":
        return None
    try:
        num = float(str(value).replace("%", "").strip())
    except (ValueError, TypeError):
        return None
    return None if num < 0 else num


def calc_weighted_score(tomato_raw, audience_raw, douban_raw):
    """加权评分（0–100）：番茄影评人 0.3 + 番茄观众 0.3 + 豆瓣 0.4。

    量纲按来源显式指定，不靠数值大小猜：烂番茄 8% 是 8 分而不是 80 分，
    豆瓣 8 是 8/10。缺失的源不参与，权重按比例分给其余来源。
    """
    parsed = {
        "tomatometer": _as_float(tomato_raw),
        "audience": _as_float(audience_raw),
        "douban": _as_float(douban_raw),
    }
    normalized = {
        "tomatometer": None if parsed["tomatometer"] is None else parsed["tomatometer"] / 100.0,
        "audience": None if parsed["audience"] is None else parsed["audience"] / 100.0,
        "douban": None if parsed["douban"] is None else parsed["douban"] / 10.0,
    }
    available = {k: v for k, v in normalized.items() if v is not None}
    if not available:
        return None

    total_weight = sum(SCORE_WEIGHTS[k] for k in available)
    if total_weight == 0:
        return None
    weighted = sum(available[k] * SCORE_WEIGHTS[k] for k in available) / total_weight
    return round(weighted * 100, 1)


def process_movies_pipeline(movies_list, db, logger):
    """处理流水线: 评分计算 → 入库"""
    for movie in movies_list:
        ws = calc_weighted_score(
            movie.get("tomatometer", ""),
            movie.get("audience_score", ""),
            movie.get("douban_score", ""),
        )
        movie["weighted_score"] = f"{ws:.1f}" if ws is not None else ""

    success_count = db.batch_insert_movies(movies_list)
    logger.info(f"入库完成: 成功 {success_count}/{len(movies_list)}")
    return success_count


# ==================== 从电影列表获取数据 ====================
def fetch_from_movie_list(logger, limit=None):
    """从电影列表逐部拉取 TMDB + RT Algolia 数据"""
    from crawler.movie_list import DOUBAN_TOP_250
    from crawler.tmdb_api import is_available as tmdb_available, search_and_get_details
    from crawler.rotten_tomatoes import RottenTomatoesCrawler

    use_tmdb = tmdb_available()
    rt_crawler = RottenTomatoesCrawler()
    movies_list = []
    entries = DOUBAN_TOP_250[:limit] if limit else DOUBAN_TOP_250
    total = len(entries)

    logger.info(f"===== 从电影列表获取数据 (共 {total} 部) =====")
    logger.info(f"数据源: TMDB={'ON' if use_tmdb else 'OFF'} | RT Algolia=ON")

    for i, entry in enumerate(entries):
        title_en = entry["title_en"]
        title_cn = entry["title_cn"]
        year = entry.get("year")
        label = f"{title_en} ({year})" if year else title_en

        logger.info(f"[{i+1}/{total}] {label} / {title_cn}")

        # rt_url 留空，由 RT 匹配结果填入 —— 之前用 /unknown/<slug> 兜底
        # 会在网站上生成点开就 404 的烂番茄链接
        movie_data = {
            "rt_url": "",
            "title": title_en,
            "original_title": title_en,
            "year": year,
            "category": "豆瓣Top250",
            "douban_title": title_cn,
        }

        if use_tmdb:
            try:
                tmdb_data = search_and_get_details(title_en, year)
                if tmdb_data:
                    movie_data.update(tmdb_data)
                    logger.info(f"  TMDB: ✓ {tmdb_data.get('title', '')[:30]} | "
                                f"poster={'✓' if tmdb_data.get('poster_url') else '✗'} | "
                                f"synopsis={'✓' if tmdb_data.get('synopsis') else '✗'}")
                else:
                    logger.info(f"  TMDB: ✗ 未找到")
            except Exception as e:
                logger.warning(f"  TMDB 异常: {e}")

        try:
            rt_data = rt_crawler.search_movie(title_en, year)
            if rt_data:
                for key, value in rt_data.items():
                    if value:
                        movie_data[key] = value
                logger.info(f"  RT: 🍅{rt_data.get('tomatometer') or '-'} "
                            f"🍿{rt_data.get('audience_score') or '-'} "
                            f"| {rt_data.get('rt_url', '')}")
            else:
                logger.info(f"  RT: ✗ 索引内无本片，放弃番茄分")
        except Exception as e:
            logger.warning(f"  RT 异常: {e}")

        movies_list.append(movie_data)

    rt_crawler.close()
    logger.info(f"数据获取完成: {len(movies_list)} 部电影")
    return movies_list


# ==================== 豆瓣匹配 ====================
def match_douban(movies_list, logger):
    """豆瓣匹配 — 缓存优先，中文片名 + 年份共同裁决

    豆瓣对数据中心 IP 会软封（200 + 空壳页）。检测到后停止继续敲接口，
    剩余影片只走缓存，避免 100+ 次无意义请求把每次运行都拖成假"查无此片"。
    """
    from crawler.douban import DoubanMatcher

    matcher = DoubanMatcher(use_cache=True)
    logger.info(f"===== 豆瓣匹配 (缓存 {len(matcher._cache)} 条) =====")

    matched = 0
    cache_only = False
    for i, movie in enumerate(movies_list):
        title_cn = movie.get("douban_title") or movie.get("title", "")
        year = movie.get("year")
        try:
            if cache_only:
                douban_data = matcher.cached_only(title_cn, year)
            else:
                douban_data = matcher.match_and_fetch(title_cn, year)
                if matcher.blocked:
                    cache_only = True
                    logger.warning(
                        f"  连续 {matcher.empty_page_streak} 次拿到空壳页面，"
                        f"判定豆瓣已限流；剩余 {len(movies_list) - i - 1} 部只读缓存")
        except Exception as e:
            logger.error(f"豆瓣匹配失败: {movie.get('title')} - {e}")
            continue

        for key, value in douban_data.items():
            if value:
                movie[key] = value

        if douban_data.get("douban_id"):
            matched += 1
            logger.info(f"  [{i+1}/{len(movies_list)}] {title_cn} → "
                        f"豆瓣 {douban_data['douban_score']} "
                        f"({douban_data['douban_title'][:30]})")
        else:
            logger.warning(f"  [{i+1}/{len(movies_list)}] {title_cn} → 豆瓣未匹配")

    matcher._save_cache()
    logger.info(f"豆瓣匹配完成: {matched}/{len(movies_list)}"
                + ("（受限流影响，未全量检索）" if cache_only else ""))
    return movies_list


# ==================== 主流程 ====================
class CrawlError(Exception):
    """预期内的失败原因，只记一行日志；未预期的异常才打完整堆栈。"""


def main():
    """返回进程退出码：0 成功，1 失败（供 CI 判断是否部署）"""
    logger = setup_logging()
    start_time = time.time()
    mode = os.environ.get("CRAWLER_MODE", "full").lower()
    min_publish = int(os.environ.get("MIN_MOVIES_TO_PUBLISH", 50))
    # 本地调试用 CRAWLER_LIMIT=5 只跑前几部；CI 不设，全量抓取
    limit = int(os.environ["CRAWLER_LIMIT"]) if os.environ.get("CRAWLER_LIMIT") else None

    db = Database()
    try:
        if mode == "site_only":
            logger.info("===== 仅生成网站 =====")

        elif mode == "douban_only":
            logger.info("===== 仅豆瓣匹配 =====")
            existing = db.get_all_movies()
            if not existing:
                raise CrawlError("数据库无数据，无法仅做豆瓣匹配")
            movies_list = match_douban([dict(row) for row in existing], logger)
            process_movies_pipeline(movies_list, db, logger)

        elif mode == "full":
            movies_list = fetch_from_movie_list(logger, limit)
            if not movies_list:
                raise CrawlError("电影列表为空")
            movies_list = match_douban(movies_list, logger)
            logger.info("===== 处理流水线 =====")
            process_movies_pipeline(movies_list, db, logger)
            db.conn.commit()

            if SCORE_HISTORY_ENABLED:
                logger.info("===== 记录评分历史 =====")
                for movie in db.get_all_movies():
                    db.record_score_history(movie["id"], movie)
                db.conn.commit()
        else:
            raise CrawlError(f"未知 CRAWLER_MODE: {mode}")

        stats = db.get_statistics()
        total = stats.get("total_movies", 0)
        logger.info("===== 统计 =====")
        logger.info(f"电影: {total} | 平均分: {stats.get('avg_weighted', 0):.1f} | "
                    f"豆瓣匹配: {stats.get('matched_douban', 0)} | "
                    f"历史记录: {stats.get('history_records', 0)}")

        # 上游 API 抖动时宁可让 CI 红灯并保留线上旧数据，也不发布半截结果
        if total < min_publish:
            raise CrawlError(
                f"仅取到 {total} 部电影，低于发布下限 {min_publish}，不覆盖站点数据")

        logger.info("===== 生成网站数据 =====")
        generate_site_data(db, SITE_DIR)
        logger.info(f"总耗时: {time.time() - start_time:.1f}s")
        return 0

    except CrawlError as e:
        logger.error(f"{e}（线上数据保持不变）")
        return 1
    except Exception:
        logger.critical(f"主流程异常:\n{traceback.format_exc()}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
