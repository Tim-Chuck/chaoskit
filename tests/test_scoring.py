import json
import os

import pytest
from playwright.sync_api import expect, sync_playwright

from chaoskit.app import Settings, create_app
from chaoskit.check import run_check
from chaoskit.demo import FIXTURES
from chaoskit.rules import Rule
from chaoskit.sampler import Scenario
from chaoskit.transport import FaultProtocol
from conftest import live_server

API = "/api/orders/1"


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(channel=os.getenv("CHAOSKIT_BROWSER_CHANNEL"))
        yield instance
        instance.close()


def execute(browser, proxy, directory, fixture, fault="status", value=500, capture=(250, 650)):
    scenario = Scenario(proxy, f"/demo/{fixture}", API, capture, directory)
    return run_check(browser, scenario, Rule(id="browser-test", path=API, fault=fault, value=value))


def test_fixture_scores_are_ordered_and_repeatable(browser, proxy, tmp_path):
    for repeat in range(3):
        scores = []
        for fixture in FIXTURES:
            directory = tmp_path / str(repeat) / fixture
            report = execute(browser, proxy, directory, fixture)
            assert report["valid"], report["reason"]
            assert report["recovery"] == (fixture == "complete")
            assert json.loads((directory / "report.json").read_text(encoding="utf-8"))["valid"]
            html = (directory / "report.html").read_text(encoding="utf-8")
            assert 'lang="zh-CN"' in html and "分项结果" in html
            scores.append(report["score"])
        assert scores == [100, 80, 50, 0]


@pytest.mark.parametrize(("fault", "value", "samples"), [
    ("abort", 0, (250, 650)),
    ("delay", 1100, (250, 1450)),
    ("timeout", 3000, (250, 2550)),
])
def test_other_faults_confirmed_by_browser_and_proxy(browser, proxy, tmp_path, fault, value, samples):
    report = execute(browser, proxy, tmp_path / fault, "complete", fault, value, samples)
    assert report["valid"], report["reason"]
    assert report["score"] == 100
    assert fault in {event["outcome"] for event in report["proxy"]}
    if fault in {"timeout", "abort"}:
        assert any(event["phase"] == "fault" and event["event"] == "failed" for event in report["network"])
    assert report["samples"][0]["state"] == ("error" if fault == "abort" else "loading")


def test_baseline_failure_is_unscored(browser, proxy, tmp_path):
    report = execute(browser, proxy, tmp_path / "invalid", "missing")
    assert not report["valid"]
    assert report["score"] is None
    assert "基线检查失败" in report["reason"]
    assert "本次未评分" in (tmp_path / "invalid" / "report.html").read_text(encoding="utf-8")


def test_unavailable_upstream_is_unscored(browser, tmp_path):
    with live_server(create_app(Settings("http://127.0.0.1:1", str(tmp_path / "offline.sqlite3"))),
                     FaultProtocol) as offline:
        report = execute(browser, offline, tmp_path / "offline", "complete")
        assert not report["valid"]
        assert report["score"] is None
        assert "基线检查失败" in report["reason"]
        assert any(event["event"] == "response" and event["value"] == 502 for event in report["network"])
        assert any(event["outcome"] == "upstream_error" for event in report["proxy"])


def test_workbench_rule_lifecycle(browser, proxy):
    page = browser.new_page()
    try:
        page.goto(proxy)
        page.get_by_role("button", name="保存规则").click()
        expect(page.get_by_role("alert")).to_contain_text("规则编号须为")
        expect(page.get_by_label("规则编号")).to_be_focused()
        page.get_by_label("规则编号").fill("test-rule")
        page.get_by_label("路径匹配表达式").fill("/api/orders/**bad")
        page.get_by_label("故障类型", exact=True).select_option("status")
        page.get_by_role("button", name="保存规则").click()
        expect(page.get_by_role("alert")).to_contain_text("** 必须独占一段")
        page.get_by_label("路径匹配表达式").fill(API)
        page.get_by_role("button", name="保存规则").click()
        expect(page.get_by_role("button", name="停用")).to_be_visible()
        assert page.request.get(proxy + API).status == 500
        search = page.get_by_role("searchbox")
        search.fill("missing rule")
        expect(page.get_by_text("没有找到匹配的规则。")).to_be_visible()
        search.fill("TEST-RULE GET")
        page.get_by_role("button", name="规则", exact=True).click()
        expect(page.get_by_role("heading", name="最近请求")).to_be_hidden()
        expect(page.get_by_role("heading", name="test-rule")).to_be_visible()
        page.reload()
        expect(search).to_have_value("TEST-RULE GET")
        expect(page.get_by_role("button", name="规则", exact=True)).to_have_attribute("aria-pressed", "true")
        page.get_by_role("button", name="清空搜索").click()
        expect(search).to_be_focused()
        page.get_by_role("button", name="全部", exact=True).click()
        search.fill("500 test-rule")
        page.get_by_role("button", name="请求日志", exact=True).click()
        expect(page.get_by_role("heading", name="已保存规则")).to_be_hidden()
        expect(page.get_by_role("row").filter(has_text="1 条规则匹配")).to_be_visible()
        page.get_by_role("button", name="清空搜索").click()
        page.get_by_role("button", name="全部", exact=True).click()
        page.get_by_role("button", name="编辑").click()
        page.get_by_label("HTTP 状态码").fill("503")
        page.get_by_role("button", name="保存规则").click()
        expect(page.get_by_text("503", exact=False).first).to_be_visible()
        assert page.request.get(proxy + API).status == 503
        page.get_by_role("button", name="停用").click()
        expect(page.get_by_role("button", name="启用", exact=True)).to_be_visible()
        assert page.request.get(proxy + API).status == 200
        page.get_by_role("button", name="删除").click()
        expect(page.get_by_text("暂无规则", exact=False)).to_be_visible()
        expect(page.get_by_text("1 条规则匹配", exact=False).first).to_be_visible()
    finally:
        page.close()
