"""Ohne-Makler (ohne-makler.net), private Vermieter ohne Provision.

Die Trefferliste nennt Ort, Zimmer, Fläche und Kaltmiete. Nebenkosten und
Ausstattung (Balkon, Terrasse, Garten) stehen auf der Detailseite.
"""

from __future__ import annotations

import dataclasses
import logging
import re

from bs4 import BeautifulSoup

from ..config import Criteria
from ..models import Listing
from . import Source, register
from .common import Fetcher, clean, number, split_place, year_from_text

log = logging.getLogger(__name__)

BASE = "https://www.ohne-makler.net"
REGIONS = {
    "hamburg": "hamburg",
    "schenefeld": "schleswig-holstein/schenefeld",
    "halstenbek": "schleswig-holstein/halstenbek",
}
CATEGORIES = {"wohnung": "wohnung-mieten", "haus": "haus-mieten"}
MAX_PAGES = 4

_LABELS = {
    "Kaltmiete", "Warmmiete", "Heizkosten", "Nebenkosten ohne Heizkosten",
    "Summe Nebenkosten/Heizkosten", "Objektart", "Objekttyp", "Übernahme ab", "Ausstattung",
    "Baujahr",
}
_END = ("Lage-Check", "Energie", "Ähnliche Immobilien", "Makleranfragen unerwünscht!")


def parse_search(html: str, property_type: str) -> list[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    listings = []
    for card in soup.select("a[data-om-id]"):
        if card.get("data-om-type", "RENT") != "RENT":
            continue
        h4 = card.find("h4")
        if not h4:
            continue
        place_el = h4.find_next("span")
        zip_code, city, district = split_place(place_el.get_text() if place_el else None)
        rooms_el = card.select_one('[title="Zimmer"]')
        area_el = card.select_one('[title="Wohnfläche"]')
        price_el = card.select_one("span.font-semibold")
        img = card.find("img")
        listings.append(
            Listing(
                source="ohnemakler",
                external_id=card["data-om-id"],
                url=f"{BASE}/immobilie/{card['data-om-id']}/",
                title=clean(h4.get_text()),
                property_type=property_type,
                zip_code=zip_code,
                city=city,
                district=district,
                rooms=number(rooms_el.get_text()) if rooms_el else None,
                living_area=number(area_el.get_text()) if area_el else None,
                rent_cold=number(price_el.get_text()) if price_el else None,
                image_url=img.get("src") if img else None,
            )
        )
    return listings


def page_tokens(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    for el in soup(["script", "style", "svg", "template", "dialog", "form", "button", "nav", "header", "footer"]):
        el.decompose()
    h1 = soup.find("h1")
    root = h1.find_parent("main") or soup.body or soup if h1 else soup
    tokens = [clean(t) for t in root.stripped_strings]
    tokens = [t for t in tokens if t]
    if h1:
        title = clean(h1.get_text())
        if title in tokens:
            tokens = tokens[tokens.index(title):]
    for i, t in enumerate(tokens):
        if t in _END:
            return tokens[:i]
    return tokens


def parse_details(html: str, listing: Listing) -> Listing:
    soup = BeautifulSoup(html, "html.parser")
    tokens = page_tokens(html)
    facts: dict[str, str] = {}
    for i, t in enumerate(tokens[:-1]):
        if t in _LABELS and t not in facts:
            facts[t] = tokens[i + 1]

    def euro(label: str) -> float | None:
        return number(facts.get(label))

    service = euro("Summe Nebenkosten/Heizkosten")
    if service is None and (euro("Heizkosten") or euro("Nebenkosten ohne Heizkosten")):
        service = (euro("Heizkosten") or 0) + (euro("Nebenkosten ohne Heizkosten") or 0)

    features = [f.strip().lower() for f in facts.get("Ausstattung", "").split(",")]
    has_garden = "garten" in features
    is_house = listing.property_type == "haus"

    # Die Liste der Infrastruktur (Kindergarten, Schulen …) gehört nicht zur Wohnung.
    text = []
    skip = False
    for t in tokens:
        if skip:
            skip = False
            continue
        if t == "Infrastruktur":
            skip = True
            continue
        text.append(t)

    street = None
    address = soup.select_one('a[href="#ort"]')
    if address:
        parts = [p.strip() for p in clean(address.get_text()).split(",")]
        if len(parts) > 1 and re.search(r"\d", parts[0]):
            street = parts[0]

    return dataclasses.replace(
        listing,
        street=street or listing.street,
        rent_cold=euro("Kaltmiete") if euro("Kaltmiete") is not None else listing.rent_cold,
        rent_warm=euro("Warmmiete"),
        service_charges=service,
        garden=True if has_garden and is_house else None,
        garden_maybe_shared=has_garden and not is_house,
        balcony=True if {"balkon", "terrasse", "loggia", "dachterrasse"} & set(features) else None,
        description="\n".join(text),
        available_from=facts.get("Übernahme ab"),
        year_built=_year(facts.get("Baujahr")) or year_from_text("\n".join(text)),
    )


def _year(text: str | None) -> int | None:
    m = re.search(r"\b(1[89]\d\d|20\d\d)\b", text or "")
    return int(m.group(1)) if m else None


@register
class OhneMakler(Source):
    name = "ohnemakler"

    def __init__(self, fetcher: Fetcher | None = None):
        self.fetcher = fetcher or Fetcher()

    def search(self, criteria: Criteria) -> list[Listing]:
        found: dict[str, Listing] = {}
        for ptype in criteria.property_types:
            if ptype not in CATEGORIES:
                continue
            for city in criteria.cities:
                region = REGIONS.get(city.lower())
                if not region:
                    log.warning("Ohne-Makler: Ort %s nicht hinterlegt", city)
                    continue
                url = f"{BASE}/immobilien/{CATEGORIES[ptype]}/{region}/"
                for page in range(1, MAX_PAGES + 1):
                    response = self.fetcher.get(url if page == 1 else f"{url}?page={page}")
                    if response.status_code == 404:
                        break
                    response.raise_for_status()
                    listings = parse_search(response.text, ptype)
                    new = [x for x in listings if x.external_id not in found]
                    for listing in new:
                        found[listing.external_id] = listing
                    # Kleine Orte zeigen auch Anzeigen aus der Umgebung; die
                    # sind schon von der Hamburg-Suche bekannt.
                    if not new:
                        break
        return list(found.values())

    def details(self, listing: Listing) -> Listing:
        return parse_details(self.fetcher.text(listing.url), listing)
