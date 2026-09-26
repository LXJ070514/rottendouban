"""workflow 文件能被 YAML 解析，且关键约束没被人改动回来。"""
import glob
import os

import pytest

yaml = pytest.importorskip("yaml")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOWS = {
    os.path.basename(p): yaml.safe_load(open(p, encoding="utf-8"))
    for p in glob.glob(os.path.join(ROOT, ".github", "workflows", "*.yml"))
}


def test_workflows_found():
    assert WORKFLOWS, "未找到任何 workflow 文件"


@pytest.mark.parametrize("name", sorted(WORKFLOWS))
def test_workflow_parses(name):
    doc = WORKFLOWS[name]
    assert doc.get("jobs"), f"{name} 没有 jobs"
    for job_id, job in doc["jobs"].items():
        assert job.get("steps") or job.get("uses"), f"{name}:{job_id} 既无 steps 也无 uses"
        # 'on' 在 YAML 1.1 里会被解析成 True，两种键名都要认
        triggers = doc.get("on", doc.get(True))
        assert triggers, f"{name} 没有触发器"


def test_crawl_job_does_not_own_the_pages_environment():
    """environment 挂在抓取 job 上，爬虫一失败就会留下永久 queued 的 Pages deployment，
    把后续所有 run 堵在队列里 —— 这正是数据停更三个多月的成因。"""
    doc = WORKFLOWS["crawl-deploy.yml"]
    assert "environment" not in doc["jobs"]["fetch"]
    assert doc["jobs"]["deploy"]["environment"]["name"] == "github-pages"


def test_deploy_waits_for_fetch():
    doc = WORKFLOWS["crawl-deploy.yml"]
    assert doc["jobs"]["deploy"]["needs"] == "fetch"


def test_crawler_step_is_not_error_tolerant():
    """`|| true` + continue-on-error 会让失败的抓取看起来成功，然后部署空数据。"""
    doc = WORKFLOWS["crawl-deploy.yml"]
    fetch = doc["jobs"]["fetch"]["steps"]
    crawler = next(s for s in fetch if s.get("name") == "Fetch movie data")
    assert not crawler.get("continue-on-error")
    assert "||" not in crawler["run"]


@pytest.mark.parametrize("name", sorted(WORKFLOWS))
def test_no_expression_interpolated_into_shell(name):
    """${{ }} 直插 run 脚本是 Actions 的经典注入面，值一律经 env 传入。"""
    offenders = []
    for job_id, job in WORKFLOWS[name]["jobs"].items():
        for step in job.get("steps", []):
            run = step.get("run")
            if run and "${{" in run:
                offenders.append(f"{job_id}/{step.get('name')}")
    assert not offenders, f"{name} 中表达式直插 shell: {offenders}"


def _fetch_step(doc, name):
    return next(s for s in doc["jobs"]["fetch"]["steps"] if s.get("name") == name)


def test_douban_cache_is_committed_even_when_the_crawl_fails():
    """豆瓣按 IP 的时间窗配额撑不住一轮抓满 250×2 次请求，
    覆盖率靠"每轮补一点、下轮从缓存续抓"爬升。缓存若只在成功时提交，
    抓取一失败这轮就白跑 —— 实测过一次 75 分钟被强杀、缓存颗粒无收。"""
    doc = WORKFLOWS["crawl-deploy.yml"]
    cache_step = _fetch_step(doc, "Commit douban cache")
    assert cache_step.get("if") == "always()"
    assert "douban_cache.json" in cache_step["run"]
    # 必须排在 Verify 之前：Verify 失败会让后续步骤全部跳过
    names = [s.get("name") for s in doc["jobs"]["fetch"]["steps"]]
    assert names.index("Commit douban cache") < names.index("Verify site data")


def test_pushes_rebase_onto_the_remote_branch_first():
    """裸 git push 在并发推送下会非快进失败，整轮成果丢失。

    checkout@v4 是 detached HEAD，git pull --rebase 用不了，只能 fetch + rebase。
    """
    doc = WORKFLOWS["crawl-deploy.yml"]
    for name in ("Commit douban cache", "Commit refreshed data"):
        run = _fetch_step(doc, name)["run"]
        assert "git fetch origin" in run, f"{name} 未先 fetch"
        assert "git rebase" in run, f"{name} 未 rebase"
        assert "git push origin" in run, f"{name} 未显式推到分支"
        assert "\n          git push\n" not in run, f"{name} 仍有裸 push"


def test_deploy_checks_out_the_branch_not_the_trigger_sha():
    """fetch job 刚推了新提交，而 github.sha 仍是触发时的旧 SHA。
    用默认检出会把上一轮的 movies.json 部署上去。"""
    doc = WORKFLOWS["crawl-deploy.yml"]
    checkout = doc["jobs"]["deploy"]["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"]["ref"] == "${{ github.ref_name }}"


def test_fetch_timeout_leaves_headroom_over_the_douban_budget():
    """超时若只比预算大一点点，退避与 RT/TMDB 会把整轮顶穿。"""
    import re
    text = open(os.path.join(ROOT, ".github", "workflows", "crawl-deploy.yml"),
                encoding="utf-8").read()
    timeout = WORKFLOWS["crawl-deploy.yml"]["jobs"]["fetch"]["timeout-minutes"]
    budget = int(re.search(r"DOUBAN_TIME_BUDGET:\s*'?(\d+)'?", text).group(1))
    assert budget > 0, "workflow 未显式设定豆瓣时间预算"
    assert timeout * 60 >= budget + 600, (
        f"fetch 超时 {timeout}min 相对豆瓣预算 {budget}s 余量不足")
