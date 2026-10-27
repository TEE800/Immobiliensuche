"""Datenmodell für eine Anzeige."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Listing:
    """Eine Mietanzeige, so wie sie ein Portal liefert.

    Felder, die ein Portal nicht kennt, bleiben ``None``. Strukturierte
    Angaben (``pets_allowed``, ``garden``, ``balcony``) werden bevorzugt,
    der Freitext dient als Rückfall für die Filter.
    """

    source: str
    external_id: str
    url: str
    title: str
    property_type: str = "wohnung"  # "wohnung" oder "haus"
    street: str | None = None
    zip_code: str | None = None
    city: str | None = None
    district: str | None = None
    rooms: float | None = None
    living_area: float | None = None
    rent_cold: float | None = None
    rent_warm: float | None = None
    service_charges: float | None = None
    pets_allowed: str | None = None  # "ja", "nach_vereinbarung", "nein" oder None
    garden: bool | None = None
    balcony: bool | None = None
    balcony_area: float | None = None
    description: str = ""
    image_url: str | None = None
    contact_name: str | None = None
    contact_company: str | None = None
    available_from: str | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.external_id}"

    @property
    def address(self) -> str:
        place = " ".join(p for p in (self.zip_code, self.city) if p)
        if self.district and self.district not in (self.city or ""):
            place = f"{place} ({self.district})" if place else self.district
        return ", ".join(p for p in (self.street, place) if p)

    @property
    def warm_rent_estimate(self) -> tuple[float | None, bool]:
        """Warmmiete und ob sie nur geschätzt ist."""
        if self.rent_warm is not None:
            return self.rent_warm, False
        if self.rent_cold is not None and self.service_charges is not None:
            return self.rent_cold + self.service_charges, False
        return None, True
