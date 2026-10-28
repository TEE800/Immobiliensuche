"""Fahrzeit mit Bus und Bahn von einer Wohnung zu den Arbeitsorten.

Rechnet über Transitous (https://transitous.org), einen freien Fahrplandienst
ohne Konto. Gesendet werden nur Koordinaten bzw. die Adresse der Anzeige.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

from .config import WORKDAYS, Arrival, Workplace
from .models import Listing
from .sources.common import USER_AGENT

log = logging.getLogger(__name__)

API = "https://api.transitous.org/api"
BERLIN = ZoneInfo("Europe/Berlin")
MODES = {"BUS": "Bus", "SUBWAY": "U", "SUBURBAN": "S", "TRAM": "Tram", "FERRY": "Fähre",
         "RAIL": "Zug", "REGIONAL_RAIL": "Zug", "REGIONAL_FAST_RAIL": "Zug", "METRO": "U"}


def next_day_at(clock: str, days: tuple[int, ...] = WORKDAYS, now: datetime | None = None) -> datetime:
    """Nächster passender Wochentag (ab morgen) zur Uhrzeit ``"08:00"``, in UTC."""
    now = now or datetime.now(BERLIN)
    hour, minute = (int(x) for x in clock.split(":"))
    day = now.astimezone(BERLIN).date() + timedelta(days=1)
    while day.weekday() not in days:
        day += timedelta(days=1)
    return datetime.combine(day, time(hour, minute), BERLIN).astimezone(ZoneInfo("UTC"))


def _lines(itinerary: dict) -> str:
    parts = []
    for leg in itinerary.get("legs", []):
        if leg.get("mode") == "WALK":
            continue
        name = leg.get("routeShortName") or ""
        mode = MODES.get(leg.get("mode", ""), "")
        # Hamburger Linien heißen schon „U3“ oder „S1“, Busse nur „5“.
        label = name if name[:1] in ("U", "S") and mode in ("U", "S") else f"{mode} {name}".strip()
        if label and label not in parts:
            parts.append(label)
    return ", ".join(parts)


class Transitous:
    def __init__(self, client: httpx.Client | None = None):
        self.client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30)
        self._places: dict[str, tuple[float, float] | None] = {}

    def geocode(self, text: str) -> tuple[float, float] | None:
        if text not in self._places:
            r = self.client.get(f"{API}/v1/geocode", params={"text": text, "language": "de"})
            r.raise_for_status()
            hits = r.json()
            self._places[text] = (hits[0]["lat"], hits[0]["lon"]) if hits else None
        return self._places[text]

    def plan(self, start: tuple[float, float], goal: tuple[float, float], arrive: datetime) -> dict | None:
        r = self.client.get(f"{API}/v5/plan", params={
            "fromPlace": f"{start[0]},{start[1]}",
            "toPlace": f"{goal[0]},{goal[1]}",
            "time": arrive.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "arriveBy": "true",
        })
        r.raise_for_status()
        data = r.json()
        options = [
            {"minuten": round(it["duration"] / 60), "umstiege": it.get("transfers", 0), "linien": _lines(it)}
            for it in data.get("itineraries", [])
        ]
        # Zu Fuß ist manchmal schneller als jede Bahn.
        options += [
            {"minuten": round(it["duration"] / 60), "umstiege": 0, "linien": "zu Fuß"}
            for it in data.get("direct", [])
            if all(leg.get("mode") == "WALK" for leg in it.get("legs", []))
        ]
        return min(options, key=lambda o: (o["minuten"], o["umstiege"])) if options else None


def listing_place(listing: Listing) -> tuple[str, bool]:
    """Suchtext für die Wohnung und ob er nur ungefähr ist (ohne Straße)."""
    place = " ".join(p for p in (listing.zip_code, listing.city) if p)
    if listing.street:
        return f"{listing.street}, {place}", False
    return " ".join(p for p in (listing.district, place) if p), True


def commute_times(listing: Listing, workplaces: list[Workplace], arrival: str,
                  service: Transitous) -> list[dict]:
    """Fahrzeiten zu allen Arbeitsorten; leer, wenn die Wohnung nicht zu finden ist."""
    text, approx = listing_place(listing)
    if not text:
        return []
    start = service.geocode(text)
    if start is None:
        return []
    results = []
    for work in workplaces:
        goal = service.geocode(work.address)
        if goal is None:
            log.warning("Arbeitsort nicht gefunden: %s", work.name)
            continue
        for when in work.arrivals or [Arrival(WORKDAYS, arrival)]:
            best = service.plan(start, goal, next_day_at(when.clock, when.days))
            if best:
                results.append({"name": work.name, "tage": when.label, "ankunft": when.clock,
                                **best, "ungefaehr": approx})
    return results
