"""Bilder der Treffer lokal speichern.

Die Bildadressen der Portale laufen zum Teil nach einem Tag ab (Ohne-Makler),
deshalb lädt die Mietsuche die Bilder eines Treffers einmal herunter. Die App
zeigt sie dann von hier, das Handy fragt also nicht bei den Portalen an.
"""

from __future__ import annotations

import logging
import re
import shutil
import time
from pathlib import Path

import httpx

from .sources.common import USER_AGENT

log = logging.getLogger(__name__)

MAX_IMAGES = 20
MAX_BYTES = 8 * 1024 * 1024
TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}


def folder(base: Path, key: str) -> Path:
    return base / re.sub(r"[^\w-]", "_", key)


def files(base: Path, key: str) -> list[Path]:
    """Gespeicherte Bilder in der Reihenfolge der Anzeige."""
    target = folder(base, key)
    if not target.is_dir():
        return []
    return sorted((p for p in target.iterdir() if p.suffix in TYPES.values()), key=lambda p: int(p.stem))


def download(base: Path, key: str, urls: list[str], client: httpx.Client | None = None,
             pause: float = 0.3) -> int:
    """Lädt die Bilder eines Treffers und gibt zurück, wie viele es sind."""
    client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True)
    target = folder(base, key)
    shutil.rmtree(target, ignore_errors=True)
    count = 0
    for url in urls[:MAX_IMAGES]:
        if count:
            time.sleep(pause)
        try:
            r = client.get(url)
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("Bild %s nicht geladen: %s", url, e)
            continue
        suffix = TYPES.get(r.headers.get("content-type", "").split(";")[0].strip())
        if not suffix or len(r.content) > MAX_BYTES:
            continue
        target.mkdir(parents=True, exist_ok=True)
        count += 1
        (target / f"{count}{suffix}").write_bytes(r.content)
    return count
