"""Portal-Parser gegen nachgebaute Ausschnitte der echten Seiten."""

import httpx
import pytest

from mietsuche.config import Criteria
from mietsuche.filters import JA, UNKLAR, evaluate, worth_details
from mietsuche.models import Listing
from mietsuche.sources import REGISTRY
from mietsuche.sources.common import number, split_place
from mietsuche.sources import kleinanzeigen, ohne_makler

CRITERIA = Criteria(zip_ranges=[(20095, 21149), (22041, 22769), (22869, 22869), (25469, 25469)])


def test_sources_are_registered():
    assert {"kleinanzeigen", "ohnemakler"} <= set(REGISTRY)


@pytest.mark.parametrize(
    "text,expected",
    [("2.000 €", 2000), ("1.225 € VB", 1225), ("103,40 m²", 103.4), ("3,5 Zi.", 3.5),
     ("87.06", 87.06), ("50m²", 50), ("", None), ("VB", None)],
)
def test_number(text, expected):
    assert number(text) == expected


def test_split_place():
    assert split_place("22145 Hamburg\n\t\t(Jenfeld)") == ("22145", "Hamburg", "Jenfeld")
    assert split_place("20539 Hamburg Rothenburgsort") == ("20539", "Hamburg", "Rothenburgsort")
    assert split_place("25469 Halstenbek") == ("25469", "Halstenbek", None)


# --- Kleinanzeigen ---------------------------------------------------------

KA_ARTICLE = """
<article class="flex" data-adid="{id}" data-href="/s-anzeige/x/{id}-203-9423">
  <div><a href="/s-anzeige/x/{id}-203-9423"><img src="https://img.example/{id}.jpg"></a></div>
  <div>
    <div><div><span>{place}</span></div></div>
    <div class="flex flex-col">
      <h3><a href="/s-anzeige/x/{id}-203-9423">{title}</a></h3>
      <p class="mb-xsmall text-bodyRegular">Willkommen im neuen Quartier...</p>
      <p class="font-strong">{facts}</p>
      <div class="flex"><p class="my-xsmall text-title3">{price}</p></div>
    </div>
    <div><p class="flex"><span>Von Privat</span></p></div>
  </div>
</article>"""


def ka_list(*articles):
    return "<html><body><ul>" + "".join(KA_ARTICLE.format(**a) for a in articles) + "</ul></body></html>"


def ka_ad(**kw):
    base = dict(id="3542180773", place="20539 Hamburg Rothenburgsort",
                title="Helle 3-Zimmer-Wohnung mit Terrasse", facts="103,40 m² · 3 Zi.", price="2.000 €")
    base.update(kw)
    return base


def test_kleinanzeigen_search_page():
    html = ka_list(
        ka_ad(),
        ka_ad(id="2", title="TAUSCHWOHNUNG 3 Zimmer gegen größer"),
        ka_ad(id="3", title="Familie sucht ein Haus"),
        ka_ad(id="4", facts="0 m² · 0 Zi.", price="1.500 €"),
    )
    listings = kleinanzeigen.parse_search(html, "wohnung")
    assert [x.external_id for x in listings] == ["3542180773", "4"]
    first = listings[0]
    assert first.url == "https://www.kleinanzeigen.de/s-anzeige/x/3542180773-203-9423"
    assert (first.zip_code, first.city, first.district) == ("20539", "Hamburg", "Rothenburgsort")
    assert (first.rooms, first.living_area, first.rent_cold) == (3, 103.4, 2000)
    assert first.description.startswith("Willkommen")
    assert first.image_url == "https://img.example/3542180773.jpg"
    assert listings[1].rooms is None


def test_kleinanzeigen_hamburg_district_as_place():
    html = ka_list(ka_ad(place="21073 Harburg"))
    listing = kleinanzeigen.parse_search(html, "wohnung", "Hamburg")[0]
    assert (listing.city, listing.district) == ("Hamburg", "Harburg")
    assert kleinanzeigen.parse_search(html, "wohnung", "Schenefeld")[0].city == "Harburg"


KA_DETAIL = """
<html><head><script>var x = {"ad_attributes":"%s","ad_source":"desktopSITE"};</script></head>
<body>
<h1 id="viewad-title">Helle 3-Zimmer-Wohnung</h1>
<h2 id="viewad-price">2.000 €</h2>
<span id="street-address">Marckmannstraße 27,&nbsp;</span>
<span id="viewad-locality">20539 Hamburg-Mitte - Hamburg Rothenburgsort</span>
<ul class="checktaglist"><li class="checktag">Terrasse</li><li class="checktag">Garten/-mitnutzung</li>
<li class="checktag">Haustiere erlaubt</li></ul>
<p id="viewad-description-text">Ruhige Lage.<br />Neubau.</p>
<span class="userprofile-vip"><a href="/s-bestandsliste.html?userId=1">Anna Muster</a></span>
</body></html>"""


def test_kleinanzeigen_details():
    attrs = ("pets_allowed_b:t|terrace_b:t|garden_b:t|nebenkosten_i:390|heizkosten_i:60"
             "|qm_d:103.40|zimmer_d:3.0|verfuegbarm_i:11|verfuegbary_i:2026")
    listing = kleinanzeigen.parse_search(ka_list(ka_ad()), "wohnung")[0]
    full = kleinanzeigen.parse_details(KA_DETAIL % attrs, listing)
    assert full.street == "Marckmannstraße 27"
    assert full.pets_allowed == "ja"
    assert full.balcony is True
    assert full.garden is None and full.garden_maybe_shared
    assert full.warm_rent_estimate == (2450, False)
    assert full.contact_name == "Anna Muster"
    assert full.available_from == "11/2026"
    assert full.description == "Ruhige Lage.\nNeubau.\nAusstattung: Terrasse, Garten/-mitnutzung, Haustiere erlaubt"
    checks = {c.name: c.result for c in evaluate(full, CRITERIA).checks}
    assert checks["miete"] != JA  # 2450 € warm ist zu teuer
    assert checks["haustiere"] == JA and checks["aussenbereich"] == JA


def test_kleinanzeigen_swap_and_warm_rent():
    listing = kleinanzeigen.parse_search(ka_list(ka_ad(price="1.400 €")), "wohnung")[0]
    full = kleinanzeigen.parse_details(KA_DETAIL % "swap_s:t|warmmiete_i:1700|garden_b:t", listing)
    assert full.rent_warm == 1700
    assert not evaluate(full, CRITERIA).matches  # Tauschangebot
    house = kleinanzeigen.parse_details(KA_DETAIL % "garden_b:t", Listing(
        source="kleinanzeigen", external_id="9", url="u", title="Reihenhaus", property_type="haus"))
    assert house.garden is True and not house.garden_maybe_shared


def test_kleinanzeigen_urls():
    urls = [u for _, _, u in kleinanzeigen.search_urls(CRITERIA)]
    assert "https://www.kleinanzeigen.de/s-wohnung-mieten/hamburg/c203l9409+wohnung_mieten.zimmer_d:3%2C" in urls
    assert "https://www.kleinanzeigen.de/s-wohnung-mieten/hamburg/seite:2/c203l9409+wohnung_mieten.zimmer_d:3%2C" in urls
    assert "https://www.kleinanzeigen.de/s-haus-mieten/halstenbek/c205l616+haus_mieten.zimmer_d:3%2C" in urls


# --- Ohne-Makler -----------------------------------------------------------

OM_CARD = """
<div id="bookmark_{id}">
<a href="/immobilie/{id}/" data-om-id="{id}" data-om-type="RENT">
  <div><img src="https://media.example/{id}.jpg"></div>
  <div><span class="block font-semibold text-xl">{price}</span><button>Merken</button></div>
  <div><div>
    <h4 class="line-clamp-1">{title}</h4>
    <div class="flex"><i></i><span>{zip} Hamburg
        ({district})</span></div>
  </div>
  <div><div>
    <div class="flex items-center" title="Zimmer"><i></i><span>{rooms}</span></div>
    <div class="flex items-center" title="Wohnfläche"><i></i><span>{area}m²</span></div>
  </div></div></div>
</a></div>"""


def om_list(*cards):
    return "<html><body>" + "".join(OM_CARD.format(**c) for c in cards) + "</body></html>"


def om_card(**kw):
    base = dict(id="194716", price="1.100 €", title="Schöne 3-Zimmer-Wohnung", zip="22145",
                district="Jenfeld", rooms="3,5", area="80")
    base.update(kw)
    return base


OM_DETAIL = """
<html><body><header>Menü</header><main>
<h1>Schöne 3-Zimmer-Wohnung</h1>
<a href="#ort"><span>Rehmkoppel 11,
     22391 Hamburg
     (Wellingsbüttel)</span></a>
<span>1.100 €</span><span>Kaltmiete (zzgl. NK)</span>
<p>Helle Wohnung mit Blick ins Grüne. Haustiere nach Absprache.</p>
<dl>
<dt>Objektart</dt><dd>Wohnung</dd>
<dt>Baujahr</dt><dd>1972</dd>
<dt>Übernahme ab</dt><dd>1. Januar 2027</dd>
<dt>Kaltmiete</dt><dd>1.100 €</dd><dd>(zzgl. NK)</dd>
<dt>Heizkosten</dt><dd>100 €</dd>
<dt>Nebenkosten ohne Heizkosten</dt><dd>190 €</dd>
<dt>Summe Nebenkosten/Heizkosten</dt><dd>290 €</dd>
<dt>Ausstattung</dt><dd>{features}</dd>
<dt>Infrastruktur</dt><dd>Kindergarten, Grundschule</dd>
</dl>
<h3>Lage-Check</h3><p>Natur: Garten der Nachbarn</p>
</main></body></html>"""


def test_ohne_makler_search_page():
    listings = ohne_makler.parse_search(om_list(om_card(), om_card(id="2", rooms="2")), "wohnung")
    assert len(listings) == 2
    first = listings[0]
    assert first.url == "https://www.ohne-makler.net/immobilie/194716/"
    assert (first.zip_code, first.city, first.district) == ("22145", "Hamburg", "Jenfeld")
    assert (first.rooms, first.living_area, first.rent_cold) == (3.5, 80, 1100)
    assert worth_details(first, CRITERIA)
    assert not worth_details(listings[1], CRITERIA)


def test_ohne_makler_details():
    listing = ohne_makler.parse_search(om_list(om_card()), "wohnung")[0]
    full = ohne_makler.parse_details(OM_DETAIL.format(features="Balkon, Garten, Keller"), listing)
    assert full.street == "Rehmkoppel 11"
    assert full.warm_rent_estimate == (1390, False)
    assert full.balcony is True and full.garden_maybe_shared
    assert full.available_from == "1. Januar 2027"
    assert full.year_built == 1972
    assert "Kindergarten" not in full.description and "Lage-Check" not in full.description
    checks = {c.name: c.result for c in evaluate(full, CRITERIA).checks}
    assert checks["haustiere"] == JA
    assert checks["aussenbereich"] == UNKLAR
    assert evaluate(full, CRITERIA).matches


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        if url not in self.pages:
            return httpx.Response(404, request=httpx.Request("GET", url))
        return httpx.Response(200, text=self.pages[url], request=httpx.Request("GET", url))

    def text(self, url):
        return self.get(url).raise_for_status().text


def test_ohne_makler_pages_until_nothing_new():
    base = "https://www.ohne-makler.net/immobilien/wohnung-mieten/hamburg/"
    fetcher = FakeFetcher({
        base: om_list(om_card(id="1"), om_card(id="2")),
        base + "?page=2": om_list(om_card(id="3")),
    })
    source = ohne_makler.OhneMakler(fetcher)
    found = source.search(Criteria(property_types=["wohnung"], cities=["Hamburg"]))
    assert [x.external_id for x in found] == ["1", "2", "3"]
    assert fetcher.urls[-1] == base + "?page=3"


def test_still_online():
    from mietsuche.sources.common import still_online

    class Redirecting(FakeFetcher):
        def get(self, url):
            self.urls.append(url)
            target = self.pages.get(url, url)
            status = 404 if url.endswith("/weg/") else 200
            return httpx.Response(status, text="<p>Wohnung</p>" if "gelöscht" not in url else
                                  "<p>Diese Anzeige wurde gelöscht.</p>", request=httpx.Request("GET", target))

    fetcher = Redirecting({"https://ka/s-anzeige/x/1": "https://ka/s-wohnung-mieten/hamburg"})
    assert still_online(fetcher, "https://ka/s-anzeige/x/2", "/s-anzeige/")
    assert not still_online(fetcher, "https://ka/s-anzeige/x/1", "/s-anzeige/")
    assert not still_online(fetcher, "https://om/immobilie/weg/", "/immobilie/weg/")
    assert not still_online(fetcher, "https://ka/s-anzeige/gelöscht", "/s-anzeige/")
