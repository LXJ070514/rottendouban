"""
RottenDouban 一键启动脚本
运行: python run.py
"""
import sys
import os

project_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_dir)
os.chdir(project_dir)

from crawler.main import main

if __name__ == "__main__":
    main()
