"""Rexxar 请求变体对照实验。

背景：CI 实测 `m.douban.com/rexxar/api/v2/movie/<id>` 前 26 次成功、之后一律
HTTP 400（不是 403/429，所以不是限流而是请求本身被判非法），而同一份代码在
住宅 IP 上 100% 成功。差异只能在请求头或会话上，所以逐个变体在 Runner 上实测。

每个变体连发 SAMPLES 次请求，统计成功数与首个失败的状态码 —— 只看一次成功
没有意义，故障是在若干次之后才出现的。
"""
import json
import ssl
import sys
import time
import urllib.error
import urllib.request

SAMPLES = int(sys.argv[1]) if len(sys.argv) > 1 else 30
SUBJECT_IDS = [
    "1292052", "1291546", "1296141", "1292722", "1291561", "1295644", "1292064",
    "1292001", "1291841", "1291858", "1292000", "1291543", "1292720", "1291549",
    "1291571", "1292719", "1291832", "1292262", "1291843", "1291853", "1291875",
    "1292213", "1292270", "1292328", "1292337", "1292365", "1292371", "1292401",
    "1292402", "1292481", "1292656", "1292679", "1292722", "1292792", "1293172",
]

CTX = ssl.create_default_context()
DESKTOP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")


def bootstrap_cookie_jar():
    """先访问手机站首页拿 cookie（Rexxar 常要求带 ck）。"""
    jar = urllib.request.HTTPCookieProcessor()
    opener = urllib.request.build_opener(jar)
    try:
        opener.open(urllib.request.Request(
            "https://m.douban.com/movie/", headers={"User-Agent": MOBILE_UA}), timeout=15)
        return opener
    except Exception as e:
        print(f"    cookie 预热失败: {type(e).__name__}: {e}")
        return opener


def attempt(opener, subject_id, headers):
    url = f"https://m.douban.com/rexxar/api/v2/movie/{subject_id}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with opener.open(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", "replace")
        data = json.loads(body)
        return resp.status, bool(data.get("title")), ""
    except urllib.error.HTTPError as e:
        return e.code, False, e.read()[:120].decode("utf-8", "replace").replace("\n", " ")
    except Exception as e:
        return None, False, f"{type(e).__name__}: {e}"


def run_variant(name, opener, headers):
    ok = 0
    first_fail = None
    for i in range(SAMPLES):
        sid = SUBJECT_IDS[i % len(SUBJECT_IDS)]
        status, good, detail = attempt(opener, sid, headers)
        if status == 200 and good:
            ok += 1
        elif first_fail is None:
            first_fail = (i + 1, status, detail)
        time.sleep(0.5)
    print(f"  {name:34} 成功 {ok:>2}/{SAMPLES}"
          + (f"  首次失败于第 {first_fail[0]} 次: HTTP {first_fail[1]} {first_fail[2][:70]}"
             if first_fail else "  全程无失败"))
    return ok


def main():
    print(f"Rexxar 变体对照，每变体连发 {SAMPLES} 次\n")
    results = {}

    plain = urllib.request.build_opener()
    results["A 桌面UA 无cookie（当前实现）"] = run_variant(
        "A 桌面UA 无cookie（当前实现）", plain,
        {"User-Agent": DESKTOP_UA, "Accept": "application/json",
         "Referer": "https://m.douban.com/movie/"})

    results["B 移动UA 无cookie"] = run_variant(
        "B 移动UA 无cookie", plain,
        {"User-Agent": MOBILE_UA, "Accept": "application/json",
         "Referer": "https://m.douban.com/movie/"})

    jar_opener = bootstrap_cookie_jar()
    results["C 移动UA + cookie预热"] = run_variant(
        "C 移动UA + cookie预热", jar_opener,
        {"User-Agent": MOBILE_UA, "Accept": "application/json",
         "Referer": "https://m.douban.com/movie/"})

    results["D 移动UA + subject级Referer"] = run_variant(
        "D 移动UA + subject级Referer", plain,
        {"User-Agent": MOBILE_UA, "Accept": "application/json",
         "Referer": f"https://m.douban.com/movie/subject/{SUBJECT_IDS[0]}/"})

    print("\n排名:")
    for name, ok in sorted(results.items(), key=lambda kv: -kv[1]):
        print(f"  {ok:>2}/{SAMPLES}  {name}")

    summary = __import__("os").environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"## Rexxar 变体对照（各 {SAMPLES} 次连续请求）\n\n")
            f.write("| 变体 | 成功 |\n|---|---:|\n")
            for name, ok in sorted(results.items(), key=lambda kv: -kv[1]):
                f.write(f"| {name} | {ok}/{SAMPLES} |\n")


if __name__ == "__main__":
    main()
