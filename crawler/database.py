"""数据库管理模块 - SQLite 数据库操作、自动迁移、批量提交、评分历史"""
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
        """连接数据库"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        logger.info(f"数据库连接成功: {self.db_path}")

    def _init_tables(self):
        """初始化数据库表"""
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
        logger.info("数据库表初始化完成")

    def _migrate(self):
        """自动数据库迁移 - 添加缺失的列"""
        existing_columns = set()
        cursor = self.conn.execute("PRAGMA table_info(movies)")
        for row in cursor:
            existing_columns.add(row[1])

        migrations = [
            ("douban_id", "TEXT"),
            ("douban_url", "TEXT"),
            ("douban_score", "REAL DEFAULT -1"),
            ("douban_vote_count", "INTEGER DEFAULT 0"),
            ("douban_title", "TEXT"),
            ("douban_genre", "TEXT"),
            ("douban_director", "TEXT"),
            ("douban_cast", "TEXT"),
            ("douban_synopsis", "TEXT"),
            ("douban_poster", "TEXT"),
            ("weighted_score", "REAL DEFAULT -1"),
            ("original_title", "TEXT"),
            ("category", "TEXT"),
            ("critics_consensus", "TEXT"),
        ]

        for col_name, col_type in migrations:
            if col_name not in existing_columns:
                try:
                    self.conn.execute(f"ALTER TABLE movies ADD COLUMN {col_name} {col_type}")
                    logger.info(f"迁移: 添加列 {col_name}")
                except sqlite3.OperationalError as e:
                    logger.warning(f"迁移失败 {col_name}: {e}")

        self.conn.commit()
        logger.info("数据库迁移完成")

    def truncate_field(self, value, max_len):
        """自动截断超长字段"""
        if value and len(str(value)) > max_len:
            logger.warning(f"字段截断: {max_len} 字符")
            return str(value)[:max_len]
        return value

    def _normalize_score(self, value, default=-1):
        """标准化评分为数字: '98%' → 98, '8.5' → 8.5, '' → -1"""
        if value is None or value == '':
            return default
        raw = str(value).replace('%', '').strip()
        try:
            num = float(raw)
            # 判断尺度：≤10 是豆瓣0-10分制，>10 是百分比
            if num <= 10:
                return round(num, 1)  # 豆瓣评分保留1位小数
            else:
                return int(num)  # 番茄百分比取整
        except (ValueError, TypeError):
            return default

    def _insert_movie_no_commit(self, movie_data: dict) -> bool:
        """插入或更新单条电影记录（不提交事务，供批量操作使用）"""
        # 标准化评分字段为数字
        for score_field in ["tomatometer", "audience_score"]:
            movie_data[score_field] = self._normalize_score(movie_data.get(score_field), -1)
        movie_data["douban_score"] = self._normalize_score(movie_data.get("douban_score"), -1)
        movie_data["weighted_score"] = self._normalize_score(movie_data.get("weighted_score"), -1)
        # 年份标准化
        try:
            movie_data["year"] = int(movie_data.get("year", 0) or 0) if movie_data.get("year") else None
        except (ValueError, TypeError):
            movie_data["year"] = None
        # 豆瓣评分人数标准化
        try:
            movie_data["douban_vote_count"] = int(str(movie_data.get("douban_vote_count", "0")).replace(",", "")) if movie_data.get("douban_vote_count") else 0
        except (ValueError, TypeError):
            movie_data["douban_vote_count"] = 0

        # 截断超长字段
        movie_data["director"] = self.truncate_field(movie_data.get("director"), MAX_DIRECTOR_LENGTH)
        movie_data["cast"] = self.truncate_field(movie_data.get("cast"), MAX_CAST_LENGTH)
        movie_data["synopsis"] = self.truncate_field(movie_data.get("synopsis"), MAX_SYNOPSIS_LENGTH)
        movie_data["douban_director"] = self.truncate_field(movie_data.get("douban_director"), MAX_DIRECTOR_LENGTH)
        movie_data["douban_cast"] = self.truncate_field(movie_data.get("douban_cast"), MAX_CAST_LENGTH)
        movie_data["douban_synopsis"] = self.truncate_field(movie_data.get("douban_synopsis"), MAX_SYNOPSIS_LENGTH)

        columns = [
            "rt_url", "title", "original_title", "year", "rating",
            "tomatometer", "audience_score", "genre", "director", "cast",
            "critics_consensus", "synopsis", "release_date", "runtime",
            "poster_url", "poster_local", "douban_id", "douban_url",
            "douban_score", "douban_vote_count", "douban_title", "douban_genre",
            "douban_director", "douban_cast", "douban_synopsis", "douban_poster",
            "weighted_score", "category", "updated_at"
        ]

        values = []
        for col in columns:
            v = movie_data.get(col)
            if col == "updated_at":
                v = datetime.now().isoformat()
            values.append(v)

        update_clause = ", ".join(
            f"{col}=EXCLUDED.{col}" for col in columns if col != "rt_url"
        )

        sql = f"""
            INSERT INTO movies ({','.join(columns)})
            VALUES ({','.join(['?'] * len(columns))})
            ON CONFLICT(rt_url) DO UPDATE SET {update_clause}
        """
        self.conn.execute(sql, values)
        logger.info(f"入库成功: {movie_data.get('title')}")
        return True

    def insert_movie(self, movie_data: dict) -> bool:
        """插入或更新单条电影记录（自动提交）"""
        try:
            result = self._insert_movie_no_commit(movie_data)
            if result:
                self.conn.commit()
            return result
        except sqlite3.Error as e:
            logger.error(f"入库失败: {movie_data.get('title')} - {e}")
            self.log_error(movie_data.get("rt_url", ""), "db_insert", str(e))
            return False

    def batch_insert_movies(self, movies_list: list) -> int:
        """批量插入电影记录（使用事务，一条失败不影响其余）"""
        success_count = 0
        failed = []
        try:
            for movie_data in movies_list:
                try:
                    if self._insert_movie_no_commit(movie_data):
                        success_count += 1
                except Exception as e:
                    logger.error(f"批量插入单条失败: {movie_data.get('title')} - {e}")
                    self.log_error(movie_data.get("rt_url", ""), "batch_insert", str(e))
                    failed.append(movie_data.get('title'))
            self.conn.commit()
        except Exception as e:
            logger.error(f"批量插入事务失败: {e}")
            try:
                self.conn.rollback()
            except Exception:
                pass
        logger.info(f"批量入库完成: 成功 {success_count}/{len(movies_list)}")
        if failed:
            logger.warning(f"失败列表: {failed[:5]}...")
        return success_count

    def record_score_history(self, movie_id, tomatometer, audience_score, douban_score, weighted_score):
        """记录评分历史"""
        if not movie_id:
            return
        try:
            self.conn.execute("""
                INSERT INTO score_history (movie_id, tomatometer, audience_score, douban_score, weighted_score)
                VALUES (?, ?, ?, ?, ?)
            """, (movie_id, tomatometer, audience_score, douban_score, weighted_score))
            self.conn.commit()
            logger.debug(f"评分历史记录: movie_id={movie_id}")
        except sqlite3.Error as e:
            logger.error(f"评分历史记录失败: {e}")

    def log_error(self, movie_url, error_type, error_message, stack_trace=None):
        """记录错误日志到数据库"""
        try:
            self.conn.execute("""
                INSERT INTO error_log (movie_url, error_type, error_message, stack_trace)
                VALUES (?, ?, ?, ?)
            """, (movie_url, error_type, error_message, stack_trace))
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"错误日志入库失败: {e}")

    def get_movie_by_rt_url(self, rt_url):
        """根据烂番茄 URL 查询电影"""
        cursor = self.conn.execute("SELECT * FROM movies WHERE rt_url=?", (rt_url,))
        return cursor.fetchone()

    def get_movie_by_douban_id(self, douban_id):
        """根据豆瓣 ID 查询电影"""
        cursor = self.conn.execute("SELECT * FROM movies WHERE douban_id=?", (douban_id,))
        return cursor.fetchone()

    def get_all_movies(self):
        """获取所有电影"""
        cursor = self.conn.execute("SELECT * FROM movies ORDER BY weighted_score DESC")
        return cursor.fetchall()

    def get_score_history(self, movie_id):
        """获取电影评分历史"""
        cursor = self.conn.execute("""
            SELECT * FROM score_history
            WHERE movie_id=? ORDER BY recorded_at ASC
        """, (movie_id,))
        return cursor.fetchall()

    def search_movies(self, keyword):
        """搜索电影（标题模糊匹配）"""
        cursor = self.conn.execute("""
            SELECT * FROM movies
            WHERE title LIKE ? OR original_title LIKE ? OR douban_title LIKE ?
            ORDER BY weighted_score DESC
        """, (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"))
        return cursor.fetchall()

    def get_movies_by_genre(self, genre):
        """按类型筛选电影"""
        cursor = self.conn.execute("""
            SELECT * FROM movies WHERE genre LIKE ? ORDER BY weighted_score DESC
        """, (f"%{genre}%",))
        return cursor.fetchall()

    def _get_all_score_histories(self) -> Dict[int, List[Dict[str, Any]]]:
        """批量获取所有电影的评分历史，返回 {movie_id: [history_records]}"""
        cursor = self.conn.execute("""
            SELECT * FROM score_history ORDER BY movie_id, recorded_at ASC
        """)
        history_map = {}
        for row in cursor:
            movie_id = row['movie_id']
            if movie_id not in history_map:
                history_map[movie_id] = []
            history_map[movie_id].append(dict(row))
        return history_map

    def export_json(self):
        """导出所有数据为 JSON 格式"""
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
        """导出所有数据为 CSV 格式"""
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
        """获取统计信息"""
        stats = {}
        try:
            cursor = self.conn.execute("SELECT COUNT(*) FROM movies")
            stats["total_movies"] = cursor.fetchone()[0]
            cursor = self.conn.execute("SELECT AVG(weighted_score) FROM movies WHERE weighted_score > 0")
            stats["avg_weighted"] = cursor.fetchone()[0] or 0
            cursor = self.conn.execute("SELECT COUNT(*) FROM movies WHERE douban_id IS NOT NULL AND douban_id != ''")
            stats["matched_douban"] = cursor.fetchone()[0]
            cursor = self.conn.execute("SELECT COUNT(*) FROM score_history")
            stats["history_records"] = cursor.fetchone()[0]
        except Exception as e:
            logger.error(f"统计查询失败: {e}")
        return stats

    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
            logger.info("数据库连接已关闭")