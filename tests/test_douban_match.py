"""豆瓣检索结果择优：标题必须含检索词，且年份必须吻合。"""
import pytest

from crawler.douban import DoubanMatcher


@pytest.fixture()
def matcher():
    return DoubanMatcher(use_cache=False)


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
