"""Push-Nachrichten direkt aus der Übersichts-App (Web Push)."""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid01
from pywebpush import WebPushException, webpush

from .storage import Storage

log = logging.getLogger("mietsuche")


def ensure_vapid_key(directory: Path) -> tuple[Path, str]:
    """Legt beim ersten Start ein Schlüsselpaar an.

    Gibt den Pfad zum privaten Schlüssel und den öffentlichen Schlüssel
    (base64url) zurück, den der Browser beim Abonnieren braucht.
    """
    path = directory / "vapid_private.pem"
    if path.exists():
        vapid = Vapid01.from_file(str(path))
    else:
        directory.mkdir(parents=True, exist_ok=True)
        vapid = Vapid01()
        vapid.generate_keys()
        vapid.save_key(str(path))
    raw = vapid.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return path, base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def push_payload(row) -> dict:
    facts = []
    if row["rooms"]:
        facts.append(f"{row['rooms']:g} Zi.")
    if row["rent_warm"]:
        facts.append(f"{row['rent_warm']:.0f} € warm")
    elif row["rent_cold"]:
        facts.append(f"{row['rent_cold']:.0f} € kalt")
    body = " · ".join(facts)
    if row["address"]:
        body = f"{row['address']}\n{body}" if body else row["address"]
    commute = json.loads(row["data"]).get("commute", [])
    if commute:
        body += "\n🚆 " + " · ".join(f"{c['name']} {c['minuten']} Min." for c in commute)
    return {
        "title": row["title"][:120],
        "body": body,
        "image": row["image_url"],
        "url": f"/#/anzeige/{row['key']}",
        "tag": row["key"],
    }


class WebPush:
    def __init__(self, storage: Storage, private_key: Path, contact: str):
        self.storage = storage
        self.private_key = str(private_key)
        self.claims = {"sub": f"mailto:{contact}" if contact else "mailto:mietsuche@example.org"}

    def notify(self, row) -> None:
        subscriptions = self.storage.push_subscriptions()
        if not subscriptions:
            raise RuntimeError("Noch kein Gerät für Push-Nachrichten angemeldet.")
        data = json.dumps(push_payload(row), ensure_ascii=False)
        delivered = 0
        for sub in subscriptions:
            try:
                webpush(sub, data, vapid_private_key=self.private_key, vapid_claims=dict(self.claims))
                delivered += 1
            except WebPushException as e:
                status = getattr(e.response, "status_code", None)
                if status in (404, 410):
                    # Abmeldung im Browser oder App gelöscht.
                    self.storage.remove_push_subscription(sub["endpoint"])
                else:
                    log.warning("Push an %s fehlgeschlagen: %s", sub["endpoint"][:60], e)
        if not delivered:
            raise RuntimeError("Push an kein Gerät zugestellt.")
