"""Kleinanzeigen (kleinanzeigen.de).

Die Trefferliste nennt Ort, Zimmer, Fläche und Kaltmiete. Haustiere,
Garten, Balkon und Nebenkosten stehen erst auf der Detailseite, die deshalb
nur für Anzeigen geladen wird, die nach der Liste noch passen können.
"""

from __future__ import annotations

import dataclasses
import logging
import re

from bs4 import BeautifulSoup

from ..config import Criteria
from ..models import Listing
from . import Source, register
from .common import Fetcher, clean, number, split_place

log = logging.getLogger(__name__)

BASE = "https://www.kleinanzeigen.de"
# Ortskennungen von Kleinanzeigen (aus den Links auf deren Ortsseiten).
LOCATIONS = {
    "hamburg": ("hamburg", 9409),
    "schenefeld": ("schenefeld", 759),
    "halstenbek": ("halstenbek", 616),
}
CATEGORIES = {
    "wohnung": ("s-wohnung-mieten", 203, "wohnung_mieten"),
    "haus": ("s-haus-mieten", 205, "haus_mieten"),
}
# Die Liste ist nach Datum sortiert; bei einem Suchlauf alle paar Minuten
# reichen wenige Seiten.
PAGES = {"hamburg": 2}

# Gesuche und Tauschangebote stehen in derselben Rubrik, passen aber nie.
_SKIP_TITLE = re.compile(r"\b(suche|sucht|suchen|gesucht)\b|tausch|swap", re.I)


def search_urls(criteria: Criteria):
    for ptype in criteria.property_types:
        if ptype not in CATEGORIES:
            continue
        path, cat, attr = CATEGORIES[ptype]
        rooms = f"+{attr}.zimmer_d:{criteria.rooms_min:g}%2C" if criteria.rooms_min else ""
        for city in criteria.cities:
            if city.lower() not in LOCATIONS:
                log.warning("Kleinanzeigen: Ort %s nicht hinterlegt", city)
                continue
            slug, loc = LOCATIONS[city.lower()]
            for page in range(1, PAGES.get(city.lower(), 1) + 1):
                seite = f"seite:{page}/" if page > 1 else ""
                yield ptype, city, f"{BASE}/{path}/{slug}/{seite}c{cat}l{loc}{rooms}"


def parse_search(html: str, property_type: str, searched_city: str | None = None) -> list[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    listings = []
    for art in soup.select("article[data-adid]"):
        href = art.get("data-href", "")
        h3 = art.find("h3")
        if not href or not h3:
            continue
        title = clean(h3.get_text())
        if _SKIP_TITLE.search(title):
            continue
        place = next(
            (clean(s.get_text()) for s in art.find_all("span") if re.match(r"\s*\d{5}\s", s.get_text())),
            None,
        )
        zip_code, city, district = split_place(place)
        # In Hamburg steht oft nur der Bezirk als Ort („21073 Harburg“).
        if searched_city and searched_city.lower() == "hamburg" and city and city.lower() != "hamburg":
            city, district = "Hamburg", " ".join(p for p in (city, district) if p)
        rooms = area = price = None
        teaser = ""
        for p in art.find_all("p"):
            text = clean(p.get_text())
            if "Zi." in text or "m²" in text:
                for part in text.split("·"):
                    if "Zi." in part:
                        rooms = number(part)
                    elif "m²" in part:
                        area = number(part)
            elif "€" in text and price is None:
                price = number(text)
            elif not teaser and p.find_previous("h3") is h3:
                teaser = text
        img = art.find("img")
        listings.append(
            Listing(
                source="kleinanzeigen",
                external_id=art["data-adid"],
                url=BASE + href,
                title=title,
                property_type=property_type,
                zip_code=zip_code,
                city=city,
                district=district,
                # 0 Zimmer heißt bei Kleinanzeigen „keine Angabe“.
                rooms=rooms or None,
                living_area=area or None,
                rent_cold=price,
                description=teaser,
                image_url=img.get("src") if img else None,
            )
        )
    return listings


def parse_attributes(html: str) -> dict[str, str]:
    m = re.search(r'"ad_attributes":"([^"]*)"', html)
    if not m:
        return {}
    pairs = (item.split(":", 1) for item in m.group(1).split("|") if ":" in item)
    return {k: v for k, v in pairs}


def parse_details(html: str, listing: Listing) -> Listing:
    soup = BeautifulSoup(html, "html.parser")
    attrs = parse_attributes(html)

    def num(key: str) -> float | None:
        return number(attrs.get(key))

    def flag(key: str) -> bool:
        return attrs.get(key) == "t"

    desc_el = soup.find(id="viewad-description-text")
    if desc_el:
        for br in desc_el.find_all("br"):
            br.replace_with("\n")
    description = desc_el.get_text().strip() if desc_el else listing.description
    features = [clean(li.get_text()) for li in soup.select("li.checktag")]
    if features:
        description += "\nAusstattung: " + ", ".join(features)

    service = None
    if num("nebenkosten_i") is not None or num("heizkosten_i") is not None:
        heating = num("heizkosten_i") or 0
        # Ab und zu steht hier die Jahressumme; die ist dann keine Monatsangabe.
        if heating > 1000:
            heating = 0
        service = (num("nebenkosten_i") or 0) + heating

    garden = flag("garden_b")
    street_el = soup.find(id="street-address")
    contact_el = soup.select_one(".userprofile-vip")
    month, year = attrs.get("verfuegbarm_i"), attrs.get("verfuegbary_i")
    price = number(soup.find(id="viewad-price").get_text()) if soup.find(id="viewad-price") else None

    return dataclasses.replace(
        listing,
        # Ein Tauschangebot setzt eine eigene Wohnung zum Tauschen voraus.
        property_type="tausch" if flag("swap_s") else listing.property_type,
        street=clean(street_el.get_text()).rstrip(",") if street_el else listing.street,
        rooms=num("zimmer_d") or listing.rooms,
        living_area=num("qm_d") or listing.living_area,
        rent_cold=listing.rent_cold if listing.rent_cold is not None else price,
        rent_warm=num("warmmiete_i"),
        service_charges=service,
        pets_allowed="ja" if flag("pets_allowed_b") else None,
        # „Garten/-mitnutzung“ ist bei Wohnungen oft nur der Hof für alle.
        garden=True if garden and listing.property_type == "haus" else None,
        garden_maybe_shared=garden and listing.property_type != "haus",
        balcony=True if flag("balcony_b") or flag("terrace_b") else None,
        description=description,
        contact_name=clean(contact_el.get_text()) if contact_el else None,
        available_from=f"{month}/{year}" if month and year else None,
        raw=attrs,
    )


@register
class Kleinanzeigen(Source):
    name = "kleinanzeigen"

    def __init__(self, fetcher: Fetcher | None = None):
        self.fetcher = fetcher or Fetcher()

    def search(self, criteria: Criteria) -> list[Listing]:
        found: dict[str, Listing] = {}
        for ptype, city, url in search_urls(criteria):
            for listing in parse_search(self.fetcher.text(url), ptype, city):
                found.setdefault(listing.external_id, listing)
        return list(found.values())

    def details(self, listing: Listing) -> Listing:
        return parse_details(self.fetcher.text(listing.url), listing)
