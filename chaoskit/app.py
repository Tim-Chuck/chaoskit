import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .boundary import incoming_local, pin_upstream
from .proxy import serve_proxy
from .rules import Rule
from .runs import Runs
from .store import Store

FILES = Path(__file__).parent
PREFIX = "/_chaoskit"
templates = Jinja2Templates(directory=FILES / "templates")


@dataclass
class Settings:
    upstream: str = "http://127.0.0.1:9001"
    database: str = "chaoskit.sqlite3"
    allow_hosts: tuple[str, ...] = ()
    allow_unsafe: bool = False


class LocalBoundary:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            request = Request(scope)
            host = request.headers.get("host", "")
            origin = request.headers.get("origin")
            if not incoming_local(host):
                response = JSONResponse({"detail": "Local Host required"}, status_code=403)
                return await response(scope, receive, send)
            if origin and origin != f"http://{host}" and request.method not in ("GET", "HEAD", "OPTIONS"):
                response = JSONResponse({"detail": "Cross-origin writes are disabled"}, status_code=403)
                return await response(scope, receive, send)
        return await self.app(scope, receive, send)


def create_app(settings):
    @asynccontextmanager
    async def lifespan(app):
        app.state.settings = settings
        app.state.store = Store(settings.database)
        app.state.runs = Runs()
        try:
            app.state.target = pin_upstream(settings.upstream, settings.allow_hosts)
        except (ValueError, OSError) as exc:
            logging.getLogger(__name__).warning("Configured upstream refused: %s", type(exc).__name__)
            app.state.target = None
        async with httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(10.0, connect=2.0),
                                     limits=httpx.Limits(max_connections=32)) as client:
            app.state.client = client
            yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(LocalBoundary)
    app.mount(PREFIX + "/static", StaticFiles(directory=FILES / "static"))

    @app.get("/ping")
    async def ping():
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        return templates.TemplateResponse(request, "dashboard.html", {
            "rules": app.state.store.rules(), "requests": app.state.store.recent(),
            "upstream_ready": app.state.target is not None,
        })

    @app.get(PREFIX + "/rules")
    def list_rules():
        return app.state.store.rules()

    @app.put(PREFIX + "/rules/{rule_id}")
    def save_rule(rule_id: str, rule: Rule):
        if rule_id != rule.id:
            raise HTTPException(400, "Rule ID does not match the path")
        app.state.store.save(rule)
        return rule

    @app.delete(PREFIX + "/rules/{rule_id}", status_code=204)
    def delete_rule(rule_id: str):
        app.state.store.delete(rule_id)

    @app.post(PREFIX + "/runs", status_code=201)
    async def create_run(rule: Rule):
        if rule.method != "GET":
            raise HTTPException(400, "Browser checks only support GET rules")
        return {"key": app.state.runs.create(rule)}

    @app.get(PREFIX + "/runs/{key}")
    async def run_events(key: str):
        return list(app.state.runs.get(key).events)

    @app.delete(PREFIX + "/runs/{key}", status_code=204)
    async def delete_run(key: str):
        app.state.runs.delete(key)

    @app.api_route("/{path:path}", methods=["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"])
    async def proxy(request: Request, path: str):
        if path == "_chaoskit" or path.startswith("_chaoskit/"):
            raise HTTPException(404)
        return await serve_proxy(request)

    return app
