"""在 Runner 的真实出口 IP 上探测各数据源可用性。

豆瓣对不同 IP 段策略不同，且限流时返回 HTTP 200 但内容是空壳，
所以每个探测都要判定"结构是否真的解析出来了"，不能只看状态码。
"""
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawler.config import build_ssl_context
from crawler.rotten_tomatoes import ALGOLIA_HEADERS, ALGOLIA_URL

CTX = build_ssl_context()
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
SHAWSHANK_ID = "1292052"

results = []


def fetch(url, headers=None, timeout=15):
    merged = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "zh-CN,zh;q=0.9"}
    merged.update(headers or {})
    req = urllib.request.Request(url, headers=merged)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        # 错误体必须读出来：豆瓣的两种拒绝都靠 msg 区分
        # （400 subject_ip_rate_limit = 配额；403 need_permission = 条目级权限），
        # 丢掉 body 就只剩一个状态码，无法判断"等一等能不能好"。
        try:
            body = e.read()[:300].decode("utf-8", "replace")
        except OSError:
            body = ""
        return e.code, body
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def record(name, status, body, verdict, detail=""):
    results.append({
        "name": name, "status": status, "bytes": len(body or ""),
        "verdict": verdict, "detail": detail,
    })
    mark = {"OK": "✅", "EMPTY": "⚠️", "FAIL": "❌"}.get(verdict, "•")
    print(f"{mark} {name:26} HTTP {str(status):5} {len(body or ''):>7}B  {verdict:6} {detail}")


# ---------- 1. RT Algolia（基线，已知可用）----------
status, body = None, ""
try:
    payload = json.dumps({"query": "The Shawshank Redemption", "hitsPerPage": 3,
                          "filters": "type:movie", "page": 0}).encode()
    req = urllib.request.Request(ALGOLIA_URL, data=payload, headers=ALGOLIA_HEADERS)
    with urllib.request.urlopen(req, timeout=15, context=CTX) as resp:
        status, body = resp.status, resp.read().decode("utf-8", "replace")
    hits = json.loads(body).get("hits", [])
    record("RT Algolia 搜索", status, body,
           "OK" if hits else "EMPTY", f"{len(hits)} hits")
except Exception as e:
    record("RT Algolia 搜索", status, body, "FAIL", str(e)[:80])

# ---------- 2. 豆瓣搜索页（爬虫已弃用，留作对照）----------
status, body = fetch(
    "https://search.douban.com/movie/subject_search?search_text="
    + urllib.parse.quote("肖申克的救赎"),
    headers={"Referer": "https://www.douban.com/"})
if status == 200 and "window.__DATA__" in body:
    blob = body[body.find("window.__DATA__"):]
    has_items = '"items"' in blob and re.search(r'"items"\s*:\s*\[\s*\{', blob) is not None
    record("豆瓣搜索页 __DATA__", status, body, "OK" if has_items else "EMPTY",
           "有 items" if has_items else "__DATA__ 存在但 items 为空（限流形态）")
elif status == 200:
    record("豆瓣搜索页 __DATA__", status, body, "EMPTY", "页面无 window.__DATA__")
else:
    record("豆瓣搜索页 __DATA__", status, body, "FAIL", body[:80])

# ---------- 3. Top250 HTML（爬虫已弃用，留作对照：榜单走 j/chart/top_list）----------
status, body = fetch("https://movie.douban.com/top250?start=0&filter=")
if status == 200:
    titles = re.findall(r'<span class="title">([^<]+)</span>', body)
    ids = re.findall(r'/subject/(\d+)/', body)
    blocked = "sec.douban.com" in body or "检测到有异常请求" in body
    record("Top250 HTML", status, body,
           "FAIL" if blocked else ("OK" if len(titles) >= 20 else "EMPTY"),
           f"{len(titles)} 个 title / {len(set(ids))} 个 subject id"
           + ("（疑似验证页）" if blocked else ""))
else:
    record("Top250 HTML", status, body, "FAIL", body[:80])

# ---------- 4. j/chart/top_list JSON（榜单接口）----------
status, body = fetch(
    "https://movie.douban.com/j/chart/top_list"
    "?type=11&interval_id=100%3A90&action=&start=0&limit=20",
    headers={"Referer": "https://movie.douban.com/explore"})
if status == 200:
    try:
        data = json.loads(body)
        first = data[0] if data else {}
        record("j/chart/top_list", status, body, "OK" if data else "EMPTY",
               f"{len(data)} 条，首条 {first.get('title')} score={first.get('score')}")
    except json.JSONDecodeError as e:
        record("j/chart/top_list", status, body, "EMPTY", f"非 JSON: {e}")
else:
    record("j/chart/top_list", status, body, "FAIL", body[:80])

# ---------- 5. Rexxar 电影详情（中文简介/导演/演员）----------
ref = f"https://m.douban.com/movie/subject/{SHAWSHANK_ID}/"
status, body = fetch(f"https://m.douban.com/rexxar/api/v2/movie/{SHAWSHANK_ID}",
                     headers={"Referer": ref, "Accept": "application/json"})
if status == 200:
    try:
        d = json.loads(body)
        intro = d.get("intro") or ""
        directors = [x.get("name") for x in (d.get("directors") or [])]
        actors = [x.get("name") for x in (d.get("actors") or [])]
        ok = bool(intro) and bool(directors) and bool(actors)
        record("Rexxar 电影详情", status, body, "OK" if ok else "EMPTY",
               f"intro {len(intro)} 字 / 导演 {len(directors)} / 演员 {len(actors)}")
    except json.JSONDecodeError as e:
        record("Rexxar 电影详情", status, body, "EMPTY", f"非 JSON: {e}")
else:
    record("Rexxar 电影详情", status, body, "FAIL", body[:80])

# ---------- 5b. Rexxar 条目级权限拒绝（need_permission）----------
# 与配额限流是两回事：配额等一等就好，条目级权限永远拿不到。
# 用一个已知被拒的 subject 探测，确认该信号仍能被识别（爬虫据此跳过重试）。
# 判据不是"返回 403"而是"响应体里是 need_permission" —— 若哪天豆瓣改成别的形式，
# 这里会立刻显现，避免爬虫把它误当配额去退避。
DENIED_ID = "1307528"   # 《盲井》，实测稳定 403 need_permission
status, body = fetch(f"https://m.douban.com/rexxar/api/v2/movie/{DENIED_ID}",
                     headers={"Referer": f"https://m.douban.com/movie/subject/{DENIED_ID}/",
                              "Accept": "application/json"})
if "need_permission" in (body or ""):
    record("Rexxar 条目级拒绝", status, body, "OK",
           "need_permission 已正确识别（爬虫会跳过、不重试）")
elif status == 200:
    record("Rexxar 条目级拒绝", status, body, "OK",
           "该条目现在可访问了 —— 豆瓣放开了权限，可重新纳入抓取")
elif "subject_ip_rate_limit" in (body or ""):
    record("Rexxar 条目级拒绝", status, body, "EMPTY",
           "撞上配额，本次探测无效（换时机再试）")
else:
    record("Rexxar 条目级拒绝", status, body, "FAIL",
           f"预期 need_permission，实际: {(body or '')[:70]}")

# ---------- 6. Rexxar 热门短评 ----------
status, body = fetch(
    f"https://m.douban.com/rexxar/api/v2/movie/{SHAWSHANK_ID}/interests"
    "?count=5&order_by=hot&start=0",
    headers={"Referer": ref, "Accept": "application/json"})
if status == 200:
    try:
        d = json.loads(body)
        items = d.get("interests") or []
        with_comment = [i for i in items if (i.get("comment") or "").strip()]
        record("Rexxar 热门短评", status, body, "OK" if with_comment else "EMPTY",
               f"total={d.get('total')} 本页 {len(items)} 条，有正文 {len(with_comment)}")
    except json.JSONDecodeError as e:
        record("Rexxar 热门短评", status, body, "EMPTY", f"非 JSON: {e}")
else:
    record("Rexxar 热门短评", status, body, "FAIL", body[:80])

# ---------- 7. TMDB 中文片名反查（language 是否影响命中质量）----------
# 反查本身不依赖 language：run 36267829360 里用 en-US 搜中文片名也拿到了
# 153/250 的结果（TMDB 索引了译名，落在 results[0]）。这一项要回答的是
# 更细的问题：换 zh-CN 能否让命中从"兜底取首条"抬到"标题精确匹配"，
# 从而少走后面那道纯靠年份的模糊判断。改 search_movie 的检索语言前先跑一次。
TMDB_KEY = os.environ.get("TMDB_API_KEY", "")
TMDB_TOKEN = os.environ.get("TMDB_BEARER_TOKEN", "")
if not TMDB_KEY and not TMDB_TOKEN:
    record("TMDB 中文反查", None, "", "FAIL", "未配置 TMDB_API_KEY / TMDB_BEARER_TOKEN")
else:
    # 两条凭据路径都要探，且要标明哪条是生产实际走的。
    # crawl-deploy.yml 只传 TMDB_API_KEY，所以爬虫走 api_key 分支；
    # 而 tmdb_api 优先用 bearer_token —— 若 bearer 无效而 api_key 有效，
    # 只探 bearer 会误报"TMDB 不可用"（本轮就踩了：bearer 401、api_key 其实是好的）。
    def _tmdb_auth(mode):
        if mode == "bearer" and TMDB_TOKEN:
            return {"Authorization": f"Bearer {TMDB_TOKEN}"}, {}
        if mode == "api_key" and TMDB_KEY:
            return {}, {"api_key": TMDB_KEY}
        return None, None

    for mode in ("bearer", "api_key"):
        headers, query = _tmdb_auth(mode)
        if headers is None:
            record(f"TMDB 凭据 {mode}", None, "", "FAIL",
                   f"未提供 {mode}（生产走的是 api_key 分支）" if mode == "api_key"
                   else "未提供 bearer")
            continue
        probe_headers = dict(headers, **{"Content-Type": "application/json"})
        params = dict(query, query="控方证人", language="zh-CN",
                      primary_release_year="1957", include_adult="false")
        status, body = fetch(
            "https://api.themoviedb.org/3/search/movie?" + urllib.parse.urlencode(params),
            headers=probe_headers)
        prod = "  ← 生产实际使用" if mode == "api_key" else ""
        if status == 200:
            try:
                res = json.loads(body).get("results") or []
            except json.JSONDecodeError as e:
                record(f"TMDB 凭据 {mode}", status, body, "EMPTY", f"非 JSON: {e}{prod}")
                continue
            first = res[0] if res else {}
            if mode == "api_key" and first.get("id"):
                found_id = first["id"]
            record(f"TMDB 凭据 {mode}", status, body, "OK" if res else "EMPTY",
                   f"{len(res)} 条，首条 title={first.get('title')!r}{prod}")
        elif status == 401:
            record(f"TMDB 凭据 {mode}", status, body, "FAIL",
                   f"密钥无效（{'但生产走这条，必须修' if mode == 'api_key' else '该 secret 可删或更新'}）")
        else:
            record(f"TMDB 凭据 {mode}", status, body, "FAIL", f"{(body or '')[:70]}{prod}")

    # 详情必须回英文 title —— main.py 靠它拿英文片名去匹配 RT
    if found_id:
        headers, query = _tmdb_auth("api_key") if TMDB_KEY else _tmdb_auth("bearer")
        status, body = fetch(
            f"https://api.themoviedb.org/3/movie/{found_id}?"
            + urllib.parse.urlencode(dict(query or {}, language="en-US")),
            headers=dict(headers or {}, **{"Content-Type": "application/json"}))
        if status == 200:
            try:
                d = json.loads(body)
                record("TMDB 详情 lang=en-US", status, body, "OK" if d.get("title") else "EMPTY",
                       f"title={d.get('title')!r} original={d.get('original_title')!r}")
            except json.JSONDecodeError as e:
                record("TMDB 详情 lang=en-US", status, body, "EMPTY", f"非 JSON: {e}")
        else:
            record("TMDB 详情 lang=en-US", status, body, "FAIL", body[:80])
    else:
        record("TMDB 详情 lang=en-US", None, "", "EMPTY", "中文搜索无结果，无从取 id")

# ---------- 出口 IP（判断是否数据中心段）----------
status, body = fetch("https://api.ipify.org?format=json")
if status == 200:
    try:
        print(f"\nRunner 出口 IP: {json.loads(body).get('ip')}")
    except json.JSONDecodeError:
        pass

# ---------- 汇总 ----------
summary = os.environ.get("GITHUB_STEP_SUMMARY")
if summary:
    with open(summary, "a", encoding="utf-8") as f:
        f.write("## 数据源连通性（Runner 出口）\n\n")
        f.write("| 数据源 | HTTP | 字节 | 结论 | 细节 |\n|---|---:|---:|---|---|\n")
        for r in results:
            f.write(f"| {r['name']} | {r['status']} | {r['bytes']} | "
                    f"{r['verdict']} | {r['detail']} |\n")
        ok = sum(1 for r in results if r["verdict"] == "OK")
        f.write(f"\n可用 {ok}/{len(results)}。\n")

failed = [r["name"] for r in results if r["verdict"] != "OK"]
print(f"\n可用 {len(results) - len(failed)}/{len(results)}"
      + (f"；不可用: {', '.join(failed)}" if failed else "；全部可用"))
