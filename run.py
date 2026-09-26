"""一键运行数据抓取：python run.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from crawler.main import main

if __name__ == "__main__":
    sys.exit(main())
