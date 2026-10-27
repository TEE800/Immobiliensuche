"""Ein Suchlauf: Portale abfragen, filtern, speichern, benachrichtigen."""

from __future__ import annotations

import argparse
import logging
import random
import time

from .config import Config, load_config
from .filters import evaluate
from .letter import load_template, render_letter
from .sources import REGISTRY, Source
from .storage import Storage
from .telegram import Telegram, format_caption

log = logging.getLogger("mietsuche")


def run_once(config: Config, storage: Storage, sources: list[Source], notifier: Telegram | None) -> int:
    """Führt einen Suchlauf aus und gibt die Zahl neuer Treffer zurück."""
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
            verdict = evaluate(listing, config.criteria)
            letter = render_letter(template, listing) if template and verdict.matches else None
            storage.add(listing, verdict, letter)
            if verdict.matches:
                new_matches += 1
                log.info("Treffer: %s %s", listing.title, listing.url)

    for row in storage.pending_notifications():
        if notifier is None:
            print("\n" + format_caption(row) + f"\n{row['url']}\n")
            if row["letter"]:
                print(row["letter"])
            storage.mark_notified(row["key"])
            continue
        try:
            notifier.notify(row)
            storage.mark_notified(row["key"])
        except Exception:
            # Bleibt offen und wird beim nächsten Lauf erneut versucht.
            log.exception("Benachrichtigung für %s fehlgeschlagen", row["key"])
    return new_matches


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="mietsuche", description="Mietwohnungssuche Hamburg")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--loop", action="store_true", help="dauerhaft im Intervall suchen")
    parser.add_argument("--dry-run", action="store_true", help="Treffer nur ausgeben, kein Telegram")
    parser.add_argument("--portale", help="nur diese Portale, kommagetrennt")
    parser.add_argument("--test-telegram", action="store_true", help="Testnachricht senden")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config(args.config)

    if args.test_telegram:
        Telegram(config.telegram_token, config.telegram_chat_id).send_text(
            "Mietsuche ist verbunden. Treffer kommen ab jetzt hier an."
        )
        return

    names = args.portale.split(",") if args.portale else list(REGISTRY)
    sources = [REGISTRY[n.strip()]() for n in names]
    if not sources:
        log.warning("Noch keine Portale eingerichtet.")
    notifier = None if args.dry_run else Telegram(config.telegram_token, config.telegram_chat_id)
    storage = Storage(config.database)

    while True:
        found = run_once(config, storage, sources, notifier)
        log.info("Suchlauf fertig, %d neue Treffer", found)
        if not args.loop:
            break
        # Etwas Zufall im Abstand, damit die Abfragen nicht im festen Takt kommen.
        pause = config.interval_minutes * 60 * random.uniform(0.8, 1.2)
        time.sleep(pause)
