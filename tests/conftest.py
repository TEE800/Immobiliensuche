import pytest

from mietsuche.mietenspiegel import Wohnlagen


@pytest.fixture(autouse=True)
def no_wohnlagen_lookup(monkeypatch):
    """Tests fragen das Wohnlagenverzeichnis nie im Netz ab."""
    monkeypatch.setattr(Wohnlagen, "lookup", lambda self, street, zip_code: None)
