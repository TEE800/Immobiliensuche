"""Benachrichtigung über einen Telegram-Bot."""

from __future__ import annotations

import html
import json

import httpx

from .filters import NEIN, UNKLAR

_API = "https://api.telegram.org/bot{token}/{method}"
_ICON = {"ja": "✅", UNKLAR: "❓", NEIN: "❌"}
_LABEL = {
    "objektart": "Objekt",
    "ort": "Ort",
    "zimmer": "Zimmer",
    "miete": "Miete",
    "haustiere": "Haustiere",
    "aussenbereich": "Garten/Balkon",
}


def format_caption(row) -> str:
    """Kurzer Text zum Foto (Telegram erlaubt höchstens 1024 Zeichen)."""
    checks = json.loads(row["checks"])
    lines = [f"<b>{html.escape(row['title'][:200])}</b>"]
    if row["address"]:
        lines.append(f"📍 {html.escape(row['address'])}")
    facts = []
    if row["rooms"]:
        facts.append(f"{row['rooms']:g} Zi.")
    if row["living_area"]:
        facts.append(f"{row['living_area']:g} m²")
    if row["rent_warm"]:
        facts.append(f"{row['rent_warm']:.0f} € warm")
    elif row["rent_cold"]:
        facts.append(f"{row['rent_cold']:.0f} € kalt")
    if facts:
        lines.append(" · ".join(facts))
    for name in ("haustiere", "aussenbereich"):
        c = checks.get(name)
        if c:
            lines.append(f"{_ICON[c['ergebnis']]} {_LABEL[name]}: {html.escape(c['hinweis'])}")
    others = [
        f"{_LABEL.get(n, n)}: {html.escape(c['hinweis'])}"
        for n, c in checks.items()
        if c["ergebnis"] == UNKLAR and n not in ("haustiere", "aussenbereich")
    ]
    if others:
        lines.append("❓ Unklar: " + "; ".join(others))
    return "\n".join(lines)[:1024]


class Telegram:
    def __init__(self, token: str, chat_id: str, client: httpx.Client | None = None):
        if not token or not chat_id:
            raise ValueError("Telegram bot_token und chat_id müssen gesetzt sein.")
        self.token = token
        self.chat_id = chat_id
        self.client = client or httpx.Client(timeout=30)

    def _call(self, method: str, **payload) -> dict:
        r = self.client.post(_API.format(token=self.token, method=method), json=payload)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} fehlgeschlagen: {data.get('description')}")
        return data["result"]

    def send_text(self, text: str) -> None:
        self._call("sendMessage", chat_id=self.chat_id, text=text, parse_mode="HTML")

    def notify(self, row) -> None:
        """Schickt Foto mit Eckdaten und danach das fertige Anschreiben."""
        caption = format_caption(row)
        buttons = {"inline_keyboard": [[{"text": "Anzeige öffnen", "url": row["url"]}]]}
        sent = False
        if row["image_url"]:
            try:
                self._call(
                    "sendPhoto", chat_id=self.chat_id, photo=row["image_url"],
                    caption=caption, parse_mode="HTML", reply_markup=buttons,
                )
                sent = True
            except RuntimeError:
                # Manche Bild-URLs lädt Telegram nicht, dann ohne Foto.
                pass
        if not sent:
            self._call(
                "sendMessage", chat_id=self.chat_id, text=caption, parse_mode="HTML",
                reply_markup=buttons, disable_web_page_preview=True,
            )
        if row["letter"]:
            # Ein Tipp auf den Codeblock kopiert den Text in die Zwischenablage.
            self._call(
                "sendMessage", chat_id=self.chat_id,
                text=f"<pre>{html.escape(row['letter'][:3900])}</pre>", parse_mode="HTML",
            )
