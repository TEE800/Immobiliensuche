"""Ein Suchlauf: Portale abfragen, filtern, speichern, benachrichtigen."""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import threading
import time

from .commute import Transitous, commute_times
from .config import Config, load_config
from .filters import evaluate, worth_details
from .letter import load_template, render_letter
from .mietenspiegel import VERSION as RENT_INDEX_VERSION, Wohnlagen, compare as compare_rent
from .models import Listing
from .sources import REGISTRY, Source
from .storage import Storage
from .telegram import Telegram, format_caption

log = logging.getLogger("mietsuche")


def _commute_key(config: Config) -> str:
    places = ";".join(
        f"{w.name}={w.address}" + "".join(f"@{a.label} {a.clock}" for a in w.arrivals)
        for w in config.workplaces
    )
    # v4: bequemere Verbindungen (kurze Fußwege, passende Ankunft); ältere Treffer werden neu gerechnet.
    return f"v4|{config.arrival_time}|{places}"


def _update_commutes(config: Config, storage: Storage, transit) -> None:
    """Fahrzeiten für ältere Treffer nachtragen oder nach geänderten Arbeitsorten neu rechnen."""
    key = _commute_key(config)
    for row in storage.matches():
        if row["status"] in ("abgesagt", "uninteressant"):
            continue
        data = json.loads(row["data"])
        if data.get("commute_for") == key:
            continue
        try:
            data["commute"] = commute_times(Listing(**data), config.workplaces, config.arrival_time, transit)
        except Exception as e:
            log.warning("Fahrzeit für %s nicht berechnet: %s", row["url"], e)
            return  # Dienst gerade nicht erreichbar, beim nächsten Lauf weiter.
        data["commute_for"] = key
        storage.update_data(row["key"], data)


def _rent_index(listing: Listing, lagen: Wohnlagen) -> dict:
    lage = None
    if listing.city == "Hamburg":
        try:
            lage = lagen.lookup(listing.street, listing.zip_code)
        except Exception as e:
            # Ohne Wohnlage geht der Vergleich trotzdem, nur mit breiterer Spanne.
            log.warning("Wohnlage für %s nicht gefunden: %s", listing.url, e)
    return compare_rent(listing, lage)


def _update_rent_index(storage: Storage, lagen: Wohnlagen) -> None:
    """Mietenspiegel-Vergleich für ältere Treffer nachtragen."""
    for row in storage.matches():
        if row["status"] in ("abgesagt", "uninteressant"):
            continue
        data = json.loads(row["data"])
        if data.get("rent_index_for") == RENT_INDEX_VERSION:
            continue
        data["rent_index"] = _rent_index(Listing(**data), lagen)
        data["rent_index_for"] = RENT_INDEX_VERSION
        storage.update_data(row["key"], data)


def run_once(config: Config, storage: Storage, sources: list[Source], notifiers: list,
             transit=None, lagen: Wohnlagen | None = None) -> int:
    """Führt einen Suchlauf aus und gibt die Zahl neuer Treffer zurück.

    Ohne ``notifiers`` werden Treffer nur im Terminal ausgegeben.
    """
    template = load_template(config.letter_file)
    if config.workplaces and transit is None:
        transit = Transitous()
    lagen = lagen or Wohnlagen()
    new_matches = 0
    for source in sources:
        try:
            listings = source.search(config.criteria)
        except Exception:
            # Ein Portal, das gerade nicht geht, soll die anderen nicht aufhalten.
            log.exception("Suche bei %s fehlgeschlagen", source.name)
            continue
        log.info("%s: %d Anzeigen", source.name, len(listings))
        for listing in listings:
            if storage.seen(listing.key):
                continue
            if worth_details(listing, config.criteria):
                try:
                    listing = source.details(listing)
                except Exception as e:
                    # Nicht speichern, damit es beim nächsten Lauf erneut versucht wird.
                    log.warning("%s: Detailseite %s nicht lesbar: %s", source.name, listing.url, e)
                    continue
            verdict = evaluate(listing, config.criteria)
            if verdict.matches and config.workplaces:
                try:
                    listing.commute = commute_times(listing, config.workplaces, config.arrival_time, transit)
                    listing.commute_for = _commute_key(config)
                except Exception as e:
                    # Ohne Fahrzeit lieber trotzdem melden als den Treffer zurückhalten.
                    log.warning("Fahrzeit für %s nicht berechnet: %s", listing.url, e)
            if verdict.matches:
                listing.rent_index = _rent_index(listing, lagen)
                listing.rent_index_for = RENT_INDEX_VERSION
            letter = render_letter(template, listing) if template and verdict.matches else None
            storage.add(listing, verdict, letter)
            if verdict.matches:
                new_matches += 1
                log.info("Treffer: %s %s", listing.title, listing.url)

    if config.workplaces:
        _update_commutes(config, storage, transit)
    _update_rent_index(storage, lagen)

    for row in storage.pending_notifications():
        if not notifiers:
            print("\n" + format_caption(row) + f"\n{row['url']}\n")
            if row["letter"]:
                print(row["letter"])
            storage.mark_notified(row["key"])
            continue
        delivered = False
        for notifier in notifiers:
            try:
                notifier.notify(row)
                delivered = True
            except Exception as e:
                log.warning("%s: Benachrichtigung für %s fehlgeschlagen: %s",
                            type(notifier).__name__, row["key"], e)
        # Wenn kein Weg geklappt hat, beim nächsten Lauf erneut versuchen.
        if delivered:
            storage.mark_notified(row["key"])
    return new_matches


def search_loop(config: Config, sources: list[Source], notifiers_factory, once: bool = False) -> None:
    storage = Storage(config.database)
    notifiers = notifiers_factory(storage)
    while True:
        found = run_once(config, storage, sources, notifiers)
        log.info("Suchlauf fertig, %d neue Treffer", found)
        if once:
            return
        # Etwas Zufall im Abstand, damit die Abfragen nicht im festen Takt kommen.
        time.sleep(config.interval_minutes * 60 * random.uniform(0.8, 1.2))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="mietsuche", description="Mietwohnungssuche Hamburg")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--serve", action="store_true",
                        help="Übersichts-App starten und dauerhaft im Hintergrund suchen")
    parser.add_argument("--loop", action="store_true", help="dauerhaft im Intervall suchen, ohne App")
    parser.add_argument("--dry-run", action="store_true", help="Treffer nur ausgeben, keine Nachrichten")
    parser.add_argument("--portale", help="nur diese Portale, kommagetrennt")
    parser.add_argument("--test-telegram", action="store_true", help="Telegram-Testnachricht senden")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    if sys.stderr is None:
        # Unter Windows mit pythonw (Autostart) gibt es keine Konsole.
        config.database.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(config.database.parent / "mietsuche.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log_file
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.test_telegram:
        Telegram(config.telegram_token, config.telegram_chat_id).send_text(
            "Mietsuche ist verbunden. Treffer kommen ab jetzt hier an."
        )
        return

    names = args.portale.split(",") if args.portale else list(REGISTRY)
    sources = [REGISTRY[n.strip()]() for n in names]
    if not sources:
        log.warning("Noch keine Portale eingerichtet.")

    def notifiers_factory(storage: Storage) -> list:
        if args.dry_run:
            return []
        notifiers: list = []
        if config.telegram_token and config.telegram_chat_id:
            notifiers.append(Telegram(config.telegram_token, config.telegram_chat_id))
        if args.serve:
            from .webpush import WebPush, ensure_vapid_key

            key, _ = ensure_vapid_key(config.database.parent)
            notifiers.append(WebPush(storage, key, config.push_contact))
        return notifiers

    if args.serve:
        import uvicorn

        from .web import create_app

        threading.Thread(
            target=search_loop, args=(config, sources, notifiers_factory), daemon=True
        ).start()
        if config.ngrok_domain:
            from .tunnel import start_ngrok

            start_ngrok(config.ngrok_domain, config.web_port)
        uvicorn.run(create_app(config), host=config.web_host, port=config.web_port)
        return

    search_loop(config, sources, notifiers_factory, once=not args.loop)
