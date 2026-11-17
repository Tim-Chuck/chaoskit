import argparse
import os
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn

from .app import Settings, create_app
from .boundary import LOOPBACK_HOSTS, parsed_origin
from .demo import FIXTURES
from .rules import Rule
from .transport import FaultProtocol


def sampling_times(raw):
    try:
        times = tuple(int(item) for item in raw.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected comma-separated milliseconds") from exc
    if not 1 <= len(times) <= 8 or tuple(sorted(set(times))) != times or times[0] < 100 or times[-1] > 30000:
        raise argparse.ArgumentTypeError("Use 1–8 increasing offsets from 100 to 30000 ms")
    return times


def relative_page(value):
    parsed = urlsplit(value)
    if not value.startswith("/") or value.startswith("//") or parsed.netloc or "\\" in value:
        raise argparse.ArgumentTypeError("Expected a local path starting with /")
    return value


def parse_args():
    root = argparse.ArgumentParser(prog="python -m chaoskit", description="Local API fault injection and browser checks")
    commands = root.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Start the proxy and workbench")
    serve.add_argument("--host", choices=("127.0.0.1", "::1", "0.0.0.0"), default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--upstream", default=os.getenv("CHAOSKIT_UPSTREAM", "http://127.0.0.1:9001"))
    serve.add_argument("--db", default=os.getenv("CHAOSKIT_DB", "chaoskit.sqlite3"))
    serve.add_argument("--allow-host", action="append", default=[])
    serve.add_argument("--allow-unsafe-methods", action="store_true")
    demo = commands.add_parser("demo", help="Start the upstream with four sample pages")
    demo.add_argument("--host", choices=("127.0.0.1", "0.0.0.0"), default="127.0.0.1")
    demo.add_argument("--port", type=int, default=9001)
    check = commands.add_parser("check", help="Run Playwright checks and export an HTML and JSON report")
    check.add_argument("--origin", default="http://127.0.0.1:8000")
    pages = check.add_mutually_exclusive_group()
    pages.add_argument("--fixture", choices=(*FIXTURES, "all"), default="all")
    pages.add_argument("--page", type=relative_page)
    check.add_argument("--api-path", default="/api/orders/1", type=relative_page)
    check.add_argument("--fault", choices=("status", "delay", "timeout", "abort"), default="status")
    check.add_argument("--value", type=int)
    check.add_argument("--samples", type=sampling_times, default=(2000, 5000, 10000))
    check.add_argument("--output", type=Path, default=Path("reports"))
    check.add_argument("--browser-channel", choices=("chrome", "msedge"),
                       default=os.getenv("CHAOSKIT_BROWSER_CHANNEL"))
    return root, root.parse_args()


def run_suite(args):
    from playwright.sync_api import sync_playwright

    from .check import run_check
    from .sampler import Scenario

    origin = args.origin.rstrip("/")
    if parsed_origin(origin).hostname not in LOOPBACK_HOSTS:
        raise ValueError("Checks only connect to a loopback ChaosKit proxy")
    defaults = {"status": 500, "delay": 5000, "timeout": 3200, "abort": 0}
    rule = Rule(id=f"check-{args.fault}", path=args.api_path, fault=args.fault,
                value=defaults[args.fault] if args.value is None else args.value)
    if "*" in rule.path:
        raise ValueError("--api-path must be exact")
    fixtures = FIXTURES if args.fixture == "all" else (args.fixture,)
    pages = {"local": args.page} if args.page else {name: f"/demo/{name}" for name in fixtures}
    expected = {"complete": 100, "no-retry": 80, "spinner": 50, "blank": 0}
    exit_code = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=args.browser_channel)
        for name, page in pages.items():
            directory = args.output / name
            scenario = Scenario(origin, page, args.api_path, args.samples, directory)
            result = run_check(browser, scenario, rule)
            print(f"{name}: {result['score'] if result['valid'] else result['reason']} | {directory / 'report.html'}")
            target = expected[name] if not args.page and args.fixture == "all" else 100
            if not result["valid"] or result["score"] != target:
                exit_code = 1
        browser.close()
    return exit_code


def main():
    parser, args = parse_args()
    if args.command == "serve":
        settings = Settings(args.upstream, args.db, tuple(args.allow_host), args.allow_unsafe_methods)
        uvicorn.run(create_app(settings), host=args.host, port=args.port, http=FaultProtocol,
                    access_log=False, proxy_headers=False, ws="none")
    elif args.command == "demo":
        uvicorn.run("chaoskit.demo:app", host=args.host, port=args.port, http="h11",
                    access_log=False, proxy_headers=False, ws="none")
    else:
        try:
            return run_suite(args)
        except ValueError as exc:
            parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
