"""发布前校验脚本：该拦的必须拦住，且不能自己先崩掉。

这个脚本是"坏数据不上线"的最后一道闸。它自己抛异常和在 CI 里输出一段
TypeError 堆栈，效果等同于没有校验 —— 所以每个用例都断言返回码，
而不是断言"抛了某个异常"。
"""
import json

import pytest

import scripts.verify_site as vs


def _movie(**over):
    base = {
        "id": 1, "slug": "douban-1", "title": "Some Film", "douban_id": "1",
        "douban_rank": 1, "tomatometer": 90, "audience_score": 80,
        "weighted_score": 85.0, "douban_score": 8.5,
        "douban_comments": [], "rt_url": "",
    }
    base.update(over)
    return base


def _run(tmp_path, movies):
    p = tmp_path / "movies.json"
    p.write_text(json.dumps(movies, ensure_ascii=False), encoding="utf-8")
    return vs.main(str(p))


def test_valid_data_passes(tmp_path):
    assert _run(tmp_path, [_movie()]) == 0


def test_missing_scores_are_allowed(tmp_path):
    """没有评分不等于坏数据 —— 缺失用空串或 -1 表示，必须放行。"""
    assert _run(tmp_path, [
        _movie(tomatometer=-1, audience_score="", weighted_score="", douban_score=-1),
    ]) == 0


@pytest.mark.parametrize("field,value", [
    ("tomatometer", 120),        # 越界
    ("tomatometer", -5),         # 负数哨兵以外的负值
    ("audience_score", 101),
    ("weighted_score", 100.1),
    ("douban_score", 12),        # 十分制写成了十二分
])
def test_out_of_range_is_rejected(tmp_path, field, value):
    assert _run(tmp_path, [_movie(**{field: value})]) == 1


@pytest.mark.parametrize("field,value", [
    ("tomatometer", "90%"),      # 量纲没归一，前端会把它当数字用
    ("audience_score", "80"),
    ("douban_score", [8.5]),
])
def test_non_numeric_scores_are_reported_not_crashed(tmp_path, field, value):
    """曾经这里会抛 TypeError: '>=' not supported between 'str' and 'int' ——
    校验脚本的职责是报告问题，自己先崩掉就失去意义了。"""
    assert _run(tmp_path, [_movie(**{field: value})]) == 1


def test_boolean_score_is_rejected(tmp_path):
    """bool 是 int 的子类，不特判就会当成 1 分混过去。"""
    assert _run(tmp_path, [_movie(tomatometer=True)]) == 1


def test_placeholder_rt_url_is_rejected(tmp_path):
    assert _run(tmp_path, [_movie(rt_url="https://www.rottentomatoes.com/m/unknown/")]) == 1


def test_missing_douban_id_is_rejected(tmp_path):
    m = _movie()
    del m["douban_id"]
    assert _run(tmp_path, [m]) == 1


def test_comments_must_be_an_array(tmp_path):
    assert _run(tmp_path, [_movie(douban_comments="not-a-list")]) == 1


def test_empty_or_non_list_payload_is_rejected(tmp_path):
    assert _run(tmp_path, []) == 1
    p = tmp_path / "bad.json"
    p.write_text('{"not": "a list"}', encoding="utf-8")
    assert vs.main(str(p)) == 1


def test_missing_file_is_reported(tmp_path):
    assert vs.main(str(tmp_path / "nope.json")) == 1


def test_malformed_json_is_reported(tmp_path):
    p = tmp_path / "broken.json"
    p.write_text("{not json", encoding="utf-8")
    assert vs.main(str(p)) == 1
