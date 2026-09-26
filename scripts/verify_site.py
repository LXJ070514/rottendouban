"""校验 site/data/movies.json 是否可发布：量纲、必填字段、链接真实性。"""
import json
import sys

SCHEMA = {
    "tomatometer": (0, 100),
    "audience_score": (0, 100),
    "weighted_score": (0, 100),
    "douban_score": (0, 10),
}


def main(path="site/data/movies.json"):
    try:
        with open(path, encoding="utf-8") as f:
            movies = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"无法读取 {path}: {e}")
        return 1

    if not isinstance(movies, list) or not movies:
        print(f"{path} 不是非空列表")
        return 1

    problems = []
    for movie in movies:
        mid = movie.get("id")
        title = (movie.get("title") or "?")[:30]
        if not (movie.get("title") or movie.get("douban_title")):
            problems.append(f"#{mid} 缺标题")
        for field, (low, high) in SCHEMA.items():
            value = movie.get(field)
            if value is None or value == -1 or value == "":
                continue
            if not isinstance(value, (int, float)):
                problems.append(f"#{mid} {title} 的 {field} 不是数字: {value!r}")
            elif not low <= value <= high:
                problems.append(f"#{mid} {title} 的 {field}={value} 越界 [{low},{high}]")
        rt_url = movie.get("rt_url") or ""
        if rt_url and "/unknown/" in rt_url:
            problems.append(f"#{mid} {title} 仍有占位 rt_url: {rt_url}")

    scored = [m for m in movies if (m.get("weighted_score") or 0) > 0]
    douban = [m for m in movies if (m.get("douban_score") or 0) > 0]
    rt = [m for m in movies if (m.get("tomatometer") or 0) >= 0]

    print(f"电影 {len(movies)} | 有加权分 {len(scored)} | 有豆瓣 {len(douban)} | 有RT {len(rt)}")
    for movie in movies[:3]:
        print(f"  {movie.get('title', '?')[:24]:26} "
              f"🍅{movie.get('tomatometer')} 🍿{movie.get('audience_score')} "
              f"豆{movie.get('douban_score')} 加权{movie.get('weighted_score')}")

    if problems:
        print(f"\n发现 {len(problems)} 个问题:")
        for p in problems[:20]:
            print("  -", p)
        return 1
    print("\n校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
