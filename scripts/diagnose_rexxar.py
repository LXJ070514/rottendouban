"""Rexxar 配额探测：在单个 Runner（单个出口 IP）上按固定节流连发请求。

已知事实（CI 实测）：`m.douban.com/rexxar/api/v2/movie/<id>` 超额后返回
HTTP 400，响应体 `{"msg":"subject_ip_rate_limit"}` —— 按 IP 配额，与请求头无关
（桌面/移动 UA、有无 cookie 预热、Referer 粒度，四种变体表现一致）。

本脚本由 matrix 以不同 REXXAR_DELAY 各跑一个独立 Runner，用来区分配额是
"每 IP 总量"还是"每 IP 时间窗"：
  放慢后成功数显著上升 → 时间窗，调大节流即可；
  放慢后仍卡在相近数量 → 总量，只能靠分片（每片一个 IP）扩容。
"""
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

DELAY = float(os.environ.get("REXXAR_DELAY", "0.5"))
SAMPLES = int(os.environ.get("REXXAR_SAMPLES", "20"))

# 用不同的 subject id，避免命中豆瓣自己的结果缓存而看不出配额
SUBJECT_IDS = [
    "1292052", "1291546", "1296141", "1292722", "1291561", "1295644", "1292064",
    "1292001", "1291841", "1291858", "1292000", "1291543", "1292720", "1291549",
    "1291571", "1292719", "1291832", "1292262", "1291843", "1291853", "1291875",
    "1292213", "1292270", "1292328", "1292337", "1292365", "1292371", "1292401",
    "1292402", "1292481", "1292656", "1292679", "1292792", "1293172", "1293181",
]

CTX = ssl.create_default_context()
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def fetch_ip():
    try:
        with urllib.request.urlopen("https://api.ipify.org?format=json",
                                    timeout=10, context=CTX) as r:
            return json.loads(r.read().decode()).get("ip")
    except Exception:
        return "unknown"


def attempt(subject_id):
    url = f"https://m.douban.com/rexxar/api/v2/movie/{subject_id}"
    headers = {
        "User-Agent": UA,
        "Accept": "application/json",
        "Referer": f"https://m.douban.com/movie/subject/{subject_id}/",
    }
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers=headers), timeout=15, context=CTX) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        return r.status, bool(data.get("title")), ""
    except urllib.error.HTTPError as e:
        body = e.read()[:150].decode("utf-8", "replace").replace("\n", " ")
        reason = ""
        try:
            reason = json.loads(body).get("msg", "")
        except json.JSONDecodeError:
            reason = body[:60]
        return e.code, False, reason
    except Exception as e:
        return None, False, f"{type(e).__name__}: {e}"


def main():
    ip = fetch_ip()
    print(f"出口 IP: {ip} | 节流 {DELAY}s | 计划 {SAMPLES} 次\n")

    ok = 0
    first_fail = None
    reasons = {}
    started = time.time()

    for i in range(SAMPLES):
        sid = SUBJECT_IDS[i % len(SUBJECT_IDS)]
        status, good, reason = attempt(sid)
        if status == 200 and good:
            ok += 1
            print(f"  [{i+1:>2}/{SAMPLES}] ✅ {sid}")
        else:
            reasons[reason or str(status)] = reasons.get(reason or str(status), 0) + 1
            if first_fail is None:
                first_fail = (i + 1, status, reason)
            print(f"  [{i+1:>2}/{SAMPLES}] ❌ {sid} HTTP {status} {reason}")
        time.sleep(DELAY)

    elapsed = time.time() - started
    print(f"\n=== 结论 ===")
    print(f"  出口 IP     : {ip}")
    print(f"  节流        : {DELAY}s")
    print(f"  成功        : {ok}/{SAMPLES}")
    print(f"  耗时        : {elapsed:.0f}s")
    if first_fail:
        print(f"  首次失败    : 第 {first_fail[0]} 次，HTTP {first_fail[1]}，{first_fail[2]}")
    if reasons:
        print(f"  失败原因分布: {reasons}")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"## Rexxar 配额探测（节流 {DELAY}s）\n\n")
            f.write(f"- 出口 IP: `{ip}`\n- 成功: **{ok}/{SAMPLES}**\n"
                    f"- 耗时: {elapsed:.0f}s\n")
            if first_fail:
                f.write(f"- 首次失败: 第 {first_fail[0]} 次，HTTP {first_fail[1]}，"
                        f"`{first_fail[2]}`\n")
            if reasons:
                f.write(f"- 失败原因: {reasons}\n")

    # 全部失败时让 job 变红，便于在列表里一眼看出哪个档位不可用
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
