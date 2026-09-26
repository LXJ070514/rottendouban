"""豆瓣检索结果择优：标题必须含检索词，且年份必须吻合。"""
import pytest

import crawler.douban as douban_mod
from crawler.douban import DoubanMatcher


@pytest.fixture()
def matcher(tmp_path, monkeypatch):
    """开启缓存，但把缓存文件指向不存在的路径，避免测试读到仓库里真实的 douban_cache.json。"""
    monkeypatch.setattr(douban_mod, "DOUBAN_CACHE_PATH", str(tmp_path / "absent.json"))
    return DoubanMatcher(use_cache=True)


def item(title, score="9.6", votes="100000", subject_id="1292052"):
    return {
        "url": f"https://movie.douban.com/subject/{subject_id}/",
        "id": subject_id,
        "title": title,
        "score": score,
        "vote_count": votes,
        "chinese_title": title,
        "genre": "",
        "poster": "",
    }


def test_matches_title_with_year(matcher):
    results = [item("霸王别姬 (1993)"), item("霸王别姬‎ (2014)")]
    best = matcher._best_match(results, "霸王别姬", 1993)
    assert best["title"] == "霸王别姬 (1993)"


def test_rejects_when_no_year_agrees(matcher):
    """只有 2014 版时不能冒充 1993 版——旧实现会直接返回 results[0]。"""
    assert matcher._best_match([item("霸王别姬‎ (2014)")], "霸王别姬", 1993) == {}


def test_rejects_unrelated_titles(matcher):
    results = [item("教父 The Godfather (1972)")]
    assert matcher._best_match(results, "教父2", 1974) == {}


def test_one_year_tolerance(matcher):
    best = matcher._best_match([item("星际穿越 (2014)")], "星际穿越", 2014)
    assert best["title"] == "星际穿越 (2014)"


def test_prefers_most_voted_among_equal_matches(matcher):
    results = [
        item("泰坦尼克号 (1997)", votes="1000", subject_id="1"),
        item("泰坦尼克号 (1997)", votes="1200000", subject_id="2"),
    ]
    assert matcher._best_match(results, "泰坦尼克号", 1997)["id"] == "2"


def test_tolerates_comma_grouped_vote_counts(matcher):
    assert matcher._best_match([item("活着 (1994)", votes="460,000")], "活着", 1994)["id"] == "1292052"


def test_empty_results(matcher):
    assert matcher._best_match([], "活着", 1994) == {}
    assert matcher._best_match([item("教父 (1972)")], "", 1972) == {}


def test_cache_key_includes_year(matcher):
    """同名不同年的两片不能共用一个缓存键，否则第二片永远读到第一片的结果。"""
    assert matcher._cache_key("霸王别姬", 1993) != matcher._cache_key("霸王别姬", 2014)


def test_legacy_bare_title_key_still_hits(matcher):
    """6.1 把键从 title 改成 title|year，仓库里已有的裸片名条目会集体失效，
    逼着每次运行去敲豆瓣接口 —— 而豆瓣对 CI 的 IP 是软封的。"""
    matcher._cache["泰坦尼克号"] = item("泰坦尼克号 Titanic (1997)")
    hit = matcher._check_cache("泰坦尼克号", 1997)
    assert hit and hit["title"] == "泰坦尼克号 Titanic (1997)"


def test_legacy_key_rejected_on_year_conflict(matcher):
    """回退到旧键不能放松年份校验，否则同名剧集会被当成电影。"""
    matcher._cache["霸王别姬"] = item("霸王别姬 (1993)")
    assert matcher._check_cache("霸王别姬", 2014) is None


def test_legacy_key_accepted_when_entry_has_no_year(matcher):
    matcher._cache["教父"] = item("教父 The Godfather")
    assert matcher._check_cache("教父", 1972) is not None


def test_blocked_needs_enough_samples(matcher):
    """样本太少就判限流会把"确实查无此片"误当成封禁。"""
    matcher.live_lookups = 5
    matcher.empty_lookups = 5
    assert matcher.blocked is False


def test_blocked_on_empty_result_ratio(matcher):
    """实测形态：豆瓣返回 200 + 合法 __DATA__ 但 items 为空（95 次里 87 次）。"""
    matcher.live_lookups = 95
    matcher.empty_lookups = 87
    assert matcher.blocked is True


def test_not_blocked_when_mostly_answered(matcher):
    matcher.live_lookups = 40
    matcher.empty_lookups = 6
    assert matcher.blocked is False


class _FakeResponse:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _serve(monkeypatch, body):
    monkeypatch.setattr(
        douban_mod.urllib.request, "urlopen",
        lambda *a, **k: _FakeResponse(body))


def test_api_search_counts_empty_items(monkeypatch, tmp_path):
    """豆瓣最常见的软封形态：200 + 合法 __DATA__ + items 为空。"""
    monkeypatch.setattr(douban_mod, "DOUBAN_CACHE_PATH", str(tmp_path / "absent.json"))
    m = DoubanMatcher(use_cache=True)
    _serve(monkeypatch, b'<script>window.__DATA__ = {"items": []}</script>')
    assert m._api_search("教父2") == []
    assert (m.live_lookups, m.empty_lookups) == (1, 1)


def test_api_search_counts_shell_page(monkeypatch, tmp_path):
    """第二种形态：返回登录/验证页，连 __DATA__ 都没有。"""
    monkeypatch.setattr(douban_mod, "DOUBAN_CACHE_PATH", str(tmp_path / "absent.json"))
    m = DoubanMatcher(use_cache=True)
    _serve(monkeypatch, b"<html>please login</html>")
    assert m._api_search("教父2") == []
    assert (m.live_lookups, m.empty_lookups) == (1, 1)


def test_api_search_real_results_not_counted_as_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(douban_mod, "DOUBAN_CACHE_PATH", str(tmp_path / "absent.json"))
    m = DoubanMatcher(use_cache=True)
    _serve(monkeypatch,
           b'window.__DATA__ = {"items":[{"title":"\xe6\x95\x99\xe7\x88\xb6 (1972)",'
           b'"url":"https://movie.douban.com/subject/1291841/",'
           b'"rating":{"value":9.3,"count":900000}}]}')
    results = m._api_search("教父")
    assert len(results) == 1
    assert (m.live_lookups, m.empty_lookups) == (1, 0)


def test_cached_only_does_not_hit_the_network(matcher):
    """限流后剩余影片仍要吃到缓存，所以这条路径绝不能触发请求。"""
    def boom(title):
        raise AssertionError("cached_only 不应发起网络请求")

    matcher._api_search = boom
    matcher._cache["让子弹飞"] = item("让子弹飞 (2010)")
    out = matcher.cached_only("让子弹飞", 2010)
    assert out["douban_score"] == "9.6"

    empty = matcher.cached_only("不存在", 2020)
    assert empty["douban_id"] == ""
