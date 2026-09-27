"""豆瓣数据模块 — Top250 榜单 + Rexxar 详情/短评

只用 CI 实测可用的端点（Runner 出口 IP 上验证过，见 scripts/diagnose_sources.py）：

- `movie.douban.com/j/chart/top_list`  榜单，返回带官方 rank 的条目与 subject id
- `m.douban.com/rexxar/api/v2/movie/<id>`            详情：简介/导演/演员/原名/别名
- `m.douban.com/rexxar/api/v2/movie/<id>/interests`  热门短评

**不使用** `search.douban.com/movie/subject_search`：它在数据中心 IP 上返回
HTTP 200 但 `items` 为空，是本项目豆瓣覆盖率长期上不去的根因。Rexxar 端点
要求带 `Referer: https://m.douban.com/movie/subject/<id>/`，缺了会被拒。

拿到 subject id 后就不需要任何模糊匹配 —— 旧实现里"匹配到同名剧集"这一整类
bug 随之消失。
"""
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, Iterator, List, Optional

from crawler.config import DATA_DIR, build_ssl_context

logger = logging.getLogger("douban")

_SSL_CTX = build_ssl_context()

TOP_LIST_URL = "https://movie.douban.com/j/chart/top_list"
REXXAR_BASE = "https://m.douban.com/rexxar/api/v2/movie"

# type 必填，缺了返回空数组；interval_id=100:90 这一段覆盖 rank 1..250+
TOP_LIST_PARAMS = {"type": "11", "interval_id": "100:90", "action": ""}

DEFAULT_CACHE_PATH = os.path.join(DATA_DIR, "douban_cache.json")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 限流判定：至少这么多实时样本，且空结果占比达到该阈值
BLOCK_MIN_SAMPLES = int(os.environ.get("DOUBAN_BLOCK_MIN_SAMPLES", 12))
BLOCK_EMPTY_RATIO = float(os.environ.get("DOUBAN_BLOCK_EMPTY_RATIO", 0.85))
# 基础节流 1.5s，撞配额时由退避接管（但退避有总额上限，见下）。
# CI matrix 实测（每档独立 Runner/IP，各连发 20 次）：0.5s→10 成功、2s→14、4s→19、
# 8s→17（8s 的失败是 SSL 握手超时而非配额），各档首次失败都在第 10-11 次，
# 说明配额按时间窗滚动。但固定 4s 不可行：250 部 × 2 请求 = 500 次，
# 光节流就 33 分钟，叠加退避后实测 75 分钟被 CI 强杀、缓存颗粒无收。
# 故改为"小基础节流 + 有限退避 + 时间预算 + 跨轮续抓"。
REQUEST_DELAY = float(os.environ.get("DOUBAN_REQUEST_DELAY", 1.5))
RATE_LIMIT_RETRIES = int(os.environ.get("DOUBAN_RATE_LIMIT_RETRIES", 3))
RATE_LIMIT_BACKOFF = float(os.environ.get("DOUBAN_RATE_LIMIT_BACKOFF", 20))
# 退避累计睡眠上限（秒）。run 36296432188 实测：25 次退避睡掉 920s，
# 占满 1200s 预算的 77%，只换来 4 条详情，代价是 139 条连试都没试上。
# 配额是时间窗额度，"多等一会儿"换不来额度，只换掉别人的机会 —— 所以给退避
# 封顶，把预算留给真正能发出去的请求，剩余的下一轮再说。
RATE_LIMIT_SLEEP_BUDGET = float(os.environ.get("DOUBAN_RATE_LIMIT_SLEEP_BUDGET", 180))
# 豆瓣阶段的时间预算（秒）。用尽即停止发请求，剩余条目留给下一轮从缓存续抓，
# 以保证整轮抓取必定能在 CI 超时内跑完并把缓存提交上去。
TIME_BUDGET = float(os.environ.get("DOUBAN_TIME_BUDGET", 1200))
COMMENT_COUNT = int(os.environ.get("DOUBAN_COMMENT_COUNT", 3))


class DoubanError(Exception):
    """豆瓣端点不可用（网络失败或被限流）。"""


class RateLimited(DoubanError):
    """按 IP 的时间窗配额已用尽：HTTP 400 + {"msg":"subject_ip_rate_limit"}。

    必须与普通失败区分 —— 这种是"等一等就能继续"，若当成"该片无详情"
    就会永久跳过，缓存也永远补不上。
    """


def _get(url: str, referer: str, timeout: int = 15):
    """GET 并解析 JSON。返回 None 表示拿不到数据；配额耗尽抛 RateLimited。"""
    headers = {
        "User-Agent": _UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": referer,
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read()[:200].decode("utf-8", "replace")
        except OSError:
            pass
        if "subject_ip_rate_limit" in detail:
            raise RateLimited(url) from e
        logger.warning(f"豆瓣 HTTP {e.code}: {url[:90]}")
        return None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        logger.warning(f"豆瓣请求失败: {type(e).__name__}: {e}")
        return None

    try:
        return json.loads(body)
    except json.JSONDecodeError:
        # 限流的另一形态：200 + HTML 验证页
        logger.warning(f"豆瓣返回非 JSON（{len(body)}B），疑似验证页: {url[:90]}")
        return None


def _names(people) -> List[str]:
    return [p.get("name", "") for p in (people or []) if p.get("name")]


def english_title_candidates(detail: Dict) -> Iterator[str]:
    """RT 检索用的英文片名候选，按可信度排序。

    华语片的 original_title 是空的（原名就是中文），英文名藏在 aka 里，
    且顺序不保证 —— 《活着》的 aka 是 ['人生', 'Lifetimes', 'To Live']，
    'Lifetimes' 排在前面却不是 RT 收录的那个。所以逐个产出，
    由调用方拿严格匹配器（精确标题 + 年份）自校验，第一个通过的才算数。
    """
    original = (detail.get("original_title") or "").strip()
    if original.isascii() and any(c.isalpha() for c in original):
        yield original

    for alias in detail.get("aka") or []:
        alias = (alias or "").strip()
        if alias.isascii() and any(c.isalpha() for c in alias):
            yield alias


class DoubanClient:
    """豆瓣榜单与详情客户端，带 id 级缓存与限流熔断。"""

    def __init__(self, cache_path: Optional[str] = None, use_cache: bool = True,
                 time_budget: Optional[float] = None):
        self.cache_path = cache_path or DEFAULT_CACHE_PATH
        self.use_cache = use_cache
        self.time_budget = TIME_BUDGET if time_budget is None else time_budget
        self._cache: Dict[str, Dict] = {}
        self.live_lookups = 0
        self.empty_lookups = 0
        self._last_request = 0.0
        self._started = time.time()
        self.budget_exhausted = False
        # 累计退避睡眠，用于给退避封顶（见 _request）
        self.backoff_slept = 0.0
        if use_cache:
            self._load_cache()

    # ==================== 限流与节流 ====================

    @property
    def elapsed(self) -> float:
        return time.time() - self._started

    def _check_budget(self) -> bool:
        """预算是否还够再发一次请求。用尽时置标志，交由上层跨轮续抓。"""
        if self.budget_exhausted:
            return False
        # 预留一次退避的余量，避免刚判定"还够"就撞上限流等待而超预算
        if self.elapsed + RATE_LIMIT_BACKOFF >= self.time_budget:
            self.budget_exhausted = True
            logger.warning(
                f"豆瓣时间预算用尽（{self.elapsed:.0f}s / {self.time_budget:.0f}s），"
                f"停止实时请求；已缓存 {len(self._cache)} 条，剩余留给下一轮续抓")
            return False
        return True

    @property
    def blocked(self) -> bool:
        """样本足够且几乎全是空结果 —— 判定为被限流，上层应停止实时请求。

        豆瓣限流时返回 HTTP 200 但内容是空壳或验证页，只看状态码会得出
        完全错误的结论，所以按空结果比例判。
        """
        if self.live_lookups < BLOCK_MIN_SAMPLES:
            return False
        return self.empty_lookups / self.live_lookups >= BLOCK_EMPTY_RATIO

    def _throttle(self):
        elapsed = time.time() - self._last_request
        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)
        self._last_request = time.time()

    def _request(self, url: str, referer: str):
        """带配额退避的单次请求。

        撞上 subject_ip_rate_limit 时等待后重试，但退避总睡眠受
        RATE_LIMIT_SLEEP_BUDGET 封顶 —— 配额是时间窗额度，睡久了换不来额度，
        只会把预算从"还能发出去的请求"手里抢走。封顶用尽后立即放弃本次请求，
        由上层跨轮续抓。
        预算用尽时不再发请求，直接返回 None。
        """
        if not self._check_budget():
            return None
        self.live_lookups += 1
        data = None
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            self._throttle()
            try:
                data = _get(url, referer)
                break
            except RateLimited:
                wait = RATE_LIMIT_BACKOFF * (attempt + 1)
                if attempt >= RATE_LIMIT_RETRIES:
                    logger.warning(f"配额重试 {attempt} 次仍被限流，放弃: {url[:80]}")
                    break
                if self.backoff_slept + wait > RATE_LIMIT_SLEEP_BUDGET:
                    logger.info(
                        f"退避睡眠已达上限（{self.backoff_slept:.0f}s/"
                        f"{RATE_LIMIT_SLEEP_BUDGET:.0f}s），不再等待，本条留给下一轮")
                    break
                logger.info(f"豆瓣配额限流，等待 {wait:.0f}s 后重试 "
                            f"({attempt + 1}/{RATE_LIMIT_RETRIES}): {url[:70]}")
                time.sleep(wait)
                self.backoff_slept += wait

        if data is None or (isinstance(data, (list, dict)) and not data):
            self.empty_lookups += 1
        return data

    # ==================== 缓存 ====================

    def _load_cache(self):
        try:
            if os.path.exists(self.cache_path):
                with open(self.cache_path, encoding="utf-8") as f:
                    loaded = json.load(f)
                # 旧版缓存以中文片名为键，新版以 subject id 为键；结构不合就丢弃
                if isinstance(loaded, dict) and all(
                        k.isdigit() for k in list(loaded)[:5]):
                    self._cache = loaded
                    logger.info(f"豆瓣缓存加载: {len(loaded)} 条")
                elif loaded:
                    logger.info(f"豆瓣缓存为旧格式（{len(loaded)} 条片名键），忽略并重建")
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"豆瓣缓存加载失败: {e}")

    def save_cache(self):
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, ensure_ascii=False, indent=2, sort_keys=True)
            logger.info(f"豆瓣缓存保存: {len(self._cache)} 条")
        except OSError as e:
            logger.error(f"豆瓣缓存保存失败: {e}")

    @property
    def cache_size(self) -> int:
        return len(self._cache)

    # ==================== 榜单 ====================

    def fetch_top_list(self, limit: int = 250, page_size: int = 20) -> List[Dict]:
        """抓取豆瓣 Top250 榜单。

        `rank` 是官方名次（不严格按分数递减 —— 榜单是加权的，rank 250 的
        《爱·回家》评分 9.0 高于 rank 249 的《东邪西毒》8.6），以 rank 为准。
        """
        collected: Dict[int, Dict] = {}
        start = 0
        empty_pages = 0
        # 硬性页数上限：只靠"连续空页"退出是不够的 —— 若豆瓣对 start 设了上限、
        # 始终返回同一页，collected 不再增长而 empty_pages 也不增长，就会死循环
        # 烧满 CI 超时。
        max_pages = (limit + page_size - 1) // page_size + 5

        for _ in range(max_pages):
            if len(collected) >= limit or empty_pages >= 3:
                break
            params = dict(TOP_LIST_PARAMS, start=str(start), limit=str(page_size))
            url = f"{TOP_LIST_URL}?{urllib.parse.urlencode(params)}"
            page = self._request(url, "https://movie.douban.com/explore")

            if not isinstance(page, list) or not page:
                empty_pages += 1
                start += page_size
                continue
            empty_pages = 0

            for item in page:
                rank = item.get("rank")
                subject_id = str(item.get("id") or "")
                if not subject_id or not isinstance(rank, int):
                    continue
                if rank > limit:
                    continue
                collected[rank] = {
                    "douban_rank": rank,
                    "douban_id": subject_id,
                    "douban_title": item.get("title") or "",
                    "douban_score": item.get("score"),
                    "douban_vote_count": item.get("vote_count"),
                    "douban_url": item.get("url") or f"https://movie.douban.com/subject/{subject_id}/",
                    "douban_poster": item.get("cover_url") or "",
                    "douban_genre": ", ".join(item.get("types") or []),
                    "douban_regions": ", ".join(item.get("regions") or []),
                    "douban_release_date": item.get("release_date") or "",
                }
            start += page_size

        ordered = [collected[r] for r in sorted(collected)[:limit]]
        logger.info(f"豆瓣 Top{limit} 榜单: 取到 {len(ordered)} 部"
                    + (f"（rank 1-{ordered[-1]['douban_rank']}）" if ordered else ""))
        return ordered

    # ==================== 详情与短评 ====================

    def fetch_subject(self, subject_id: str) -> Optional[Dict]:
        """Rexxar 详情，按 id 缓存。不含短评 —— 短评是独立一次请求，
        拆开后详情可以先补齐、短评在预算允许时再补，跨轮续抓。

        简介/导演/演员/别名基本不变，评分与名次每轮从榜单取新值，
        所以缓存详情既省请求又不会让评分过期。
        """
        subject_id = str(subject_id)
        if self.use_cache and subject_id in self._cache:
            return self._cache[subject_id]

        referer = f"https://m.douban.com/movie/subject/{subject_id}/"
        detail = self._request(f"{REXXAR_BASE}/{subject_id}", referer)
        if not isinstance(detail, dict) or not detail.get("title"):
            return None

        rating = detail.get("rating") or {}
        record = {
            "title": detail.get("title") or "",
            "original_title": detail.get("original_title") or "",
            "aka": detail.get("aka") or [],
            "year": detail.get("year") or "",
            "intro": (detail.get("intro") or "").strip(),
            "directors": _names(detail.get("directors")),
            "actors": _names(detail.get("actors"))[:10],
            "genres": detail.get("genres") or [],
            "countries": detail.get("countries") or [],
            "durations": detail.get("durations") or [],
            "rating_value": rating.get("value"),
            "rating_count": rating.get("count"),
            "cover_url": detail.get("cover_url") or "",
            "url": detail.get("url") or f"https://movie.douban.com/subject/{subject_id}/",
            "comments": [],
        }

        if self.use_cache:
            self._cache[subject_id] = record
        return record

    def needs_comments(self, subject_id: str) -> bool:
        """已有详情但还没短评 —— 短评阶段的待办判定。"""
        record = self._cache.get(str(subject_id))
        return bool(record) and not record.get("comments")

    def fetch_comments(self, subject_id: str) -> List[Dict]:
        """抓热门短评并写入已缓存的详情记录。详情缺失时不发请求。"""
        subject_id = str(subject_id)
        record = self._cache.get(subject_id)
        if record is None:
            return []

        referer = f"https://m.douban.com/movie/subject/{subject_id}/"
        params = urllib.parse.urlencode({
            "count": str(COMMENT_COUNT), "order_by": "hot", "start": "0",
        })
        data = self._request(f"{REXXAR_BASE}/{subject_id}/interests?{params}", referer)
        if not isinstance(data, dict):
            return []

        comments = []
        for item in data.get("interests") or []:
            text = (item.get("comment") or "").strip()
            if not text:
                continue
            comments.append({
                "user": (item.get("user") or {}).get("name", ""),
                "rating": (item.get("rating") or {}).get("value"),
                "comment": text,
                "time": item.get("create_time") or "",
            })
        record["comments"] = comments
        return comments

    def cached_subject(self, subject_id: str) -> Optional[Dict]:
        """只读缓存，不发请求。"""
        return self._cache.get(str(subject_id))
