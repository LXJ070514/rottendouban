"""数据库管理模块 — SQLite 操作、schema 自愈、批量提交"""
import sqlite3
import logging
import os
from datetime import datetime

from crawler.config import (
    DB_PATH, MAX_DIRECTOR_LENGTH, MAX_CAST_LENGTH, MAX_SYNOPSIS_LENGTH,
)

logger = logging.getLogger("database")


class Database:
    """电影数据库管理类"""

    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        self.conn = None
        self._connect()
        self._init_tables()
        self._migrate()

    def _connect(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        logger.info(f"数据库连接: {self.db_path}")

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS movies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,
                rt_url TEXT,
                title TEXT NOT NULL,
                original_title TEXT,
                year INTEGER,
                rating TEXT,
                tomatometer INTEGER DEFAULT -1,
                audience_score INTEGER DEFAULT -1,
                genre TEXT,
                director TEXT,
                writers TEXT,
                cast TEXT,
                critics_consensus TEXT,
                synopsis TEXT,
                release_date TEXT,
                runtime TEXT,
                poster_url TEXT,
                douban_id TEXT,
                douban_url TEXT,
                douban_score REAL DEFAULT -1,
                douban_vote_count INTEGER DEFAULT 0,
                douban_title TEXT,
                douban_genre TEXT,
                douban_director TEXT,
                douban_writers TEXT,
                douban_cast TEXT,
                douban_synopsis TEXT,
                douban_poster TEXT,
                weighted_score REAL DEFAULT -1,
                category TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS score_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                movie_id INTEGER NOT NULL,
                tomatometer INTEGER DEFAULT -1,
                audience_score INTEGER DEFAULT -1,
                douban_score REAL DEFAULT -1,
                weighted_score REAL DEFAULT -1,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (movie_id) REFERENCES movies(id)
            );

            CREATE INDEX IF NOT EXISTS idx_movies_title ON movies(title);
            CREATE INDEX IF NOT EXISTS idx_movies_weighted ON movies(weighted_score);
            CREATE INDEX IF NOT EXISTS idx_movies_douban_id ON movies(douban_id);
            CREATE INDEX IF NOT EXISTS idx_history_movie ON score_history(movie_id);
            CREATE INDEX IF NOT EXISTS idx_history_date ON score_history(recorded_at);
        """)
        self.conn.commit()

    def _migrate(self):
        existing = {row[1] for row in self.conn.execute("PRAGMA table_info(movies)")}
        if not existing:
            return

        # slug 之前的库以 rt_url 作唯一键：RT 时有时无会让同一部片裂成两行。
        # movies.db 是 gitignore 的一次性缓存，结构不合就重建，不做数据搬迁。
        if "slug" not in existing:
            logger.info("检测到旧版 movies.db（无 slug 列），重建")
            self.conn.close()
            os.remove(self.db_path)
            self._connect()
            self._init_tables()
            return

        migrations = [
            ("douban_id", "TEXT"), ("douban_url", "TEXT"),
            ("douban_score", "REAL DEFAULT -1"), ("douban_vote_count", "INTEGER DEFAULT 0"),
            ("douban_title", "TEXT"), ("douban_genre", "TEXT"),
            ("douban_director", "TEXT"), ("douban_cast", "TEXT"),
            ("douban_synopsis", "TEXT"), ("douban_poster", "TEXT"),
            ("weighted_score", "REAL DEFAULT -1"), ("original_title", "TEXT"),
            ("category", "TEXT"), ("critics_consensus", "TEXT"),
            ("writers", "TEXT"), ("douban_writers", "TEXT"),
        ]
        for col_name, col_type in migrations:
            if col_name not in existing:
                try:
                    self.conn.execute(f"ALTER TABLE movies ADD COLUMN {col_name} {col_type}")
                except sqlite3.OperationalError:
                    pass
        self.conn.commit()

    @staticmethod
    def make_slug(title, year):
        """片名 + 年份构成跨抓取稳定的唯一键，不依赖 RT 是否匹配成功。"""
        return f"{(title or '').strip().lower()}-{year or 0}"

    @staticmethod
    def truncate_field(value, max_len):
        if value and len(str(value)) > max_len:
            return str(value)[:max_len]
        return value

    # 三套评分量纲不同，混用同一个 normalizer 会把 88.7 分截成 88
    _SCORE_SCALES = {
        "tomatometer": "percent",
        "audience_score": "percent",
        "weighted_score": "percent",
        "douban_score": "out_of_ten",
    }

    @classmethod
    def _normalize_score(cls, field, value, default=-1):
        """统一评分：百分制保留一位小数并夹在 0–100，豆瓣夹在 0–10。"""
        if value is None or value == "":
            return default
        raw = str(value).replace("%", "").strip()
        try:
            num = float(raw)
        except (ValueError, TypeError):
            return default
        if cls._SCORE_SCALES[field] == "out_of_ten":
            return round(max(0.0, min(num, 10.0)), 1)
        return round(max(0.0, min(num, 100.0)), 1)

    def _insert_movie_no_commit(self, movie_data: dict) -> bool:
        for field in self._SCORE_SCALES:
            movie_data[field] = self._normalize_score(field, movie_data.get(field))

        try:
            movie_data["year"] = int(str(movie_data.get("year")).strip() or 0) or None
        except (ValueError, TypeError):
            movie_data["year"] = None

        raw_votes = str(movie_data.get("douban_vote_count") or "0").replace(",", "")
        try:
            movie_data["douban_vote_count"] = int(raw_votes)
        except ValueError:
            movie_data["douban_vote_count"] = 0

        movie_data["director"] = self.truncate_field(movie_data.get("director"), MAX_DIRECTOR_LENGTH)
        movie_data["cast"] = self.truncate_field(movie_data.get("cast"), MAX_CAST_LENGTH)
        movie_data["synopsis"] = self.truncate_field(movie_data.get("synopsis"), MAX_SYNOPSIS_LENGTH)

        movie_data["slug"] = self.make_slug(
            movie_data.get("original_title") or movie_data.get("title"),
            movie_data.get("year"),
        )

        columns = [
            "slug", "rt_url", "title", "original_title", "year", "rating",
            "tomatometer", "audience_score", "genre", "director", "writers", "cast",
            "critics_consensus", "synopsis", "release_date", "runtime",
            "poster_url", "douban_id", "douban_url",
            "douban_score", "douban_vote_count", "douban_title", "douban_genre",
            "douban_director", "douban_writers", "douban_cast", "douban_synopsis", "douban_poster",
            "weighted_score", "category", "updated_at"
        ]
        values = []
        for col in columns:
            v = movie_data.get(col)
            if col == "updated_at":
                v = datetime.now().isoformat()
            values.append(v)

        update_clause = ", ".join(f"{col}=EXCLUDED.{col}" for col in columns if col != "slug")
        sql = f"""
            INSERT INTO movies ({','.join(columns)})
            VALUES ({','.join(['?'] * len(columns))})
            ON CONFLICT(slug) DO UPDATE SET {update_clause}
        """
        self.conn.execute(sql, values)
        return True

    def insert_movie(self, movie_data: dict) -> bool:
        try:
            result = self._insert_movie_no_commit(movie_data)
            if result:
                self.conn.commit()
            return result
        except sqlite3.Error as e:
            logger.error(f"入库失败: {movie_data.get('title')} - {e}")
            return False

    def batch_insert_movies(self, movies_list: list) -> int:
        success = 0
        try:
            for movie_data in movies_list:
                try:
                    if self._insert_movie_no_commit(movie_data):
                        success += 1
                except Exception as e:
                    logger.error(f"批量插入失败: {movie_data.get('title')} - {e}")
            self.conn.commit()
        except Exception as e:
            logger.error(f"批量插入事务失败: {e}")
            try:
                self.conn.rollback()
            except Exception:
                pass
        return success

    def record_score_history(self, movie_id, scores):
        """记录一次抓取的评分快照。scores 为 movies 行中的四个评分字段。"""
        if not movie_id:
            return
        try:
            self.conn.execute("""
                INSERT INTO score_history
                    (movie_id, tomatometer, audience_score, douban_score, weighted_score)
                VALUES (?, ?, ?, ?, ?)
            """, (
                movie_id,
                scores["tomatometer"], scores["audience_score"],
                scores["douban_score"], scores["weighted_score"],
            ))
        except sqlite3.Error as e:
            logger.error(f"评分历史记录失败: {e}")

    def get_movie_by_slug(self, slug):
        return self.conn.execute("SELECT * FROM movies WHERE slug=?", (slug,)).fetchone()

    def get_all_movies(self):
        return self.conn.execute("SELECT * FROM movies ORDER BY weighted_score DESC").fetchall()

    def get_score_history(self, movie_id):
        return self.conn.execute("SELECT * FROM score_history WHERE movie_id=? ORDER BY recorded_at ASC", (movie_id,)).fetchall()

    def export_json(self):
        """导出站点数据。

        不含 score_history：每跑一次就给每部片追加一行，嵌进 JSON 会让部署产物无上限
        膨胀（实测已占 9.1%，按每周两次一年约 1.8 MB），而前端从不渲染它。
        历史仍完整保存在 movies.db 与 movies.csv 里。
        """
        import json
        return json.dumps([dict(m) for m in self.get_all_movies()],
                          ensure_ascii=False, indent=2)

    def export_csv(self):
        import csv
        import io
        movies = self.get_all_movies()
        output = io.StringIO()
        if movies:
            writer = csv.DictWriter(output, fieldnames=movies[0].keys())
            writer.writeheader()
            for m in movies:
                writer.writerow(dict(m))
        return output.getvalue()

    def get_statistics(self):
        stats = {}
        try:
            stats["total_movies"] = self.conn.execute("SELECT COUNT(*) FROM movies").fetchone()[0]
            stats["avg_weighted"] = self.conn.execute("SELECT AVG(weighted_score) FROM movies WHERE weighted_score > 0").fetchone()[0] or 0
            stats["matched_douban"] = self.conn.execute("SELECT COUNT(*) FROM movies WHERE douban_id IS NOT NULL AND douban_id != ''").fetchone()[0]
            stats["history_records"] = self.conn.execute("SELECT COUNT(*) FROM score_history").fetchone()[0]
        except Exception as e:
            logger.error(f"统计查询失败: {e}")
        return stats

    def close(self):
        if self.conn:
            self.conn.close()
