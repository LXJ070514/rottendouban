"""豆瓣模块：榜单分页、Rexxar 详情/短评、id 级缓存、限流熔断。

端点选择依据 scripts/diagnose_sources.py 在 GitHub Runner 上的实测结论：
search.douban.com 在数据中心 IP 上返回 200 但 items 为空，故整个模块不再使用它。
"""
import json

import pytest

import crawler.douban as douban_mod
from crawler.douban import DoubanClient, english_title_candidates


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(douban_mod, "REQUEST_DELAY", 0.0)
    return DoubanClient(cache_path=str(tmp_path / "cache.json"), use_cache=True)


def chart_item(rank, subject_id, title, score="9.7", votes=1000000):
    return {
        "rank": rank, "id": subject_id, "title": title, "score": score,
        "vote_count": votes, "types": ["剧情"], "regions": ["美国"],
        "cover_url": f"https://img/{subject_id}.jpg",
        "url": f"https://movie.douban.com/subject/{subject_id}/",
        "release_date": "1994-09-10",
    }


def detail(subject_id="1292052", title="肖申克的救赎", original="The Shawshank Redemption",
           aka=None, intro="一场谋杀案……", year="1994"):
    return {
        "id": subject_id, "title": title, "original_title": original,
        "aka": aka if aka is not None else ["月黑高飞(港)"],
        "year": year, "intro": intro,
        "directors": [{"name": "弗兰克·德拉邦特"}],
        "actors": [{"name": "蒂姆·罗宾斯"}, {"name": "摩根·弗里曼"}],
        "genres": ["剧情", "犯罪"], "countries": ["美国"], "durations": ["142分钟"],
        "rating": {"value": 9.7, "count": 3344878, "max": 10},
        "cover_url": "https://img/cover.jpg",
        "url": f"https://movie.douban.com/subject/{subject_id}/",
    }


# ==================== 榜单 ====================

def test_top_list_pages_until_limit(client, monkeypatch):
    pages = {
        0: [chart_item(1, "1292052", "肖申克的救赎"), chart_item(2, "1291546", "霸王别姬")],
        2: [chart_item(3, "1296141", "控方证人")],
    }
    calls = []

    def fake_request(url, referer):
        calls.append(url)
        start = int(url.split("start=")[1].split("&")[0])
        return pages.get(start, [])

    monkeypatch.setattr(client, "_request", fake_request)
    got = client.fetch_top_list(limit=3, page_size=2)

    assert [e["douban_rank"] for e in got] == [1, 2, 3]
    assert [e["douban_id"] for e in got] == ["1292052", "1291546", "1296141"]
    assert len(calls) >= 2


def test_top_list_keeps_official_rank_not_score_order(client, monkeypatch):
    """豆瓣榜单是加权的，rank 250 的评分可能高于 rank 249 —— 必须以 rank 为准。"""
    monkeypatch.setattr(client, "_request", lambda url, referer: [
        chart_item(2, "b", "乙", score="9.6"),
        chart_item(1, "a", "甲", score="9.7"),
        chart_item(3, "c", "丙", score="9.0"),
    ])
    got = client.fetch_top_list(limit=3, page_size=20)
    assert [e["douban_rank"] for e in got] == [1, 2, 3]
    assert [e["douban_title"] for e in got] == ["甲", "乙", "丙"]


def test_top_list_stops_after_repeated_empty_pages(client, monkeypatch):
    """被限流时榜单会返回空，不能无限翻页。"""
    calls = []
    monkeypatch.setattr(client, "_request",
                        lambda url, referer: calls.append(url) or [])
    assert client.fetch_top_list(limit=250, page_size=20) == []
    assert len(calls) == 3, "连续 3 页为空即应停止"


def test_top_list_has_hard_page_cap(client, monkeypatch):
    """豆瓣若对 start 设上限、永远返回同一页，只靠"空页计数"会死循环烧满 CI 超时。"""
    calls = []
    monkeypatch.setattr(client, "_request", lambda url, referer: calls.append(url) or [
        chart_item(1, "1292052", "肖申克的救赎"),
        chart_item(999, "x", "超出 limit，被过滤"),
    ])
    got = client.fetch_top_list(limit=10, page_size=2)
    assert [e["douban_rank"] for e in got] == [1]
    # limit=10/page_size=2 → 5 页 + 5 页余量
    assert len(calls) <= 10, f"翻页未封顶，实际请求 {len(calls)} 次"


def test_top_list_drops_entries_without_id_or_rank(client, monkeypatch):
    monkeypatch.setattr(client, "_request", lambda url, referer: [
        chart_item(1, "1292052", "肖申克的救赎"),
        {"rank": 2, "title": "没有 id"},
        {"id": "999", "title": "没有 rank"},
    ])
    got = client.fetch_top_list(limit=10, page_size=20)
    assert [e["douban_id"] for e in got] == ["1292052"]


# ==================== 英文名解析 ====================

def test_english_candidates_prefers_original_title():
    got = list(english_title_candidates(detail()))
    assert got[0] == "The Shawshank Redemption"


def test_english_candidates_falls_back_to_ascii_aka():
    """华语片 original_title 为空，英文名在 aka 里。"""
    d = detail(original="", aka=["再见，我的妾", "Farewell My Concubine", "Adieu Ma Concubine"])
    got = list(english_title_candidates(d))
    assert got == ["Farewell My Concubine", "Adieu Ma Concubine"]


def test_english_candidates_yields_all_ascii_aliases_in_order():
    """《活着》的 aka 是 ['人生','Lifetimes','To Live']，Lifetimes 并非 RT 收录的那个，
    所以必须全部产出、由严格匹配器逐个自校验。"""
    d = detail(original="", aka=["人生", "Lifetimes", "To Live"])
    assert list(english_title_candidates(d)) == ["Lifetimes", "To Live"]


def test_english_candidates_empty_when_no_latin_title():
    assert list(english_title_candidates(detail(original="", aka=["人生", "活着"]))) == []
    assert list(english_title_candidates({})) == []


# ==================== 详情与缓存 ====================

def test_fetch_subject_normalizes_fields(client, monkeypatch):
    monkeypatch.setattr(client, "_request", lambda url, referer: detail())
    got = client.fetch_subject("1292052")
    assert got["intro"].startswith("一场谋杀案")
    assert got["directors"] == ["弗兰克·德拉邦特"]
    assert got["actors"] == ["蒂姆·罗宾斯", "摩根·弗里曼"]
    assert got["rating_value"] == 9.7
    assert got["year"] == "1994"


def test_fetch_subject_sends_mobile_referer(client, monkeypatch):
    """Rexxar 缺 Referer 会被拒，这是硬要求。"""
    seen = {}

    def fake_get(url, referer, timeout=15):
        seen["referer"] = referer
        return detail()

    monkeypatch.setattr(douban_mod, "_get", fake_get)
    client.fetch_subject("1292052")
    assert seen["referer"] == "https://m.douban.com/movie/subject/1292052/"


def test_fetch_subject_is_cached_by_id(client, monkeypatch):
    calls = []
    monkeypatch.setattr(client, "_request", lambda url, referer: calls.append(url) or detail())
    client.fetch_subject("1292052")
    first = len(calls)
    client.fetch_subject("1292052")
    assert len(calls) == first, "同一 id 第二次不应再发请求"


def test_fetch_subject_returns_none_on_blocked_response(client, monkeypatch):
    monkeypatch.setattr(client, "_request", lambda url, referer: None)
    assert client.fetch_subject("1292052") is None


def test_cache_roundtrip_and_old_format_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(douban_mod, "REQUEST_DELAY", 0.0)
    path = tmp_path / "cache.json"
    first = DoubanClient(cache_path=str(path), use_cache=True)
    first._cache["1292052"] = {"title": "肖申克的救赎", "intro": "x"}
    first.save_cache()

    reloaded = DoubanClient(cache_path=str(path), use_cache=True)
    assert reloaded.cache_size == 1
    assert reloaded._cache["1292052"]["title"] == "肖申克的救赎"

    # 旧版以中文片名为键，结构不合必须丢弃而不是误当成 id 缓存
    path.write_text(json.dumps({"肖申克的救赎": {"score": "9.7"}}, ensure_ascii=False),
                    encoding="utf-8")
    legacy = DoubanClient(cache_path=str(path), use_cache=True)
    assert legacy.cache_size == 0


# ==================== 短评 ====================

def test_comments_are_normalized(client, monkeypatch):
    def fake_request(url, referer):
        if "interests" in url:
            return {"total": 666846, "interests": [
                {"user": {"name": "文泽尔"}, "rating": {"value": 4},
                 "comment": "人的生命不过是从一个洞穴通往另一个世界", "create_time": "2020-01-01"},
                {"user": {"name": "某人"}, "rating": None, "comment": "   ", "create_time": ""},
            ]}
        return detail()

    monkeypatch.setattr(client, "_request", fake_request)
    got = client.fetch_subject("1292052")
    assert len(got["comments"]) == 1, "空正文的短评应被丢弃"
    assert got["comments"][0]["user"] == "文泽尔"
    assert got["comments"][0]["rating"] == 4


def test_comments_can_be_disabled(client, monkeypatch):
    client.fetch_comments = False
    urls = []
    monkeypatch.setattr(client, "_request",
                        lambda url, referer: urls.append(url) or detail())
    client.fetch_subject("1292052")
    assert not any("interests" in u for u in urls)


# ==================== 限流熔断 ====================

def test_blocked_needs_enough_samples(client):
    client.live_lookups, client.empty_lookups = 5, 5
    assert client.blocked is False


def test_blocked_on_empty_ratio(client):
    client.live_lookups, client.empty_lookups = 95, 87
    assert client.blocked is True


def test_not_blocked_when_mostly_answered(client):
    client.live_lookups, client.empty_lookups = 40, 6
    assert client.blocked is False


def test_request_counts_empty_and_nonempty(client, monkeypatch):
    monkeypatch.setattr(douban_mod, "_get", lambda url, referer, timeout=15: None)
    client._request("https://x", "https://r")
    assert (client.live_lookups, client.empty_lookups) == (1, 1)

    monkeypatch.setattr(douban_mod, "_get", lambda url, referer, timeout=15: {"ok": 1})
    client._request("https://x", "https://r")
    assert (client.live_lookups, client.empty_lookups) == (2, 1)


def test_non_json_response_counts_as_empty(client, monkeypatch):
    """限流时豆瓣返回 200 + HTML 验证页，JSON 解析失败必须计入空结果。"""
    class FakeResp:
        def read(self):
            return b"<html>please verify</html>"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(douban_mod.urllib.request, "urlopen",
                        lambda *a, **k: FakeResp())
    assert douban_mod._get("https://m.douban.com/rexxar/api/v2/movie/1", "https://r") is None


# ==================== 配额退避 ====================

class _RateLimitHTTPError(douban_mod.urllib.error.HTTPError):
    def __init__(self):
        super().__init__("https://m.douban.com/rexxar/api/v2/movie/1", 400,
                         "Bad Request", {}, None)
        self._body = b'{"request": "GET /v2/movie/1", "msg": "subject_ip_rate_limit"}'

    def read(self, *a):
        return self._body


def test_get_raises_ratelimited_on_ip_quota(client, monkeypatch):
    """HTTP 400 + subject_ip_rate_limit 必须抛 RateLimited，不能和普通失败混为一谈。

    混为一谈的后果是"等一等就能继续"被当成"该片没有详情"永久跳过，缓存永远补不满。
    """
    def boom(*a, **k):
        raise _RateLimitHTTPError()

    monkeypatch.setattr(douban_mod.urllib.request, "urlopen", boom)
    with pytest.raises(douban_mod.RateLimited):
        douban_mod._get("https://m.douban.com/rexxar/api/v2/movie/1", "https://r")


def test_request_retries_after_rate_limit(client, monkeypatch):
    """撞配额后应等待重试并成功，而不是直接放弃。"""
    monkeypatch.setattr(douban_mod, "RATE_LIMIT_BACKOFF", 0)
    calls = []

    def flaky(url, referer, timeout=15):
        calls.append(url)
        if len(calls) < 3:
            raise douban_mod.RateLimited(url)
        return {"title": "肖申克的救赎"}

    monkeypatch.setattr(douban_mod, "_get", flaky)
    got = client._request("https://x", "https://r")
    assert got == {"title": "肖申克的救赎"}
    assert len(calls) == 3
    assert client.empty_lookups == 0, "重试成功后不应计为空结果"


def test_request_gives_up_after_max_retries(client, monkeypatch):
    monkeypatch.setattr(douban_mod, "RATE_LIMIT_BACKOFF", 0)
    monkeypatch.setattr(douban_mod, "RATE_LIMIT_RETRIES", 2)
    calls = []

    def always_limited(url, referer, timeout=15):
        calls.append(url)
        raise douban_mod.RateLimited(url)

    monkeypatch.setattr(douban_mod, "_get", always_limited)
    assert client._request("https://x", "https://r") is None
    assert len(calls) == 3, "首次 + 2 次重试"
    assert client.empty_lookups == 1


def test_fetch_subject_survives_rate_limit(client, monkeypatch):
    """详情被限流时返回 None 让上层降级，而不是把异常抛穿整轮抓取。"""
    monkeypatch.setattr(douban_mod, "RATE_LIMIT_BACKOFF", 0)
    monkeypatch.setattr(douban_mod, "RATE_LIMIT_RETRIES", 0)

    def limited(url, referer, timeout=15):
        raise douban_mod.RateLimited(url)

    monkeypatch.setattr(douban_mod, "_get", limited)
    assert client.fetch_subject("1292052") is None
