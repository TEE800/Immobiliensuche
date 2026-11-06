"""Antworten der Vermieter im E-Mail-Postfach erkennen.

Kleinanzeigen schickt zu jeder neuen Nachricht eine E-Mail, bei Ohne-Makler
antworten die Vermieter direkt per E-Mail. Die Mietsuche schaut deshalb
regelmäßig ins Postfach (nur lesen, Mails bleiben ungelesen), ordnet neue
Mails einem Treffer zu und meldet sie per Push.

Das Passwort bleibt auf diesem Rechner: Unter Windows ist es mit dem
Windows-Benutzerkonto verschlüsselt (DPAPI), nur dieser Benutzer kann es
wieder lesen. Auf anderen Systemen liegt es in einer Datei, die nur der
eigene Benutzer lesen darf.
"""

from __future__ import annotations

import base64
import email
import email.policy
import html
import imaplib
import json
import logging
import os
import re
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

log = logging.getLogger("mietsuche")

SETTINGS_FILE = "postfach.json"
PASSWORD_FILE = "postfach_passwort"

# Bekannte Anbieter: Adresse des Posteingangsservers (IMAP, verschlüsselt auf Port 993).
SERVERS = {
    "web.de": "imap.web.de",
    "gmx.de": "imap.gmx.net", "gmx.net": "imap.gmx.net", "gmx.at": "imap.gmx.net",
    "gmail.com": "imap.gmail.com", "googlemail.com": "imap.gmail.com",
    "outlook.com": "outlook.office365.com", "outlook.de": "outlook.office365.com",
    "hotmail.com": "outlook.office365.com", "hotmail.de": "outlook.office365.com",
    "live.com": "outlook.office365.com", "live.de": "outlook.office365.com",
    "t-online.de": "secureimap.t-online.de",
    "freenet.de": "mx.freenet.de",
    "yahoo.com": "imap.mail.yahoo.com", "yahoo.de": "imap.mail.yahoo.com",
    "icloud.com": "imap.mail.me.com", "me.com": "imap.mail.me.com",
    "posteo.de": "posteo.de",
    "mailbox.org": "imap.mailbox.org",
    "1und1.de": "imap.1und1.de", "online.de": "imap.1und1.de",
}

# Was man beim Anbieter einschalten muss, damit Programme ins Postfach dürfen.
HINTS = {
    "imap.web.de": "Bei web.de unter Einstellungen → POP3/IMAP Abruf das Häkchen "
                   "„POP3 und IMAP Zugriff erlauben“ setzen und speichern. Mit Zwei-Faktor-Anmeldung "
                   "braucht es ein anwendungsspezifisches Passwort (web.de → Sicherheit).",
    "imap.gmx.net": "Bei GMX unter Einstellungen → POP3/IMAP Abruf das Häkchen "
                    "„POP3 und IMAP Zugriff erlauben“ setzen.",
    "imap.gmail.com": "Gmail braucht ein App-Passwort (Google-Konto → Sicherheit → App-Passwörter).",
}

# Beim ersten Mal so weit zurückschauen.
FIRST_DAYS = 14
# Größere Mails (meist mit Anhängen) nur anhand von Absender und Betreff prüfen.
MAX_BODY_BYTES = 3_000_000
TIMEOUT = 30

PORTAL_DOMAINS = {"kleinanzeigen": "kleinanzeigen.de", "ohne_makler": "ohne-makler.net"}
# Betreff von Portal-Mails, die eine Nachricht melden (nicht Newsletter oder Suchaufträge).
PORTAL_MESSAGE = re.compile(r"nachricht|antwort|geantwortet", re.I)
REPLY_SUBJECT = re.compile(r"^\s*(re|aw|antw|wg|fw|fwd)\s*:", re.I)

_lock = threading.Lock()


# --- Passwort und Einstellungen ------------------------------------------------

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _dpapi(data: bytes, protect: bool) -> bytes:
        crypt = ctypes.windll.crypt32
        buf = ctypes.create_string_buffer(data, len(data))
        blob_in = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_out = _Blob()
        fn = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
        if not fn(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
            raise OSError("Windows konnte das Passwort nicht ver- oder entschlüsseln.")
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
else:
    def _dpapi(data: bytes, protect: bool) -> bytes:
        return data


def save_password(directory: Path, password: str) -> None:
    path = directory / PASSWORD_FILE
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text(base64.b64encode(_dpapi(password.encode(), True)).decode())
    if sys.platform != "win32":
        os.chmod(path, 0o600)


def load_password(directory: Path) -> str | None:
    env = os.environ.get("POSTFACH_PASSWORT")
    if env:
        return env
    path = directory / PASSWORD_FILE
    if not path.exists():
        return None
    return _dpapi(base64.b64decode(path.read_text().strip()), False).decode()


def load_settings(directory: Path) -> dict:
    path = directory / SETTINGS_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def save_settings(directory: Path, settings: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / SETTINGS_FILE).write_text(json.dumps(settings, ensure_ascii=False, indent=1), encoding="utf-8")


def disconnect(directory: Path) -> None:
    for name in (SETTINGS_FILE, PASSWORD_FILE):
        (directory / name).unlink(missing_ok=True)


def server_for(address: str) -> str | None:
    return SERVERS.get(address.rsplit("@", 1)[-1].strip().lower())


def status(directory: Path) -> dict:
    s = load_settings(directory)
    return {
        "verbunden": bool(s.get("adresse")) and (directory / PASSWORD_FILE).exists(),
        "adresse": s.get("adresse", ""),
        "server": s.get("server", ""),
        "zuletzt": s.get("zuletzt"),
        "fehler": s.get("fehler"),
    }


# --- Postfach lesen -------------------------------------------------------------

class MailboxError(Exception):
    """Fehler mit verständlichem Text für die App."""


def open_mailbox(server: str, address: str, password: str) -> imaplib.IMAP4_SSL:
    try:
        imap = imaplib.IMAP4_SSL(server, 993, timeout=TIMEOUT)
    except OSError as e:
        raise MailboxError(f"Der Posteingangsserver {server} ist nicht erreichbar ({e}).")
    try:
        if password.isascii():
            imap.login(address, password)
        else:
            # LOGIN kann nur einfache Zeichen; Umlaute oder € im Passwort gehen so.
            imap.authenticate("PLAIN", lambda _: f"\0{address}\0{password}".encode())
    except imaplib.IMAP4.error as e:
        hint = HINTS.get(server, "Beim Anbieter prüfen, ob der Zugriff für Mail-Programme (IMAP) erlaubt ist.")
        detail = _server_reply(e)
        raise MailboxError(f"Anmeldung abgelehnt. Adresse und Passwort prüfen. {hint}"
                           + (f" (Antwort des Servers: {detail})" if detail else ""))
    return imap


def _server_reply(error: Exception) -> str:
    text = str(error)
    # imaplib liefert z. B. "b'[AUTHENTICATIONFAILED] Authentication failed'"
    match = re.fullmatch(r"b['\"](.*)['\"]", text)
    return (match.group(1) if match else text).strip()[:200]


@dataclass
class Mail:
    uid: int
    sender_name: str
    sender: str
    subject: str
    date: str
    text: str
    reply: bool


def _decode(value) -> str:
    return str(value or "").strip()


def _text(msg) -> str:
    """Lesbarer Text einer Mail; HTML wird grob in Text umgewandelt."""
    plain, rich = [], []
    for part in msg.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        kind = part.get_content_type()
        if kind not in ("text/plain", "text/html"):
            continue
        try:
            content = part.get_content()
        except (LookupError, ValueError):
            payload = part.get_payload(decode=True) or b""
            content = payload.decode("utf-8", "replace")
        (plain if kind == "text/plain" else rich).append(content)
    if plain:
        return "\n".join(plain)
    joined = "\n".join(rich)
    # Links behalten, weil darin oft die Anzeigennummer steht.
    joined = re.sub(r'href="([^"]*)"', r" \1 ", joined)
    joined = re.sub(r"<(script|style)\b.*?</\1>", " ", joined, flags=re.S | re.I)
    return html.unescape(re.sub(r"<[^>]+>", " ", joined))


def _parse(uid: int, raw: bytes, header_only: bool) -> Mail:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    name, sender = parseaddr(_decode(msg.get("From")))
    subject = _decode(msg.get("Subject"))
    try:
        date = parsedate_to_datetime(msg.get("Date")).astimezone(timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        date = datetime.now(timezone.utc).isoformat(timespec="seconds")
    reply = bool(msg.get("In-Reply-To") or REPLY_SUBJECT.match(subject))
    return Mail(uid, name, sender.lower(), subject, date, "" if header_only else _text(msg), reply)


def _portal(sender: str) -> str | None:
    domain = sender.rsplit("@", 1)[-1]
    for source, portal in PORTAL_DOMAINS.items():
        if domain == portal or domain.endswith("." + portal):
            return source
    return None


def _worth_reading(name: str, sender: str, reply: bool, contacts: set[str]) -> bool:
    """Nur Mails, die eine Antwort sein können; Newsletter und Werbung bleiben ungelesen."""
    if _portal(sender):
        return True
    return reply or _norm(name) in contacts


def fetch_new(imap, settings: dict, contacts: set[str]) -> tuple[list[Mail], dict]:
    """Neue Mails seit dem letzten Mal. Gibt die Mails und den neuen Stand zurück."""
    folder = settings.get("ordner") or "INBOX"
    typ, data = imap.select(f'"{folder}"', readonly=True)  # readonly: nichts wird als gelesen markiert
    if typ != "OK":
        raise MailboxError(f"Ordner {folder} nicht gefunden.")
    validity = (imap.untagged_responses.get("UIDVALIDITY") or [b"0"])[0].decode()
    last = settings.get("letzte_uid") if settings.get("uidvalidity") == validity else None
    if last is None:
        since = (datetime.now() - timedelta(days=FIRST_DAYS)).strftime("%d-%b-%Y")
        typ, data = imap.uid("SEARCH", None, "SINCE", since)
    else:
        typ, data = imap.uid("SEARCH", None, "UID", f"{last + 1}:*")
    uids = sorted(int(u) for u in (data[0] or b"").split() if last is None or int(u) > last)
    mails: list[Mail] = []
    for uid in uids:
        typ, data = imap.uid("FETCH", str(uid), "(RFC822.SIZE BODY.PEEK[HEADER])")
        part = next((d for d in data if isinstance(d, tuple)), None)
        if part is None:
            continue
        size = int(re.search(rb"RFC822\.SIZE (\d+)", part[0]).group(1)) if b"RFC822.SIZE" in part[0] else 0
        head = _parse(uid, part[1], header_only=True)
        if head.sender == settings.get("adresse", "").lower():
            continue  # eigene Mails
        if not _worth_reading(head.sender_name, head.sender, head.reply, contacts):
            continue
        if size > MAX_BODY_BYTES:
            mails.append(head)
            continue
        typ, data = imap.uid("FETCH", str(uid), "(BODY.PEEK[])")
        body = next((d for d in data if isinstance(d, tuple)), None)
        mails.append(_parse(uid, body[1], header_only=False) if body else head)
    state = {"uidvalidity": validity, "letzte_uid": max(uids) if uids else last or 0}
    if last is None and not uids:
        # Leerer Ordner: ab jetzt alles Neue.
        state["letzte_uid"] = 0
    return mails, state


# --- Zuordnung zu Treffern ------------------------------------------------------

def _norm(text: str | None) -> str:
    return re.sub(r"\W+", " ", (text or "").lower()).strip()


MIN_TITLE = 20


def _title_score(title: str, text: str) -> int:
    """Länge des Titelanfangs, der in der Mail vorkommt (Betreffzeilen kürzen lange Titel)."""
    title = _norm(title)
    if len(title) < 12:
        return 0
    if title in text:
        return len(title)
    for length in range(len(title) - 1, MIN_TITLE - 1, -1):
        if title[length - 1] != " " and title[:length] in text:
            return length
    return 0


def match(mail: Mail, rows) -> str | None:
    """Schlüssel des Treffers, zu dem die Mail gehört, oder None."""
    portal = _portal(mail.sender)
    if portal and not PORTAL_MESSAGE.search(mail.subject):
        return None
    text = _norm(mail.subject + " " + mail.text)
    raw = mail.subject + " " + mail.text
    sender_name = _norm(mail.sender_name)
    best, best_score = None, 0
    for row in rows:
        if portal and row["source"] != portal:
            continue
        score = 0
        external_id = row["key"].split(":", 1)[-1]
        if external_id.isdigit() and len(external_id) >= 6 and re.search(rf"(?<!\d){external_id}(?!\d)", raw):
            score = 1000
        score = max(score, _title_score(row["title"], text))
        contact = _norm(row["contact_name"])
        if not score and not portal and len(contact) >= 5 and contact == sender_name:
            score = 15
        # Bei gleich gutem Treffer gewinnt der angefragte.
        score = score * 2 + (row["status"] in ("angefragt", "antwort"))
        if score > best_score:
            best, best_score = row["key"], score
    return best if best_score >= 2 else None


# --- Ein Durchgang --------------------------------------------------------------

def check(directory: Path, storage, notifiers: list) -> int:
    """Postfach einmal prüfen. Gibt die Zahl erkannter Antworten zurück."""
    if not _lock.acquire(blocking=False):
        return 0  # läuft schon
    try:
        return _check(directory, storage, notifiers)
    finally:
        _lock.release()


def _check(directory: Path, storage, notifiers: list) -> int:
    settings = load_settings(directory)
    password = load_password(directory) if settings.get("adresse") else None
    if not password:
        return 0
    rows = [r for r in storage.matches() if r["status"] != "uninteressant"]
    contacts = {_norm(r["contact_name"]) for r in rows if len(_norm(r["contact_name"])) >= 5}
    found = 0
    try:
        imap = open_mailbox(settings["server"], settings["adresse"], password)
        try:
            mails, state = fetch_new(imap, settings, contacts)
        finally:
            try:
                imap.logout()
            except Exception:
                pass
    except (MailboxError, imaplib.IMAP4.error, OSError) as e:
        log.warning("Postfach: %s", e)
        settings["fehler"] = str(e)
        save_settings(directory, settings)
        return 0

    for mail in mails:
        key = match(mail, rows)
        if key is None or not storage.add_reply(key, mail):
            continue
        found += 1
        row = storage.get(key)
        if row["status"] in ("neu", "angefragt"):
            storage.set_status(key, "antwort")
        log.info("Antwort erkannt: %s (%s)", row["title"], mail.subject)
        for notifier in notifiers:
            if hasattr(notifier, "notify_reply"):
                try:
                    notifier.notify_reply(row, mail)
                except Exception as e:
                    log.warning("Push für Antwort %s fehlgeschlagen: %s", key, e)

    # Neu laden, falls die App die Einstellungen inzwischen geändert hat.
    current = load_settings(directory)
    if current.get("adresse") == settings["adresse"]:
        current.update(state)
        current["zuletzt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        current.pop("fehler", None)
        save_settings(directory, current)
    return found


def connect(directory: Path, address: str, password: str, server: str | None = None) -> dict:
    """Zugang prüfen und speichern. Wirft MailboxError mit verständlichem Text."""
    address = address.strip()
    if "@" not in address:
        raise MailboxError("Bitte die vollständige E-Mail-Adresse eingeben.")
    server = (server or "").strip() or server_for(address)
    if not server:
        raise MailboxError("Diesen Anbieter kenne ich nicht. Bitte den Posteingangsserver (IMAP) mit angeben.")
    imap = open_mailbox(server, address, password)
    try:
        imap.logout()
    except Exception:
        pass
    save_password(directory, password)
    save_settings(directory, {"adresse": address, "server": server})
    return status(directory)


def reply_payload(row, mail: Mail) -> dict:
    who = mail.sender_name or mail.sender
    return {
        "title": f"📬 Antwort: {row['title']}"[:120],
        "body": f"Von {who}\n{mail.subject}"[:300],
        "image": row["image_url"],
        "url": f"/#/anzeige/{row['key']}",
        "tag": f"antwort-{row['key']}",
    }

