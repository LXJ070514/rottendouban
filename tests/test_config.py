"""配置路径：SITE_DIR 指错过一次，代价是线上数据静默停更三个多月。"""
import os

from crawler import config


def test_site_dir_is_the_repo_root_site():
    expected = os.path.join(config.ROOT_DIR, "site")
    assert os.path.normpath(config.SITE_DIR) == os.path.normpath(expected)
    assert os.path.basename(os.path.normpath(config.SITE_DIR)) == "site"
    assert "crawler" not in os.path.normpath(config.SITE_DIR).split(os.sep)


def test_deployed_page_and_data_are_siblings():
    """Pages 上传 ./site，数据必须落在 site/data/ 才可能被部署到。"""
    data_dir = os.path.join(config.SITE_DIR, "data")
    assert os.path.isfile(os.path.join(config.SITE_DIR, "index.html"))
    assert os.path.isfile(os.path.join(data_dir, "movies.json"))


def test_data_dir_lives_inside_site_not_crawler():
    """站点产物必须全在 SITE_DIR 下——历史上 crawler/site/ 与 site/ 并存，
    爬虫写前者、Pages 传后者，数据因此静默停更。"""
    assert os.path.normpath(config.DATA_DIR).startswith(os.path.normpath(config.CRAWLER_DIR))
    assert os.path.normpath(config.SITE_DIR).startswith(os.path.normpath(config.ROOT_DIR))
    assert not os.path.normpath(config.SITE_DIR).startswith(os.path.normpath(config.CRAWLER_DIR))


def test_weights_sum_to_one():
    assert sum(config.SCORE_WEIGHTS.values()) == 1.0
