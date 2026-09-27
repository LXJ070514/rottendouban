"""校验 site/data/movies.json 是否可发布：量纲、必填字段、链接真实性。"""
import json
import sys

SCHEMA = {
    "tomatometer": (0, 100),
    "audience_score": (0, 100),
    "weighted_score": (0, 100),
    "douban_score": (0, 10),
}


# 覆盖率统计用的取值器：字段类型不对时返回 None 而不是抛异常。
# 直接写 (m.get("tomatometer") or 0) >= 0 会在遇到字符串 "90%" 时崩成
# TypeError 堆栈 —— 校验脚本的职责是**报告**问题，自己先崩掉就失去意义了。
def _num(value):
    return value if isinstance(value, (int, float)) else None


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
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                problems.append(f"#{mid} {title} 的 {field} 不是数字: {value!r}")
            elif not low <= value <= high:
                problems.append(f"#{mid} {title} 的 {field}={value} 越界 [{low},{high}]")
        rt_url = movie.get("rt_url") or ""
        if rt_url and "/unknown/" in rt_url:
            problems.append(f"#{mid} {title} 仍有占位 rt_url: {rt_url}")
        if not movie.get("douban_id"):
            problems.append(f"#{mid} {title} 缺 douban_id（榜单驱动下不该发生）")
        comments = movie.get("douban_comments")
        if comments is not None and not isinstance(comments, list):
            problems.append(f"#{mid} {title} 的 douban_comments 不是数组: {type(comments).__name__}")

    scored = [m for m in movies if (_num(m.get("weighted_score")) or 0) > 0]
    douban = [m for m in movies if (_num(m.get("douban_score")) or 0) > 0]
    rt = [m for m in movies if (_num(m.get("tomatometer")) or -1) >= 0]
    intro = [m for m in movies if m.get("douban_synopsis")]
    cast = [m for m in movies if m.get("douban_cast")]
    comments = [m for m in movies if m.get("douban_comments")]
    ranked = [m for m in movies if m.get("douban_rank")]

    print(f"电影 {len(movies)} | 有加权分 {len(scored)} | 有豆瓣 {len(douban)} | 有RT {len(rt)}")
    print(f"豆瓣详情: 简介 {len(intro)} | 演员 {len(cast)} | 短评 {len(comments)} | 名次 {len(ranked)}")
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
