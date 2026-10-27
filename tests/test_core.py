import json

import pytest

from mietsuche.config import Criteria, load_config
from mietsuche.filters import JA, NEIN, UNKLAR, evaluate
from mietsuche.letter import render_letter, salutation
from mietsuche.main import run_once
from mietsuche.models import Listing
from mietsuche.sources import Source
from mietsuche.storage import Storage
from mietsuche.telegram import format_caption

CRITERIA = Criteria(zip_ranges=[(20095, 21149), (22041, 22769), (22869, 22869), (25469, 25469)])


def make(**kw) -> Listing:
    base = dict(
        source="test", external_id="1", url="https://example.org/1", title="Schöne 3-Zimmer-Wohnung",
        city="Hamburg", zip_code="22765", rooms=3, rent_warm=1800,
        description="Großer Südbalkon. Haustiere nach Absprache.",
    )
    base.update(kw)
    return Listing(**base)


def result(listing, name):
    return {c.name: c.result for c in evaluate(listing, CRITERIA).checks}[name]


def test_good_listing_matches():
    assert evaluate(make(), CRITERIA).matches


@pytest.mark.parametrize(
    "kw,check,expected",
    [
        ({"rooms": 2.5}, "zimmer", NEIN),
        ({"rooms": 3.5}, "zimmer", JA),
        ({"rent_warm": 2100}, "miete", NEIN),
        ({"rent_warm": None, "rent_cold": 1400}, "miete", UNKLAR),
        ({"rent_warm": None, "rent_cold": 1400, "service_charges": 300}, "miete", JA),
        ({"rent_warm": None, "rent_cold": 1900}, "miete", NEIN),
        ({"city": "Pinneberg", "zip_code": "25421"}, "ort", NEIN),
        ({"city": "Schenefeld", "zip_code": "22869"}, "ort", JA),
        ({"city": "Hamburg-Altona"}, "ort", JA),
        ({"city": None, "zip_code": "25469"}, "ort", JA),
        ({"city": "Oststeinbek", "zip_code": "22113"}, "ort", NEIN),
        ({"property_type": "wg"}, "objektart", NEIN),
    ],
)
def test_hard_criteria(kw, check, expected):
    assert result(make(**kw), check) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Haustiere nach Vereinbarung.", JA),
        ("Tierhaltung ist nach Absprache möglich", JA),
        ("Hunde sind willkommen", JA),
        ("Keine Haustiere!", NEIN),
        ("Haustiere sind leider nicht erlaubt.", NEIN),
        ("Tierhaltung nicht gestattet", NEIN),
        ("Helle Wohnung mit Einbauküche.", UNKLAR),
    ],
)
def test_pets_from_text(text, expected):
    assert result(make(description=f"Großer Balkon. {text}"), "haustiere") == expected


def test_pets_structured_field_wins():
    assert result(make(pets_allowed="nein", description="Haustiere erlaubt"), "haustiere") == NEIN
    assert result(make(pets_allowed="nach_vereinbarung", description=""), "haustiere") == JA


def test_pets_unknown_rejected_if_configured():
    strict = Criteria(pets_unknown_ok=False)
    verdict = evaluate(make(description="Großer Balkon."), strict)
    assert not verdict.matches


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Wohnung mit eigenem Garten", JA),
        ("Erdgeschoss mit Gartenanteil", JA),
        ("Große Dachterrasse", JA),
        ("Großzügiger Süd-Balkon", JA),
        ("Balkon ca. 12 m²", JA),
        ("Balkon (4 qm)", NEIN),
        ("Balkon zur Straße", UNKLAR),
        ("Schöner Gartenblick, kein Außenbereich", NEIN),
        ("Gemeinschaftsgarten im Hof", NEIN),
        ("Helle Räume, Keller", NEIN),
    ],
)
def test_outdoor_from_text(text, expected):
    assert result(make(description=f"Haustiere erlaubt. {text}"), "aussenbereich") == expected


def test_house_with_garden():
    house = make(property_type="haus", description="Reihenhaus, Garten nach Westen")
    assert result(house, "aussenbereich") == JA


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Frau Anna Müller", "Sehr geehrte Frau Müller,"),
        ("Herr Dr. Max Mustermann", "Sehr geehrter Herr Dr. Mustermann,"),
        ("SAGA Unternehmensgruppe", "Sehr geehrte Damen und Herren,"),
        (None, "Sehr geehrte Damen und Herren,"),
    ],
)
def test_salutation(name, expected):
    assert salutation(name) == expected


def test_render_letter_fills_and_blanks_unknown():
    tpl = "{anrede}\n\nIhre Anzeige „{titel}“ in {ort} auf {portal}. {unbekannt}Ende"
    text = render_letter(tpl, make(source="immoscout", contact_name="Frau Meier", district="Ottensen"))
    assert text.startswith("Sehr geehrte Frau Meier,")
    assert "„Schöne 3-Zimmer-Wohnung“ in Ottensen auf ImmoScout24." in text
    assert text.endswith("Ende")


class FakeSource(Source):
    name = "test"

    def __init__(self, listings):
        self.listings = listings

    def search(self, criteria):
        return self.listings


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def notify(self, row):
        self.sent.append(row["key"])


def test_run_once_notifies_each_match_once(tmp_path):
    (tmp_path / "config.yaml").write_text("anschreiben_datei: a.txt\ndatenbank: db.sqlite\n")
    (tmp_path / "a.txt").write_text("{anrede} {titel}")
    config = load_config(tmp_path / "config.yaml")
    storage = Storage(config.database)
    source = FakeSource([make(), make(external_id="2", rooms=1)])
    notifier = FakeNotifier()

    assert run_once(config, storage, [source], notifier) == 1
    assert notifier.sent == ["test:1"]
    assert run_once(config, storage, [source], notifier) == 0
    assert notifier.sent == ["test:1"]

    row = storage.matches()[0]
    assert row["letter"] == "Sehr geehrte Damen und Herren, Schöne 3-Zimmer-Wohnung"
    assert json.loads(row["checks"])["haustiere"]["ergebnis"] == JA
    assert "Haustiere" in format_caption(row)


def test_failed_source_does_not_stop_others(tmp_path):
    class Broken(Source):
        name = "kaputt"

        def search(self, criteria):
            raise RuntimeError("boom")

    (tmp_path / "config.yaml").write_text("datenbank: db.sqlite\n")
    config = load_config(tmp_path / "config.yaml")
    storage = Storage(config.database)
    notifier = FakeNotifier()
    assert run_once(config, storage, [Broken(), FakeSource([make()])], notifier) == 1
