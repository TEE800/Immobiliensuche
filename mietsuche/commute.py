"""Fahrzeit mit Bus und Bahn von einer Wohnung zu den Arbeitsorten.

Rechnet über Transitous (https://transitous.org), einen freien Fahrplandienst
ohne Konto. Gesendet werden nur Koordinaten bzw. die Adresse der Anzeige.
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

from .config import WORKDAYS, Arrival, Workplace
from .models import Listing
from .sources.common import USER_AGENT

log = logging.getLogger(__name__)

API = "https://api.transitous.org/api"
BERLIN = ZoneInfo("Europe/Berlin")
# So früh darf man höchstens vor der gewünschten Zeit ankommen.
EARLY = timedelta(minutes=20)
MODES = {"BUS": "Bus", "SUBWAY": "U", "SUBURBAN": "S", "TRAM": "Tram", "FERRY": "Fähre",
         "RAIL": "Zug", "REGIONAL_RAIL": "Zug", "REGIONAL_FAST_RAIL": "Zug", "METRO": "U"}


def _easter(year: int) -> date:
    """Ostersonntag (Meeus/Jones/Butcher)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def is_holiday(day: date) -> bool:
    """Gesetzlicher Feiertag in Hamburg und Schleswig-Holstein (dann fährt alles wie sonntags)."""
    if (day.month, day.day) in {(1, 1), (5, 1), (10, 3), (10, 31), (12, 25), (12, 26)}:
        return True
    offset = (day - _easter(day.year)).days
    return offset in (-2, 1, 39, 50)  # Karfreitag, Ostermontag, Himmelfahrt, Pfingstmontag


def next_day_at(clock: str, days: tuple[int, ...] = WORKDAYS, now: datetime | None = None) -> datetime:
    """Nächster passender Wochentag (ab morgen, ohne Feiertage) zur Uhrzeit ``"08:00"``, in UTC."""
    now = now or datetime.now(BERLIN)
    hour, minute = (int(x) for x in clock.split(":"))
    day = now.astimezone(BERLIN).date() + timedelta(days=1)
    while day.weekday() not in days or is_holiday(day):
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

    def _plan(self, start, goal, arrive: datetime) -> dict:
        r = self.client.get(f"{API}/v5/plan", params={
            "fromPlace": f"{start[0]},{start[1]}",
            "toPlace": f"{goal[0]},{goal[1]}",
            "time": arrive.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "arriveBy": "true",
            # Mehr Vorschläge, damit einer kurz vor der Zielzeit ankommt.
            "numItineraries": 8,
        })
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _options(data: dict, goal_name: str) -> list[dict]:
        def option(it: dict, lines: str, changes: int) -> dict:
            legs = it.get("legs", [])
            return {"minuten": round(it["duration"] / 60), "umstiege": changes, "linien": lines,
                    "schritte": _steps(legs, goal_name),
                    "_an": _when(it["endTime"]) if it.get("endTime") else None}

        options = [option(it, _lines(it), it.get("transfers", 0)) for it in data.get("itineraries", [])]
        # Zu Fuß ist manchmal schneller als jede Bahn.
        options += [option(it, "zu Fuß", 0) for it in data.get("direct", [])
                    if all(leg.get("mode") == "WALK" for leg in it.get("legs", []))]
        return options

    def plan(self, start: tuple[float, float], goal: tuple[float, float], arrive: datetime,
             goal_name: str = "Arbeit") -> dict | None:
        options = self._options(self._plan(start, goal, arrive), goal_name)
        best = choose(options, arrive)
        return {k: v for k, v in best.items() if not k.startswith("_")} if best else None


def _when(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def choose(options: list[dict], arrive: datetime) -> dict | None:
    """Schnellste Verbindung, die kurz vor der Zielzeit ankommt.

    Nur Verbindungen, die höchstens 20 Minuten zu früh da sind (sonst die
    spätesten); Fußwege zählen nicht extra (Kevins Wunsch).
    """
    if not options:
        return None
    timed = [o for o in options if o["_an"] and o["_an"] <= arrive]
    window = [o for o in timed if o["_an"] >= arrive - EARLY]
    if not window and timed:
        latest = max(o["_an"] for o in timed)
        window = [o for o in timed if o["_an"] >= latest - EARLY]
    pool = window or options
    return min(pool, key=lambda o: (o["minuten"], o["umstiege"], -(o["_an"] or arrive).timestamp()))


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
