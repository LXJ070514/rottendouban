"""加权评分：量纲按来源指定，缺失源重新分配权重。"""
import pytest

from crawler.main import calc_weighted_score


def test_all_three_sources():
    # 0.96*0.3 + 0.99*0.3 + 0.83*0.4 = 0.917
    assert calc_weighted_score("96%", "99%", "8.3") == pytest.approx(91.7)


def test_low_rotten_tomatoes_score_is_not_read_as_tenths():
    """8% 是 8 分而不是 80 分 —— 旧实现用 `num <= 10` 猜量纲，会把它当 0.8。"""
    assert calc_weighted_score("8%", "8%", "") == pytest.approx(8.0)


def test_douban_only_fills_whole_weight():
    assert calc_weighted_score("", "", "9.6") == pytest.approx(96.0)


def test_missing_source_reweights():
    # 豆瓣缺失：(0.82*0.3 + 0.75*0.3) / 0.6 = 0.785
    assert calc_weighted_score("82%", "75%", "") == pytest.approx(78.5)


def test_no_source_returns_none():
    assert calc_weighted_score("", "", "") is None
    assert calc_weighted_score(None, "-1", "") is None


def test_garbage_is_ignored_not_raised():
    # 影评人缺失后只剩 观众0.3 + 豆瓣0.4：(0.99*0.3 + 0.83*0.4) / 0.7
    assert calc_weighted_score("N/A", "99%", "8.3") == pytest.approx(89.9)


def test_accepts_numbers_and_percent_strings_alike():
    assert calc_weighted_score(96, 99, 8.3) == calc_weighted_score("96%", "99%", "8.3")
