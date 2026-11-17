from dataclasses import asdict

import httpx
from playwright.sync_api import Error

from .proxy import scrub_path
from .report import write_report
from .sampler import Probe
from .scoring import score

INVALID = {"fail_open", "upstream_error", "blocked", "tool_error"}


def proxy_events(client, control_url):
    response = client.get(control_url)
    response.raise_for_status()
    return response.json()


def run_check(browser, scenario, rule):
    scenario.directory.mkdir(parents=True, exist_ok=True)
    base = scenario.origin + "/_chaoskit/runs"
    result = {
        "scenario": {name: value for name, value in asdict(scenario).items() if name != "directory"},
        "rule": rule.model_dump(), "score": None, "valid": False, "reason": None,
        "checks": [], "samples": [], "baseline": False, "recovery": False,
        "console": [], "errors": [], "network": [], "proxy": [],
    }
    with httpx.Client(trust_env=False, timeout=5) as client:
        try:
            response = client.post(base, json=rule.model_dump())
            response.raise_for_status()
            url = base + "/" + response.json()["key"]
        except httpx.HTTPError:
            result["reason"] = "无法启动验收。请确认本地 ChaosKit 代理正在运行。"
        else:
            probe = Probe(browser, scenario, url.rsplit("/", 1)[1])
            try:
                evaluate(client, url, probe, result, rule)
            except (AssertionError, Error, httpx.HTTPError, ValueError) as exc:
                stage = "故障验收失败" if result["baseline"] else "基线检查失败"
                if isinstance(exc, ValueError):
                    detail = str(exc)
                elif isinstance(exc, httpx.HTTPError):
                    detail = "无法与本地代理通信，请确认代理正在运行"
                elif isinstance(exc, AssertionError):
                    detail = "未观察到预期的页面状态，请检查页面标记"
                else:
                    detail = "浏览器未完成页面操作，请确认页面和目标接口可访问"
                result["reason"] = f"{stage}。{detail}。"
            finally:
                result.update(network=probe.events, errors=probe.errors, console=probe.console)
                probe.context.close()
                try:
                    result["proxy"] = proxy_events(client, url)
                    client.delete(url)
                except httpx.HTTPError:
                    pass
    write_report(scenario.directory, result)
    return result


def evaluate(client, url, probe, result, rule):
    api = scrub_path(probe.scenario.api_path)
    probe.baseline()
    events = [event for event in proxy_events(client, url) if event["path"] == api]
    if not any(event["phase"] == "baseline" and event["outcome"] == "pass" and
               event["status"] is not None and 200 <= event["status"] < 300 for event in events):
        raise ValueError("目标接口未返回正常响应，请检查上游服务")
    result["baseline"] = True
    result["samples"] = samples = probe.capture()
    if any(item["actual_ms"] - item["planned_ms"] > max(250, item["planned_ms"] // 10) for item in samples):
        raise ValueError("一次采样错过了时间窗口，可降低浏览器负载或增大采样间隔后重试")
    if samples[-1]["retry"]:
        result["recovery"] = probe.recover()
    events = proxy_events(client, url)
    if any(event["outcome"] in INVALID for event in events):
        raise ValueError("工具或上游服务在验收期间异常，请查看代理事件")
    if not any(event["path"] == api and event["phase"] == "fault" and event["rule"] == rule.id and event["outcome"] == rule.fault
               for event in events):
        raise ValueError("代理未确认故障已注入，请检查接口路径和采样窗口")
    errors = [item for item in probe.errors if item["phase"] != "baseline"]
    points, checks = score(samples, result["recovery"], errors, rule.fault)
    result.update(score=points, checks=checks, valid=True)
