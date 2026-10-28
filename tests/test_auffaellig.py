import pytest

from mietsuche.auffaellig import warnings


@pytest.mark.parametrize("text, expected", [
    ("Die Schlüssel schicke ich Ihnen per Post zu.", "Schlüssel"),
    ("Bitte die Kaution vor der Besichtigung überweisen.", "Geld"),
    ("Die Abwicklung erfolgt über Airbnb.", "Zahlungsweg"),
    ("Ich arbeite zurzeit in London und kann nicht vorbeikommen.", "Ausland"),
    ("Eine Besichtigung ist leider nicht möglich.", "Besichtigung"),
    ("Schreiben Sie mir an max.muster@example.com", "WhatsApp"),
])
def test_text_warnings(text, expected):
    found = warnings({"title": "Wohnung", "description": text})
    assert any(expected in w for w in found), found


def test_normal_listing_is_quiet():
    text = ("Helle 3-Zimmer-Wohnung mit Balkon. Die Miete ist monatlich im Voraus zu zahlen, "
            "Kaution drei Monatsmieten vor Schlüsselübergabe. Besichtigung nach Absprache.")
    assert warnings({"title": "Wohnung", "description": text, "bilder": 5}) == []


def test_english_text():
    text = ("Hello, the apartment is available now. The rent includes all costs and you can move in. "
            "Please send me a message and the deposit details.")
    assert "Anzeigentext auf Englisch" in warnings({"description": text})


def test_price_and_new_account():
    data = {"rent_index": {"qm": 7.0, "spanne_von": 12.0}, "contact_since": "2026-10-20"}
    found = warnings(data, first_seen="2026-10-28T10:00:00+00:00")
    assert "Miete weit unter dem Mietenspiegel" in found
    assert "Konto des Anbieters erst seit 20.10.2026" in found
    assert warnings({"contact_since": "2023-12-21"}, first_seen="2026-10-28T10:00:00+00:00") == []
    assert warnings({}, {"abweichung": -40}) == ["Miete 40 % unter vergleichbaren Anzeigen"]
    assert warnings({"rent_index": {"art": "angebote", "abweichung": -50}}) == [
        "Miete 50 % unter dem Durchschnitt im Ort"]
    assert warnings({"bilder": 0, "images": []}) == ["Keine Fotos"]
