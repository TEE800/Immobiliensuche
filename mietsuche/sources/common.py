"""Hilfen, die alle Portale brauchen: zurückhaltender Abruf und Zahlen lesen."""

from __future__ import annotations

import random
import re
import time

import httpx

USER_AGENT = "Mietsuche/1.0 (private Wohnungssuche; https://github.com/TEE800/Immobiliensuche)"


class Fetcher:
    """Lädt Seiten mit Pause dazwischen, damit kein Portal belastet wird."""

    def __init__(self, client: httpx.Client | None = None, pause: tuple[float, float] = (2.0, 5.0)):
        self.client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "de-DE,de;q=0.9"},
            timeout=30,
            follow_redirects=True,
        )
        self.pause = pause
        self._last = 0.0

    def get(self, url: str) -> httpx.Response:
        wait = self._last + random.uniform(*self.pause) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            return self.client.get(url)
        finally:
            self._last = time.monotonic()

    def text(self, url: str) -> str:
        response = self.get(url)
        response.raise_for_status()
        return response.text


_NUMBER = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?")


def number(text: str | None) -> float | None:
    """Erste Zahl in deutscher Schreibweise: ``"1.234,50 €"`` → 1234.5."""
    if not text:
        return None
    m = _NUMBER.search(text)
    if not m:
        return None
    raw = m.group()
    if "," in raw or raw.count(".") > 1 or re.fullmatch(r"\d{1,3}\.\d{3}", raw):
        raw = raw.replace(".", "").replace(",", ".")
    return float(raw)


_PLACE = re.compile(r"^(?P<zip>\d{5})\s+(?P<city>[^\s(]+)\s*(?:\((?P<d1>[^)]*)\)|(?P<d2>.+))?$")


def split_place(text: str | None) -> tuple[str | None, str | None, str | None]:
    """``"22145 Hamburg (Jenfeld)"`` oder ``"22145 Hamburg Jenfeld"`` → PLZ, Ort, Stadtteil."""
    text = " ".join((text or "").split())
    m = _PLACE.match(text)
    if not m:
        return None, text or None, None
    district = (m.group("d1") or m.group("d2") or "").strip() or None
    return m.group("zip"), m.group("city"), district


def clean(text: str | None) -> str:
    return " ".join((text or "").split())


_YEAR = re.compile(r"\b(?:Baujahr|Bj\.?)\s*:?\s*(?:ca\.?\s*|um\s*)?((?:18|19|20)\d\d)\b", re.I)


def year_from_text(text: str) -> int | None:
    """Baujahr aus dem Anzeigentext, z. B. „Baujahr: 1928“ oder „Bj. ca. 1965“."""
    m = _YEAR.search(text or "")
    return int(m.group(1)) if m else None


_GONE_TEXT = re.compile(r"Anzeige ist nicht mehr verfügbar|Anzeige wurde gelöscht|bereits gelöscht|"
                        r"nicht mehr online|nicht mehr aktiv", re.I)


def still_online(fetcher: "Fetcher", url: str, path: str) -> bool:
    """Prüft eine Anzeige: weg bei 404/410, bei Weiterleitung weg von ``path``
    (Kleinanzeigen leitet gelöschte Anzeigen auf eine Suche um) oder bei einem
    Hinweis auf der Seite. Andere Fehler werfen, damit später neu geprüft wird."""
    response = fetcher.get(url)
    if response.status_code in (404, 410):
        return False
    response.raise_for_status()
    if path not in str(response.url):
        return False
    return not _GONE_TEXT.search(response.text[:200000])
