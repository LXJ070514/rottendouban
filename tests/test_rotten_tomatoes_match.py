"""RT Algolia 结果择优：同名翻拍片/重映活动必须被年份否掉。

这些用例来自 2026-09-26 的实测事故——旧实现无脑取 hits[0]，
把《泰坦尼克号》匹配到 2018 年同名片、《霸王别姬》匹配到 2014 年纪录片。
"""
from crawler.rotten_tomatoes import _algolia_best_match, _query_variants


def hit(title, year, vanity, critics=None, audience=None):
    return {
        "title": title,
        "releaseYear": year,
        "vanity": vanity,
        "rottenTomatoes": {"criticsScore": critics, "audienceScore": audience},
    }


TITANIC_HITS = [
    hit("Titanic", 2018, "titanic_2018"),
    hit("Titanic", 1943, "1133451-titanic", 60),
    hit("Titanic", 1953, "1056130-titanic", 91),
    hit("Titanic", 1997, "titanic", 88, 69),
]


def test_picks_the_year_matching_film_not_the_first_hit():
    best = _algolia_best_match(TITANIC_HITS, "Titanic", 1997)
    assert best["vanity"] == "titanic"
    assert best["releaseYear"] == 1997


def test_returns_none_when_no_candidate_matches_the_year():
    """宁缺毋滥：索引里只有 1943 版时不能冒充 1997 版。"""
    assert _algolia_best_match(TITANIC_HITS[:3], "Titanic", 1997) is None


def test_rejects_longer_title_that_merely_contains_the_query():
    """"Se7en" 不能匹配 "Se7en days"（旧实现的子串规则会选中它）。"""
    hits = [hit("Se7en days", 2010, "se7en_days"), hit("The Edge of Seventeen", 2016, "edge")]
    assert _algolia_best_match(hits, "Se7en", 1995) is None


def test_accent_and_punctuation_insensitive():
    hits = [hit("Amélie", 2001, "amelie", 95)]
    assert _algolia_best_match(hits, "Amelie", 2001)["vanity"] == "amelie"


def test_prefers_scored_duplicate_over_undated_one():
    hits = [hit("Trainspotting", 1996, "trainspotting_2"), hit("Trainspotting", 1996, "trainspotting", 90)]
    assert _algolia_best_match(hits, "Trainspotting", 1996)["vanity"] == "trainspotting"


def test_empty_inputs():
    assert _algolia_best_match([], "Titanic", 1997) is None
    assert _algolia_best_match(None, "Titanic", 1997) is None
    assert _algolia_best_match(TITANIC_HITS, "", 1997) is None


def test_one_year_tolerance_for_release_year_skew():
    hits = [hit("Dune", 2021, "dune_2021", 83)]
    assert _algolia_best_match(hits, "Dune", 2021)["vanity"] == "dune_2021"


def test_query_variants_cover_colon_aliases_and_accents():
    variants = _query_variants("Léon: The Professional")
    assert variants[0] == "Léon: The Professional"
    assert "Leon: The Professional" in variants
    # 《这个杀手不太冷》在 RT 索引里挂在 《The Professional》 名下
    assert "The Professional" in variants
    assert len(variants) <= 4


def test_query_variants_deduplicate_plain_titles():
    assert _query_variants("Titanic") == ["Titanic"]
