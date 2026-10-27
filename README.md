# Immobiliensuche

Sucht regelmäßig Mietwohnungen und Häuser in Hamburg, Schenefeld und
Halstenbek, prüft sie gegen die eigenen Kriterien und schickt passende
Treffer per Telegram – zusammen mit einem fertig ausgefüllten Anschreiben
zum Kopieren. Abgeschickt wird die Anfrage von Hand.

## Stand

| Schritt | Inhalt | Stand |
|---|---|---|
| 1 | Kriterien, Filter, Datenbank, Telegram, Anschreiben | fertig |
| 1 | Portale (ImmoScout24, Immowelt, Kleinanzeigen, Ohne-Makler, SAGA, Genossenschaften) | offen |
| 3 | Übersichts-App | offen |
| 4 | Dauerbetrieb auf Server/Raspberry Pi | offen |

## Einrichten

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
cp anschreiben.example.txt anschreiben.txt
```

`config.yaml` und `anschreiben.txt` anpassen. Beide Dateien bleiben lokal
und landen nicht im Repository.

### Telegram-Bot

1. In Telegram `@BotFather` öffnen, `/newbot` senden und den Token kopieren.
2. Dem neuen Bot eine beliebige Nachricht schreiben.
3. `https://api.telegram.org/bot<TOKEN>/getUpdates` im Browser öffnen und
   dort `"chat":{"id": ...}` ablesen.
4. Token und Chat-ID in `config.yaml` eintragen und testen:
   `python -m mietsuche --test-telegram`

## Benutzen

```bash
python -m mietsuche --dry-run   # ein Lauf, Treffer nur im Terminal
python -m mietsuche             # ein Lauf mit Telegram
python -m mietsuche --loop      # dauerhaft, alle ~7 Minuten
```

## Kriterien

Jede Anzeige wird in sechs Punkten geprüft: Objektart, Ort, Zimmer, Miete,
Haustiere und Garten/Balkon. Jeder Punkt ist `ja`, `unklar` oder `nein`.
Ein `nein` sortiert die Anzeige aus, `unklar` erscheint in der Nachricht
mit ❓, damit man gezielt nachfragen kann.

- **Haustiere:** strukturierte Angabe des Portals, sonst Anzeigentext
  („nach Absprache“, „keine Haustiere“ …). Ohne Angabe: siehe
  `haustiere.unbekannt_zulassen`.
- **Garten/Balkon:** eigener Garten, Gartenanteil, Terrasse, Dachterrasse
  oder Balkon ab `balkon_min_qm`. Gemeinschaftsgärten zählen nicht.
- **Miete:** Warmmiete bis `warmmiete_max`. Ist nur die Kaltmiete bekannt,
  zählt `kaltmiete_max_wenn_warm_unbekannt`.

## Anschreiben

Platzhalter in `anschreiben.txt`: `{anrede}`, `{name}`, `{titel}`,
`{adresse}`, `{ort}`, `{zimmer}`, `{flaeche}`, `{portal}`. Die Anrede wird
aus dem Kontaktnamen gebildet („Sehr geehrte Frau Müller,“), sonst
„Sehr geehrte Damen und Herren,“.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```
