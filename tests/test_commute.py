from datetime import datetime

import httpx

from mietsuche.commute import BERLIN, Transitous, commute_times, next_workday_at
from mietsuche.config import Workplace, load_config
from mietsuche.models import Listing
from mietsuche.webpush import push_payload

PLACES = {
    "Musterweg 1, 22765 Hamburg": (53.55, 9.93),
    "Arbeit 1, 20095 Hamburg": (53.55, 10.0),
    "Ottensen 22765 Hamburg": (53.556, 9.92),
}


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/geocode"):
        hit = PLACES.get(request.url.params["text"])
        return httpx.Response(200, json=[{"lat": hit[0], "lon": hit[1]}] if hit else [])
    assert request.url.params["arriveBy"] == "true"
    return httpx.Response(200, json={
        "itineraries": [
            {"duration": 1500, "transfers": 1, "legs": [
                {"mode": "WALK"}, {"mode": "BUS", "routeShortName": "2"},
                {"mode": "SUBWAY", "routeShortName": "U3"}, {"mode": "WALK"}]},
            {"duration": 1800, "transfers": 0, "legs": [{"mode": "SUBURBAN", "routeShortName": "S1"}]},
        ],
        "direct": [{"duration": 4000, "legs": [{"mode": "WALK"}]}],
    })


def service():
    return Transitous(httpx.Client(transport=httpx.MockTransport(handler)))


def listing(**kw):
    base = dict(source="test", external_id="1", url="u", title="t", street="Musterweg 1",
                zip_code="22765", city="Hamburg", district="Ottensen")
    base.update(kw)
    return Listing(**base)


WORK = [Workplace("Kevin", "Arbeit 1, 20095 Hamburg"), Workplace("Fritzi", "Unbekannt 9")]


def test_fastest_connection_per_workplace():
    result = commute_times(listing(), WORK, "08:00", service())
    assert result == [{"name": "Kevin", "minuten": 25, "umstiege": 1, "linien": "Bus 2, U3", "ungefaehr": False}]


def test_without_street_uses_district_and_marks_approximate():
    result = commute_times(listing(street=None), WORK[:1], "08:00", service())
    assert result[0]["ungefaehr"] is True
    assert commute_times(listing(street=None, district="Nirgendwo"), WORK[:1], "08:00", service()) == []


def test_next_workday_skips_weekend():
    friday = datetime(2026, 10, 30, 12, 0, tzinfo=BERLIN)
    arrive = next_workday_at("08:00", friday)
    assert arrive.astimezone(BERLIN).strftime("%a %H:%M") == "Mon 08:00"
    assert arrive.utcoffset().total_seconds() == 0


def test_config_and_push_text(tmp_path):
    (tmp_path / "config.yaml").write_text(
        'pendeln:\n  ankunft: "07:30"\n  ziele:\n    - name: Fritzi\n      adresse: "Arbeit 1, 20095 Hamburg"\n'
    )
    config = load_config(tmp_path / "config.yaml")
    assert config.workplaces == [Workplace("Fritzi", "Arbeit 1, 20095 Hamburg")]
    assert config.arrival_time == "07:30"
    row = {"rooms": 3, "rent_warm": 1800, "rent_cold": None, "address": "Musterweg 1", "title": "t",
           "image_url": None, "key": "k", "data": '{"commute": [{"name": "Fritzi", "minuten": 18}]}'}
    assert push_payload(row)["body"].endswith("🚆 Fritzi 18 Min.")
