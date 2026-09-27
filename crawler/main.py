"""
RottenDouban 数据获取主入口
============================
以豆瓣 Top250 榜单为驱动，分两轮抓取（配额所迫，见 `_fetch_comments`）：

第一轮 —— 榜单与详情
  1. `j/chart/top_list` 取榜单：名次、豆瓣评分、评分人数、subject id
  2. Rexxar `movie/<id>` 取详情：中文简介、导演、演员、原名/别名、制片国家
  3. 用详情里的英文名去 RT Algolia 匹配新鲜度与爆米花指数
  4. TMDB（可选，需 TMDB_API_KEY / TMDB_BEARER_TOKEN）补海报与英文简介

第二轮 —— 短评
  5. Rexxar `movie/<id>/interests` 取热门短评，只补"有详情但缺短评"的条目

subject id 直接来自榜单，豆瓣侧不需要任何模糊匹配。豆瓣阶段有共享的时间预算，
用尽即停，未完成的条目下一轮从缓存续抓；缓存在 finally 里保存，失败也不丢。

模式：`full` 全流程 / `site_only` 仅从 movies.db 重出站点数据。
"""
import os
import sys
import json
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


class CrawlError(Exception):
    """预期内的失败原因，只记一行日志；未预期的异常才打完整堆栈。"""


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


# ==================== 数据获取 ====================
TOP250_SIZE = int(os.environ.get("DOUBAN_TOP_N", 250))


def _as_year(value):
    try:
        return int(str(value).strip()[:4])
    except (TypeError, ValueError):
        return None


def _hand_english_map():
    """movie_list.py 的手工中英映射 —— Rexxar 给不出英文名时的最后兜底。"""
    from crawler.movie_list import DOUBAN_TOP_250
    return {e["title_cn"]: e["title_en"] for e in DOUBAN_TOP_250}


def _fetch_details(client, entries, hand_map, rt_crawler, use_tmdb, logger, hits):
    """第一轮：详情 + 英文名解析 + RT 匹配 + TMDB 补字段。

    详情是短评的前提（fetch_comments 对无缓存记录的条目不发请求），所以这一轮
    优先吃时间预算 —— 冷启动时预算可能全花在详情上，短评顺延到下一轮。
    """
    from crawler.douban import english_title_candidates
    from crawler.tmdb_api import search_and_get_details

    total = len(entries)
    movies = []

    for i, entry in enumerate(entries):
        subject_id = entry["douban_id"]
        cn_title = entry["douban_title"]
        logger.info(f"[{i+1}/{total}] #{entry['douban_rank']:>3} {cn_title} "
                    f"(id={subject_id}) 豆{entry['douban_score']}")

        # slug 用 subject id：比片名+年份更稳，且不受改名影响
        movie = dict(entry)
        movie["slug"] = f"douban-{subject_id}"
        movie["category"] = "豆瓣Top250"
        movie["rt_url"] = ""
        movie["year"] = _as_year(entry.get("douban_release_date"))
        # 榜单自带制片国家/地区，详情未抓到时先顶上 —— 冷启动的头几轮全靠它
        movie["douban_countries"] = entry.get("douban_regions") or ""

        detail = None
        if client.blocked:
            logger.warning("  豆瓣已限流，跳过详情/短评，仅用榜单字段")
        else:
            try:
                detail = client.fetch_subject(subject_id)
            except Exception as e:
                logger.warning(f"  豆瓣详情异常: {e}")

        en_title = ""
        if detail:
            hits["detail"] += 1
            # 榜单每轮取新值，详情走缓存，所以评分/人数以榜单为准，详情只补静态字段
            movie.update({
                "douban_synopsis": detail.get("intro") or "",
                "douban_director": ", ".join(detail.get("directors") or []),
                "douban_cast": ", ".join(detail.get("actors") or []),
                "douban_comments": json.dumps(detail.get("comments") or [], ensure_ascii=False),
                "douban_countries": (", ".join(detail.get("countries") or [])
                                     or movie.get("douban_countries") or ""),
                "douban_durations": ", ".join(detail.get("durations") or []),
                "douban_genre": movie.get("douban_genre") or ", ".join(detail.get("genres") or []),
                "douban_poster": movie.get("douban_poster") or detail.get("cover_url") or "",
            })
            movie["year"] = _as_year(detail.get("year")) or movie["year"]
            if detail.get("comments"):
                hits["comments"] += 1
            en_title = next(english_title_candidates(detail), "")
            logger.info(f"  详情: 简介 {len(detail.get('intro') or '')} 字 | "
                        f"导演 {len(detail.get('directors') or [])} | "
                        f"演员 {len(detail.get('actors') or [])} | "
                        f"短评 {len(detail.get('comments') or [])} | 英文名 {en_title or '无'}")

        if not en_title:
            en_title = hand_map.get(cn_title, "")
            if en_title:
                logger.info(f"  英文名取自手工片单: {en_title}")

        year = movie["year"]

        # TMDB 除了补海报与英文简介，还承担英文名反查：Rexxar 与手工片单都给不出
        # 英文名时用中文片名查（TMDB 支持中文检索）。这样 RT 覆盖率不再依赖
        # 豆瓣详情接口是否可达 —— CI 实测 Rexxar 会在若干次后返回 400。
        tmdb_data = None
        if use_tmdb:
            try:
                tmdb_data = search_and_get_details(en_title or cn_title, year)
            except Exception as e:
                logger.warning(f"  TMDB 异常: {e}")
            if tmdb_data and not en_title:
                en_title = (tmdb_data.get("title")
                            or tmdb_data.get("original_title") or "")
                if en_title:
                    logger.info(f"  英文名由 TMDB 反查: {en_title}")

        movie["title"] = en_title or cn_title
        movie["original_title"] = (detail or {}).get("original_title") or en_title or cn_title

        # RT：逐个英文候选试。aka 里可能有多个英文名（《活着》是
        # ['Lifetimes', 'To Live']，前者并非 RT 收录的那个），靠严格匹配器
        # （精确标题 + 年份）自校验，第一个通过的才算命中。
        candidates = list(dict.fromkeys(
            ([en_title] if en_title else []) + list(english_title_candidates(detail or {}))
        ))
        rt_data = None
        for candidate in candidates:
            try:
                rt_data = rt_crawler.search_movie(candidate, year)
            except Exception as e:
                logger.warning(f"  RT 异常 [{candidate[:24]}]: {e}")
                rt_data = None
            if rt_data:
                if candidate != en_title:
                    logger.info(f"  英文名改用别名: {candidate}")
                    movie["title"] = candidate
                break

        if rt_data:
            for key, value in rt_data.items():
                if value:
                    movie[key] = value
            hits["rt"] += 1
            logger.info(f"  RT: 🍅{rt_data.get('tomatometer') or '-'} "
                        f"🍿{rt_data.get('audience_score') or '-'} "
                        f"| {rt_data.get('rt_url', '')}")
        elif not candidates:
            logger.info("  RT: ✗ 无英文片名可用")
        else:
            logger.info(f"  RT: ✗ 索引内无本片（试过 {len(candidates)} 个英文名）")

        if tmdb_data:
            # 只补缺，不覆盖豆瓣与 RT 已给出的字段
            if not movie.get("poster_url"):
                movie["poster_url"] = tmdb_data.get("poster_url", "")
            if not movie.get("synopsis"):
                movie["synopsis"] = tmdb_data.get("synopsis", "")
            if not movie.get("runtime") and tmdb_data.get("runtime"):
                movie["runtime"] = tmdb_data["runtime"]
            if not movie.get("rating"):
                movie["rating"] = tmdb_data.get("rating", "")
            if not movie.get("release_date"):
                movie["release_date"] = tmdb_data.get("release_date", "")
            if not movie.get("genre"):
                movie["genre"] = tmdb_data.get("genre", "")
            if not movie.get("director"):
                movie["director"] = tmdb_data.get("director", "")
            if not movie.get("cast"):
                movie["cast"] = tmdb_data.get("cast", "")

        movies.append(movie)

    return movies


def _fetch_comments(client, movies, logger, hits):
    """第二轮：只给"已有详情但缺短评"的条目补短评。

    与详情拆成两轮是配额所迫 —— 豆瓣按 IP 的时间窗配额撑不住一轮跑完
    250×2 次请求。拆开后详情先落地、短评用剩余预算补，补不完的下一轮
    从缓存接着补，覆盖率单调递增。
    """
    todo = [m for m in movies if client.needs_comments(m["douban_id"])]
    if not todo:
        if client.budget_exhausted:
            logger.info("短评阶段跳过：时间预算已用尽")
        return

    logger.info(f"===== 短评补抓: 待办 {len(todo)} 部 =====")
    filled = 0
    for i, movie in enumerate(todo):
        if client.budget_exhausted or client.blocked:
            reason = "时间预算用尽" if client.budget_exhausted else "豆瓣限流"
            logger.info(f"  短评阶段中止（{reason}）: 已补 {filled}，"
                        f"剩余 {len(todo) - i} 部留给下一轮")
            break
        try:
            comments = client.fetch_comments(movie["douban_id"])
        except Exception as e:
            logger.warning(f"  短评异常 {movie['douban_title']}: {e}")
            continue
        if comments:
            movie["douban_comments"] = json.dumps(comments, ensure_ascii=False)
            filled += 1

    hits["comments"] += filled
    logger.info(f"短评补抓完成: {filled}/{len(todo)}")


def fetch_from_douban_top250(logger, limit=None):
    """豆瓣 Top250 榜单驱动的抓取流水线。

    榜单给名次/评分/subject id → Rexxar 详情给简介/导演/演员/英文名 → 用英文名
    去 RT Algolia 匹配 → TMDB（可选）补海报与英文简介 → 剩余预算补短评。

    因为 subject id 直接来自榜单，豆瓣侧不再需要任何模糊匹配，
    "匹配到同名剧集"这一整类 bug 从结构上消失了。

    缓存在 finally 里保存：豆瓣阶段被限流、超预算甚至抛异常，已抓到的部分
    也必须落盘提交，否则下一轮从零开始，覆盖率永远爬不上去。
    """
    from crawler.douban import DoubanClient
    from crawler.rotten_tomatoes import RottenTomatoesCrawler
    from crawler.tmdb_api import is_available as tmdb_available

    client = DoubanClient()
    rt_crawler = RottenTomatoesCrawler()
    hits = {"detail": 0, "rt": 0, "comments": 0}
    try:
        entries = client.fetch_top_list(limit=limit or TOP250_SIZE)
        if not entries:
            raise CrawlError("豆瓣 Top250 榜单不可用，放弃本轮（线上数据保持不变）")

        use_tmdb = tmdb_available()
        logger.info(f"===== 豆瓣 Top{len(entries)} 驱动抓取 =====")
        logger.info(f"数据源: 豆瓣榜单+Rexxar=ON | RT Algolia=ON | TMDB={'ON' if use_tmdb else 'OFF'}")
        logger.info(f"豆瓣缓存: {client.cache_size} 条 | 时间预算: {client.time_budget:.0f}s")

        movies = _fetch_details(client, entries, _hand_english_map(),
                                rt_crawler, use_tmdb, logger, hits)
        _fetch_comments(client, movies, logger, hits)

        logger.info(f"抓取完成: {len(movies)} 部 | 详情 {hits['detail']} | "
                    f"RT {hits['rt']} | 短评 {hits['comments']} | 缓存 {client.cache_size}")
        return movies
    finally:
        client.save_cache()
        rt_crawler.close()


# ==================== 主流程 ====================

def main():
    """返回进程退出码：0 成功，1 失败（供 CI 判断是否部署）"""
    logger = setup_logging()
    start_time = time.time()
    mode = os.environ.get("CRAWLER_MODE", "full").lower()
    # 本地调试用 CRAWLER_LIMIT=5 只跑前几部；CI 不设，抓满 Top250
    limit = int(os.environ["CRAWLER_LIMIT"]) if os.environ.get("CRAWLER_LIMIT") else None
    target = limit or TOP250_SIZE
    # 发布下限随目标条数走：上游只回一半数据时宁可失败并保留线上旧版
    min_publish = int(os.environ.get("MIN_MOVIES_TO_PUBLISH") or max(1, int(target * 0.8)))

    db = Database()
    try:
        if mode == "site_only":
            logger.info("===== 仅重出站点数据 =====")

        elif mode == "full":
            movies_list = fetch_from_douban_top250(logger, limit)
            logger.info("===== 处理流水线 =====")
            process_movies_pipeline(movies_list, db, logger)
            db.conn.commit()

            if SCORE_HISTORY_ENABLED:
                logger.info("===== 记录评分历史 =====")
                for movie in db.get_all_movies():
                    db.record_score_history(movie["id"], movie)
                db.conn.commit()
        else:
            raise CrawlError(f"未知 CRAWLER_MODE: {mode}（可用: full / site_only）")

        stats = db.get_statistics()
        total = stats.get("total_movies", 0)
        logger.info("===== 统计 =====")
        logger.info(f"电影: {total}/{target} | 平均分: {stats.get('avg_weighted', 0):.1f} | "
                    f"有豆瓣: {stats.get('matched_douban', 0)} | "
                    f"历史记录: {stats.get('history_records', 0)}")

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
