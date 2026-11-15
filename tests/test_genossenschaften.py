"""Genossenschaften über die öffentliche Immomio-Angebotsliste."""

import json

import httpx
import pytest

from mietsuche.config import Criteria
from mietsuche.filters import evaluate
from mietsuche.sources import REGISTRY
from mietsuche.sources.common import Fetcher
from mietsuche.sources.genossenschaften import (
    GENOSSENSCHAFTEN, GRAPHQL, Genossenschaften, find_token, parse_nodes,
)

TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJjdXN0b21lcklkIjoxfQ.abc-DEF_123"
COOP = GENOSSENSCHAFTEN[0]

FLAT = {
    "name": "Schöne 3-Zimmer-Wohnung mit Balkon",
    "totalRooms": 3,
    "availableFrom": {"dateAvailable": "2027-01-01", "stringAvailable": "01.01.2027"},
    "titleImage": {"url": "https://img.immomio.example/1.jpg"},
    "address": {"city": "Hamburg", "street": "Musterweg", "houseNumber": "5", "zipCode": "22307",
                "district": "Barmbek-Nord"},
    "showAddress": True,
    "size": 74.5,
    "totalRentGross": 1180.5,
    "propertyType": "FLAT",
    "externalId": "063.902",
    "wbs": False,
    "applicationLink": "https://tenant.immomio.com/de/apply/123",
    "floor": 2,
    "status": "DEFAULT",
    "marketingType": "RENT",
    "objectId": "123",
}
PARKING = {**FLAT, "name": "Stellplatz", "propertyType": "GARAGE", "objectId": "124"}


def test_registered():
    assert "genossenschaft" in REGISTRY


def test_find_token():
    html = f'<iframe src="https://homepage.immomio.com/de/properties?token={TOKEN} "></iframe>'
    assert find_token(html) == TOKEN
    assert find_token("<p>nichts</p>") is None


def test_parse_nodes():
    [listing] = parse_nodes([FLAT, PARKING], COOP)
    assert listing.source == "genossenschaft"
    assert listing.external_id == f"{COOP.key}-123"
    assert listing.url == "https://tenant.immomio.com/de/apply/123"
    assert listing.street == "Musterweg 5"
    assert (listing.zip_code, listing.city, listing.district) == ("22307", "Hamburg", "Barmbek-Nord")
    assert (listing.rooms, listing.living_area, listing.rent_warm) == (3, 74.5, 1180.5)
    assert listing.contact_company == COOP.name
    assert listing.available_from == "01.01.2027"
    assert "Wohnberechtigungsschein" not in listing.description


def test_hidden_address_and_wbs():
    [listing] = parse_nodes([{**FLAT, "showAddress": False, "wbs": True, "applicationLink": None}], COOP)
    assert listing.street is None
    assert listing.url == COOP.page
    assert "Wohnberechtigungsschein" in listing.description


def test_matches_criteria():
    [listing] = parse_nodes([FLAT], COOP)
    criteria = Criteria(zip_ranges=[(22041, 22769)])
    verdict = evaluate(listing, criteria)
    assert verdict.as_dict()["miete"]["ergebnis"] == "ja"


def test_search_reads_token_then_list():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url) == GRAPHQL:
            body = json.loads(request.content)
            assert body["variables"]["input"]["token"] == TOKEN
            other = {**FLAT, "objectId": "999"}
            if body["variables"]["input"]["balconyOrTerrace"]:
                return httpx.Response(200, json={"data": {"propertyList": {"nodes": [FLAT]}}})
            return httpx.Response(200, json={"data": {"propertyList": {"nodes": [FLAT, other, PARKING]}}})
        if str(request.url) == COOP.page:
            return httpx.Response(200, text=f'<iframe src="https://homepage.immomio.com/de/properties?token={TOKEN}">')
        return httpx.Response(200, text="<p>keine Liste</p>")

    fetcher = Fetcher(httpx.Client(transport=httpx.MockTransport(handler)), pause=(0, 0))
    found = Genossenschaften(fetcher).search(Criteria())
    balcony = {x.key: x.balcony for x in found}
    assert balcony == {f"genossenschaft:{COOP.key}-123": True, f"genossenschaft:{COOP.key}-999": None}
    assert GRAPHQL in calls


def test_search_fails_when_nothing_reachable():
    def handler(request):
        return httpx.Response(503)

    fetcher = Fetcher(httpx.Client(transport=httpx.MockTransport(handler)), pause=(0, 0))
    with pytest.raises(RuntimeError):
        Genossenschaften(fetcher).search(Criteria())
