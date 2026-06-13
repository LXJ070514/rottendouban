"""
CI辅助脚本 - 检查数据库中的电影数量
用法: python scripts/check_db.py
输出: 电影数量 (整数)
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawler.database import Database

db = Database()
c = db.conn.execute('SELECT COUNT(*) FROM movies')
count = c.fetchone()[0]
db.close()
print(count)
