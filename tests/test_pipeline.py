"""端到端流水线：打桩三个数据源，验证 main() 的落盘位置、量纲与退出码。

这一层存在的理由是历史上两类事故都发生在"接线"上而不是算法里：
数据写到了 .gitignore 忽略的目录、以及失败被 except 吞掉后 CI 照样绿灯。
"""
import json
import os

import pytest

import crawler.database as database_mod
import crawler.main as main_mod


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    site_dir = tmp_path / "site"
    (site_dir / "data").mkdir(parents=True)
    monkeypatch.setattr(main_mod, "SITE_DIR", str(site_dir))
    monkeypatch.setattr(database_mod, "DB_PATH", str(tmp_path / "movies.db"))
    monkeypatch.setattr(main_mod, "LOG_FILE", str(tmp_path / "crawler.log"))
    monkeypatch.setenv("CRAWLER_MODE", "full")
    monkeypatch.delenv("CRAWLER_LIMIT", raising=False)
    monkeypatch.setenv("MIN_MOVIES_TO_PUBLISH", "1")
    return site_dir


@pytest.fixture()
def stub_sources(monkeypatch):
    """伪造 movie_list / RT / 豆瓣 三个源。"""
    import crawler.movie_list as movie_list_mod
    import crawler.rotten_tomatoes as rt_mod
    import crawler.douban as douban_mod

    entries = [
        {"title_en": "Titanic", "title_cn": "泰坦尼克号", "year": 1997},
        {"title_en": "Se7en", "title_cn": "七宗罪", "year": 1995},
    ]
    monkeypatch.setattr(movie_list_mod, "DOUBAN_TOP_250", entries)

    rt_table = {
        "Titanic": {
            "rt_url": "https://www.rottentomatoes.com/m/titanic",
            "tomatometer": "88%", "audience_score": "69%",
            "genre": "Romance", "director": "James Cameron",
        },
        # 七宗罪在 RT 索引里不存在 —— 必须走"放弃该源"分支
        "Se7en": None,
    }

    class FakeCrawler:
        def search_movie(self, title, year=None):
            return rt_table.get(title)

        def close(self):
            pass

    monkeypatch.setattr(rt_mod, "RottenTomatoesCrawler", FakeCrawler)

    import crawler.tmdb_api as tmdb_mod
    monkeypatch.setattr(tmdb_mod, "is_available", lambda: False)

    douban_table = {
        "泰坦尼克号": {"douban_id": "1292722", "douban_url": "https://movie.douban.com/subject/1292722/",
                    "douban_score": "9.5", "douban_vote_count": "2500000",
                    "douban_title": "泰坦尼克号 Titanic (1997)", "douban_genre": "剧情, 爱情",
                    "douban_poster": ""},
        "七宗罪": {"douban_id": "1291870", "douban_url": "https://movie.douban.com/subject/1291870/",
                 "douban_score": "8.8", "douban_vote_count": "1100000",
                 "douban_title": "七宗罪 Se7en (1995)", "douban_genre": "犯罪, 悬疑",
                 "douban_poster": ""},
    }

    class FakeMatcher:
        def __init__(self, use_cache=True):
            self._cache = {}
            self.empty_page_streak = 0

        @property
        def blocked(self):
            return False

        def match_and_fetch(self, title, year=None):
            return douban_table.get(title, {})

        def cached_only(self, title, year=None):
            return douban_table.get(title, {})

        def _save_cache(self):
            pass

    monkeypatch.setattr(douban_mod, "DoubanMatcher", FakeMatcher)
    return rt_table, douban_table


def test_pipeline_writes_into_site_dir(sandbox, stub_sources):
    assert main_mod.main() == 0
    out = sandbox / "data" / "movies.json"
    assert out.exists(), f"数据没写到 SITE_DIR，实际在 {sandbox}"
    movies = json.loads(out.read_text(encoding="utf-8"))
    assert len(movies) == 2
    assert (sandbox / "data" / "stats.json").exists()


def test_weighted_scores_use_explicit_scales(sandbox, stub_sources):
    main_mod.main()
    movies = {m["title"]: m for m in
              json.loads((sandbox / "data" / "movies.json").read_text(encoding="utf-8"))}

    # 0.88*0.3 + 0.69*0.3 + 0.95*0.4 = 0.851
    assert movies["Titanic"]["tomatometer"] == pytest.approx(88)
    assert movies["Titanic"]["douban_score"] == pytest.approx(9.5)
    assert movies["Titanic"]["weighted_score"] == pytest.approx(85.1, abs=0.1)

    # RT 缺失时权重全给豆瓣：0.88 -> 88.0，而不是被当成 8.8%
    assert movies["Se7en"]["tomatometer"] == -1
    assert movies["Se7en"]["weighted_score"] == pytest.approx(88.0, abs=0.1)


def test_no_placeholder_rt_url_reaches_the_site(sandbox, stub_sources):
    main_mod.main()
    movies = json.loads((sandbox / "data" / "movies.json").read_text(encoding="utf-8"))
    assert all("/unknown/" not in (m.get("rt_url") or "") for m in movies)
    assert not any(m.get("rt_url") for m in movies if m["title"] == "Se7en")


def test_each_movie_keeps_its_own_row(sandbox, stub_sources):
    """rt_url 曾是唯一键，空串会让未匹配的多部片合并成一行。"""
    main_mod.main()
    movies = json.loads((sandbox / "data" / "movies.json").read_text(encoding="utf-8"))
    assert len({m["slug"] for m in movies}) == 2


def test_below_publish_floor_keeps_previous_data(sandbox, stub_sources, monkeypatch):
    target = sandbox / "data" / "movies.json"
    target.write_text('[{"title": "sentinel"}]', encoding="utf-8")
    monkeypatch.setenv("MIN_MOVIES_TO_PUBLISH", "50")
    assert main_mod.main() == 1
    assert json.loads(target.read_text(encoding="utf-8"))[0]["title"] == "sentinel"


def test_unexpected_upstream_error_exits_nonzero(sandbox, stub_sources, monkeypatch):
    import crawler.douban as douban_mod

    class ExplodingMatcher:
        def __init__(self, use_cache=True):
            self._cache = {}

        def match_and_fetch(self, title, year=None):
            raise OSError("douban unreachable")

        def _save_cache(self):
            pass

    monkeypatch.setattr(douban_mod, "DoubanMatcher", ExplodingMatcher)
    # 单部失败只记日志继续；全量拿不到数据时由发布下限兜底
    monkeypatch.setenv("MIN_MOVIES_TO_PUBLISH", "50")
    assert main_mod.main() == 1
