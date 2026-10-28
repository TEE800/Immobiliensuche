import httpx
import pytest

from mietsuche.mietenspiegel import Wohnlagen, compare, split_street
from mietsuche.models import Listing
from mietsuche.sources.common import year_from_text


def flat(**kw):
    base = dict(source="t", external_id="1", url="u", title="t", city="Hamburg", zip_code="20539",
                street="Marckmannstraße 27", rent_cold=1000, living_area=80)
    base.update(kw)
    return Listing(**base)


def test_one_field_when_year_and_location_known():
    # Normale Lage, 66 bis unter 91 m², Baujahr 1968–1977: Mittel 6,76, Spanne 6,25–9,48
    result = compare(flat(year_built=1972), "normal")
    assert result["qm"] == 12.5
    assert (result["mittel_von"], result["spanne_von"], result["spanne_bis"]) == (6.76, 6.25, 9.48)
    assert result["abweichung"] == 85
    assert result["bewertung"] == "über"


def test_range_when_year_unknown():
    result = compare(flat(rent_cold=900), None)
    assert "abweichung" not in result
    assert result["mittel_von"] < result["mittel_bis"]
    assert result["bewertung"] == "im Rahmen"


def test_not_applicable():
    assert "gilt hier nicht" in compare(flat(city="Pinneberg"), None)["hinweis"]
    assert "Häuser" in compare(flat(property_type="haus"), None)["hinweis"]
    assert "fehlt" in compare(flat(living_area=None), None)["hinweis"]
    assert "neuer" in compare(flat(year_built=2026), None)["hinweis"]


@pytest.mark.parametrize("text,year", [
    ("Altbau, Baujahr: 1908, frisch saniert", 1908), ("Bj. ca. 1965", 1965), ("BAUJAHR 2019", 2019),
    ("Wohnung von 1965", None), ("", None),
])
def test_year_from_text(text, year):
    assert year_from_text(text) == year


def test_split_street():
    assert split_street("Marckmannstr. 27a") == ("Marckmannstraße", 27, "a")
    assert split_street("Am Kroog 12") == ("Am Kroog", 12, "")
    assert split_street("Rehmkoppel") == ("Rehmkoppel", None, "")


def test_wohnlage_by_house_number(monkeypatch):
    monkeypatch.undo()  # echte lookup-Methode, aber ohne Netz
    addresses = [
        {"hausnummer": 2, "hausnummer_zusatz": "", "bezeichnung": "Normale Wohnlage"},
        {"hausnummer": 10, "hausnummer_zusatz": "", "bezeichnung": "Gute Wohnlage"},
        {"hausnummer": 11, "hausnummer_zusatz": "", "bezeichnung": "Normale Wohnlage"},
    ]
    seen = []

    def handler(request):
        seen.append(request.url.params["FILTER"])
        return httpx.Response(200, json={"features": [{"properties": a} for a in addresses]})

    lagen = Wohnlagen(httpx.Client(transport=httpx.MockTransport(handler)))
    assert lagen.lookup("Teststr. 10", "22765") == "gut"
    assert lagen.lookup("Teststraße 8", "22765") == "gut"  # nächste gerade Nummer
    assert lagen.lookup("Teststraße 9", "22765") == "normal"
    assert lagen.lookup("Teststraße", "22765") is None
    assert lagen.lookup(None, "22765") is None
    assert len(seen) == 1 and "Teststraße" in seen[0] and "22765" in seen[0]


def test_offers_outside_hamburg():
    # Schenefeld, ab 80 m²: Median 12,16 €/m² (IB.SH 2024)
    result = compare(flat(city="Schenefeld", zip_code="22869", rent_cold=1100, living_area=88), None)
    assert result["art"] == "angebote"
    assert (result["median"], result["qm"], result["abweichung"]) == (12.16, 12.5, 3)
    assert "Kein amtlicher Mietspiegel" in result["hinweis"]
    new = compare(flat(city="Halstenbek", zip_code="25469", year_built=2023), None)
    assert new["median"] == 14.68 and "Neubau" in new["gruppe"]
