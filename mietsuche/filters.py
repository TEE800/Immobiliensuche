"""Prüft, ob eine Anzeige zu den Kriterien passt.

Jede Prüfung liefert ``ja``, ``unklar`` oder ``nein``. Eine Anzeige mit
mindestens einem ``nein`` fällt raus. ``unklar`` wird in der
Benachrichtigung angezeigt, damit man gezielt nachfragen kann.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import Criteria
from .models import Listing

JA, UNKLAR, NEIN = "ja", "unklar", "nein"


@dataclass
class Check:
    name: str
    result: str
    note: str = ""


@dataclass
class Verdict:
    checks: list[Check] = field(default_factory=list)

    @property
    def matches(self) -> bool:
        return all(c.result != NEIN for c in self.checks)

    @property
    def unclear(self) -> list[Check]:
        return [c for c in self.checks if c.result == UNKLAR]

    def as_dict(self) -> dict:
        return {c.name: {"ergebnis": c.result, "hinweis": c.note} for c in self.checks}


def _text(listing: Listing) -> str:
    return f"{listing.title}\n{listing.description}".lower()


# --- Haustiere -------------------------------------------------------------

_PETS_NO = re.compile(
    r"keine\s+(haus)?tiere"
    r"|(haus)?tiere?(haltung)?\s+(sind\s+|ist\s+)?(leider\s+)?(nicht|unerwünscht|ausgeschlossen|verboten)"
    r"|(haus)?tierhaltung\s+(ist\s+)?(nicht\s+(erlaubt|gestattet|möglich)|ausgeschlossen|untersagt)"
    r"|ohne\s+(haus)?tiere"
    r"|nichtraucher\s+(und|&)\s+ohne\s+tiere"
)
_PETS_YES = re.compile(
    r"(haus)?tiere?(haltung)?\s+(sind\s+|ist\s+)?(nach\s+(absprache|vereinbarung|rücksprache)|erlaubt|gestattet|willkommen|möglich|gerne)"
    r"|(haus)?tiere?\s*:\s*(ja|nach\s+(absprache|vereinbarung))"
    r"|(hunde?|katzen?)\s+(sind\s+|ist\s+)?(erlaubt|willkommen|gestattet|möglich)"
    r"|tierfreundlich"
)


def check_pets(listing: Listing, criteria: Criteria) -> Check:
    if listing.pets_allowed == "ja":
        return Check("haustiere", JA, "erlaubt")
    if listing.pets_allowed == "nach_vereinbarung":
        return Check("haustiere", JA, "nach Vereinbarung")
    if listing.pets_allowed == "nein":
        return Check("haustiere", NEIN, "nicht erlaubt")

    text = _text(listing)
    if _PETS_NO.search(text):
        return Check("haustiere", NEIN, "laut Anzeigentext nicht erlaubt")
    if _PETS_YES.search(text):
        return Check("haustiere", JA, "laut Anzeigentext möglich")
    if criteria.pets_unknown_ok:
        return Check("haustiere", UNKLAR, "keine Angabe, bitte nachfragen")
    return Check("haustiere", NEIN, "keine Angabe")


# --- Garten / Balkon -------------------------------------------------------

_GARDEN = re.compile(
    r"eigene[mnrs]?\s+garten|privatgarten|gartenanteil|garten\s*anteil|sondernutzungs\w*\s+(am\s+|für\s+den\s+)?garten"
    r"|garten\s+zur\s+(alleinigen|eigenen|exklusiven)\s+nutzung|mit\s+(eigenem\s+|privatem\s+)?garten\b"
    r"|garten\s+(mit|und)\s+terrasse|terrasse\s+(mit|und)\s+garten\b"
)
_GARDEN_SHARED_ONLY = re.compile(r"gemeinschaftsgarten|gemeinschaftlich\w*\s+garten")
_BIG_OUTDOOR = re.compile(
    r"(große[rnms]?|großzügige[rnms]?|riesige[rnms]?|weitläufige[rnms]?|geräumige[rnms]?)\s+(süd|west|ost)?[-\s]?(balkon|terrasse|loggia|dachterrasse)"
    r"|dachterrasse|sonnenterrasse|terrasse"
)
_BALCONY_SIZE = re.compile(
    r"(balkon|loggia|terrasse)[^.\n]{0,40}?(\d{1,2}(?:[.,]\d{1,2})?)\s*(m²|qm|m2|quadratmeter)"
    r"|(\d{1,2}(?:[.,]\d{1,2})?)\s*(m²|qm|m2)\s*(große[rnms]?\s+)?(balkon|loggia|terrasse)"
)
_BALCONY = re.compile(r"balkon|loggia")


def _balcony_size_from_text(text: str) -> float | None:
    sizes = []
    # "ca." würde sonst wie ein Satzende den Treffer abschneiden.
    text = re.sub(r"\b(ca|rd|gr)\.", r"\1", text)
    for m in _BALCONY_SIZE.finditer(text):
        raw = m.group(2) or m.group(4)
        sizes.append(float(raw.replace(",", ".")))
    return max(sizes) if sizes else None


def check_outdoor(listing: Listing, criteria: Criteria) -> Check:
    text = _text(listing)

    if listing.garden or _GARDEN.search(text):
        return Check("aussenbereich", JA, "eigener Garten")
    if listing.property_type == "haus" and re.search(r"\bgarten\b", text) and not _GARDEN_SHARED_ONLY.search(text):
        return Check("aussenbereich", JA, "Haus mit Garten")

    size = listing.balcony_area or _balcony_size_from_text(text)
    if size is not None:
        if size >= criteria.balcony_min_sqm:
            return Check("aussenbereich", JA, f"Balkon/Terrasse ca. {size:g} m²")
        return Check("aussenbereich", NEIN, f"Balkon nur ca. {size:g} m²")

    if _BIG_OUTDOOR.search(text):
        return Check("aussenbereich", JA, "großer Balkon oder Terrasse")
    if listing.garden_maybe_shared and not _GARDEN_SHARED_ONLY.search(text):
        return Check("aussenbereich", UNKLAR, "Garten, evtl. nur zur Mitnutzung")
    if listing.balcony or _BALCONY.search(text):
        if criteria.balcony_without_size_ok:
            return Check("aussenbereich", UNKLAR, "Balkon, Größe unklar")
        return Check("aussenbereich", NEIN, "Balkon, Größe unklar")
    return Check("aussenbereich", NEIN, "weder Garten noch Balkon")


# --- Harte Kriterien -------------------------------------------------------


def check_type(listing: Listing, criteria: Criteria) -> Check:
    ok = listing.property_type in criteria.property_types
    return Check("objektart", JA if ok else NEIN, listing.property_type.capitalize())


def check_rooms(listing: Listing, criteria: Criteria) -> Check:
    if listing.rooms is None:
        return Check("zimmer", UNKLAR, "keine Angabe")
    ok = listing.rooms >= criteria.rooms_min
    return Check("zimmer", JA if ok else NEIN, f"{listing.rooms:g} Zimmer".replace(".", ","))


def check_rent(listing: Listing, criteria: Criteria) -> Check:
    warm, unknown = listing.warm_rent_estimate
    if not unknown:
        ok = warm <= criteria.warm_rent_max
        return Check("miete", JA if ok else NEIN, f"{warm:.0f} € warm")
    if listing.rent_cold is not None:
        if listing.rent_cold <= criteria.cold_rent_max_if_warm_unknown:
            return Check("miete", UNKLAR, f"{listing.rent_cold:.0f} € kalt, warm unbekannt")
        return Check("miete", NEIN, f"{listing.rent_cold:.0f} € kalt, warm vermutlich zu hoch")
    return Check("miete", UNKLAR, "keine Angabe")


def check_location(listing: Listing, criteria: Criteria) -> Check:
    cities = {c.lower() for c in criteria.cities}
    if listing.city:
        city = listing.city.lower().strip()
        # "Hamburg-Altona" oder "Hamburg / Eimsbüttel" zählen als Hamburg.
        head = re.split(r"[\s\-/,(]", city, maxsplit=1)[0]
        ok = city in cities or head in cities
        return Check("ort", JA if ok else NEIN, listing.city)
    if listing.zip_code and listing.zip_code.isdigit():
        z = int(listing.zip_code)
        ok = any(a <= z <= b for a, b in criteria.zip_ranges)
        return Check("ort", JA if ok else NEIN, listing.zip_code)
    return Check("ort", UNKLAR, "keine Angabe")


def worth_details(listing: Listing, criteria: Criteria) -> bool:
    """Lohnt es, die Detailseite zu laden?

    Geprüft wird nur, was schon in der Trefferliste steht. Die Miete ist dort
    meist kalt, deshalb reicht hier eine Kaltmiete bis zur Warmmiete-Grenze.
    """
    if any(c(listing, criteria).result == NEIN for c in (check_type, check_location, check_rooms)):
        return False
    warm, unknown = listing.warm_rent_estimate
    if not unknown:
        return warm <= criteria.warm_rent_max
    return listing.rent_cold is None or listing.rent_cold <= criteria.warm_rent_max


CHECKS = (check_type, check_location, check_rooms, check_rent, check_pets, check_outdoor)


def evaluate(listing: Listing, criteria: Criteria) -> Verdict:
    return Verdict([check(listing, criteria) for check in CHECKS])
