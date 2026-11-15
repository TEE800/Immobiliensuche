"""Hamburger Wohnungsbaugenossenschaften, deren Angebote über Immomio laufen.

Die Genossenschaften binden auf ihrer Webseite eine öffentliche Angebotsliste
von Immomio ein (``homepage.immomio.com/de/properties?token=…``). Die Liste lädt
ihre Daten von ``gql-hp.immomio.com``; genau diese Abfrage stellt die
Mietsuche auch. Den Schlüssel (``token``) liest sie bei jedem Lauf frisch von
der Angebotsseite der Genossenschaft, falls er sich ändert.

Beworben wird über „Jetzt bewerben“ bei Immomio mit eigenem Konto. Ein
Anschreiben per E-Mail gibt es hier nicht, und die meisten Genossenschaften
vergeben zuerst an Mitglieder.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from ..config import Criteria
from ..models import Listing
from . import Source, register
from .common import Fetcher, clean

log = logging.getLogger(__name__)

GRAPHQL = "https://gql-hp.immomio.com/homepage/graphql"
WIDGET = "https://homepage.immomio.com/de/properties?token="
PAGE_SIZE = 50
MAX_PAGES = 4

_TOKEN = re.compile(r"properties\?token=(eyJ[\w-]+\.eyJ[\w-]+\.[\w-]+)")


@dataclass(frozen=True)
class Genossenschaft:
    key: str
    name: str
    # Seite der Genossenschaft, auf der die Immomio-Liste eingebunden ist
    page: str


GENOSSENSCHAFTEN = [
    Genossenschaft("bgfg", "Baugenossenschaft freier Gewerkschafter (BGFG)",
                   "https://www.bgfg.de/wohnen/wohnungsangebote"),
    Genossenschaft("vhw", "Vereinigte Hamburger Wohnungsbaugenossenschaft (VHW)",
                   "https://www.vhw-hamburg.de/wohnen/aktuelle-angebote.html"),
    Genossenschaft("ebv", "Eisenbahnbauverein Harburg",
                   "https://www.ebv-harburg.de/wohnungen/wohnungsangebote/"),
    Genossenschaft("buchdrucker", "Baugenossenschaft der Buchdrucker",
                   "https://www.buchdrucker.de/de/wohnungsangebote-stellplatzangebote-gewerbeangebote"),
    Genossenschaft("harabau", "Hamburg-Rahlstedter Baugenossenschaft (HARABAU)",
                   "https://www.harabau.de/wohnen/wohnungsangebote/"),
    Genossenschaft("mgf", "Mietergenossenschaft Farmsen (MGF)",
                   "https://www.mgf-farmsen.de/immobiliensuche/aktuelle-angebote-wohnungen/stellplaetze/"),
    Genossenschaft("bds", "Baugenossenschaft Dennerstraße-Selbsthilfe (bds)",
                   "https://www.bds-hamburg.de/unser-angebot/interessentenportal-immomio/"),
    Genossenschaft("reiherstieg", "Bauverein Reiherstieg",
                   "https://www.reiherstieg.de/wohnungsangebote/"),
    Genossenschaft("fuhlsbuettel", "Baugenossenschaft Fuhlsbüttel",
                   "https://www.bg-fuhlsbuettel.de/index.php?id=22"),
]

QUERY = """query propertyList($input: HomepagePropertySearchRequest!) {
  propertyList(input: $input) {
    nodes {
      name
      totalRooms
      availableFrom { dateAvailable stringAvailable }
      titleImage { url }
      address { city street houseNumber zipCode district }
      showAddress
      size
      totalRentGross
      propertyType
      externalId
      wbs
      applicationLink
      floor
      status
      marketingType
      objectId
    }
  }
}"""


def search_input(token: str, page: int, balcony: bool | None = None) -> dict:
    """Dieselben Angaben, die die Liste auf der Genossenschaftsseite schickt.

    ``balcony=True`` entspricht dem Häkchen „Balkon / Terrasse“ der Liste.
    """
    empty = {"from": None, "to": None}
    return {
        "page": page, "size": PAGE_SIZE, "token": token,
        "propertyType": None, "wbs": None, "barrierFree": None, "balconyOrTerrace": balcony,
        "roomNumber": empty, "floor": empty, "totalRentGross": empty, "salesPrice": empty,
        "propertySize": empty, "externalId": None, "sort": ["created,desc"], "area": None,
        "marketingType": None,
    }


def find_token(html: str) -> str | None:
    m = _TOKEN.search(html or "")
    return m.group(1) if m else None


def _is_flat(node: dict) -> bool:
    kind = str(node.get("propertyType") or "").upper()
    marketing = str(node.get("marketingType") or "RENT").upper()
    # Stellplätze, Gewerbe und Verkauf gehören nicht zur Mietsuche.
    return marketing in ("RENT", "") and kind in ("FLAT", "APARTMENT", "HOUSE", "")


def _float(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def parse_nodes(nodes: list[dict], coop: Genossenschaft) -> list[Listing]:
    listings = []
    for node in nodes or []:
        if not _is_flat(node):
            continue
        ident = str(node.get("objectId") or node.get("externalId") or "").strip()
        if not ident:
            continue
        address = node.get("address") or {}
        street = " ".join(p for p in (address.get("street"), address.get("houseNumber")) if p) or None
        if node.get("showAddress") is False:
            street = None
        available = node.get("availableFrom") or {}
        image = (node.get("titleImage") or {}).get("url")
        wbs = bool(node.get("wbs"))
        listings.append(Listing(
            source="genossenschaft",
            external_id=f"{coop.key}-{ident}",
            url=node.get("applicationLink") or coop.page,
            title=clean(node.get("name")) or "Wohnung",
            property_type="haus" if str(node.get("propertyType") or "").upper() == "HOUSE" else "wohnung",
            street=street,
            zip_code=address.get("zipCode") or None,
            city=address.get("city") or None,
            district=address.get("district") or None,
            rooms=_float(node.get("totalRooms")),
            living_area=_float(node.get("size")),
            # Immomio nennt die Gesamtmiete (warm).
            rent_warm=_float(node.get("totalRentGross")),
            image_url=image,
            images=[image] if image else [],
            contact_company=coop.name,
            available_from=available.get("stringAvailable") or available.get("dateAvailable"),
            description="\n".join(filter(None, [
                f"Angebot der {coop.name}.",
                "Wohnberechtigungsschein (WBS) erforderlich." if wbs else "",
                f"Etage: {node['floor']}" if node.get("floor") not in (None, "") else "",
            ])),
            raw={"genossenschaft": coop.key, "wbs": wbs, "externalId": node.get("externalId")},
        ))
    return listings


@register
class Genossenschaften(Source):
    name = "genossenschaft"

    def __init__(self, fetcher: Fetcher | None = None):
        self.fetcher = fetcher or Fetcher()

    def _token(self, coop: Genossenschaft) -> str | None:
        response = self.fetcher.get(coop.page)
        response.raise_for_status()
        return find_token(response.text)

    def search(self, criteria: Criteria) -> list[Listing]:
        found: dict[str, Listing] = {}
        failed = 0
        for coop in GENOSSENSCHAFTEN:
            try:
                token = self._token(coop)
                if not token:
                    log.warning("Genossenschaft %s: keine Angebotsliste auf %s gefunden", coop.name, coop.page)
                    continue
                flats = {x.key: x for x in parse_nodes(self._all(token), coop)}
                if flats:
                    # Die Liste nennt Balkon/Terrasse nur über ihren Filter, also einmal mit Häkchen fragen.
                    for listing in parse_nodes(self._all(token, balcony=True), coop):
                        if listing.key in flats:
                            flats[listing.key].balcony = True
                found.update(flats)
            except Exception as e:
                # Eine Genossenschaft, deren Seite gerade nicht geht, hält die anderen nicht auf.
                failed += 1
                log.warning("Genossenschaft %s nicht abrufbar: %s", coop.name, e)
        if failed == len(GENOSSENSCHAFTEN):
            raise RuntimeError("keine Genossenschaft erreichbar")
        return list(found.values())

    def _all(self, token: str, balcony: bool | None = None) -> list[dict]:
        nodes: list[dict] = []
        for page in range(MAX_PAGES):
            batch = self._query(token, page, balcony)
            nodes.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
        return nodes

    def _query(self, token: str, page: int, balcony: bool | None = None) -> list[dict]:
        response = self.fetcher.post(GRAPHQL, json={
            "operationName": "propertyList",
            "variables": {"input": search_input(token, page, balcony)},
            "query": QUERY,
        }, headers={"Origin": "https://homepage.immomio.com", "Referer": "https://homepage.immomio.com/"})
        response.raise_for_status()
        data = response.json()
        if data.get("errors"):
            raise RuntimeError(str(data["errors"])[:300])
        return ((data.get("data") or {}).get("propertyList") or {}).get("nodes") or []
