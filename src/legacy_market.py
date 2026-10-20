"""Small, allowlisted retail scrapers. Compare exact SKUs and gross item prices."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from html.parser import HTMLParser
import json
from urllib.request import Request, urlopen

SOURCES = {
    "The Pi Hut": "https://thepihut.com/products/raspberry-pi-pico-2",
    "Pimoroni": "https://shop.pimoroni.com/products/raspberry-pi-pico-2",
}
FAMILIES = {
    "pico2": ("raspberry-pi-pico-2", "SC1631"),
    "pico-w": ("raspberry-pi-pico-w", "SC0918"),
    "pico2-w": ("raspberry-pi-pico-2-w", "SC1633"),
}
CATALOG = {
    "pico2": {"name": "Raspberry Pi Pico 2 · without headers", "sku": "SC1631", "gtin": "5056561803951", "family": "pico2", "currentPrice": "6.50", "minimumPrice": "4.00"},
    "pico2-headers": {"name": "Raspberry Pi Pico 2 · with headers", "sku": "SC1632", "gtin": "5056561803968", "family": "pico2", "currentPrice": "6.50", "minimumPrice": "4.00"},
    "pico-w": {"name": "Raspberry Pi Pico W · without headers", "sku": "SC0918", "gtin": "5056561803173", "family": "pico-w", "currentPrice": "8.50", "minimumPrice": "5.00"},
    "pico-w-headers": {"name": "Raspberry Pi Pico WH · with headers", "sku": "SC0919", "gtin": "5056561800196", "family": "pico-w", "currentPrice": "8.50", "minimumPrice": "5.00"},
    "pico2-w": {"name": "Raspberry Pi Pico 2 W · without headers", "sku": "SC1633", "gtin": "5056561803975", "family": "pico2-w", "currentPrice": "9.50", "minimumPrice": "5.80"},
    "pico2-w-headers": {"name": "Raspberry Pi Pico 2 W · with headers", "sku": "SC1634", "gtin": "5056561803982", "family": "pico2-w", "currentPrice": "9.50", "minimumPrice": "5.80"},
}
BASIS = "item_price_including_vat_excluding_delivery"


def fetch_page(url):
    # URLs are constants, never caller supplied. Respect errors; do not bypass blocks.
    request = Request(url, headers={"User-Agent": "PricingClassDemo/1.0 (educational price comparison)",
                                    "Accept": "text/html,application/json"})
    with urlopen(request, timeout=15) as response:
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("Source response exceeds size limit")
        return raw.decode("utf-8")


class _Scripts(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.items, self.attrs, self.parts, self.currencies = [], None, [], []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and attrs.get("property") == "product:price:currency":
            self.currencies.append(attrs.get("content"))
        if tag == "script":
            self.attrs, self.parts = attrs, []

    def handle_data(self, text):
        if self.attrs is not None:
            self.parts.append(text)

    def handle_endtag(self, tag):
        if tag == "script" and self.attrs is not None:
            self.items.append((self.attrs, "".join(self.parts)))
            self.attrs = None


def _offers(seller, html, url, root_sku):
    parsed = _Scripts(html)
    scripts = parsed.items
    if seller == "The Pi Hut":
        if not parsed.currencies or set(parsed.currencies) != {"GBP"}:
            raise ValueError("Retail page must explicitly declare GBP")
        # This store's Shopify metadata is gross GBP pence. It is NOT a generic
        # Shopify parser: other stores expose net VAT values in the same field.
        for _, body in scripts:
            if "var meta = " in body:
                data, _ = json.JSONDecoder().raw_decode(body.split("var meta = ", 1)[1])
                return [{"sku": row["sku"], "price": Decimal(str(row["price"])) / 100,
                         "currency": "GBP", "availability": "unknown", "url": url + "?variant=" + str(row["id"])}
                        for row in data["product"]["variants"]]
    else:
        for attrs, body in scripts:
            if attrs.get("type") == "application/ld+json":
                data = json.loads(body)
                if isinstance(data, dict) and data.get("@type") == "Product" and data.get("sku") == root_sku:
                    offers = data.get("offers", [])
                    if isinstance(offers, dict):
                        offers = [offers]
                    return [{"sku": row.get("sku"), "price": Decimal(str(row["price"])),
                             "currency": row.get("priceCurrency"), "url": row.get("url", url),
                             "availability": "in_stock" if row.get("availability", "").endswith("/InStock") else "unavailable"}
                            for row in offers]
    raise ValueError("Expected exact product metadata missing; retailer markup may have changed")


def collect_catalog(fetch=fetch_page, catalog_ids=None):
    """Fetch each retailer once; return per-SKU prices and explicit partial errors."""
    checked = datetime.now(timezone.utc).isoformat()
    selected = list(CATALOG) if catalog_ids is None else catalog_ids
    if not isinstance(selected, list) or any(not isinstance(key, str) or key not in CATALOG for key in selected):
        raise ValueError("Unknown fixed catalog product")
    products = {key: {**CATALOG[key], "currency": "GBP", "comparisonBasis": BASIS, "offers": [], "excludedOffers": []}
                for key in selected}
    errors = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = []
        for family in dict.fromkeys(CATALOG[key]["family"] for key in selected):
            slug, root_sku = FAMILIES[family]
            for seller, base in SOURCES.items():
                url = base.rsplit("/", 1)[0] + "/" + slug
                jobs.append((seller, family, url, root_sku, pool.submit(fetch, url)))
        for seller, family, url, root_sku, job in jobs:
            try:
                rows = _offers(seller, job.result(), url, root_sku)
            except Exception as exc:
                errors.append({"seller": seller, "family": family, "error": type(exc).__name__ + ": " + str(exc)[:160]})
                continue
            for key, product in products.items():
                if product["family"] != family:
                    continue
                try:
                    for row in rows:
                        if row["sku"] == product["sku"] and row["availability"] == "unavailable":
                            product["excludedOffers"].append({"seller": seller, "reason": "Out of stock or not declared in stock", "url": row["url"]})
                    matches = [row for row in rows if row["sku"] == product["sku"] and row["currency"] == "GBP"
                               and row["price"].is_finite() and 0 < row["price"] < 10000
                               and row["availability"] != "unavailable"]
                    if len(matches) != 1:
                        raise ValueError("Exact SKU has no unique eligible offer")
                    row = matches[0]
                    product["offers"].append({"seller": seller, "price": format(row["price"], ".2f"),
                                               "url": row["url"], "availability": row["availability"], "checkedAt": checked})
                except Exception as exc:
                    errors.append({"seller": seller, "catalogId": key, "error": type(exc).__name__ + ": " + str(exc)[:160]})
    for product in products.values():
        amounts = [Decimal(row["price"]) for row in product["offers"]]
        product.update(lowest=format(min(amounts), ".2f") if amounts else None,
                       highest=format(max(amounts), ".2f") if amounts else None,
                       sellerCount=len(amounts))
    return {"checkedAt": checked, "products": products, "errors": errors}
