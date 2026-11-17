import gzip

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from .app import templates

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
FIXTURES = ("complete", "no-retry", "spinner", "blank")


@app.get("/ping")
async def ping():
    return {"status": "ok"}


@app.get("/demo/{fixture}")
async def demo(request: Request, fixture: str):
    if fixture not in FIXTURES:
        raise HTTPException(404)
    return templates.TemplateResponse(request, "orders.html", {"fixture": fixture})


@app.get("/api/orders/1")
async def orders():
    return {"number": "CK-1024", "item": "Mechanical keyboard", "status": "Paid", "amount": "¥349.00"}


@app.get("/api/plain")
async def plain():
    return Response("ChaosKit sample", media_type="text/plain")


@app.get("/api/payload")
async def payload(compressed: bool = True):
    raw = bytes(range(256)) * 8192
    if not compressed:
        return Response(raw, media_type="application/octet-stream")
    return Response(gzip.compress(raw, mtime=0), headers={"Content-Encoding": "gzip"})


@app.get("/api/redirect")
async def redirect():
    return RedirectResponse("/api/orders/1", status_code=307)


@app.post("/api/echo")
async def echo(request: Request):
    return Response(await request.body(), media_type="application/octet-stream")
