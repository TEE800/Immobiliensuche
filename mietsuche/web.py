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
from .letter import PLACEHOLDERS, load_template, render_letter
from .models import Listing
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
p{{margin:8px 0 0}}.err{{color:#b42318}}</style></head><body><form method="post" action="/login">
<h2 style="margin:0">Mietsuche</h2>{body}{error}<button>{button}</button></form></body></html>"""

LOGIN_FIELDS = '<input type="password" name="passwort" placeholder="Passwort" autofocus required>'
SETUP_FIELDS = (
    "<p>Willkommen! Lege ein Passwort für die App fest (mindestens 8 Zeichen).</p>"
    '<input type="password" name="passwort" placeholder="Neues Passwort" minlength="8" autofocus required>'
    '<input type="password" name="wiederholung" placeholder="Passwort wiederholen" minlength="8" required>'
)


def login_page(setup: bool, error: str = "") -> str:
    return LOGIN_PAGE.format(
        body=SETUP_FIELDS if setup else LOGIN_FIELDS,
        error=f'<p class="err">{error}</p>' if error else "",
        button="Passwort festlegen" if setup else "Anmelden",
    )


class Passwords:
    """Passwort aus config.yaml oder, falls leer, beim ersten Aufruf festgelegt."""

    def __init__(self, configured: str, directory: Path, secret: bytes):
        self.configured = configured
        self.path = directory / "app_passwort"
        self.secret = secret

    @property
    def is_set(self) -> bool:
        return bool(self.configured) or self.path.exists()

    def set(self, password: str) -> None:
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
        self.path.write_text(f"{salt.hex()}:{digest.hex()}")

    def check(self, password: str) -> bool:
        if self.configured:
            return hmac.compare_digest(password.encode(), self.configured.encode())
        if not self.path.exists():
            return False
        salt, digest = self.path.read_text().strip().split(":")
        test = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
        return hmac.compare_digest(test.hex(), digest)

    def token(self) -> str:
        # Hängt am Passwort: nach einer Änderung sind alte Anmeldungen ungültig.
        basis = self.configured if self.configured else self.path.read_text()
        return hmac.new(self.secret, basis.encode(), hashlib.sha256).hexdigest()


def _secret(directory: Path) -> bytes:
    path = directory / "app_secret"
    if not path.exists():
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32))
    return path.read_text().strip().encode()


def row_to_dict(row, full: bool = False, template: str | None = None) -> dict:
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
        "commute": json.loads(row["data"]).get("commute", []),
        "rent_index": json.loads(row["data"]).get("rent_index", {}),
    }
    if full:
        listing = json.loads(row["data"])
        # Mit der aktuellen Vorlage neu füllen, damit Änderungen sofort gelten.
        data["letter"] = render_letter(template, Listing(**listing)) if template else (row["letter"] or "")
        data["description"] = listing.get("description", "")
        data["contact_name"] = row["contact_name"]
        data["available_from"] = listing.get("available_from")
    return data


class TemplateUpdate(BaseModel):
    text: str


class StatusUpdate(BaseModel):
    status: str | None = None
    notes: str | None = None


def create_app(config: Config) -> FastAPI:
    data_dir = config.database.parent
    passwords = Passwords(config.web_password, data_dir, _secret(data_dir))
    _, public_key = ensure_vapid_key(data_dir)
    storage = Storage(config.database)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)
        if passwords.is_set and hmac.compare_digest(request.cookies.get(COOKIE, ""), passwords.token()):
            return await call_next(request)
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Nicht angemeldet"}, status_code=401)
        return RedirectResponse("/login", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    def show_login():
        return login_page(setup=not passwords.is_set)

    @app.post("/login")
    def login(passwort: str = Form(...), wiederholung: str | None = Form(None)):
        if not passwords.is_set:
            if len(passwort) < 8:
                return HTMLResponse(login_page(True, "Mindestens 8 Zeichen"), status_code=400)
            if passwort != wiederholung:
                return HTMLResponse(login_page(True, "Passwörter stimmen nicht überein"), status_code=400)
            passwords.set(passwort)
        elif not passwords.check(passwort):
            return HTMLResponse(login_page(False, "Falsches Passwort"), status_code=401)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(COOKIE, passwords.token(), max_age=365 * 24 * 3600,
                            httponly=True, samesite="lax")
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
        return row_to_dict(row, full=True, template=load_template(config.letter_file))

    @app.post("/api/listings/{key}")
    def update_listing(key: str, update: StatusUpdate):
        row = storage.get(key)
        if row is None:
            raise HTTPException(404, "Anzeige nicht gefunden")
        try:
            storage.set_status(key, update.status or row["status"], update.notes)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return row_to_dict(storage.get(key), full=True, template=load_template(config.letter_file))

    @app.get("/api/vorlage")
    def get_template():
        return {"text": load_template(config.letter_file) or "", "placeholders": PLACEHOLDERS}

    @app.put("/api/vorlage")
    def put_template(update: TemplateUpdate):
        try:
            update.text.format_map({k: "" for k in PLACEHOLDERS} | {"__x": ""})
        except (KeyError, ValueError, IndexError) as e:
            raise HTTPException(
                400, f"Geschweifte Klammern sind nur für Platzhalter wie {{anrede}} erlaubt ({e})."
            )
        config.letter_file.parent.mkdir(parents=True, exist_ok=True)
        config.letter_file.write_text(update.text.strip() + "\n", encoding="utf-8")
        return {"ok": True}

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
