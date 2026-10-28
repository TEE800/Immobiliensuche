"""Fahrzeit mit Bus und Bahn von einer Wohnung zu den Arbeitsorten.

Rechnet über Transitous (https://transitous.org), einen freien Fahrplandienst
ohne Konto. Gesendet werden nur Koordinaten bzw. die Adresse der Anzeige.
"""

from __future__ import annotations

import logging
import math
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


def _clock(stamp: str) -> str:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(BERLIN).strftime("%H:%M")


def _steps(legs: list[dict], goal: str) -> list[dict]:
    """Die einzelnen Abschnitte einer Verbindung, kurz für die Anzeige."""
    names = {"START": "Wohnung", "END": goal}
    steps = []
    for leg in legs:
        step = {
            "ab": _clock(leg["startTime"]),
            "an": _clock(leg["endTime"]),
            "von": names.get(leg["from"]["name"], leg["from"]["name"]),
            "nach": names.get(leg["to"]["name"], leg["to"]["name"]),
            "minuten": round(leg.get("duration", 0) / 60),
        }
        if leg.get("mode") == "WALK":
            step["linie"] = "zu Fuß"
            if leg.get("distance"):
                step["meter"] = round(leg["distance"])
        else:
            step["linie"] = _lines({"legs": [leg]})
            step["richtung"] = leg.get("headsign", "")
        steps.append(step)
    return steps


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

    def plan(self, start: tuple[float, float], goal: tuple[float, float], arrive: datetime,
             goal_name: str = "Arbeit") -> dict | None:
        r = self.client.get(f"{API}/v5/plan", params={
            "fromPlace": f"{start[0]},{start[1]}",
            "toPlace": f"{goal[0]},{goal[1]}",
            "time": arrive.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "arriveBy": "true",
        })
        r.raise_for_status()
        data = r.json()
        options = [
            {"minuten": round(it["duration"] / 60), "umstiege": it.get("transfers", 0), "linien": _lines(it),
             "schritte": _steps(it.get("legs", []), goal_name)}
            for it in data.get("itineraries", [])
        ]
        # Zu Fuß ist manchmal schneller als jede Bahn.
        options += [
            {"minuten": round(it["duration"] / 60), "umstiege": 0, "linien": "zu Fuß",
             "schritte": _steps(it.get("legs", []), goal_name)}
            for it in data.get("direct", [])
            if all(leg.get("mode") == "WALK" for leg in it.get("legs", []))
        ]
        return min(options, key=lambda o: (o["minuten"], o["umstiege"])) if options else None


def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Luftlinie in Kilometern."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


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
            best = service.plan(start, goal, next_day_at(when.clock, when.days), f"Arbeit {work.name}")
            if best:
                results.append({"name": work.name, "tage": when.label, "ankunft": when.clock,
                                **best, "ungefaehr": approx,
                                # Für die Karte: Wohnung, Arbeitsort und Luftlinie
                                "von": [round(x, 5) for x in start], "nach": [round(x, 5) for x in goal],
                                "km": round(distance_km(start, goal), 1)})
    return results
