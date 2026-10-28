"""Hinweise auf mögliche Fake-Anzeigen.

Prüft typische Merkmale von Betrugsanzeigen: Geld oder Schlüssel per Post vor
der Besichtigung, Vermieter angeblich im Ausland, Kontakt außerhalb des Portals,
ein Preis weit unter vergleichbaren Wohnungen oder ein ganz neues Konto. Das
sind Warnzeichen, kein Beweis: echte Anzeigen können auffallen, gut gemachte
Fakes nicht.
"""

from __future__ import annotations

import re
from datetime import date, datetime

_TEXT = [
    (re.compile(r"schl[üu]ssel\W+(?:\w+\W+){0,6}?(?:per post|zu ?ge?schickt|zusenden|versand|versendet|"
                r"verschick|schicken)", re.I),
     "Schlüssel sollen per Post kommen"),
    (re.compile(r"(?:kaution|miete|anzahlung|reservierung|geld|betrag)\W+(?:\w+\W+){0,8}?"
                r"vor (?:der )?besichtigung", re.I),
     "Geld soll vor der Besichtigung fließen"),
    (re.compile(r"western union|moneygram|paysafe|bitcoin|airbnb", re.I),
     "Ungewöhnlicher Zahlungsweg (z. B. Airbnb, Western Union)"),
    (re.compile(r"\b(?:bin|befinde mich|lebe|arbeite|wohne|sind|leben|arbeiten)\b\W+(?:\w+\W+){0,6}?"
                r"(?:im ausland|in (?:england|london|spanien|frankreich|italien|den usa|amerika|schottland|"
                r"irland|norwegen|dänemark|schweden|kanada|dubai|nigeria))", re.I),
     "Vermieter angeblich im Ausland"),
    (re.compile(r"keine besichtigung|besichtigung\W+(?:\w+\W+){0,3}?nicht möglich", re.I),
     "Keine Besichtigung möglich"),
    (re.compile(r"whatsapp|[\w.+-]+@[\w-]+\.[a-z]{2,}", re.I),
     "Will Kontakt über WhatsApp oder E-Mail statt über das Portal"),
]
_ENGLISH = re.compile(r"\b(?:the|and|apartment|flat|deposit|rent|you|please|available|bedroom)\b", re.I)

# Ab so viel Prozent unter dem Vergleich wird der Preis auffällig.
PRICE_DROP = 35
NEW_ACCOUNT_DAYS = 30


def warnings(data: dict, market: dict | None = None, first_seen: str | None = None) -> list[str]:
    """Liste der Auffälligkeiten einer gespeicherten Anzeige (leer, wenn nichts auffällt)."""
    text = f"{data.get('title', '')}\n{data.get('description', '')}"
    found = [message for pattern, message in _TEXT if pattern.search(text)]
    if len(_ENGLISH.findall(text)) >= 8:
        found.append("Anzeigentext auf Englisch")

    rent = data.get("rent_index") or {}
    if rent.get("art") == "angebote" and rent.get("abweichung", 0) <= -PRICE_DROP:
        found.append(f"Miete {-rent['abweichung']} % unter dem Durchschnitt im Ort")
    elif rent.get("spanne_von") and rent.get("qm", 99) < rent["spanne_von"] * (1 - PRICE_DROP / 100):
        found.append("Miete weit unter dem Mietenspiegel")
    elif market and market.get("abweichung") is not None and market["abweichung"] <= -PRICE_DROP:
        found.append(f"Miete {-market['abweichung']} % unter vergleichbaren Anzeigen")

    since = data.get("contact_since")
    if since:
        seen = datetime.fromisoformat(first_seen).date() if first_seen else date.today()
        start = date.fromisoformat(since)
        if (seen - start).days <= NEW_ACCOUNT_DAYS:
            found.append(f"Konto des Anbieters erst seit {start:%d.%m.%Y}")
    if data.get("bilder") == 0 and data.get("images") == []:
        found.append("Keine Fotos")
    return found
