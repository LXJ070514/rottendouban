"""
CI辅助脚本 - 检查豆瓣缓存条目数
用法: python scripts/check_cache.py
输出: 缓存条目数 (整数)
"""
import json
import os

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    with open('crawler/data/douban_cache.json', 'r', encoding='utf-8') as f:
        d = json.load(f)
    print(len(d))
except Exception:
    print(0)
