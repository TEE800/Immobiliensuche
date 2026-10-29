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


@pytest.mark.parametrize("title, expected", [
    ("Schöne Wohnung in Altona", "Ihre schöne Wohnung in Altona"),
    ("Moderne, ruhige 2-Zi-Whg. mit Terrasse", "Ihre moderne, ruhige 2-Zi-Whg. mit Terrasse"),
    ("ÖFFENTLICHE BESICHTIGUNG 30.10. Altbauvilla", "Ihre Altbauvilla"),
    ("3-Zimmer-Wohntraum in Wohldorf-Ohlstedt", "Ihren 3-Zimmer-Wohntraum in Wohldorf-Ohlstedt"),
    ("Schöner Altbau in Eimsbüttel", "Ihren schönen Altbau in Eimsbüttel"),
    ("Helles Reihenhaus mit Garten – provisionsfrei", "Ihr helles Reihenhaus mit Garten"),
    ("Ihr neues Zuhause: günstige 2-Zimmer-Wohnung", "Ihre günstige 2-Zimmer-Wohnung"),
    ("Helle 3 Zimmer Wohnung", "Ihre helle 3 Zimmer Wohnung"),
    ("Nachmieter gesucht für 2-Zimmer-Wohnung", "Ihre Anzeige „Nachmieter gesucht für 2-Zimmer-Wohnung“"),
])
def test_your_listing(title, expected):
    assert render_letter("für {ihre_anzeige}.", make(title=title)) == f"für {expected}."


def test_render_letter_object_and_number():
    tpl = "wir interessieren uns sehr für {ihr_objekt} (Nr. {objektnr}) in {ort}."
    house = make(property_type="haus", external_id="152388", city="Halstenbek")
    assert render_letter(tpl, house) == "wir interessieren uns sehr für Ihr Haus (Nr. 152388) in Halstenbek."
    assert "Ihre Wohnung" in render_letter(tpl, make())


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

    assert run_once(config, storage, [source], [notifier]) == 1
    assert notifier.sent == ["test:1"]
    assert run_once(config, storage, [source], [notifier]) == 0
    assert notifier.sent == ["test:1"]

    row = storage.matches()[0]
    assert row["letter"] == "Sehr geehrte Damen und Herren, Schöne 3-Zimmer-Wohnung"
    assert json.loads(row["checks"])["haustiere"]["ergebnis"] == JA
    assert "Haustiere" in format_caption(row)


def test_match_stays_pending_until_a_notifier_succeeds(tmp_path):
    class Failing:
        def notify(self, row):
            raise RuntimeError("offline")

    (tmp_path / "config.yaml").write_text("datenbank: db.sqlite\n")
    config = load_config(tmp_path / "config.yaml")
    storage = Storage(config.database)
    run_once(config, storage, [FakeSource([make()])], [Failing()])
    assert len(storage.pending_notifications()) == 1
    ok = FakeNotifier()
    run_once(config, storage, [FakeSource([make()])], [Failing(), ok])
    assert ok.sent == ["test:1"]
    assert storage.pending_notifications() == []


def test_failed_source_does_not_stop_others(tmp_path):
    class Broken(Source):
        name = "kaputt"

        def search(self, criteria):
            raise RuntimeError("boom")

    (tmp_path / "config.yaml").write_text("datenbank: db.sqlite\n")
    config = load_config(tmp_path / "config.yaml")
    storage = Storage(config.database)
    notifier = FakeNotifier()
    assert run_once(config, storage, [Broken(), FakeSource([make()])], [notifier]) == 1


def test_details_loaded_only_for_promising_listings(tmp_path):
    class Detailed(FakeSource):
        loaded: list = []

        def details(self, listing):
            self.loaded.append(listing.external_id)
            if listing.external_id == "3":
                raise RuntimeError("Seite nicht erreichbar")
            return listing

    (tmp_path / "config.yaml").write_text("datenbank: db.sqlite\n")
    config = load_config(tmp_path / "config.yaml")
    storage = Storage(config.database)
    source = Detailed([make(), make(external_id="2", rooms=1), make(external_id="3")])
    run_once(config, storage, [source], [FakeNotifier()])
    assert source.loaded == ["1", "3"]
    # Fehlgeschlagene Detailseite wird nicht gespeichert und beim nächsten Lauf erneut versucht.
    assert not storage.seen("test:3")
    run_once(config, storage, [source], [FakeNotifier()])
    assert source.loaded == ["1", "3", "3"]


def test_commute_only_for_matches(tmp_path):
    from mietsuche.config import Workplace

    class FakeTransit:
        pass

    (tmp_path / "config.yaml").write_text("datenbank: db.sqlite\n")
    config = load_config(tmp_path / "config.yaml")
    config.workplaces = [Workplace("Kevin", "Arbeit 1")]
    storage = Storage(config.database)
    calls = []

    import mietsuche.main as main_module

    def fake_commute(listing, workplaces, arrival, transit):
        calls.append(listing.external_id)
        return [{"name": "Kevin", "minuten": 20, "umstiege": 0, "linien": "S1", "ungefaehr": False}]

    original = main_module.commute_times
    main_module.commute_times = fake_commute
    try:
        run_once(config, storage, [FakeSource([make(), make(external_id="2", rooms=1)])], [FakeNotifier()],
                 transit=FakeTransit())
    finally:
        main_module.commute_times = original
    assert calls == ["1"]
    assert json.loads(storage.get("test:1")["data"])["commute"][0]["minuten"] == 20


def test_commute_backfilled_for_older_matches(tmp_path):
    from mietsuche.config import Workplace
    import mietsuche.main as main_module

    (tmp_path / "config.yaml").write_text("datenbank: db.sqlite\n")
    config = load_config(tmp_path / "config.yaml")
    storage = Storage(config.database)
    run_once(config, storage, [FakeSource([make()])], [FakeNotifier()])  # noch ohne Arbeitsorte
    assert json.loads(storage.get("test:1")["data"])["commute"] == []

    calls = []

    def fake_commute(listing, workplaces, arrival, transit):
        calls.append(listing.external_id)
        return [{"name": workplaces[0].name, "minuten": 20, "umstiege": 0, "linien": "S1", "ungefaehr": False}]

    original = main_module.commute_times
    main_module.commute_times = fake_commute
    try:
        config.workplaces = [Workplace("Fritzi", "Arbeit 1")]
        run_once(config, storage, [], [FakeNotifier()], transit=object())
        run_once(config, storage, [], [FakeNotifier()], transit=object())
        assert calls == ["1"]  # nur einmal nachgetragen
        config.workplaces = [Workplace("Kevin", "Arbeit 2")]
        run_once(config, storage, [], [FakeNotifier()], transit=object())
    finally:
        main_module.commute_times = original
    assert calls == ["1", "1"]
    assert json.loads(storage.get("test:1")["data"])["commute"][0]["name"] == "Kevin"


def test_rent_index_for_matches_and_backfill(tmp_path):
    from mietsuche.mietenspiegel import VERSION

    (tmp_path / "config.yaml").write_text("datenbank: db.sqlite\n")
    config = load_config(tmp_path / "config.yaml")
    storage = Storage(config.database)
    run_once(config, storage, [FakeSource([make(), make(external_id="2", rooms=1)])], [FakeNotifier()])
    data = json.loads(storage.get("test:1")["data"])
    assert data["rent_index_for"] == VERSION and data["rent_index"]
    assert not json.loads(storage.get("test:2")["data"]).get("rent_index")  # kein Treffer

    # Ältere Treffer ohne Vergleich bekommen ihn beim nächsten Lauf.
    data["rent_index"], data["rent_index_for"] = {}, ""
    storage.update_data("test:1", data)
    run_once(config, storage, [FakeSource([])], [FakeNotifier()])
    assert json.loads(storage.get("test:1")["data"])["rent_index_for"] == VERSION


def test_matches_gone_from_portal_are_marked_offline(tmp_path):
    import mietsuche.main as main_module

    class Checking(FakeSource):
        def __init__(self, listings, online):
            super().__init__(listings)
            self.online = online
            self.checked = []

        def is_online(self, url):
            self.checked.append(url)
            if self.online == "error":
                raise RuntimeError("Portal antwortet nicht")
            return self.online

    (tmp_path / "config.yaml").write_text("datenbank: db.sqlite\n")
    config = load_config(tmp_path / "config.yaml")
    storage = Storage(config.database)
    run_once(config, storage, [Checking([make()], True)], [FakeNotifier()])

    # Noch in der Suche: keine Einzelprüfung nötig.
    source = Checking([make()], False)
    run_once(config, storage, [source], [FakeNotifier()])
    assert source.checked == []

    # Aus der Suche verschwunden, Portal nicht erreichbar: nichts markieren.
    source = Checking([], "error")
    run_once(config, storage, [source], [FakeNotifier()])
    assert source.checked and "offline_seit" not in json.loads(storage.get("test:1")["data"])

    # Gelöscht: als offline merken und nicht ständig neu prüfen.
    source = Checking([], False)
    run_once(config, storage, [source], [FakeNotifier()])
    run_once(config, storage, [source], [FakeNotifier()])
    assert len(source.checked) == 1
    assert json.loads(storage.get("test:1")["data"])["offline_seit"]

    # Taucht wieder auf: Markierung weg.
    run_once(config, storage, [Checking([make()], True)], [FakeNotifier()])
    assert "offline_seit" not in json.loads(storage.get("test:1")["data"])

    # Vor kurzem geprüft: erst nach ein paar Stunden wieder.
    source = Checking([], True)
    run_once(config, storage, [source], [FakeNotifier()])
    assert source.checked == [] and main_module.ONLINE_CHECK_HOURS >= 1


def test_from_data_ignores_extra_fields():
    listing = Listing.from_data({**make().__dict__, "offline_seit": "2026-10-28", "bilder": 3})
    assert listing.key == "test:1"


def test_images_saved_for_matches_and_backfilled(tmp_path, monkeypatch):
    import mietsuche.main as main_module

    saved = []

    def fake_download(base, key, urls, client=None, pause=0.3):
        saved.append((key, list(urls)))
        return len(urls)

    monkeypatch.setattr(main_module.bilder, "download", fake_download)

    class WithImages(FakeSource):
        def details(self, listing):
            listing.images = ["https://img/1", "https://img/2"]
            listing.contact_since = "2026-10-20"
            return listing

    (tmp_path / "config.yaml").write_text("datenbank: db.sqlite\n")
    config = load_config(tmp_path / "config.yaml")
    storage = Storage(config.database)
    run_once(config, storage, [WithImages([make()])], [FakeNotifier()])
    assert saved == [("test:1", ["https://img/1", "https://img/2"])]
    assert json.loads(storage.get("test:1")["data"])["bilder"] == 2

    # Älterer Treffer ohne Bilder: Detailseite wird einmal nachgeladen.
    data = json.loads(storage.get("test:1")["data"])
    del data["bilder"]
    storage.update_data("test:1", data)
    run_once(config, storage, [WithImages([make()])], [FakeNotifier()])
    run_once(config, storage, [WithImages([make()])], [FakeNotifier()])
    data = json.loads(storage.get("test:1")["data"])
    assert len(saved) == 2 and data["bilder"] == 2 and data["contact_since"] == "2026-10-20"


def test_new_portal_address_is_remembered(tmp_path):
    (tmp_path / "config.yaml").write_text("datenbank: db.sqlite\n")
    config = load_config(tmp_path / "config.yaml")
    storage = Storage(config.database)
    run_once(config, storage, [FakeSource([make()])], [FakeNotifier()])
    run_once(config, storage, [FakeSource([make(url="https://example.org/neu")])], [FakeNotifier()])
    row = storage.get("test:1")
    assert row["url"] == json.loads(row["data"])["url"] == "https://example.org/neu"


def test_missing_gallery_is_retried(tmp_path, monkeypatch):
    import mietsuche.main as main_module

    monkeypatch.setattr(main_module.bilder, "download", lambda base, key, urls, **kw: len(urls))

    class Flaky(FakeSource):
        gallery: list = []

        def details(self, listing):
            listing.images = list(self.gallery)
            return listing

    (tmp_path / "config.yaml").write_text("datenbank: db.sqlite\n")
    config = load_config(tmp_path / "config.yaml")
    storage = Storage(config.database)
    source = Flaky([make(image_url="https://img/thumb")])
    run_once(config, storage, [source], [FakeNotifier()])
    data = json.loads(storage.get("test:1")["data"])
    assert "bilder" not in data and data["bilder_versuche"] == 1

    # Nach der Wartezeit klappt es mit der Galerie.
    data["bilder_versucht"] = "2026-01-01T00:00:00+00:00"
    storage.update_data("test:1", data)
    source.gallery = ["https://img/1", "https://img/2", "https://img/3"]
    run_once(config, storage, [source], [FakeNotifier()])
    data = json.loads(storage.get("test:1")["data"])
    assert data["bilder"] == 3 and "bilder_versuche" not in data

    # Früher gespeichert: nur das Vorschaubild → wird erneut geladen.
    data.update(bilder=1, images=["https://img/thumb"])
    storage.update_data("test:1", data)
    run_once(config, storage, [source], [FakeNotifier()])
    assert json.loads(storage.get("test:1")["data"])["bilder"] == 3

    # Nach mehreren Fehlversuchen bleibt es beim Vorschaubild.
    data = json.loads(storage.get("test:1")["data"])
    data.pop("bilder")
    data.update(bilder_versuche=main_module.IMAGE_ATTEMPTS - 1, bilder_versucht="2026-01-01T00:00:00+00:00")
    storage.update_data("test:1", data)
    source.gallery = []
    run_once(config, storage, [source], [FakeNotifier()])
    data = json.loads(storage.get("test:1")["data"])
    assert data["bilder"] == 1 and data["images"] == ["https://img/thumb"]
