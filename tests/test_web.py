from fastapi.testclient import TestClient

from mietsuche.config import load_config
from mietsuche.filters import evaluate
from mietsuche.storage import Storage
from mietsuche.web import create_app
from mietsuche.webpush import WebPush, ensure_vapid_key, push_payload
from tests.test_core import CRITERIA, make


def setup(tmp_path):
    (tmp_path / "config.yaml").write_text("datenbank: data/db.sqlite\napp:\n  passwort: geheim\n")
    config = load_config(tmp_path / "config.yaml")
    storage = Storage(config.database)
    listing = make()
    storage.add(listing, evaluate(listing, CRITERIA), "Sehr geehrte Damen und Herren,")
    return config, storage, TestClient(create_app(config))


def login(client):
    r = client.post("/login", data={"passwort": "geheim"}, follow_redirects=False)
    assert r.status_code == 303


def test_requires_login(tmp_path):
    _, _, client = setup(tmp_path)
    assert client.get("/api/listings").status_code == 401
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"
    assert client.post("/login", data={"passwort": "falsch"}).status_code == 401
    # Diese Dateien braucht der Browser vor der Anmeldung.
    for path in ("/sw.js", "/manifest.webmanifest", "/icon.svg"):
        assert client.get(path).status_code == 200


def test_list_detail_and_status(tmp_path):
    _, _, client = setup(tmp_path)
    login(client)
    data = client.get("/api/listings").json()
    assert [l["key"] for l in data["listings"]] == ["test:1"]
    assert data["listings"][0]["status"] == "neu"

    detail = client.get("/api/listings/test:1").json()
    assert detail["letter"].startswith("Sehr geehrte")
    assert "Haustiere" in detail["description"]

    r = client.post("/api/listings/test:1", json={"status": "angefragt", "notes": "Mail raus"})
    assert r.json()["status"] == "angefragt" and r.json()["notes"] == "Mail raus"
    r = client.post("/api/listings/test:1", json={"notes": "nur Notiz"})
    assert r.json()["status"] == "angefragt"
    assert client.post("/api/listings/test:1", json={"status": "quatsch"}).status_code == 400
    assert client.get("/api/listings/gibtsnicht").status_code == 404
    assert "Mietsuche" in client.get("/").text


def test_first_visit_sets_password(tmp_path):
    (tmp_path / "config.yaml").write_text("datenbank: data/db.sqlite\n")
    client = TestClient(create_app(load_config(tmp_path / "config.yaml")))
    assert "Passwort festlegen" in client.get("/login").text
    assert client.post("/login", data={"passwort": "kurz", "wiederholung": "kurz"}).status_code == 400
    r = client.post("/login", data={"passwort": "geheim123", "wiederholung": "anders123"})
    assert r.status_code == 400
    r = client.post("/login", data={"passwort": "geheim123", "wiederholung": "geheim123"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/api/listings").status_code == 200

    other = TestClient(create_app(load_config(tmp_path / "config.yaml")))
    assert "Anmelden" in other.get("/login").text
    assert other.post("/login", data={"passwort": "falsch123"}).status_code == 401
    assert other.post("/login", data={"passwort": "geheim123"}, follow_redirects=False).status_code == 303


def test_template_edit_applies_to_existing_listings(tmp_path):
    _, _, client = setup(tmp_path)
    login(client)
    assert "anrede" in client.get("/api/vorlage").json()["placeholders"]
    assert client.put("/api/vorlage", json={"text": "Hallo {kaputt"}).status_code == 400
    assert client.put("/api/vorlage", json={"text": "{anrede} {ihr_objekt} in {ort}"}).status_code == 200
    detail = client.get("/api/listings/test:1").json()
    assert detail["letter"] == "Sehr geehrte Damen und Herren, Ihre Wohnung in Hamburg"


def test_push_subscription_and_send(tmp_path, monkeypatch):
    config, storage, client = setup(tmp_path)
    login(client)
    key = client.get("/api/push/key").json()["publicKey"]
    assert len(key) == 87  # 65 Byte, base64url ohne Padding
    sub = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "x", "auth": "y"}}
    assert client.post("/api/push/subscribe", json=sub).json() == {"ok": True}
    assert client.post("/api/push/subscribe", json={"foo": 1}).status_code == 400

    sent = []
    monkeypatch.setattr("mietsuche.webpush.webpush", lambda s, data, **kw: sent.append((s, data)))
    private, _ = ensure_vapid_key(config.database.parent)
    row = storage.get("test:1")
    WebPush(storage, private, "").notify(row)
    assert sent[0][0]["endpoint"] == "https://push.example/abc"
    assert '"url": "/#/anzeige/test:1"' in sent[0][1]
    assert push_payload(row)["body"].endswith("3 Zi. · 1800 € warm")


def test_leaflet_is_served_locally(tmp_path):
    _, _, client = setup(tmp_path)
    login(client)
    assert client.get("/leaflet/leaflet.js").status_code == 200
    assert client.get("/leaflet/leaflet.css").headers["content-type"].startswith("text/css")
    assert client.get("/leaflet/..%2Fweb.py").status_code == 404
