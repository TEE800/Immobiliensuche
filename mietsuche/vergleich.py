"""Vergleich mit anderen Anzeigen, die die Mietsuche selbst gesammelt hat.

Gerechnet wird nur auf dem Laptop aus der eigenen Datenbank: Median der
Kaltmiete je m² ähnlicher Wohnungen in derselben Gegend.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from statistics import median

MIN_COUNT = 5
DAYS = 180
# Diese Angebote haben eigene Preise und würden den Vergleich verzerren.
_SKIP = re.compile(r"möbliert|\bWG\b|zwischenmiete|untermiete|befristet|tausch|ferienwohnung|monteur", re.I)


def offers(rows, now: datetime | None = None) -> list[dict]:
    """Vergleichbare Anzeigen aus Datenbankzeilen (alle gesehenen, nicht nur Treffer)."""
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=DAYS)).isoformat()
    result = []
    for row in rows:
        if row["first_seen"] < cutoff or not row["rent_cold"] or not row["living_area"]:
            continue
        data = json.loads(row["data"])
        if data.get("property_type") != "wohnung" or _SKIP.search(row["title"] or ""):
            continue
        qm = row["rent_cold"] / row["living_area"]
        if row["living_area"] < 20 or not 5 <= qm <= 40:
            continue  # Tippfehler oder Warmmiete im Feld Kaltmiete
        result.append({"key": row["key"], "city": data.get("city"), "district": data.get("district"),
                       "area": row["living_area"], "qm": qm})
    return result


def compare(listing: dict, pool: list[dict]) -> dict:
    """Median ähnlicher Wohnungen, vom engsten zum weitesten Kreis."""
    if listing.get("property_type") != "wohnung" or not listing.get("rent_cold") or not listing.get("living_area"):
        return {}
    area = listing["living_area"]
    others = [o for o in pool if o["key"] != listing["key"] and o["city"] == listing.get("city")]
    similar = [o for o in others if abs(o["area"] - area) <= area * 0.25]
    district = listing.get("district")
    steps = []
    if district:
        steps += [([o for o in similar if o["district"] == district], f"{district}, ähnliche Größe"),
                  ([o for o in others if o["district"] == district], f"{district}, alle Größen")]
    steps += [(similar, f"ganz {listing.get('city')}, ähnliche Größe"), (others, f"ganz {listing.get('city')}")]
    for group, label in steps:
        if len(group) >= MIN_COUNT:
            qm = listing["rent_cold"] / area
            mid = median(o["qm"] for o in group)
            return {"qm": round(qm, 2), "median": round(mid, 2), "anzahl": len(group), "gruppe": label,
                    "abweichung": round((qm / mid - 1) * 100), "wenige": len(group) < 15}
    return {"anzahl": len(others), "gruppe": f"ganz {listing.get('city')}", "zu_wenige": True}
