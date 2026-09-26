"""数据库层：稳定唯一键与评分量纲。"""
import pytest

from crawler.database import Database


@pytest.fixture()
def db(tmp_path):
    database = Database(db_path=str(tmp_path / "movies.db"))
    yield database
    database.close()


def movie(**overrides):
    data = {
        "title": "Titanic", "original_title": "Titanic", "year": 1997,
        "rt_url": "https://www.rottentomatoes.com/m/titanic",
        "tomatometer": "88%", "audience_score": "69%", "douban_score": "9.5",
        "weighted_score": "84.3",
    }
    data.update(overrides)
    return data


def test_percent_scores_keep_one_decimal(db):
    """旧实现的 normalizer 把 >10 的数强转 int，84.3 会被截成 84。"""
    db.insert_movie(movie())
    row = db.get_movie_by_slug(db.make_slug("Titanic", 1997))
    assert row["weighted_score"] == pytest.approx(84.3)
    assert row["douban_score"] == pytest.approx(9.5)
    assert row["tomatometer"] == pytest.approx(88)


def test_scores_are_clamped(db):
    db.insert_movie(movie(tomatometer="500%", douban_score="42"))
    row = db.get_movie_by_slug(db.make_slug("Titanic", 1997))
    assert row["tomatometer"] == pytest.approx(100)
    assert row["douban_score"] == pytest.approx(10)


def test_missing_scores_stay_sentinel(db):
    db.insert_movie(movie(tomatometer="", audience_score=None, douban_score="", weighted_score=""))
    row = db.get_movie_by_slug(db.make_slug("Titanic", 1997))
    assert row["tomatometer"] == -1
    assert row["weighted_score"] == -1


def test_empty_rt_url_does_not_merge_movies(db):
    """rt_url 曾是唯一键：两部没匹配到 RT 的片会因空串相同而覆盖彼此。"""
    db.batch_insert_movies([
        movie(title="Se7en", original_title="Se7en", year=1995, rt_url=""),
        movie(title="To Live", original_title="To Live", year=1994, rt_url=""),
    ])
    assert db.get_statistics()["total_movies"] == 2


def test_rescrape_updates_instead_of_duplicating(db):
    db.insert_movie(movie(tomatometer="88%"))
    db.insert_movie(movie(tomatometer="90%"))
    rows = db.get_all_movies()
    assert len(rows) == 1
    assert rows[0]["tomatometer"] == pytest.approx(90)


def test_slug_normalizes_title_and_year(db):
    assert db.make_slug("Titanic", 1997) == "titanic-1997"
    assert db.make_slug("  Titanic  ", 1997) == "titanic-1997"
    assert db.make_slug("Titanic", None) == "titanic-0"


def test_score_history_snapshots_each_run(db):
    db.insert_movie(movie())
    row = db.get_movie_by_slug(db.make_slug("Titanic", 1997))
    db.record_score_history(row["id"], row)
    db.conn.commit()
    history = db.get_score_history(row["id"])
    assert len(history) == 1
    assert history[0]["weighted_score"] == pytest.approx(84.3)


def test_export_json_roundtrips(db):
    import json
    db.insert_movie(movie())
    data = json.loads(db.export_json())
    assert data[0]["title"] == "Titanic"
    assert data[0]["douban_score"] == pytest.approx(9.5)


def test_export_json_excludes_score_history(db):
    """历史每次运行都追加一行，嵌进部署产物会让 JSON 无上限膨胀（实测已占 9.1%），
    而前端从不渲染它。历史仍应留在 DB 与 CSV 里。"""
    import json
    db.insert_movie(movie())
    row = db.get_movie_by_slug(db.make_slug("Titanic", 1997))
    db.record_score_history(row["id"], row)
    db.conn.commit()

    assert "score_history" not in json.loads(db.export_json())[0]
    assert len(db.get_score_history(row["id"])) == 1
    assert "weighted_score" in db.export_csv().splitlines()[0]
