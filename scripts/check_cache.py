"""CI 环境下检查豆瓣缓存数量的脚本"""
import json

with open('crawler/data/douban_cache.json', 'r') as f:
    d = json.load(f)
print(len(d))
