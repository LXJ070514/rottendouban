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
