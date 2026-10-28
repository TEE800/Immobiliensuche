import json
from datetime import datetime, timezone

from mietsuche.vergleich import compare, offers

NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)


def row(key, qm, area=80, district="Ottensen", title="Wohnung", seen="2026-10-01", ptype="wohnung"):
    data = {"city": "Hamburg", "district": district, "property_type": ptype}
    return {"key": key, "title": title, "data": json.dumps(data), "rent_cold": qm * area,
            "living_area": area, "first_seen": seen}


def test_median_of_similar_flats_in_district():
    rows = [row(f"k{i}", qm) for i, qm in enumerate([14, 15, 16, 17, 18])]
    rows += [row("old", 30, seen="2025-01-01"), row("wg", 30, title="WG-Zimmer möbliert"),
             row("tiny", 30, area=12), row("haus", 30, ptype="haus"), row("big", 30, area=200)]
    pool = offers(rows, NOW)
    assert {o["key"] for o in pool} == {"k0", "k1", "k2", "k3", "k4", "big"}
    me = {"key": "me", "city": "Hamburg", "district": "Ottensen", "property_type": "wohnung",
          "rent_cold": 1360, "living_area": 80}
    result = compare(me, pool)
    assert (result["median"], result["anzahl"], result["gruppe"]) == (16, 5, "Ottensen, ähnliche Größe")
    assert result["abweichung"] == 6 and result["wenige"]


def test_falls_back_to_city_and_reports_too_few():
    pool = offers([row(f"k{i}", 15, district="Altona") for i in range(5)], NOW)
    me = {"key": "me", "city": "Hamburg", "district": "Ottensen", "property_type": "wohnung",
          "rent_cold": 1200, "living_area": 80}
    assert compare(me, pool)["gruppe"] == "ganz Hamburg, ähnliche Größe"
    assert compare(me, pool[:2])["zu_wenige"]
    assert compare({**me, "property_type": "haus"}, pool) == {}
