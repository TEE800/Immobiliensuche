"""Ein Suchlauf: Portale abfragen, filtern, speichern, benachrichtigen."""

from __future__ import annotations

import argparse
import logging
import random
import sys
import threading
import time

from .config import Config, load_config
from .filters import evaluate, worth_details
from .letter import load_template, render_letter
from .sources import REGISTRY, Source
from .storage import Storage
from .telegram import Telegram, format_caption

log = logging.getLogger("mietsuche")


def run_once(config: Config, storage: Storage, sources: list[Source], notifiers: list) -> int:
    """Führt einen Suchlauf aus und gibt die Zahl neuer Treffer zurück.

    Ohne ``notifiers`` werden Treffer nur im Terminal ausgegeben.
    """
    template = load_template(config.letter_file)
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
            letter = render_letter(template, listing) if template and verdict.matches else None
            storage.add(listing, verdict, letter)
            if verdict.matches:
                new_matches += 1
                log.info("Treffer: %s %s", listing.title, listing.url)

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
