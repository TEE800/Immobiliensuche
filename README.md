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
| 1 | Portale Kleinanzeigen und Ohne-Makler | fertig |
| 1 | ImmoScout24, Immowelt, SAGA, Genossenschaften | offen, siehe unten |
| 2 | Push-Nachrichten (App, optional Telegram) | fertig |
| 3 | Übersichts-App | fertig |
| 4 | Dauerbetrieb auf Windows-Laptop oder eigenem Server (Docker + Caddy) | vorbereitet |

## Windows-Laptop

In PowerShell (Startmenü → „PowerShell“) einfügen:

```powershell
irm https://raw.githubusercontent.com/TEE800/Immobiliensuche/main/deploy/windows/install.ps1 | iex
```

Das installiert bei Bedarf Python, legt alles unter
`%LOCALAPPDATA%\Mietsuche` ab, startet die Suche bei jeder Anmeldung
automatisch im Hintergrund und schaltet den Ruhezustand im Netzbetrieb ab.
Beim ersten Mal fragt das Skript nach Authtoken und fester Domain von
[ngrok](https://dashboard.ngrok.com) (kostenloses Konto). Darüber ist die
Übersicht auch unterwegs auf dem Handy erreichbar, inklusive
Push-Nachrichten. Am Laptop selbst läuft sie unter http://localhost:8080.
ngrok zeigt bei kostenlosen Konten beim ersten Aufruf eine Warnseite, die
man einmal mit „Visit Site“ bestätigt. Erneut ausführen
aktualisiert auf die neueste Version; Einstellungen und Anschreiben in
`%LOCALAPPDATA%\Mietsuche\data` bleiben erhalten.

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

## Portale

| Portal | Stand |
|---|---|
| Kleinanzeigen | Hamburg, Schenefeld, Halstenbek; Wohnungen und Häuser ab Mindest-Zimmerzahl. Gesuche und Tauschwohnungen werden übersprungen. |
| Ohne-Makler | Hamburg, Schenefeld, Halstenbek; Wohnungen und Häuser |
| ImmoScout24, Immowelt | sperren automatische Abrufe (Bot-Schutz), deshalb nicht angebunden |
| SAGA | Seite ist von Servern außerhalb Deutschlands gesperrt, noch nicht getestet |

Zuerst wird die Trefferliste gelesen. Nur Anzeigen, die danach noch passen
können (Ort, Zimmer, grobe Miete), werden einzeln geöffnet, um Nebenkosten,
Haustiere, Garten und Balkon zu lesen. Zwischen zwei Abrufen liegen einige
Sekunden Pause. Neue Portale sind Unterklassen von `mietsuche.sources.Source`
mit `@register` und optional `details()`.

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
  „Garten/-mitnutzung“ (Kleinanzeigen) oder „Garten“ in der Ausstattung
  einer Wohnung (Ohne-Makler) ist `unklar`, weil das auch der Hof für alle
  sein kann.
- **Miete:** Warmmiete bis `warmmiete_max`. Ist nur die Kaltmiete bekannt,
  zählt `kaltmiete_max_wenn_warm_unbekannt`.

## Fahrzeit zur Arbeit

Unter `pendeln.ziele` in `config.yaml` stehen Arbeitsadressen. Für jeden
Treffer rechnet die Mietsuche dann über [Transitous](https://transitous.org)
(kostenlos, ohne Konto) die schnellste Verbindung mit Bus und Bahn zur
Ankunftszeit `pendeln.ankunft` am nächsten Werktag aus und zeigt sie in der
App und in der Push-Nachricht. Ein Ziel kann unter `ankunft` eigene Tage und
Uhrzeiten haben (siehe `config.example.yaml`), etwa Di–Fr 9:30 und Sa 8:30;
dann gibt es für jeden Eintrag eine eigene Fahrzeit. Gesendet werden die Adresse der Anzeige und
die Koordinaten der Arbeitsorte, keine Namen. Nennt eine Anzeige keine
Straße, wird ab dem Stadtteil gerechnet und das Ergebnis als „ungefähr“
markiert.

## Mietenspiegel

Für Treffer in Hamburg vergleicht die Mietsuche die Kaltmiete je m² mit dem
Hamburger Mietenspiegel 2025 (Tabelle in `mietsuche/tabellen/mietenspiegel_2025.json`,
aus dem Geodienst der Stadt). Die Wohnlage „normal“ oder „gut“ kommt aus dem
Wohnlagenverzeichnis; dafür werden Straße und PLZ der Anzeige an
geodienste.hamburg.de geschickt. Das Baujahr steht bei Ohne-Makler in den
Angaben, bei Kleinanzeigen nur manchmal im Text. Fehlt Baujahr oder Wohnlage,
zeigt die App die Spanne über alle passenden Felder. Für Häuser sowie
Schenefeld und Halstenbek gilt der Hamburger Mietenspiegel nicht. Das Ergebnis
ist eine Orientierung, keine rechtliche Prüfung.

Für Schenefeld und Halstenbek gibt es keinen amtlichen Mietspiegel; dort
vergleicht die App mit den Median-Angebotsmieten 2024 aus dem
Wohnungsmarktprofil der IB.SH (`mietsuche/tabellen/ibsh_angebotsmieten_2024.json`).

Zusätzlich zeigt die App für jede Wohnung den Vergleich mit den Anzeigen, die
sie selbst in den letzten 6 Monaten gesehen hat (Median der Kaltmiete je m²
im selben Stadtteil und bei ähnlicher Größe, sonst im ganzen Ort; mindestens
5 Anzeigen; möblierte Wohnungen, WGs und Zwischenmieten zählen nicht mit).
Das wird nur lokal aus der eigenen Datenbank gerechnet.

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
