"""Vergleich der Kaltmiete mit dem Hamburger Mietenspiegel 2025.

Die Tabelle liegt in ``tabellen/mietenspiegel_2025.json`` (aus dem Geodienst der
Stadt Hamburg). Die Wohnlage („normal“ oder „gut“) kommt je Adresse aus dem
Hamburger Wohnlagenverzeichnis; dafür wird nur Straße und PLZ der Anzeige an
geodienste.hamburg.de geschickt. Das Ergebnis ist eine Orientierung, keine
rechtliche Prüfung.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import httpx

from .models import Listing
from .sources.common import USER_AGENT

log = logging.getLogger(__name__)

TABLE = json.loads((Path(__file__).parent / "tabellen" / "mietenspiegel_2025.json").read_text(encoding="utf-8"))
# Ändert sich die Tabelle oder die Rechnung, werden alte Treffer neu verglichen.
VERSION = "2025.1"
WFS = "https://geodienste.hamburg.de/HH_WFS_Wohnlagen"

def split_street(street: str) -> tuple[str, int | None, str]:
    """``"Marckmannstr. 27a"`` → ``("Marckmannstraße", 27, "a")``."""
    m = re.match(r"(.*?)\s*(\d+)\s*([a-zA-Z]?)\b", street.strip())
    name, nr, extra = (m.group(1), int(m.group(2)), m.group(3).lower()) if m else (street.strip(), None, "")
    name = re.sub(r"(?i)str\.?$", "straße", name.strip().rstrip(","))
    return name, nr, extra


class Wohnlagen:
    """Wohnlage je Adresse aus dem Hamburger Wohnlagenverzeichnis."""

    def __init__(self, client: httpx.Client | None = None):
        self.client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30)
        self._streets: dict[tuple[str, str], list[dict]] = {}

    def _addresses(self, name: str, zip_code: str) -> list[dict]:
        if (name, zip_code) not in self._streets:
            where = "".join(
                f"<fes:PropertyIsEqualTo><fes:ValueReference>app:{k}</fes:ValueReference>"
                f"<fes:Literal>{v}</fes:Literal></fes:PropertyIsEqualTo>"
                for k, v in (("strasse", name), ("plz", zip_code))
            )
            r = self.client.get(WFS, params={
                "SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature",
                "TYPENAMES": "app:wohnlagen", "OUTPUTFORMAT": "application/geo+json",
                "FILTER": '<fes:Filter xmlns:fes="http://www.opengis.net/fes/2.0" '
                          f'xmlns:app="http://www.deegree.org/app"><fes:And>{where}</fes:And></fes:Filter>',
            })
            r.raise_for_status()
            self._streets[(name, zip_code)] = [f["properties"] for f in r.json().get("features", [])]
        return self._streets[(name, zip_code)]

    def lookup(self, street: str | None, zip_code: str | None) -> str | None:
        """„normal“, „gut“ oder None, wenn die Adresse nicht eindeutig ist."""
        if not street or not zip_code:
            return None
        name, nr, extra = split_street(street)
        found = self._addresses(name, zip_code)
        if not found:
            return None
        lagen = {"gut" if a["bezeichnung"].startswith("Gute") else "normal" for a in found}
        if len(lagen) == 1:
            return lagen.pop()
        if nr is None:
            return None
        # Genau diese Hausnummer, sonst die nächste auf derselben Straßenseite.
        same_side = [a for a in found if int(a["hausnummer"]) % 2 == nr % 2] or found
        best = min(same_side, key=lambda a: (abs(int(a["hausnummer"]) - nr),
                                            (a.get("hausnummer_zusatz") or "") != extra))
        return "gut" if best["bezeichnung"].startswith("Gute") else "normal"


def _fields(area: float, lage: str | None, year: int | None) -> list[dict]:
    return [
        f for f in TABLE["felder"]
        if f["flaeche_von"] <= area and (f["flaeche_bis"] is None or area < f["flaeche_bis"])
        and (lage is None or f["lage"] == lage)
        and (year is None or ((f["baujahr_von"] or 0) <= year <= f["baujahr_bis"]))
    ]


def compare(listing: Listing, lage: str | None) -> dict:
    """Kaltmiete je m² im Vergleich zum Mietenspiegel."""
    if listing.city != "Hamburg":
        return {"hinweis": "Der Hamburger Mietenspiegel gilt hier nicht."}
    if listing.property_type == "haus":
        return {"hinweis": "Der Mietenspiegel gilt nur für Wohnungen, nicht für Häuser."}
    if not listing.rent_cold or not listing.living_area:
        return {"hinweis": "Kaltmiete oder Wohnfläche fehlt in der Anzeige."}
    year = listing.year_built
    if year and year > max(f["baujahr_bis"] for f in TABLE["felder"]):
        return {"hinweis": f"Baujahr {year} ist neuer als der Mietenspiegel 2025."}
    qm = listing.rent_cold / listing.living_area
    fields = _fields(listing.living_area, lage, year)
    if not fields:
        return {"hinweis": "Für diese Wohnung hat der Mietenspiegel keinen Wert."}
    result = {
        "qm": round(qm, 2),
        "lage": lage,
        "baujahr": year,
        "mittel_von": min(f["mittel"] for f in fields),
        "mittel_bis": max(f["mittel"] for f in fields),
        "spanne_von": min(f["von"] for f in fields),
        "spanne_bis": max(f["bis"] for f in fields),
    }
    if len(fields) == 1:
        result["abweichung"] = round((qm / fields[0]["mittel"] - 1) * 100)
    result["bewertung"] = ("über" if qm > result["spanne_bis"]
                           else "unter" if qm < result["spanne_von"] else "im Rahmen")
    return result
