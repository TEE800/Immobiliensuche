from email.message import EmailMessage

import pytest
from fastapi.testclient import TestClient

from mietsuche import postfach
from mietsuche.config import load_config
from mietsuche.filters import evaluate
from mietsuche.storage import Storage
from mietsuche.web import create_app
from tests.test_core import CRITERIA, make


def mail(sender, subject, body="", reply_to=None, html=False):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "kevin@web.de"
    msg["Subject"] = subject
    msg["Date"] = "Fri, 06 Nov 2026 10:00:00 +0100"
    if reply_to:
        msg["In-Reply-To"] = reply_to
    if html:
        msg.set_content(body, subtype="html")
    else:
        msg.set_content(body)
    return msg.as_bytes()


class FakeImap:
    """Postfach im Speicher mit den IMAP-Befehlen, die die Mietsuche nutzt."""

    def __init__(self, mails):
        self.mails = dict(enumerate(mails, start=1))
        self.untagged_responses = {}
        self.readonly = None
        self.fetched_bodies = []

    def select(self, folder, readonly=False):
        self.readonly = readonly
        self.untagged_responses["UIDVALIDITY"] = [b"77"]
        return "OK", [b"1"]

    def uid(self, command, *args):
        if command == "SEARCH":
            if args[1] == "UID":
                first = int(args[2].split(":")[0])
                uids = [u for u in self.mails if u >= first] or [max(self.mails)]
            else:
                uids = list(self.mails)
            return "OK", [" ".join(map(str, uids)).encode()]
        uid, what = int(args[0]), args[1]
        raw = self.mails[uid]
        if "HEADER" in what:
            head = raw.split(b"\n\n", 1)[0] + b"\n\n"
            return "OK", [(f"{uid} (RFC822.SIZE {len(raw)} BODY[HEADER] {{{len(head)}}}".encode(), head), b")"]
        self.fetched_bodies.append(uid)
        return "OK", [(f"{uid} (BODY[] {{{len(raw)}}}".encode(), raw), b")"]

    def logout(self):
        pass


@pytest.fixture
def setup(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "db.sqlite")
    ka = make(source="kleinanzeigen", external_id="3141592653", title="Helle 3 Zimmer Altbauwohnung mit Balkon in Ottensen")
    om = make(source="ohne_makler", external_id="98765", title="Familienfreundliche Wohnung am Stadtpark",
              contact_name="Erika Mustermann")
    other = make(source="kleinanzeigen", external_id="2718281828", title="Kleines Haus mit Garten in Schenefeld")
    for listing in (ka, om, other):
        storage.add(listing, evaluate(listing, CRITERIA), None)
    storage.set_status(ka.key, "angefragt")
    storage.set_status(om.key, "angefragt")
    postfach.save_settings(tmp_path, {"adresse": "kevin@web.de", "server": "imap.web.de"})
    postfach.save_password(tmp_path, "geheim")
    box = FakeImap([])
    monkeypatch.setattr(postfach, "open_mailbox", lambda server, address, password: box)
    return tmp_path, storage, box


class Notifier:
    def __init__(self):
        self.sent = []

    def notify_reply(self, row, mail):
        self.sent.append(postfach.reply_payload(row, mail))


def test_detects_replies_and_notifies(setup):
    directory, storage, box = setup
    box.mails = dict(enumerate([
        mail("Kleinanzeigen <noreply@mail.kleinanzeigen.de>",
             "Neue Nachricht zu „Helle 3 Zimmer Altbauwohnung mit Balk…“", "Hallo, gern Besichtigung."),
        mail("Newsletter <info@shop.de>", "Angebote der Woche", "Familienfreundliche Wohnung am Stadtpark"),
        mail("Erika Mustermann <erika@example.org>", "AW: Anfrage zu Ihrer Anzeige",
             "<p>Gern, kommen Sie Samstag.</p><a href='https://www.ohne-makler.net/immobilie/98765/'>x</a>",
             reply_to="<abc@ohne-makler.net>", html=True),
        mail("Kleinanzeigen <noreply@kleinanzeigen.de>", "Neue Anzeigen zu deiner Suche",
             "Kleines Haus mit Garten in Schenefeld"),
    ], start=1))
    notifier = Notifier()
    assert postfach.check(directory, storage, [notifier]) == 2
    assert box.readonly is True
    assert 2 not in box.fetched_bodies  # Newsletter wird nicht geöffnet
    assert storage.get("kleinanzeigen:3141592653")["status"] == "antwort"
    assert storage.get("ohne_makler:98765")["status"] == "antwort"
    assert storage.get("kleinanzeigen:2718281828")["status"] == "neu"  # Suchauftrag ist keine Antwort
    assert [p["url"] for p in notifier.sent] == ["/#/anzeige/kleinanzeigen:3141592653", "/#/anzeige/ohne_makler:98765"]
    assert storage.replies("ohne_makler:98765")[0]["absender"] == "Erika Mustermann <erika@example.org>"

    # Beim nächsten Lauf nichts doppelt melden, nur die neue Mail.
    box.mails[5] = mail("Kleinanzeigen <noreply@kleinanzeigen.de>",
                        "Du hast eine neue Nachricht", "Zu: Kleines Haus mit Garten in Schenefeld")
    assert postfach.check(directory, storage, [notifier]) == 1
    assert box.fetched_bodies.count(1) == 1
    assert len(notifier.sent) == 3
    assert postfach.status(directory)["zuletzt"]


def test_later_status_is_kept(setup):
    directory, storage, box = setup
    storage.set_status("ohne_makler:98765", "besichtigung")
    box.mails = {1: mail("Erika Mustermann <erika@example.org>", "Re: Familienfreundliche Wohnung am Stadtpark")}
    assert postfach.check(directory, storage, []) == 1
    assert storage.get("ohne_makler:98765")["status"] == "besichtigung"


def test_login_error_is_shown(setup, monkeypatch):
    directory, storage, _ = setup

    def refuse(server, address, password):
        raise postfach.MailboxError("Anmeldung abgelehnt.")

    monkeypatch.setattr(postfach, "open_mailbox", refuse)
    assert postfach.check(directory, storage, []) == 0
    assert postfach.status(directory)["fehler"] == "Anmeldung abgelehnt."


def test_not_connected_does_nothing(tmp_path):
    assert postfach.check(tmp_path, Storage(tmp_path / "db.sqlite"), []) == 0
    assert postfach.status(tmp_path)["verbunden"] is False


def test_server_for():
    assert postfach.server_for("Kevin@WEB.DE") == "imap.web.de"
    assert postfach.server_for("x@gmail.com") == "imap.gmail.com"
    assert postfach.server_for("x@unbekannt.example") is None


def test_web_connect_and_disconnect(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text("datenbank: data/db.sqlite\napp:\n  passwort: geheim\n")
    config = load_config(tmp_path / "config.yaml")
    monkeypatch.setattr(postfach, "open_mailbox", lambda s, a, p: FakeImap([]))
    monkeypatch.setattr(postfach, "check", lambda *a: 0)
    client = TestClient(create_app(config))
    client.post("/login", data={"passwort": "geheim"})
    assert client.get("/api/postfach").json()["verbunden"] is False
    r = client.put("/api/postfach", json={"adresse": "kevin@web.de", "passwort": "pw"})
    assert r.status_code == 200 and r.json()["verbunden"] is True
    data_dir = config.database.parent
    assert postfach.load_password(data_dir) == "pw"
    assert "pw" not in (data_dir / "postfach.json").read_text()
    assert client.put("/api/postfach", json={"adresse": "kevin", "passwort": "pw"}).status_code == 400
    assert client.delete("/api/postfach").json()["verbunden"] is False
    assert not (data_dir / "postfach_passwort").exists()


class LoginImap:
    def __init__(self, *args, **kwargs):
        self.calls = []

    def login(self, user, password):
        self.calls.append(("login", user))
        raise postfach.imaplib.IMAP4.error("b'[AUTHENTICATIONFAILED] Authentication failed'")

    def authenticate(self, mechanism, callback):
        self.calls.append((mechanism, callback(b"")))
        return "OK", [b""]


def test_login_shows_server_reply_and_handles_umlauts(monkeypatch):
    boxes = []
    monkeypatch.setattr(postfach.imaplib, "IMAP4_SSL", lambda *a, **k: boxes.append(LoginImap()) or boxes[-1])
    with pytest.raises(postfach.MailboxError, match=r"Antwort des Servers: \[AUTHENTICATIONFAILED\]"):
        postfach.open_mailbox("imap.web.de", "kevin@web.de", "geheim")
    postfach.open_mailbox("imap.web.de", "kevin@web.de", "Bär€")
    assert boxes[-1].calls == [("PLAIN", "\0kevin@web.de\0Bär€".encode())]
