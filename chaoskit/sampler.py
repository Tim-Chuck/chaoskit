import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Error, expect

from .proxy import scrub_path
from .signals import MARKERS, sample


@dataclass
class Scenario:
    origin: str
    page: str
    api_path: str
    capture_ms: tuple[int, ...]
    directory: Path


class Probe:
    def __init__(self, browser, scenario, key):
        self.scenario = scenario
        self.origin = urlsplit(scenario.origin)
        self.key = key
        self.context = browser.new_context(
            viewport={"width": 960, "height": 600}, device_scale_factor=1, service_workers="block",
        )
        self.context.route("**/*", self.route_local)
        self.page = self.context.new_page()
        self.page.set_default_timeout(3500)
        self.events = []
        self.errors = []
        self.console = []
        self.requests = {}
        self.started = None
        self.phase = "baseline"
        self.page.on("request", self.on_request)
        self.page.on("response", lambda response: self.network("response", response.request, response.status))
        self.page.on("requestfailed", lambda request: self.network("failed", request, request.failure))
        self.page.on("console", lambda message: self.add(self.console, {
            "phase": self.phase, "type": message.type,
        }))
        self.page.on("pageerror", lambda error: self.add(self.errors, {
            "phase": self.phase, "type": error.name,
        }))

    @staticmethod
    def add(events, event):
        if len(events) < 250:
            events.append(event)

    def route_local(self, route):
        url = urlsplit(route.request.url)
        if (url.scheme, url.hostname, url.port) != (self.origin.scheme, self.origin.hostname, self.origin.port):
            return route.abort()
        if route.request.method != "GET":
            return route.abort()
        return route.continue_()

    def target_request(self, request):
        return request.method == "GET" and urlsplit(request.url).path == self.scenario.api_path

    def on_request(self, request):
        if self.target_request(request) and self.started is None:
            self.started = time.monotonic()
        if self.target_request(request) or request.is_navigation_request():
            self.requests[request] = self.phase, self.started
        self.network("request", request, None)

    def network(self, event, request, value):
        if request in self.requests:
            phase, started = self.requests[request]
            self.add(self.events, {
                "phase": phase, "event": event, "path": scrub_path(urlsplit(request.url).path),
                "value": value, "ms": round((time.monotonic() - started) * 1000) if started else None,
            })

    def select_phase(self, phase):
        self.phase = phase
        self.started = None
        self.page.set_extra_http_headers({"X-ChaosKit-Run": self.key, "X-ChaosKit-Phase": phase})

    def open(self, phase):
        self.select_phase(phase)
        with self.page.expect_request(self.target_request):
            response = self.page.goto(self.scenario.origin + self.scenario.page, wait_until="domcontentloaded")
            if response is None or not response.ok:
                raise ValueError("页面未能加载，请检查页面路径和上游服务")
        if self.started is None:
            raise ValueError("未观测到目标 GET 请求，请检查接口路径")

    def baseline(self):
        self.open("baseline")
        expect(self.page.locator(MARKERS["success"]).first).to_be_visible(timeout=4000)
        self.page.screenshot(path=self.scenario.directory / "baseline.png", animations="disabled")

    def capture(self):
        self.open("fault")
        snapshots = []
        for offset in self.scenario.capture_ms:
            remaining = offset - (time.monotonic() - self.started) * 1000
            if remaining > 0:
                self.page.wait_for_timeout(remaining)
            snapshots.append(sample(self.page, self.scenario.directory / f"fault-{offset}.png", self.started, offset))
        return snapshots

    def recover(self):
        self.select_phase("recovery")
        try:
            with self.page.expect_response(lambda response: self.target_request(response.request) and response.ok):
                self.page.locator(MARKERS["retry"] + ":visible").first.click()
            expect(self.page.locator(MARKERS["success"]).first).to_be_visible(timeout=3000)
        except (Error, AssertionError):
            return False
        self.page.screenshot(path=self.scenario.directory / "recovery.png", animations="disabled")
        return True
