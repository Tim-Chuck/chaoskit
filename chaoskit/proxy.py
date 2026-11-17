import asyncio
import logging
import re
import sqlite3
from time import monotonic
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response, StreamingResponse

from .boundary import hop_headers
from .rules import match

SAFE = {"GET", "HEAD", "OPTIONS"}
CONTROL = (b"x-chaoskit-run", b"x-chaoskit-phase", b"x-chaoskit-outcome", b"x-chaoskit-rule")
logger = logging.getLogger(__name__)


class DisconnectedResponse(Response):
    def __init__(self, terminate=None):
        super().__init__()
        self.terminate = terminate

    async def __call__(self, scope, receive, send):
        if self.terminate is not None:
            self.terminate()


def failure(code, message):
    return JSONResponse({"detail": message}, status_code=code)


def scrub_path(path):
    return re.sub(r"(?i)(?:sk-|bearer|token|password)[^/]*|[\w.-]{32,}", "[redacted]", path)


class Exchange:
    def __init__(self, request):
        self.request = request
        self.state = request.app.state
        self.start = monotonic()
        self.rule = None
        self.run = None
        self.phase = request.headers.get("x-chaoskit-phase", "fault")
        self.outcome = "pass"
        self.reason = "No enabled rule matched"

    async def choose(self):
        key = self.request.headers.get("x-chaoskit-run")
        if key:
            self.run = self.state.runs.get(key)
            rules = [self.run.rule] if self.phase == "fault" else []
        else:
            rules = await run_in_threadpool(self.state.store.rules)
        self.rule, self.reason = match(rules, self.request.method, self.request.url.path)

    async def finish(self, response, status):
        rule = self.rule.id if self.rule else None
        ms = round((monotonic() - self.start) * 1000)
        path = scrub_path(self.request.url.path)
        if self.run:
            self.run.events.append(dict(phase=self.phase, path=path, outcome=self.outcome, rule=rule, status=status))
        try:
            await run_in_threadpool(
                self.state.store.log, self.request.method, path, status, ms, rule, self.outcome, self.reason,
            )
        except sqlite3.Error:
            logger.warning("Request log unavailable")
        if not isinstance(response, DisconnectedResponse):
            response.headers["X-ChaosKit-Outcome"] = self.outcome
            if rule:
                response.headers["X-ChaosKit-Rule"] = rule
        return response

    async def inject(self):
        kind, value = self.rule.fault, self.rule.value
        self.outcome = kind
        if kind == "delay":
            await asyncio.sleep(value / 1000)
            return None
        if kind == "status":
            return failure(value, "Injected HTTP error")
        if kind == "timeout":
            try:
                await asyncio.wait_for(wait_disconnect(self.request), value / 1000)
            except asyncio.TimeoutError:
                return failure(504, "Injected timeout window ended")
            return DisconnectedResponse()
        terminate = self.request.scope.get("extensions", {}).get("chaoskit.abort")
        if terminate is None:
            self.outcome = "tool_error"
            return failure(501, "Run through python -m chaoskit serve to inject connection aborts")
        return DisconnectedResponse(terminate)


async def wait_disconnect(request):
    while (await request.receive())["type"] != "http.disconnect":
        pass


def mapped_location(location, upstream):
    target = urlsplit(urljoin(upstream, location))
    base = urlsplit(upstream)
    if target.scheme != base.scheme or target.netloc != base.netloc or "\\" in location:
        raise ValueError("Redirect leaves the configured origin")
    path = target.path or "/"
    if path.startswith("//"):
        raise ValueError("Ambiguous redirect path")
    return target._replace(scheme="", netloc="", path=path).geturl()


async def forward(exchange):
    request, state = exchange.request, exchange.state
    base, host = state.target
    path = request.scope["raw_path"]
    query = request.scope["query_string"]
    url = base.copy_with(raw_path=path + (b"?" + query if query else b""))
    has_body = request.headers.get("content-length", "0") != "0" or "transfer-encoding" in request.headers
    headers = hop_headers(request.headers.raw, CONTROL + (b"host",))
    headers.append((b"host", host.encode("ascii")))
    upstream = httpx.Request(request.method, url, headers=headers, content=request.stream() if has_body else None)
    result = await state.client.send(upstream, stream=True)
    try:
        raw_headers = hop_headers(result.headers.raw, CONTROL)
        if 300 <= result.status_code < 400:
            original = urlsplit(str(url))._replace(netloc=host).geturl()
            raw_headers = [
                (key, mapped_location(value.decode("latin-1"), original).encode("latin-1"))
                if key == b"location" else (key, value)
                for key, value in raw_headers
            ]
        response = StreamingResponse(stream_upstream(result), status_code=result.status_code)
        response.raw_headers = raw_headers
        return response
    except BaseException:
        await result.aclose()
        raise


async def stream_upstream(result):
    try:
        async for chunk in result.aiter_raw():
            yield chunk
    finally:
        await result.aclose()


async def serve_proxy(request: Request):
    exchange = Exchange(request)
    if exchange.state.target is None:
        exchange.outcome = "blocked"
        exchange.reason = "Upstream host is outside the configured local boundary"
        return await exchange.finish(failure(403, exchange.reason), 403)
    if request.method not in SAFE and not exchange.state.settings.allow_unsafe:
        exchange.outcome = "blocked"
        exchange.reason = "Unsafe methods are disabled"
        return await exchange.finish(failure(405, exchange.reason), 405)
    try:
        await exchange.choose()
        if exchange.rule:
            injected = await exchange.inject()
            if injected is not None:
                status = None if isinstance(injected, DisconnectedResponse) else injected.status_code
                return await exchange.finish(injected, status)
    except HTTPException:
        raise
    except Exception:
        logger.warning("Fault engine failed, falling back to the configured upstream")
        exchange.outcome, exchange.reason = "fail_open", "Injection failed before forwarding"
    try:
        response = await forward(exchange)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        exchange.outcome, exchange.reason = "upstream_error", "Could not connect to upstream"
        response = failure(502, exchange.reason)
    except httpx.TimeoutException:
        exchange.outcome, exchange.reason = "upstream_error", "Upstream timed out"
        response = failure(504, exchange.reason)
    except httpx.RequestError:
        exchange.outcome, exchange.reason = "upstream_error", "Upstream unavailable"
        response = failure(502, exchange.reason)
    except ValueError:
        exchange.outcome, exchange.reason = "blocked", "Unsafe upstream redirect"
        response = failure(403, exchange.reason)
    return await exchange.finish(response, response.status_code)
