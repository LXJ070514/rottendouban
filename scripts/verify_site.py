"""CI 环境下验证网站数据的脚本"""
import json

try:
    with open('site/data/movies.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    print(f'Movies: {len(data)}')
    douban_matched = [m for m in data if m.get('douban_score', -1) > 0]
    print(f'Douban matched: {len(douban_matched)}')
    for m in data[:3]:
        title = m.get('title', '?')
        tomato = m.get('tomatometer', '-')
        audience = m.get('audience_score', '-')
        douban = m.get('douban_score', '-')
        weighted = m.get('weighted_score', '-')
        print(f'  {title}: tomato={tomato} audience={audience} douban={douban} weighted={weighted}')
except Exception as e:
    print(f'Error: {e}')
