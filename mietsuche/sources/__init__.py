"""Portale, die durchsucht werden.

Jedes Portal ist eine Unterklasse von :class:`Source` und wird mit
``@register`` eingetragen. Die Portal-Module folgen, sobald sie gegen die
echten Seiten getestet sind.
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
