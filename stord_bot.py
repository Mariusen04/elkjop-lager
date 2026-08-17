import requests
import csv
import time
import math
import sys
import base64

from urllib.parse import urlparse, parse_qs, unquote
from playwright.sync_api import sync_playwright


# ============================================================
# INNSTILLINGER
# ============================================================

STORD_ID = "1273"

MIN_PERCENT = 70.0
MIN_STORES_WITH_STOCK = 10

EXCLUDE_STORE_WORDS = [
    "Phonehouse",
    "Outlet",
]

ALGOLIA_APP_ID = "Z0FL7R8UBH"
INDEX_NAME = "commerce_b2c_OCNOELK"

ALGOLIA_URL = (
    "https://z0fl7r8ubh-dsn.algolia.net/"
    "1/indexes/*/queries"
)

STOREFINDER_URL = (
    "https://www.elkjop.no/api/trpc/"
    "location.getStoreFinderStores"
)

MAX_RETRIEVABLE = 1500
HITS_PER_PAGE = 500
MAX_SPLIT_DEPTH = 10

KEY_REFRESH_MARGIN = 90

SPLIT_TOLERANCE_PERCENT = 0.005
SPLIT_TOLERANCE_MIN = 10

BASE_FILTERS = [
    "sellerName:Elkjøp"
]

SPLIT_FACETS = [
    "ptLowestLevelNodeValue",
    "brand",
    "sellerName",
    "retailItemCategoryGroup",
    "articleRole",
    "articleType",
    "retailSalesStatus",
    "mainLogisticalFlow",
    "stockGrade",
    "displayGrade",
]

PRODUCT_ATTRIBUTES = [
    "articleNumber",
    "title",
    "brand",
    "sellerName",
    "urlB2C",
    "storesWithStock",
    "departmentStock",
    "wholesalesStatus",
    "retailSalesStatus",
    "isBuyableInStore",
    "isOnline",
    "isOnlineSalesStatusDIS",
    "retailItemCategoryGroup",
    "productTaxonomy",
]


# ============================================================
# GLOBALT
# ============================================================

session = requests.Session()

ALGOLIA_API_KEY = None
ALGOLIA_KEY_VALID_UNTIL = None

UNRESOLVED_SPLITS = []


# ============================================================
# ALGOLIA-NØKKEL
# ============================================================

def decode_key_valid_until(key):
    try:
        padded = key + "=" * (-len(key) % 4)

        decoded = base64.b64decode(
            padded
        ).decode(
            "utf-8",
            errors="ignore"
        )

        parsed = parse_qs(
            decoded,
            keep_blank_values=True
        )

        values = parsed.get(
            "validUntil"
        )

        if values:
            return int(values[0])

    except Exception:
        pass

    return None


def get_fresh_algolia_key():
    print()
    print("🔑 Henter fersk Algolia-nøkkel automatisk ...")

    found_key = None

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox"],
        )

        context = browser.new_context(
            locale="nb-NO",
            user_agent=(
                "Mozilla/5.0 "
                "(Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/150.0.0.0 "
                "Safari/537.36"
            )
        )

        page = context.new_page()

        def handle_request(request):
            nonlocal found_key

            url = request.url

            if (
                "algolia.net" not in url
                or "x-algolia-api-key" not in url
            ):
                return

            try:
                parsed = urlparse(url)
                params = parse_qs(parsed.query)
                keys = params.get("x-algolia-api-key")

                if keys:
                    key = unquote(keys[0])

                    if len(key) > 50:
                        found_key = key

            except Exception:
                pass

        page.on("request", handle_request)

        urls_to_try = [
            "https://www.elkjop.no/",
            "https://www.elkjop.no/search?query=tv",
        ]

        for url in urls_to_try:
            if found_key:
                break

            try:
                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )

                for _ in range(20):
                    if found_key:
                        break
                    page.wait_for_timeout(250)

            except Exception:
                pass

        browser.close()

    if not found_key:
        raise RuntimeError(
            "Fant ikke Algolia-nøkkel automatisk. "
            "Prøv å kjøre programmet igjen."
        )

    valid_until = decode_key_valid_until(found_key)

    if valid_until:
        seconds_left = int(valid_until - time.time())

        print(
            f"✓ Fersk nøkkel funnet "
            f"({seconds_left // 60} min "
            f"{seconds_left % 60} sek igjen)"
        )
    else:
        print("✓ Fersk Algolia-nøkkel funnet.")

    return found_key, valid_until


def refresh_algolia_key():
    global ALGOLIA_API_KEY
    global ALGOLIA_KEY_VALID_UNTIL

    key, valid_until = get_fresh_algolia_key()

    ALGOLIA_API_KEY = key
    ALGOLIA_KEY_VALID_UNTIL = valid_until


def ensure_valid_algolia_key():
    if not ALGOLIA_API_KEY:
        refresh_algolia_key()
        return

    if ALGOLIA_KEY_VALID_UNTIL:
        seconds_left = ALGOLIA_KEY_VALID_UNTIL - time.time()

        if seconds_left <= KEY_REFRESH_MARGIN:
            print()
            print("♻️ Algolia-nøkkelen nærmer seg utløp.")
            refresh_algolia_key()


# ============================================================
# ALGOLIA REQUEST
# ============================================================

def algolia_request(request_data):
    global ALGOLIA_API_KEY
    global ALGOLIA_KEY_VALID_UNTIL

    ensure_valid_algolia_key()

    for attempt in range(3):
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "x-algolia-application-id": ALGOLIA_APP_ID,
            "x-algolia-api-key": ALGOLIA_API_KEY,
        }

        try:
            response = session.post(
                ALGOLIA_URL,
                headers=headers,
                json={"requests": [request_data]},
                timeout=60,
            )

        except requests.RequestException as error:
            if attempt < 2:
                print(f"⚠ Nettverksfeil mot Algolia: {error}")
                time.sleep(2)
                continue
            raise

        if response.status_code == 200:
            data = response.json()
            results = data.get("results", [])

            if not results:
                raise RuntimeError("Algolia returnerte ingen resultater.")

            return results[0]

        text = response.text
        lower_text = text.lower()

        key_problem = (
            "validuntil" in lower_text
            or "expired" in lower_text
            or "invalid api key" in lower_text
        )

        if key_problem:
            print()
            print("♻️ Algolia-nøkkelen er ugyldig eller utløpt.")
            print("   Henter ny automatisk ...")

            ALGOLIA_API_KEY = None
            ALGOLIA_KEY_VALID_UNTIL = None

            refresh_algolia_key()
            continue

        if (
            response.status_code in [429, 500, 502, 503, 504]
            and attempt < 2
        ):
            wait = (attempt + 1) * 2
            print(
                f"⚠ Algolia HTTP {response.status_code}. "
                f"Prøver igjen om {wait} sek ..."
            )
            time.sleep(wait)
            continue

        raise RuntimeError(
            f"Algolia svarte HTTP {response.status_code}: "
            f"{text[:1500]}"
        )

    raise RuntimeError(
        "Algolia-request feilet etter automatisk nøkkelfornying."
    )


# ============================================================
# BUTIKKER
# ============================================================

def get_stores():
    params = {
        "batch": "1",
        "input": '{"0":{"articles":["1059795"]}}',
    }

    response = session.get(
        STOREFINDER_URL,
        params=params,
        timeout=30,
    )

    response.raise_for_status()

    raw_stores = (
        response.json()[0]
        ["result"]
        ["data"]
        ["stores"]
    )

    stores = {}

    for store in raw_stores:
        store_id = str(store.get("id", ""))
        name = store.get("displayName", "")

        if not store_id:
            continue

        if any(
            word.lower() in name.lower()
            for word in EXCLUDE_STORE_WORDS
        ):
            continue

        stores[store_id] = name

    return stores


# ============================================================
# TEST
# ============================================================

def test_algolia():
    print("Tester Algolia ...")

    result = algolia_request(
        {
            "indexName": INDEX_NAME,
            "query": "1059795",
            "hitsPerPage": 10,
            "page": 0,
            "attributesToRetrieve": [
                "articleNumber",
                "title",
                "sellerName",
            ],
        }
    )

    hits = result.get("hits", [])

    found = any(
        str(hit.get("articleNumber")) == "1059795"
        for hit in hits
    )

    if found:
        print("✓ Algolia fungerer.")
    else:
        print("⚠ Algolia fungerer, men test-SKU ble ikke funnet.")

    print()


# ============================================================
# KATEGORIER
# ============================================================

def get_taxonomy_facets():
    print("Henter produktkategorier for Elkjøp-selger ...")

    request_data = {
        "indexName": INDEX_NAME,
        "query": "",
        "hitsPerPage": 0,
        "facets": ["productTaxonomy.id"],
        "maxValuesPerFacet": 1000,
        "analytics": False,
        "clickAnalytics": False,
        "facetFilters": BASE_FILTERS,
    }

    result = algolia_request(request_data)

    taxonomy = (
        result.get("facets", {})
        .get("productTaxonomy.id", {})
    )

    categories = []

    for taxonomy_id, count in taxonomy.items():
        categories.append(
            {
                "id": taxonomy_id,
                "count": int(count),
            }
        )

    categories.sort(key=lambda x: x["count"])
    return categories


def make_category_filters(taxonomy_id):
    return BASE_FILTERS + [f"productTaxonomy.id:{taxonomy_id}"]


def add_filter(filters, facet_name, facet_value):
    return list(filters) + [f"{facet_name}:{facet_value}"]


# ============================================================
# SPLITTING
# ============================================================

def find_best_split(filters, parent_count):
    request_data = {
        "indexName": INDEX_NAME,
        "query": "",
        "hitsPerPage": 0,
        "facets": SPLIT_FACETS,
        "maxValuesPerFacet": 1000,
        "analytics": False,
        "clickAnalytics": False,
        "facetFilters": filters,
    }

    result = algolia_request(request_data)
    facets = result.get("facets", {})

    candidates = []

    allowed_difference = max(
        SPLIT_TOLERANCE_MIN,
        int(parent_count * SPLIT_TOLERANCE_PERCENT),
    )

    for facet_name in SPLIT_FACETS:
        values = facets.get(facet_name, {})

        if not isinstance(values, dict):
            continue

        if len(values) < 2:
            continue

        counts = [int(count) for count in values.values()]

        if not counts:
            continue

        total = sum(counts)
        difference = abs(total - parent_count)

        if difference > allowed_difference:
            continue

        largest_bucket = max(counts)

        if largest_bucket >= parent_count:
            continue

        candidates.append(
            {
                "facet": facet_name,
                "values": values,
                "largest": largest_bucket,
                "bucket_count": len(values),
                "difference": difference,
            }
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: (
            x["largest"],
            x["difference"],
            -x["bucket_count"],
        )
    )

    return candidates[0]


def split_partition(
    filters,
    count,
    description,
    root_category,
    depth=0,
):
    indent = "   " * depth

    if count <= MAX_RETRIEVABLE:
        return [
            {
                "filters": filters,
                "count": count,
                "description": description,
            }
        ]

    if depth >= MAX_SPLIT_DEPTH:
        print(
            f"{indent}⚠ Maks splittdybde: "
            f"{description} ({count:,})"
        )

        UNRESOLVED_SPLITS.append(
            {
                "category": root_category,
                "count": count,
                "reason": "Maks splittdybde: " + description,
            }
        )
        return []

    print(f"{indent}↳ Splitter {description} ({count:,})")

    split = find_best_split(filters, count)

    if split is None:
        print(
            f"{indent}⚠ Fant ingen sikker split for "
            f"{description} ({count:,})"
        )

        UNRESOLVED_SPLITS.append(
            {
                "category": root_category,
                "count": count,
                "reason": "Kunne ikke splitte: " + description,
            }
        )
        return []

    print(
        f"{indent}   bruker facet: "
        f"{split['facet']} "
        f"({split['bucket_count']} grupper, "
        f"største {split['largest']:,}, "
        f"avvik {split['difference']})"
    )

    final_partitions = []

    for facet_value, bucket_count in split["values"].items():
        bucket_count = int(bucket_count)

        child_filters = add_filter(
            filters,
            split["facet"],
            facet_value,
        )

        child_description = (
            f"{description} / "
            f"{split['facet']}={facet_value}"
        )

        child_partitions = split_partition(
            child_filters,
            bucket_count,
            child_description,
            root_category,
            depth + 1,
        )

        final_partitions.extend(child_partitions)

    return final_partitions


# ============================================================
# HENT PRODUKTER
# ============================================================

def fetch_partition(filters, expected_count):
    first_request = {
        "indexName": INDEX_NAME,
        "query": "",
        "page": 0,
        "hitsPerPage": HITS_PER_PAGE,
        "attributesToRetrieve": PRODUCT_ATTRIBUTES,
        "analytics": False,
        "clickAnalytics": False,
        "facetFilters": filters,
    }

    first_result = algolia_request(first_request)

    actual_total = int(
        first_result.get("nbHits", expected_count)
    )

    if actual_total > MAX_RETRIEVABLE:
        raise RuntimeError(
            f"Partisjon har nå {actual_total:,} produkter "
            f"og må splittes videre."
        )

    all_hits = list(first_result.get("hits", []))

    pages = math.ceil(actual_total / HITS_PER_PAGE)

    for page in range(1, pages):
        request_data = {
            "indexName": INDEX_NAME,
            "query": "",
            "page": page,
            "hitsPerPage": HITS_PER_PAGE,
            "attributesToRetrieve": PRODUCT_ATTRIBUTES,
            "analytics": False,
            "clickAnalytics": False,
            "facetFilters": filters,
        }

        result = algolia_request(request_data)
        hits = result.get("hits", [])
        all_hits.extend(hits)

        if not hits:
            break

        time.sleep(0.03)

    return all_hits


# ============================================================
# LAGER
# ============================================================

def get_stocked_store_ids(hit):
    stores_with_stock = hit.get("storesWithStock")

    if isinstance(stores_with_stock, list):
        return {
            str(store_id)
            for store_id in stores_with_stock
        }

    department_stock = hit.get("departmentStock")

    if not isinstance(department_stock, dict):
        return set()

    stocked = set()

    for store_id, stock in department_stock.items():
        if not isinstance(stock, dict):
            continue

        if stock.get("inStock") is True:
            stocked.add(str(store_id))

    return stocked


# ============================================================
# ANALYSE
# ============================================================

def analyse_product(hit, comparison_store_ids):
    sku = str(hit.get("articleNumber", "")).strip()

    if not sku:
        return None

    if hit.get("sellerName") != "Elkjøp":
        return None

    wholesale_status = hit.get("wholesalesStatus")

    if wholesale_status and wholesale_status != "ACT":
        return None

    retail_status = hit.get("retailSalesStatus")

    if retail_status and retail_status != "ACT":
        return None

    if hit.get("isBuyableInStore") is False:
        return None

    stocked_ids = get_stocked_store_ids(hit)

    if STORD_ID in stocked_ids:
        return None

    other_stores = comparison_store_ids - {STORD_ID}
    stocked_other = stocked_ids & other_stores

    stores_with_stock = len(stocked_other)
    stores_checked = len(other_stores)

    if stores_checked == 0:
        return None

    percentage = (
        stores_with_stock
        / stores_checked
        * 100
    )

    if stores_with_stock < MIN_STORES_WITH_STOCK:
        return None

    if percentage < MIN_PERCENT:
        return None

    taxonomy = hit.get("productTaxonomy", [])

    category = ""
    taxonomy_id = ""

    if isinstance(taxonomy, list) and taxonomy:
        last = taxonomy[-1]

        if isinstance(last, dict):
            category = last.get("name", "")
            taxonomy_id = last.get("id", "")

    if percentage >= 90:
        priority = "KRITISK"
    elif percentage >= 80:
        priority = "HØY"
    else:
        priority = "MIDDELS"

    url = hit.get("urlB2C", "") or ""
    if url.startswith("/"):
        url = "https://www.elkjop.no" + url

    return {
        "priority": priority,
        "percentage": round(percentage, 1),
        "stores_with_stock": stores_with_stock,
        "stores_checked": stores_checked,
        "sku": sku,
        "title": hit.get("title", ""),
        "brand": hit.get("brand", ""),
        "seller": hit.get("sellerName", ""),
        "category": category,
        "taxonomy_id": taxonomy_id,
        "url": url,
    }


# ============================================================
# CSV
# ============================================================

def save_results(results):
    filename = "stord_mangler_elkjop.csv"

    columns = [
        "priority",
        "percentage",
        "stores_with_stock",
        "stores_checked",
        "sku",
        "title",
        "brand",
        "seller",
        "category",
        "taxonomy_id",
        "url",
    ]

    with open(
        filename,
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=columns,
        )
        writer.writeheader()
        writer.writerows(results)

    return filename


def save_unresolved():
    if not UNRESOLVED_SPLITS:
        return

    unique = {}

    for item in UNRESOLVED_SPLITS:
        key = (
            item["category"],
            item["count"],
            item["reason"],
        )
        unique[key] = item

    with open(
        "ulosbare_grupper.csv",
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "category",
                "count",
                "reason",
            ],
        )
        writer.writeheader()
        writer.writerows(unique.values())


# ============================================================
# MAIN
# ============================================================

def main():
    print()
    print("=" * 80)
    print("ELKJØP STORD - FULL LAGERANALYSE")
    print("Kun produkter solgt av Elkjøp")
    print("Algolia-nøkkel: AUTOMATISK")
    print("=" * 80)
    print()

    refresh_algolia_key()

    print()
    print("Henter butikklisten ...")

    stores = get_stores()

    print(
        f"✓ Fant {len(stores)} "
        f"sammenligningsbutikker."
    )

    if STORD_ID not in stores:
        raise RuntimeError("Fant ikke Elkjøp Stord.")

    print(f"✓ {STORD_ID} = {stores[STORD_ID]}")
    print()

    test_algolia()

    categories = get_taxonomy_facets()

    print(
        f"✓ Fant {len(categories)} "
        f"kategorier for Elkjøp-produkter."
    )

    oversized = sum(
        1
        for category in categories
        if category["count"] > MAX_RETRIEVABLE
    )

    print(f"✓ {oversized} kategorier må splittes.")
    print()

    unique_products = {}
    total_categories = len(categories)

    for category_number, category in enumerate(
        categories,
        start=1,
    ):
        taxonomy_id = category["id"]
        count = category["count"]

        print()
        print(
            f"[{category_number}/{total_categories}] "
            f"{taxonomy_id} ({count:,} produkter)"
        )

        filters = make_category_filters(taxonomy_id)

        if count <= MAX_RETRIEVABLE:
            partitions = [
                {
                    "filters": filters,
                    "count": count,
                    "description": taxonomy_id,
                }
            ]
        else:
            partitions = split_partition(
                filters,
                count,
                taxonomy_id,
                taxonomy_id,
            )

        downloaded = 0
        new_skus = 0

        for partition in partitions:
            try:
                hits = fetch_partition(
                    partition["filters"],
                    partition["count"],
                )

            except Exception as error:
                print("   ⚠ FEIL:", error)

                UNRESOLVED_SPLITS.append(
                    {
                        "category": taxonomy_id,
                        "count": partition["count"],
                        "reason": str(error),
                    }
                )
                continue

            downloaded += len(hits)

            for hit in hits:
                sku = str(
                    hit.get("articleNumber", "")
                ).strip()

                if not sku:
                    continue

                if hit.get("sellerName") != "Elkjøp":
                    continue

                if sku not in unique_products:
                    new_skus += 1

                unique_products[sku] = hit

        print(f"   hentet: {downloaded:,}")
        print(f"   nye SKU-er: {new_skus:,}")
        print(
            f"   TOTALT UNIKE: "
            f"{len(unique_products):,}"
        )

    print()
    print("=" * 80)
    print(
        f"UNIKE ELKJØP-SKU-ER: "
        f"{len(unique_products):,}"
    )
    print("=" * 80)
    print()

    comparison_ids = set(stores.keys())
    results = []
    total_products = len(unique_products)

    for number, hit in enumerate(
        unique_products.values(),
        start=1,
    ):
        item = analyse_product(
            hit,
            comparison_ids,
        )

        if item:
            results.append(item)

        if number % 5000 == 0:
            print(
                f"Analysert "
                f"{number:,} / "
                f"{total_products:,}"
            )

    results.sort(
        key=lambda item: (
            item["percentage"],
            item["stores_with_stock"],
        ),
        reverse=True,
    )

    filename = save_results(results)
    save_unresolved()

    critical = sum(
        1 for x in results
        if x["priority"] == "KRITISK"
    )
    high = sum(
        1 for x in results
        if x["priority"] == "HØY"
    )
    medium = sum(
        1 for x in results
        if x["priority"] == "MIDDELS"
    )

    print()
    print("=" * 80)
    print("FERDIG")
    print("=" * 80)
    print()

    print(
        f"Unike Elkjøp-SKU-er undersøkt: "
        f"{len(unique_products):,}"
    )
    print(
        f"Stord-mangler funnet: "
        f"{len(results):,}"
    )
    print()

    print(f"🔴 KRITISK ≥90%: {critical}")
    print(f"🟠 HØY 80–89,9%: {high}")
    print(f"🟡 MIDDELS 70–79,9%: {medium}")

    print()
    print("TOPP 50 MANGLER")
    print("-" * 80)

    for position, item in enumerate(
        results[:50],
        start=1,
    ):
        print(
            f"{position:>3}. "
            f"{item['percentage']:>5.1f}% | "
            f"{item['sku']} | "
            f"{item['title']}"
        )
        print(
            f"     "
            f"{item['stores_with_stock']} av "
            f"{item['stores_checked']} "
            f"andre butikker har varen"
        )

    print()
    print(f"Rapport: {filename}")

    if UNRESOLVED_SPLITS:
        unique_unresolved = {
            (
                x["category"],
                x["count"],
                x["reason"],
            )
            for x in UNRESOLVED_SPLITS
        }

        print()
        print(
            f"⚠ {len(unique_unresolved)} "
            f"grupper kunne ikke "
            f"hentes fullstendig."
        )
        print("Se: ulosbare_grupper.csv")
    else:
        print()
        print(
            "✓ Alle Elkjøp-kategorier "
            "ble hentet fullstendig."
        )

    print()


if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        print()
        print("Avbrutt av bruker.")
        sys.exit(1)

    except Exception as error:
        print()
        print("=" * 80)
        print("FEIL")
        print("=" * 80)
        print(error)
        print()
        sys.exit(1)
