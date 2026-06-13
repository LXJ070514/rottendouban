"""CI 环境下检查数据库电影数量的脚本"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawler.database import Database

db = Database()
c = db.conn.execute('SELECT COUNT(*) FROM movies')
count = c.fetchone()[0]
print(count)
db.close()
