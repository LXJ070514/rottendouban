"""查看本地 movies.db 概况。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawler.database import Database


def main():
    db = Database()
    try:
        for key, value in db.get_statistics().items():
            print(f"{key}: {value}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
