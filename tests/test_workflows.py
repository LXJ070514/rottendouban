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


def test_data_commit_runs_even_when_the_crawl_fails():
    """豆瓣按 IP 的时间窗配额撑不住一轮抓满 250×2 次请求，
    覆盖率靠"每轮补一点、下轮从缓存续抓"爬升。缓存若只在成功时提交，
    抓取一失败这轮就白跑 —— 实测过一次 75 分钟被强杀、缓存颗粒无收。"""
    doc = WORKFLOWS["crawl-deploy.yml"]
    step = _fetch_step(doc, "Commit data")
    assert step.get("if") == "always()"
    assert "douban_cache.json" in step["run"]
    # 排在 Verify 之后：Verify 失败时这一步靠 steps.after.outcome 决定丢掉 movies.json
    names = [s.get("name") for s in doc["jobs"]["fetch"]["steps"]]
    assert names.index("Commit data") > names.index("Verify site data")


def test_all_pushes_happen_in_one_commit_step():
    """提交必须一次做完：分两个步骤的话，前一步提交完缓存后工作树里仍留着
    被跟踪但未提交的 site/data/movies.json（爬虫每轮都重写它），
    后一步的 git rebase 会以 "You have unstaged changes" 直接失败。
    run 36267829360 正是死在这里 —— 78 条详情抓完，缓存一条没推上去。
    """
    doc = WORKFLOWS["crawl-deploy.yml"]
    pushers = [s.get("name") for s in doc["jobs"]["fetch"]["steps"]
               if "git push" in (s.get("run") or "")]
    assert pushers == ["Commit data"], f"推送分散在多个步骤里: {pushers}"


def test_commit_step_rebases_onto_the_remote_branch_first():
    """裸 git push 在并发推送下会非快进失败，整轮成果丢失。

    checkout@v4 是 detached HEAD，git pull --rebase 用不了，只能 fetch + rebase。
    """
    run = _fetch_step(WORKFLOWS["crawl-deploy.yml"], "Commit data")["run"]
    assert "git fetch origin" in run
    assert "git rebase" in run
    assert "git push origin" in run
    assert "\n          git push\n" not in run, "仍有裸 push"


def test_failed_verification_discards_the_new_site_data():
    """校验没过却把 movies.json 提交上去，等于把坏数据变成下一轮的基线，
    deploy-site.yml 触发时还会把它部署上线。"""
    step = _fetch_step(WORKFLOWS["crawl-deploy.yml"], "Commit data")
    # 表达式走 env 传入（直插 shell 是注入面），脚本里只认 $VERIFIED
    assert step["env"]["VERIFIED"] == "${{ steps.after.outcome }}"
    run = step["run"]
    assert '"$VERIFIED" != "success"' in run
    # 被判为坏数据时要把它还原成仓库里的版本，且不再 git add 它
    assert "git checkout -- site/data/movies.json" in run
    discard_at = run.index("git checkout -- site/data/movies.json")
    add_at = run.index("git add -f site/data/movies.json", run.index("else"))
    assert discard_at < add_at, "丢弃分支必须排在 add 之前"


def test_deploy_checks_out_the_branch_not_the_trigger_sha():
    """fetch job 刚推了新提交，而 github.sha 仍是触发时的旧 SHA。
    用默认检出会把上一轮的 movies.json 部署上去。"""
    doc = WORKFLOWS["crawl-deploy.yml"]
    checkout = doc["jobs"]["deploy"]["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"]["ref"] == "${{ github.ref_name }}"


def test_pages_deployers_share_one_concurrency_group():
    """两条流水线都会部署 Pages，分组若各自独立就会互相覆盖。

    后果是抓取途中的一次前端提交把上一轮的 movies.json 重新部署上去，
    线上数据静默回退，直到半个月后的下一次定时抓取才被纠正。
    """
    groups = {n: WORKFLOWS[n].get("concurrency", {}).get("group")
              for n in ("crawl-deploy.yml", "deploy-site.yml")}
    assert groups["crawl-deploy.yml"] == groups["deploy-site.yml"], groups
    assert "${{ github.workflow }}" not in groups["crawl-deploy.yml"], (
        "组名里带 workflow 名就等于两条流水线各排各的队")
    for name, group in groups.items():
        assert WORKFLOWS[name]["concurrency"]["cancel-in-progress"] is False, (
            f"{name} 不该取消进行中的部署")
    assert group is not None


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
