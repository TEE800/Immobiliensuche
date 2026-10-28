"""Laden der Konfiguration aus config.yaml."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Criteria:
    property_types: list[str] = field(default_factory=lambda: ["wohnung", "haus"])
    rooms_min: float = 3
    warm_rent_max: float = 2000
    cold_rent_max_if_warm_unknown: float = 1650
    cities: list[str] = field(default_factory=lambda: ["Hamburg", "Schenefeld", "Halstenbek"])
    zip_ranges: list[tuple[int, int]] = field(default_factory=list)
    pets_unknown_ok: bool = True
    balcony_min_sqm: float = 8
    balcony_without_size_ok: bool = True


DAY_NAMES = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


@dataclass
class Arrival:
    """Ankunft am Arbeitsort an bestimmten Wochentagen (0 = Montag)."""
    days: tuple[int, ...]
    clock: str

    @property
    def label(self) -> str:
        days = list(self.days)
        if len(days) > 2 and days == list(range(days[0], days[-1] + 1)):
            return f"{DAY_NAMES[days[0]]}–{DAY_NAMES[days[-1]]}"
        return ", ".join(DAY_NAMES[d] for d in days)


WORKDAYS = (0, 1, 2, 3, 4)


def parse_days(text: str) -> tuple[int, ...]:
    """``"Di-Fr"``, ``"Sa"`` oder ``"Mo, Mi"`` in Wochentage umwandeln."""
    index = {n.lower(): i for i, n in enumerate(DAY_NAMES)}
    days: set[int] = set()
    for part in re.split(r"[,\s]+", text.strip()):
        if not part:
            continue
        ends = re.split(r"[-–]", part)
        first, last = index[ends[0][:2].lower()], index[ends[-1][:2].lower()]
        days.update(range(first, last + 1))
    return tuple(sorted(days))


@dataclass
class Workplace:
    name: str
    address: str
    # Leer: gemeinsame Ankunftszeit (pendeln.ankunft) von Montag bis Freitag.
    arrivals: list[Arrival] = field(default_factory=list)


@dataclass
class Config:
    criteria: Criteria
    telegram_token: str = ""
    telegram_chat_id: str = ""
    letter_file: Path = Path("anschreiben.txt")
    database: Path = Path("data/mietsuche.sqlite")
    interval_minutes: float = 7
    web_password: str = ""
    web_port: int = 8080
    web_host: str = "0.0.0.0"
    ngrok_domain: str = ""
    push_contact: str = ""
    workplaces: list[Workplace] = field(default_factory=list)
    arrival_time: str = "08:00"


def load_config(path: str | Path = "config.yaml") -> Config:
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    base = path.parent

    k = data.get("kriterien", {})
    orte = k.get("orte", {})
    tiere = k.get("haustiere", {})
    aussen = k.get("aussenbereich", {})
    defaults = Criteria()
    criteria = Criteria(
        property_types=[t.lower() for t in k.get("objektarten", defaults.property_types)],
        rooms_min=k.get("zimmer_min", defaults.rooms_min),
        warm_rent_max=k.get("warmmiete_max", defaults.warm_rent_max),
        cold_rent_max_if_warm_unknown=k.get(
            "kaltmiete_max_wenn_warm_unbekannt", defaults.cold_rent_max_if_warm_unknown
        ),
        cities=orte.get("staedte", defaults.cities),
        zip_ranges=[(int(a), int(b)) for a, b in orte.get("plz_bereiche", [])],
        pets_unknown_ok=tiere.get("unbekannt_zulassen", defaults.pets_unknown_ok),
        balcony_min_sqm=aussen.get("balkon_min_qm", defaults.balcony_min_sqm),
        balcony_without_size_ok=aussen.get(
            "balkon_ohne_groesse_zulassen", defaults.balcony_without_size_ok
        ),
    )

    tg = data.get("telegram", {}) or {}
    web = data.get("app", {}) or {}
    pendeln = data.get("pendeln", {}) or {}
    return Config(
        criteria=criteria,
        telegram_token=os.environ.get("TELEGRAM_BOT_TOKEN") or tg.get("bot_token", ""),
        telegram_chat_id=str(os.environ.get("TELEGRAM_CHAT_ID") or tg.get("chat_id", "")),
        letter_file=base / data.get("anschreiben_datei", "anschreiben.txt"),
        database=base / data.get("datenbank", "data/mietsuche.sqlite"),
        interval_minutes=data.get("intervall_minuten", 7),
        web_password=os.environ.get("APP_PASSWORT") or str(web.get("passwort", "")),
        web_port=int(web.get("port", 8080)),
        web_host=str(web.get("host", "0.0.0.0")),
        ngrok_domain=str(web.get("ngrok_domain", "") or ""),
        push_contact=web.get("kontakt_email", ""),
        workplaces=[
            Workplace(str(z.get("name", "")), str(z["adresse"]), _arrivals(z.get("ankunft")))
            for z in pendeln.get("ziele", []) or [] if z.get("adresse")
        ],
        arrival_time=str(pendeln.get("ankunft", "08:00")),
    )


def _arrivals(value) -> list[Arrival]:
    if not value:
        return []
    if isinstance(value, str):
        return [Arrival(WORKDAYS, value)]
    return [Arrival(parse_days(str(a.get("tage", "Mo-Fr"))), str(a["uhrzeit"])) for a in value]
