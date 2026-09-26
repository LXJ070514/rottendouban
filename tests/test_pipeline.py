"""端到端流水线：打桩豆瓣榜单/Rexxar/RT/TMDB，验证 main() 的落盘位置、量纲与退出码。

这一层存在的理由是历史上两类事故都发生在"接线"上而不是算法里：
数据写到了 .gitignore 忽略的目录、以及失败被 except 吞掉后 CI 照样绿灯。
"""
import json

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
    monkeypatch.setenv("DOUBAN_TOP_N", "2")
    return site_dir


# 榜单数据由真实的 fetch_top_list 产出（只桩掉网络层），
# 这样夹具不可能与真实输出的键名漂移 —— 之前正是这种漂移掩盖了
# main.py 里 entry['rank'] 的 KeyError（真实键名是 douban_rank）。
_RAW_CHART_PAGES = [
    [
        {"rank": 1, "id": "1292052", "title": "肖申克的救赎", "score": "9.7",
         "vote_count": 3344878, "types": ["犯罪", "剧情"], "regions": ["美国"],
         "cover_url": "https://img/1292052.jpg",
         "url": "https://movie.douban.com/subject/1292052/",
         "release_date": "1994-09-10"},
        {"rank": 2, "id": "1291546", "title": "霸王别姬", "score": "9.6",
         "vote_count": 2200000, "types": ["剧情", "爱情"], "regions": ["中国大陆"],
         "cover_url": "https://img/1291546.jpg",
         "url": "https://movie.douban.com/subject/1291546/",
         "release_date": "1993-01-01"},
    ],
]


def _real_chart():
    import crawler.douban as douban_mod
    client = douban_mod.DoubanClient(cache_path=None, use_cache=False)
    client._request = lambda url, referer: (
        _RAW_CHART_PAGES[0] if "start=0" in url else [])
    return client.fetch_top_list(limit=2, page_size=20)


CHART = _real_chart()

DETAILS = {
    "1292052": {
        "title": "肖申克的救赎", "original_title": "The Shawshank Redemption",
        "aka": ["月黑高飞(港)"], "year": "1994", "intro": "一场谋杀案使银行家安迪蒙冤入狱。",
        "directors": ["弗兰克·德拉邦特"], "actors": ["蒂姆·罗宾斯", "摩根·弗里曼"],
        "genres": ["剧情", "犯罪"], "countries": ["美国"], "durations": ["142分钟"],
        "rating_value": 9.7, "rating_count": 3344878, "cover_url": "", "url": "",
        "comments": [{"user": "文泽尔", "rating": 4, "comment": "希望是好东西。", "time": ""}],
    },
    # 华语片：original_title 为空，英文名只在 aka 里
    "1291546": {
        "title": "霸王别姬", "original_title": "",
        "aka": ["再见，我的妾", "Farewell My Concubine"], "year": "1993",
        "intro": "段小楼与程蝶衣是一对打小一起长大的师兄弟。",
        "directors": ["陈凯歌"], "actors": ["张国荣", "张丰毅", "巩俐"],
        "genres": ["剧情", "爱情"], "countries": ["中国大陆"], "durations": ["171分钟"],
        "rating_value": 9.6, "rating_count": 2200000, "cover_url": "", "url": "",
        "comments": [],
    },
}

RT_TABLE = {
    ("The Shawshank Redemption", 1994): {
        "rt_url": "https://www.rottentomatoes.com/m/shawshank_redemption",
        "tomatometer": "89%", "audience_score": "98%", "genre": "Drama",
    },
    ("Farewell My Concubine", 1993): {
        "rt_url": "https://www.rottentomatoes.com/m/farewell_my_concubine",
        "tomatometer": "90%", "audience_score": "93%", "genre": "Drama",
    },
}


class FakeClient:
    """替身豆瓣客户端。构造参数与真实 DoubanClient 对齐。"""

    chart = CHART
    details = DETAILS
    blocked = False
    saved = 0

    def __init__(self, cache_path=None, use_cache=True, fetch_comments=True):
        self._cache = {}
        self.live_lookups = 0
        self.empty_lookups = 0

    @property
    def cache_size(self):
        return len(self._cache)

    def fetch_top_list(self, limit=250, page_size=20):
        return [dict(e) for e in self.chart[:limit]]

    def fetch_subject(self, subject_id):
        if self.blocked:
            return None
        detail = self.details.get(str(subject_id))
        if detail:
            self._cache[str(subject_id)] = detail
        return detail

    def save_cache(self):
        type(self).saved += 1


class FakeRT:
    def search_movie(self, title, year=None):
        got = RT_TABLE.get((title, year))
        return dict(got) if got else None

    def close(self):
        pass


@pytest.fixture()
def stub_sources(monkeypatch):
    import crawler.douban as douban_mod
    import crawler.rotten_tomatoes as rt_mod
    import crawler.tmdb_api as tmdb_mod

    FakeClient.saved = 0
    FakeClient.blocked = False
    monkeypatch.setattr(douban_mod, "DoubanClient", FakeClient)
    monkeypatch.setattr(rt_mod, "RottenTomatoesCrawler", FakeRT)
    monkeypatch.setattr(tmdb_mod, "is_available", lambda: False)
    return FakeClient


def read_movies(site_dir):
    return json.loads((site_dir / "data" / "movies.json").read_text(encoding="utf-8"))


def test_pipeline_writes_into_site_dir(sandbox, stub_sources):
    assert main_mod.main() == 0
    assert (sandbox / "data" / "movies.json").exists(), f"数据没写到 SITE_DIR：{sandbox}"
    assert len(read_movies(sandbox)) == 2
    assert (sandbox / "data" / "stats.json").exists()


def test_slug_uses_douban_id(sandbox, stub_sources):
    """subject id 比片名+年份更稳，且不受改名影响。"""
    main_mod.main()
    assert {m["slug"] for m in read_movies(sandbox)} == {"douban-1292052", "douban-1291546"}


def test_chart_fields_survive_to_the_site(sandbox, stub_sources):
    main_mod.main()
    by_id = {m["douban_id"]: m for m in read_movies(sandbox)}
    top = by_id["1292052"]
    assert top["douban_rank"] == 1
    assert top["douban_score"] == pytest.approx(9.7)
    assert top["douban_vote_count"] == 3344878
    assert top["douban_title"] == "肖申克的救赎"


def test_rexxar_detail_fills_chinese_fields(sandbox, stub_sources):
    main_mod.main()
    by_id = {m["douban_id"]: m for m in read_movies(sandbox)}
    top = by_id["1292052"]
    assert top["douban_synopsis"].startswith("一场谋杀案")
    assert top["douban_director"] == "弗兰克·德拉邦特"
    assert "摩根·弗里曼" in top["douban_cast"]
    assert top["douban_countries"] == "美国"
    assert top["douban_durations"] == "142分钟"
    assert top["year"] == 1994


def test_comments_are_exported_as_array_not_json_text(sandbox, stub_sources):
    """库里存 JSON 文本，导出必须还原成数组，前端才不必自己 parse。"""
    main_mod.main()
    by_id = {m["douban_id"]: m for m in read_movies(sandbox)}
    comments = by_id["1292052"]["douban_comments"]
    assert isinstance(comments, list) and len(comments) == 1
    assert comments[0]["user"] == "文泽尔"
    assert by_id["1291546"]["douban_comments"] == []


def test_english_name_resolved_from_aka_for_chinese_films(sandbox, stub_sources):
    """华语片 original_title 为空，英文名要从 aka 里取，否则匹配不到 RT。"""
    main_mod.main()
    by_id = {m["douban_id"]: m for m in read_movies(sandbox)}
    assert by_id["1291546"]["title"] == "Farewell My Concubine"
    assert by_id["1291546"]["tomatometer"] == pytest.approx(90)
    assert by_id["1291546"]["rt_url"].endswith("/farewell_my_concubine")


def test_weighted_score_uses_explicit_scales(sandbox, stub_sources):
    main_mod.main()
    by_id = {m["douban_id"]: m for m in read_movies(sandbox)}
    # 0.89*0.3 + 0.98*0.3 + 0.97*0.4 = 0.949
    assert by_id["1292052"]["weighted_score"] == pytest.approx(94.9, abs=0.15)
    assert by_id["1292052"]["douban_score"] == pytest.approx(9.7)


def test_no_placeholder_rt_url(sandbox, stub_sources):
    main_mod.main()
    assert all("/unknown/" not in (m.get("rt_url") or "") for m in read_movies(sandbox))


def test_chart_failure_fails_the_run_and_keeps_old_data(sandbox, stub_sources, monkeypatch):
    """榜单拿不到就必须失败，不能拿半截数据覆盖线上。"""
    target = sandbox / "data" / "movies.json"
    target.write_text('[{"title": "sentinel"}]', encoding="utf-8")
    monkeypatch.setattr(stub_sources, "chart", [])
    assert main_mod.main() == 1
    assert json.loads(target.read_text(encoding="utf-8"))[0]["title"] == "sentinel"


def test_rate_limited_run_still_publishes_chart_fields(sandbox, stub_sources, monkeypatch):
    """Rexxar 被限流时降级为只用榜单字段，而不是整轮失败。

    手工片单此时顶上：它为已知影片提供英文名，所以 RT 匹配依然成功。
    """
    monkeypatch.setattr(stub_sources, "blocked", True)
    assert main_mod.main() == 0
    by_id = {m["douban_id"]: m for m in read_movies(sandbox)}
    assert len(by_id) == 2
    assert all(m["douban_score"] > 0 for m in by_id.values()), "榜单评分不受限流影响"
    assert all(m["douban_rank"] for m in by_id.values())
    assert all(m["douban_synopsis"] in ("", None) for m in by_id.values()), "详情被跳过"
    assert all(m["douban_comments"] == [] for m in by_id.values())
    # 手工片单兜底 → 英文名可用 → RT 仍能匹配
    assert by_id["1292052"]["title"] == "The Shawshank Redemption"
    assert by_id["1292052"]["tomatometer"] == pytest.approx(89)


def test_cache_is_saved_even_when_fetch_fails(sandbox, stub_sources, monkeypatch):
    monkeypatch.setattr(stub_sources, "chart", [])
    main_mod.main()
    assert stub_sources.saved >= 1, "失败也要回写缓存，已抓到的详情不能白抓"


def test_below_publish_floor_keeps_previous_data(sandbox, stub_sources, monkeypatch):
    target = sandbox / "data" / "movies.json"
    target.write_text('[{"title": "sentinel"}]', encoding="utf-8")
    monkeypatch.setenv("MIN_MOVIES_TO_PUBLISH", "50")
    assert main_mod.main() == 1
    assert json.loads(target.read_text(encoding="utf-8"))[0]["title"] == "sentinel"
