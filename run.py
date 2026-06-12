"""
RottenDouban 一键启动脚本
双击此文件或运行 python run.py 即可启动爬虫
"""
import sys
import os

# 确保项目根目录在 Python 路径中
project_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_dir)

# 设置工作目录为项目根目录
os.chdir(project_dir)

from crawler.main import main

if __name__ == "__main__":
    main()