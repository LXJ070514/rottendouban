"""
CI辅助脚本 - 验证网站数据
- 检查 movies.json 是否存在且有效
- 显示电影数量、豆瓣匹配数
- 显示前3部电影的评分概览
用法: python scripts/verify_site.py
"""
import json
import os

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    with open('site/data/movies.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    print(f'Movies: {len(data)}')
    douban_matched = [m for m in data if m.get('douban_score', -1) > 0]
    print(f'Douban matched: {len(douban_matched)}')
    for m in data[:3]:
        print(f"  {m.get('title','?')}: "
              f"tomato={m.get('tomatometer','-')} "
              f"audience={m.get('audience_score','-')} "
              f"douban={m.get('douban_score','-')} "
              f"weighted={m.get('weighted_score','-')}")
except Exception as e:
    print(f'Error: {e}')
