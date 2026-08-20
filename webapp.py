"""Local CS2 prop dashboard. Run: python webapp.py"""
from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

import board
import config
import ingest
import store

WEB_DIR = Path(__file__).resolve().parent / "web"
REFRESH_MINUTES = 15


def _safe_ingest() -> None:
    try:
        ingest.run_ingest()
    except Exception:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init_db()
    scheduler = None
    try:
        from apscheduler.schedulers.background import BackgroundScheduler

        scheduler = BackgroundScheduler()
        scheduler.add_job(
            _safe_ingest,
            "interval",
            minutes=REFRESH_MINUTES,
            id="ingest",
            max_instances=1,
            coalesce=True,
        )
        scheduler.start()
    except Exception:
        scheduler = None
    threading.Thread(target=_safe_ingest, daemon=True).start()
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title="CS2 Prop Scanner",
    description="PrizePicks vs Underdog CS2 player props, with Polymarket series odds.",
    lifespan=lifespan,
)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/")
def index():
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("{{SITE_URL}}", config.PUBLIC_SITE_URL))


@app.get("/favicon.svg")
def favicon():
    return FileResponse(WEB_DIR / "favicon.svg", media_type="image/svg+xml")


@app.get("/robots.txt")
def robots():
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        f"Sitemap: {config.PUBLIC_SITE_URL}/sitemap.xml\n"
    )
    return Response(body, media_type="text/plain")


@app.get("/sitemap.xml")
def sitemap():
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"  <url><loc>{config.PUBLIC_SITE_URL}/</loc>"
        "<changefreq>hourly</changefreq><priority>1.0</priority></url>\n"
        "</urlset>\n"
    )
    return Response(body, media_type="application/xml")


@app.get("/api/status")
def api_status():
    return ingest.status()


@app.get("/api/board")
def api_board(
    date: str | None = Query(default=None),
    threshold: float = Query(default=0.5),
):
    return board.build_dashboard(date=date, threshold=threshold)


@app.post("/api/refresh")
def api_refresh():
    return ingest.run_ingest()


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


if __name__ == "__main__":
    import os
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("webapp:app", host=host, port=port, reload=False)
