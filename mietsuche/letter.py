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
    {objektnr}   Objekt- bzw. Anzeigennummer des Portals
    {ihr_objekt} "Ihre Wohnung" oder "Ihr Haus"
    {ihre_anzeige} Titel der Anzeige mit „Ihre“ davor: "Schöne Wohnung in Altona"
                 wird zu "Ihre schöne Wohnung in Altona"; passt das nicht,
                 "Ihre Anzeige „…“"
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


PLACEHOLDERS = {
    "anrede": "Sehr geehrte Frau …, / Sehr geehrter Herr …, / Sehr geehrte Damen und Herren,",
    "name": "Name der Kontaktperson",
    "titel": "Titel der Anzeige",
    "adresse": "Straße, PLZ und Ort",
    "ort": "Stadtteil bzw. Ort",
    "zimmer": "Zimmerzahl",
    "flaeche": "Wohnfläche in m²",
    "portal": "Name des Portals",
    "objektnr": "Objekt- bzw. Anzeigennummer",
    "ihr_objekt": "„Ihre Wohnung“ oder „Ihr Haus“",
    "ihre_anzeige": "Titel mit „Ihre“ davor, z. B. „Ihre schöne Wohnung in Altona“",
}


# Hauptwort am Wortende → passendes „Ihr…“ (nach „für“, also Akkusativ).
_FEM = ("wohnung", "whg", "etage", "maisonette", "villa", "hälfte", "oase", "perle", "immobilie", "bleibe")
_NEUT = ("haus", "apartment", "appartement", "loft", "penthouse", "studio", "zuhause", "domizil", "heim",
         "nest", "juwel", "refugium", "schmuckstück", "kleinod", "objekt")
_MASC = ("altbau", "neubau", "bungalow", "traum", "erstbezug")
_FILLER = {"und", "sehr", "ca", "neu", "top", "super", "&"}
# Werbung vor dem eigentlichen Titel, z. B. „ÖFFENTLICHE BESICHTIGUNG 30.10.“
_PREFIX = re.compile(r"^(?:[^\wÄÖÜäöü]+|(?:öffentliche\s+)?besichtigung\s+(?:am\s+)?[\d.:\s]*(?:uhr)?\s*"
                     r"|provisionsfrei\W*|privat\W*|ab sofort\W*)+", re.I)


def _ending(word: str) -> str | None:
    w = word.lower().strip(".,!:;")
    for nouns, article in ((_FEM, "Ihre"), (_NEUT, "Ihr"), (_MASC, "Ihren")):
        if w.endswith(nouns):
            return article
    return None


def your_listing(title: str) -> str:
    """„Schöne Wohnung in Altona“ → „Ihre schöne Wohnung in Altona“."""
    title = _PREFIX.sub("", title).strip(" -–|!*")
    title = re.split(r"\s+[|–]\s+|\s+-\s+|!", title)[0].strip()
    title = re.sub(r"\s+(?:zu vermieten|ab sofort|frei|verfügbar)\W*$", "", title, flags=re.I)
    if ":" in title:
        # „Ihr neues Zuhause: helle Wohnung …“ → der Teil nach dem Doppelpunkt, wenn er passt
        rest = your_listing(title.split(":", 1)[1])
        if not rest.startswith("Ihre Anzeige"):
            return rest
    return _with_article(title)


def _with_article(title: str) -> str:
    words = title.split()
    for i, word in enumerate(words):
        article = _ending(word)
        if article:
            break
        bare = word.lower().strip(".,")
        if not (re.fullmatch(r"[\d,.\-]+|\d.*", bare) or bare in _FILLER or re.search(r"(e|er|es|en|em)$", bare)):
            return f"Ihre Anzeige „{title}“"
    else:
        return f"Ihre Anzeige „{title}“"
    front = []
    for word in words[:i]:
        if word[:1].isupper() and not word.isupper() and re.search(r"(e|er|es|en|em)\W*$", word) \
                and not re.search(r"zimmer|raum", word, re.I):
            word = word[:1].lower() + word[1:]
        if article == "Ihren":
            word = re.sub(r"er(\W*)$", r"en\1", word)  # schöner Altbau → Ihren schönen Altbau
        front.append(word)
    return " ".join([article, *front, *words[i:]])


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
        objektnr=listing.external_id,
        ihr_objekt="Ihr Haus" if listing.property_type == "haus" else "Ihre Wohnung",
        ihre_anzeige=your_listing(listing.title),
    )
    try:
        text = template.format_map(values)
    except (ValueError, IndexError):
        # Vorlage mit einzelnen geschweiften Klammern: lieber ungefüllt als gar nicht.
        text = template
    # Leere Platzhalter hinterlassen sonst doppelte Leerzeichen.
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def load_template(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.exists() else None
