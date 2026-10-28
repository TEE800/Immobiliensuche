"""Push-Nachrichten direkt aus der Übersichts-App (Web Push)."""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from urllib.parse import urlparse

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid01
from pywebpush import WebPushException, webpush

from .storage import Storage

log = logging.getLogger("mietsuche")

# So lange hält der Push-Dienst eine Nachricht vor, wenn das Gerät nicht erreichbar ist.
TTL = 24 * 3600


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
        body += "\n🚆 " + commute_text(commute)
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
        self.send(push_payload(row))

    def send(self, payload: dict) -> None:
        subscriptions = self.storage.push_subscriptions()
        if not subscriptions:
            raise RuntimeError("Noch kein Gerät für Push-Nachrichten angemeldet.")
        data = json.dumps(payload, ensure_ascii=False)
        delivered = 0
        for sub in subscriptions:
            host = urlparse(sub["endpoint"]).hostname
            try:
                # Ohne TTL verwirft der Push-Dienst die Nachricht, wenn das Handy gerade schläft.
                webpush(sub, data, vapid_private_key=self.private_key, vapid_claims=dict(self.claims),
                        ttl=TTL, headers={"Urgency": "high"})
                delivered += 1
                log.info("Push an %s zugestellt", host)
            except WebPushException as e:
                status = getattr(e.response, "status_code", None)
                if status in (400, 403, 404, 410):
                    # Abgemeldet, App gelöscht oder ungültig. Die App meldet sich beim
                    # nächsten Öffnen von selbst wieder an.
                    self.storage.remove_push_subscription(sub["endpoint"])
                    log.warning("Push an %s abgelehnt (%s), Anmeldung entfernt", host, status)
                else:
                    log.warning("Push an %s fehlgeschlagen: %s", host, e)
        if not delivered:
            raise RuntimeError("Push an kein Gerät zugestellt.")


def commute_text(commute: list[dict]) -> str:
    """„Kevin 34 Min. · Fritzi Di–Fr 38 Min., Sa 45 Min.“"""
    by_name: dict[str, list[dict]] = {}
    for c in commute:
        by_name.setdefault(c["name"], []).append(c)
    parts = []
    for name, items in by_name.items():
        times = [(f"{c['tage']} " if c.get("tage") and c["tage"] != "Mo–Fr" else "") + f"{c['minuten']} Min."
                 for c in items]
        parts.append(f"{name} " + ", ".join(times))
    return " · ".join(parts)
