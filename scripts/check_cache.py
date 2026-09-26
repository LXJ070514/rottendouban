"""查看豆瓣缓存概况：条目数、命中率、无评分条目。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawler.douban import DOUBAN_CACHE_PATH


def main():
    if not os.path.exists(DOUBAN_CACHE_PATH):
        print(f"缓存文件不存在: {DOUBAN_CACHE_PATH}")
        return
    with open(DOUBAN_CACHE_PATH, encoding="utf-8") as f:
        cache = json.load(f)

    with_score = [k for k, v in cache.items() if v.get("score")]
    print(f"缓存条目: {len(cache)}")
    print(f"有评分:   {len(with_score)}")
    print(f"无评分:   {len(cache) - len(with_score)}")
    for key in sorted(set(cache) - set(with_score))[:10]:
        print(f"  - {key}")


if __name__ == "__main__":
    main()
