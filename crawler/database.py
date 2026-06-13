"""数据库管理模块 — SQLite 操作、自动迁移、批量提交"""
import sqlite3
import logging
import os
from datetime import datetime
from typing import Optional, List, Dict, Any

from crawler.config import DB_PATH, DB_BATCH_SIZE, MAX_DIRECTOR_LENGTH, MAX_CAST_LENGTH, MAX_SYNOPSIS_LENGTH

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
                rt_url TEXT UNIQUE NOT NULL,
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
                poster_local TEXT,
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

            CREATE TABLE IF NOT EXISTS error_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                movie_url TEXT,
                error_type TEXT,
                error_message TEXT,
                stack_trace TEXT,
                occurred_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_movies_title ON movies(title);
            CREATE INDEX IF NOT EXISTS idx_movies_weighted ON movies(weighted_score);
            CREATE INDEX IF NOT EXISTS idx_movies_douban_id ON movies(douban_id);
            CREATE INDEX IF NOT EXISTS idx_history_movie ON score_history(movie_id);
            CREATE INDEX IF NOT EXISTS idx_history_date ON score_history(recorded_at);
        """)
        self.conn.commit()

    def _migrate(self):
        existing = set()
        for row in self.conn.execute("PRAGMA table_info(movies)"):
            existing.add(row[1])

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

    def truncate_field(self, value, max_len):
        if value and len(str(value)) > max_len:
            return str(value)[:max_len]
        return value

    def _normalize_score(self, value, default=-1):
        if value is None or value == '':
            return default
        raw = str(value).replace('%', '').strip()
        try:
            num = float(raw)
            return round(num, 1) if num <= 10 else int(num)
        except (ValueError, TypeError):
            return default

    def _insert_movie_no_commit(self, movie_data: dict) -> bool:
        for sf in ["tomatometer", "audience_score"]:
            movie_data[sf] = self._normalize_score(movie_data.get(sf), -1)
        movie_data["douban_score"] = self._normalize_score(movie_data.get("douban_score"), -1)
        movie_data["weighted_score"] = self._normalize_score(movie_data.get("weighted_score"), -1)
        try:
            movie_data["year"] = int(movie_data.get("year", 0) or 0) if movie_data.get("year") else None
        except (ValueError, TypeError):
            movie_data["year"] = None
        try:
            movie_data["douban_vote_count"] = int(str(movie_data.get("douban_vote_count", "0")).replace(",", "")) if movie_data.get("douban_vote_count") else 0
        except (ValueError, TypeError):
            movie_data["douban_vote_count"] = 0

        movie_data["director"] = self.truncate_field(movie_data.get("director"), MAX_DIRECTOR_LENGTH)
        movie_data["cast"] = self.truncate_field(movie_data.get("cast"), MAX_CAST_LENGTH)
        movie_data["synopsis"] = self.truncate_field(movie_data.get("synopsis"), MAX_SYNOPSIS_LENGTH)
        movie_data["douban_director"] = self.truncate_field(movie_data.get("douban_director"), MAX_DIRECTOR_LENGTH)
        movie_data["douban_cast"] = self.truncate_field(movie_data.get("douban_cast"), MAX_CAST_LENGTH)
        movie_data["douban_synopsis"] = self.truncate_field(movie_data.get("douban_synopsis"), MAX_SYNOPSIS_LENGTH)

        columns = [
            "rt_url", "title", "original_title", "year", "rating",
            "tomatometer", "audience_score", "genre", "director", "writers", "cast",
            "critics_consensus", "synopsis", "release_date", "runtime",
            "poster_url", "poster_local", "douban_id", "douban_url",
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

        update_clause = ", ".join(f"{col}=EXCLUDED.{col}" for col in columns if col != "rt_url")
        sql = f"""
            INSERT INTO movies ({','.join(columns)})
            VALUES ({','.join(['?'] * len(columns))})
            ON CONFLICT(rt_url) DO UPDATE SET {update_clause}
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

    def record_score_history(self, movie_id, tomatometer, audience_score, douban_score, weighted_score):
        if not movie_id:
            return
        try:
            self.conn.execute("""
                INSERT INTO score_history (movie_id, tomatometer, audience_score, douban_score, weighted_score)
                VALUES (?, ?, ?, ?, ?)
            """, (movie_id, tomatometer, audience_score, douban_score, weighted_score))
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"评分历史记录失败: {e}")

    def log_error(self, movie_url, error_type, error_message, stack_trace=None):
        try:
            self.conn.execute("""
                INSERT INTO error_log (movie_url, error_type, error_message, stack_trace)
                VALUES (?, ?, ?, ?)
            """, (movie_url, error_type, error_message, stack_trace))
            self.conn.commit()
        except sqlite3.Error:
            pass

    def get_movie_by_rt_url(self, rt_url):
        return self.conn.execute("SELECT * FROM movies WHERE rt_url=?", (rt_url,)).fetchone()

    def get_all_movies(self):
        return self.conn.execute("SELECT * FROM movies ORDER BY weighted_score DESC").fetchall()

    def get_score_history(self, movie_id):
        return self.conn.execute("SELECT * FROM score_history WHERE movie_id=? ORDER BY recorded_at ASC", (movie_id,)).fetchall()

    def _get_all_score_histories(self) -> Dict[int, List[Dict[str, Any]]]:
        history_map = {}
        for row in self.conn.execute("SELECT * FROM score_history ORDER BY movie_id, recorded_at ASC"):
            movie_id = row['movie_id']
            if movie_id not in history_map:
                history_map[movie_id] = []
            history_map[movie_id].append(dict(row))
        return history_map

    def export_json(self):
        import json
        movies = self.get_all_movies()
        history_map = self._get_all_score_histories()
        result = []
        for m in movies:
            movie_dict = dict(m)
            movie_dict["score_history"] = history_map.get(m["id"], [])
            result.append(movie_dict)
        return json.dumps(result, ensure_ascii=False, indent=2)

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
