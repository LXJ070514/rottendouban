"""从数据库重新生成 site/data/ 下的静态数据。

爬虫本身已把结果写入 site/data/（crawler.main）；本脚本用于在不动上游 API 的前提下
单独重出站点，例如手动修完 douban_cache.json 之后。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawler.config import SITE_DIR, ensure_dirs
from crawler.database import Database
from crawler.site_generator import generate_site_data


def main():
    ensure_dirs()
    db = Database()
    try:
        total = db.get_statistics().get("total_movies", 0)
        if not total:
            print(f"数据库为空，跳过生成（不覆盖 {SITE_DIR}）")
            return 1
        generate_site_data(db, SITE_DIR)
        print(f"已生成 {total} 部电影的数据 → {SITE_DIR}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
