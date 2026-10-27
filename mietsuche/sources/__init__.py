"""Portale, die durchsucht werden.

Jedes Portal ist eine Unterklasse von :class:`Source` und wird mit
``@register`` eingetragen.

ImmoScout24 und Immowelt sperren automatische Abrufe, die SAGA-Seite
ist von Servern außerhalb Deutschlands aus gesperrt. Diese Portale fehlen
deshalb noch.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..config import Criteria
from ..models import Listing

REGISTRY: dict[str, type["Source"]] = {}


def register(cls: type["Source"]) -> type["Source"]:
    REGISTRY[cls.name] = cls
    return cls


class Source(ABC):
    name: str = ""

    @abstractmethod
    def search(self, criteria: Criteria) -> list[Listing]:
        """Liefert die aktuellen Anzeigen.

        Das Portal darf grob vorfiltern (Ort, Zimmer, Miete). Die genaue
        Prüfung macht danach :func:`mietsuche.filters.evaluate`.
        """

    def details(self, listing: Listing) -> Listing:
        """Ergänzt eine neue Anzeige um Angaben von ihrer Detailseite.

        Wird nur für Anzeigen aufgerufen, die nach der Trefferliste noch
        passen können. Ohne eigene Umsetzung bleibt die Anzeige, wie sie ist.
        """
        return listing


from . import kleinanzeigen, ohne_makler  # noqa: E402,F401  (tragen sich in REGISTRY ein)
