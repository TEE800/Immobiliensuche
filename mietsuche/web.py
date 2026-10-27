"""Übersichts-App: zeigt Treffer, Status und Anschreiben, verschickt Push."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from .config import Config
from .storage import STATUSES, Storage
from .webpush import ensure_vapid_key

STATIC = Path(__file__).parent / "static"
COOKIE = "mietsuche"
PUBLIC_PATHS = {"/login", "/manifest.webmanifest", "/icon.svg", "/sw.js"}

LOGIN_PAGE = """<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mietsuche</title><link rel="manifest" href="/manifest.webmanifest">
<style>body{{font-family:system-ui,sans-serif;background:#f4f1ec;display:grid;place-items:center;
min-height:100vh;margin:0}}form{{background:#fff;padding:24px;border-radius:14px;width:min(320px,90vw);
box-shadow:0 2px 12px #0001}}input,button{{width:100%;box-sizing:border-box;font-size:16px;padding:12px;
margin-top:12px;border-radius:10px;border:1px solid #ccc}}button{{background:#1f5f4a;color:#fff;border:0}}
p{{color:#b42318;margin:8px 0 0}}</style></head><body><form method="post" action="/login">
<h2 style="margin:0">Mietsuche</h2><input type="password" name="passwort" placeholder="Passwort"
autofocus required>{error}<button>Anmelden</button></form></body></html>"""


def _secret(directory: Path) -> bytes:
    path = directory / "app_secret"
    if not path.exists():
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32))
    return path.read_text().strip().encode()


def row_to_dict(row, full: bool = False) -> dict:
    data = {
        "key": row["key"],
        "source": row["source"],
        "url": row["url"],
        "title": row["title"],
        "address": row["address"],
        "city": row["city"],
        "rooms": row["rooms"],
        "living_area": row["living_area"],
        "rent_cold": row["rent_cold"],
        "rent_warm": row["rent_warm"],
        "image_url": row["image_url"],
        "status": row["status"],
        "notes": row["notes"],
        "first_seen": row["first_seen"],
        "checks": json.loads(row["checks"]),
    }
    if full:
        listing = json.loads(row["data"])
        data["letter"] = row["letter"] or ""
        data["description"] = listing.get("description", "")
        data["contact_name"] = row["contact_name"]
        data["available_from"] = listing.get("available_from")
    return data


class StatusUpdate(BaseModel):
    status: str | None = None
    notes: str | None = None


def create_app(config: Config) -> FastAPI:
    if not config.web_password:
        raise SystemExit("Bitte in config.yaml unter app.passwort ein Passwort setzen.")
    data_dir = config.database.parent
    secret = _secret(data_dir)
    token = hmac.new(secret, config.web_password.encode(), hashlib.sha256).hexdigest()
    _, public_key = ensure_vapid_key(data_dir)
    storage = Storage(config.database)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)
        if hmac.compare_digest(request.cookies.get(COOKIE, ""), token):
            return await call_next(request)
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Nicht angemeldet"}, status_code=401)
        return RedirectResponse("/login", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    def login_page():
        return LOGIN_PAGE.format(error="")

    @app.post("/login")
    def login(passwort: str = Form(...)):
        if not hmac.compare_digest(passwort, config.web_password):
            return HTMLResponse(LOGIN_PAGE.format(error="<p>Falsches Passwort</p>"), status_code=401)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(COOKIE, token, max_age=365 * 24 * 3600, httponly=True, samesite="lax")
        return response

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/sw.js")
    def service_worker():
        return FileResponse(STATIC / "sw.js", media_type="text/javascript")

    @app.get("/manifest.webmanifest")
    def manifest():
        return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/icon.svg")
    def icon():
        return FileResponse(STATIC / "icon.svg", media_type="image/svg+xml")

    @app.get("/api/listings")
    def listings():
        return {"statuses": STATUSES, "listings": [row_to_dict(r) for r in storage.matches()]}

    @app.get("/api/listings/{key}")
    def listing(key: str):
        row = storage.get(key)
        if row is None:
            raise HTTPException(404, "Anzeige nicht gefunden")
        return row_to_dict(row, full=True)

    @app.post("/api/listings/{key}")
    def update_listing(key: str, update: StatusUpdate):
        row = storage.get(key)
        if row is None:
            raise HTTPException(404, "Anzeige nicht gefunden")
        try:
            storage.set_status(key, update.status or row["status"], update.notes)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return row_to_dict(storage.get(key), full=True)

    @app.get("/api/push/key")
    def push_key():
        return {"publicKey": public_key}

    @app.post("/api/push/subscribe")
    async def subscribe(request: Request):
        sub = await request.json()
        if not isinstance(sub, dict) or "endpoint" not in sub or "keys" not in sub:
            raise HTTPException(400, "Ungültige Anmeldung")
        storage.add_push_subscription(sub)
        return {"ok": True}

    return app
