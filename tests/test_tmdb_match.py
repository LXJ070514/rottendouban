"""TMDB 检索：language 跟着查询语言走，兜底匹配必须对年份。

TMDB 在本项目里承担中文名→英文名反查，这是 RT 覆盖率的第三级来源。
run 36267829360 实测：反查贡献 153/250 的英文名，RT 最终命中 211/250。

检索语言用 zh-CN 是为了让"标题精确匹配"那一级生效 —— 冷知识：用 en-US
搜中文片名**也能**拿到结果（TMDB 索引了译名，落在 results[0]），
所以这里锁的不是"能不能搜到"，而是"命中走的是精确匹配还是兜底取首条"。
"""
import pytest

import crawler.tmdb_api as tmdb


@pytest.fixture()
def stub_request(monkeypatch):
    """替身 _tmdb_request：记录每次调用的 endpoint 与 params，按脚本返回。"""
    calls = []
    script = {}

    def fake(endpoint, params=None, timeout=10):
        calls.append((endpoint, dict(params or {})))
        return script.get(endpoint)

    monkeypatch.setattr(tmdb, "_tmdb_request", fake)
    return {"calls": calls, "script": script}


def hit(id_, title, original=None, date=None):
    return {"id": id_, "title": title,
            "original_title": original or title, "release_date": date or ""}


# ==================== language 选择 ====================

@pytest.mark.parametrize("query,expected", [
    ("控方证人", "zh-CN"),
    ("肖申克的救赎", "zh-CN"),
    ("The Shawshank Redemption", "en-US"),
    ("Amélie", "zh-CN"),      # 含非 ASCII 重音字符，同样要按原文语种检索
    ("", "en-US"),
])
def test_search_language_follows_the_query(query, expected):
    assert tmdb._search_language(query) == expected


def test_chinese_query_is_sent_with_zh_language(stub_request):
    stub_request["script"]["/search/movie"] = {"results": []}
    tmdb.search_movie("控方证人", 1957)
    assert stub_request["calls"][0][1]["language"] == "zh-CN"


def test_english_query_is_sent_with_en_language(stub_request):
    stub_request["script"]["/search/movie"] = {"results": []}
    tmdb.search_movie("Witness for the Prosecution", 1957)
    assert stub_request["calls"][0][1]["language"] == "en-US"


def test_zh_search_matches_translated_title(stub_request):
    """language=zh-CN 时 TMDB 回的 title 就是中文，精确匹配这一级即可命中。"""
    stub_request["script"]["/search/movie"] = {"results": [
        hit(37247, "控方证人", "Witness for the Prosecution", "1957-12-17"),
    ]}
    assert tmdb.search_movie("控方证人", 1957) == 37247


# ==================== 模糊匹配的年份把关 ====================

def test_fuzzy_substring_match_requires_a_year_match(stub_request):
    """子串关系是双向的："证人" 是 "控方证人" 的子串，反过来也成立，
    光靠标题就能把无关影片选进来。有年份时可年份必须也吻合。"""
    stub_request["script"]["/search/movie"] = {"results": [
        hit(1, "证人", "The Witness", "2018-05-01"),          # 查询的子串，但年份差 61 年
        hit(2, "控方证人", "Witness for the Prosecution", "1957-12-17"),
    ]}
    assert tmdb.search_movie("控方证人", 1957) == 2


def test_fuzzy_match_without_year_is_still_accepted(stub_request):
    """年份未知时无从把关，沿用标题子串即命中 —— 榜单 release_date 缺失的条目走这条。"""
    stub_request["script"]["/search/movie"] = {"results": [
        hit(1, "证人", "The Witness", "2018-05-01"),
    ]}
    assert tmdb.search_movie("控方证人") == 1


def test_exact_title_match_wins_regardless_of_year(stub_request):
    stub_request["script"]["/search/movie"] = {"results": [
        hit(5, "控方证人", "Witness for the Prosecution", "1980-01-01"),
    ]}
    assert tmdb.search_movie("控方证人", 1957) == 5


def test_first_result_fallback_is_kept(stub_request):
    """兜底取首条是经验证有效的路径：run 36267829360 用 en-US 检索时正是靠
    这一级拿到 153/250 的英文名。改 zh-CN 后仍要保留它，否则覆盖率会掉。"""
    stub_request["script"]["/search/movie"] = {"results": [
        hit(1, "完全无关的片名", "Totally Unrelated", "2018-05-01"),
    ]}
    assert tmdb.search_movie("控方证人", 1957) == 1


def test_year_filtered_search_retries_without_the_year(stub_request, monkeypatch):
    """榜单给的是内地公映日期，与 TMDB 的 primary_release_year 常差一年以上。"""
    responses = [{"results": []}, {"results": [hit(3, "控方证人", date="1957-12-17")]}]

    def scripted(endpoint, params=None, timeout=10):
        stub_request["calls"].append((endpoint, dict(params or {})))
        return responses.pop(0)

    monkeypatch.setattr(tmdb, "_tmdb_request", scripted)
    assert tmdb.search_movie("控方证人", 1957) == 3
    assert "primary_release_year" in stub_request["calls"][0][1]
    assert "primary_release_year" not in stub_request["calls"][1][1]


def test_year_of_parses_and_rejects():
    assert tmdb._year_of("1957-12-17") == 1957
    assert tmdb._year_of("") is None
    assert tmdb._year_of(None) is None
