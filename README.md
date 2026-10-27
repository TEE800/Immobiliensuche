# Immobiliensuche

Sucht regelmäßig Mietwohnungen und Häuser in Hamburg, Schenefeld und
Halstenbek, prüft sie gegen die eigenen Kriterien und meldet passende
Treffer per Push-Nachricht aufs Handy. Die Übersichts-App zeigt jeden
Treffer mit fertig ausgefülltem Anschreiben zum Kopieren. Abgeschickt wird
die Anfrage von Hand.

## Stand

| Schritt | Inhalt | Stand |
|---|---|---|
| 1 | Kriterien, Filter, Datenbank, Anschreiben | fertig |
| 1 | Portale (ImmoScout24, Immowelt, Kleinanzeigen, Ohne-Makler, SAGA, Genossenschaften) | offen |
| 2 | Push-Nachrichten (App, optional Telegram) | fertig |
| 3 | Übersichts-App | fertig |
| 4 | Dauerbetrieb auf eigenem Server (Docker + Caddy) | vorbereitet |

## Server (Hetzner)

1. In der [Hetzner Cloud Console](https://console.hetzner.cloud) einen
   Server anlegen: Ubuntu 24.04, kleinstes Modell mit 4 GB RAM.
2. Beim Anlegen unter **Cloud config** den Inhalt von
   [`deploy/cloud-config.yaml`](deploy/cloud-config.yaml) einfügen.
3. Nach ca. 5 Minuten `https://<IP-mit-Bindestrichen>.sslip.io` öffnen,
   z. B. `https://49-12-34-56.sslip.io`, und ein Passwort festlegen.

Das Skript [`deploy/setup.sh`](deploy/setup.sh) installiert Docker, eine
Firewall und Caddy (holt automatisch HTTPS-Zertifikate) und startet die
App. Jede Nacht holt es neuen Code aus diesem Repository. Weitere Apps
bekommen in `/opt/server/docker-compose.yml` einen eigenen Dienst und im
`Caddyfile` eine eigene Adresse.

Auf dem Server liegen Konfiguration, Anschreiben und Datenbank unter
`/opt/server/data/immobiliensuche/`. Das Anschreiben lässt sich direkt in
der App unter „✎ Vorlage“ bearbeiten.

## Lokal einrichten

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
cp anschreiben.example.txt anschreiben.txt
```

`config.yaml` und `anschreiben.txt` anpassen. Beide Dateien bleiben lokal
und landen nicht im Repository.

### Übersichts-App und Push

Beim ersten Aufruf legt man in der App ein Passwort fest (alternativ
`app.passwort` in `config.yaml`). Starten mit:

```bash
python -m mietsuche --serve
```

Das startet die App auf Port 8080 und sucht im Hintergrund im eingestellten
Intervall. Push-Nachrichten brauchen HTTPS, die App gehört also hinter
einen HTTPS-Proxy (z. B. Caddy) mit eigener Domain. Auf dem Handy die
Seite in Chrome öffnen, anmelden, „Push aktivieren“ tippen und über das
Menü „Zum Startbildschirm hinzufügen“.

Ein Tipp auf die Push-Nachricht öffnet den Treffer. Dort: Anschreiben
kopieren, Anzeige öffnen, abschicken, als „angefragt“ markieren. Den
Status (Antwort, Besichtigung, Zusage, Absage) und Notizen pflegt man in
der App.

### Telegram-Bot (optional)

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
python -m mietsuche --loop      # dauerhaft, alle ~7 Minuten, ohne App
python -m mietsuche --serve     # App + dauerhafte Suche
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
`{adresse}`, `{ort}`, `{zimmer}`, `{flaeche}`, `{portal}`, `{objektnr}`,
`{ihr_objekt}` („Ihre Wohnung“ bzw. „Ihr Haus“). Die Anrede wird
aus dem Kontaktnamen gebildet („Sehr geehrte Frau Müller,“), sonst
„Sehr geehrte Damen und Herren,“.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```
