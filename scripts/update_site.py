"""CI 环境下更新网站数据的脚本
从数据库读取数据，修复评分格式，生成网站静态文件
"""
import os
import sys
import json
import shutil

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawler.database import Database
from crawler.config import POSTERS_DIR, SITE_DIR
from crawler.main import generate_site_data


def normalize_score(value, default=-1):
    """标准化评分格式"""
    if value is None or value == '' or value == 'None':
        return default
    raw = str(value).replace('%', '').strip()
    try:
        num = float(raw)
        return int(num) if num > 10 else round(num, 1)
    except (ValueError, TypeError):
        return default


def fix_scores(db):
    """修复数据库中的评分格式"""
    cursor = db.conn.execute(
        'SELECT id, tomatometer, audience_score, douban_score, '
        'weighted_score, poster_local, douban_vote_count FROM movies'
    )
    for row in cursor:
        t = normalize_score(row['tomatometer'])
        a = normalize_score(row['audience_score'])
        d = normalize_score(row['douban_score'])
        w = normalize_score(row['weighted_score'])
        vc = 0
        try:
            vc = int(str(row['douban_vote_count'] or '0').replace(',', ''))
        except (ValueError, TypeError):
            vc = 0
        poster = row['poster_local']
        if poster and not poster.startswith('posters/'):
            poster = 'posters/' + poster
        db.conn.execute(
            'UPDATE movies SET tomatometer=?, audience_score=?, douban_score=?, '
            'weighted_score=?, poster_local=?, douban_vote_count=? WHERE id=?',
            (t, a, d, w, poster, vc, row['id'])
        )

    # 修复评分历史
    cursor = db.conn.execute(
        'SELECT id, tomatometer, audience_score, douban_score, weighted_score '
        'FROM score_history'
    )
    for row in cursor:
        t = normalize_score(row['tomatometer'])
        a = normalize_score(row['audience_score'])
        d = normalize_score(row['douban_score'])
        w = normalize_score(row['weighted_score'])
        db.conn.execute(
            'UPDATE score_history SET tomatometer=?, audience_score=?, '
            'douban_score=?, weighted_score=? WHERE id=?',
            (t, a, d, w, row['id'])
        )
    db.conn.commit()


def sync_posters():
    """同步海报到网站目录"""
    for src_dir in ['crawler/site/posters', 'crawler/data/posters']:
        if os.path.exists(src_dir):
            dst_dir = os.path.join('site', 'posters')
            os.makedirs(dst_dir, exist_ok=True)
            for f in os.listdir(src_dir):
                if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                    shutil.copy2(os.path.join(src_dir, f), os.path.join(dst_dir, f))


def main():
    """主流程"""
    db = Database()
    try:
        print("修复评分格式...")
        fix_scores(db)
        print("生成网站数据...")
        generate_site_data(db, 'site')
        print("同步海报...")
        sync_posters()
        print("Site data updated successfully")
    finally:
        db.close()


if __name__ == '__main__':
    main()
