"""Füllt das Anschreiben mit den Angaben aus der Anzeige.

Platzhalter in anschreiben.txt (alle optional):

    {anrede}     "Sehr geehrte Frau Müller," / "Sehr geehrter Herr ..." /
                 "Sehr geehrte Damen und Herren,"
    {name}       Name der Kontaktperson, sonst leer
    {titel}      Titel der Anzeige
    {adresse}    Straße, PLZ und Ort, soweit bekannt
    {ort}        Stadt bzw. Stadtteil
    {zimmer}     Zimmerzahl
    {flaeche}    Wohnfläche in m²
    {portal}     Name des Portals
"""

from __future__ import annotations

import re
from pathlib import Path

from .models import Listing

_PORTALS = {
    "immoscout": "ImmoScout24",
    "immowelt": "Immowelt",
    "kleinanzeigen": "Kleinanzeigen",
    "ohnemakler": "Ohne-Makler",
    "saga": "SAGA",
}


def salutation(contact_name: str | None) -> str:
    name = (contact_name or "").strip()
    m = re.match(r"^(frau|herr|hr\.?|fr\.?)\s+(.+)$", name, re.IGNORECASE)
    if m:
        title, rest = m.group(1).lower(), m.group(2).strip()
        # "Herr Dr. Max Mustermann" -> Nachname ohne Vornamen
        parts = rest.split()
        academic = [p for p in parts[:-1] if p.lower().rstrip(".") in ("dr", "prof")]
        last = " ".join(academic + parts[-1:])
        if title.startswith("f"):
            return f"Sehr geehrte Frau {last},"
        return f"Sehr geehrter Herr {last},"
    return "Sehr geehrte Damen und Herren,"


class _Blank(dict):
    def __missing__(self, key):
        return ""


def render_letter(template: str, listing: Listing) -> str:
    values = _Blank(
        anrede=salutation(listing.contact_name),
        name=listing.contact_name or "",
        titel=listing.title,
        adresse=listing.address,
        ort=listing.district or listing.city or "",
        zimmer=f"{listing.rooms:g}" if listing.rooms else "",
        flaeche=f"{listing.living_area:g}" if listing.living_area else "",
        portal=_PORTALS.get(listing.source, listing.source),
    )
    text = template.format_map(values)
    # Leere Platzhalter hinterlassen sonst doppelte Leerzeichen.
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def load_template(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.exists() else None
