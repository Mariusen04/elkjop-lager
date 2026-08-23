import requests
import csv
import time
import math
import sys
import os
import base64
import sqlite3
import json
import gc
import secrets
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

from playwright.sync_api import sync_playwright


# ============================================================
# INNSTILLINGER
# ============================================================

STORD_ID = "1273"

MIN_PERCENT = 70.0
MIN_STORES_WITH_STOCK = 10

EXCLUDE_STORE_WORDS = ["Phonehouse", "Outlet"]

ALGOLIA_APP_ID = "Z0FL7R8UBH"
INDEX_NAME = "commerce_b2c_OCNOELK"
ALGOLIA_URL = "https://z0fl7r8ubh-dsn.algolia.net/1/indexes/*/queries"
STOREFINDER_URL = "https://www.elkjop.no/api/trpc/location.getStoreFinderStores"

MAX_RETRIEVABLE = 1500
PARTITION_TARGET = 1400
MAX_FILTER_VALUES_PER_GROUP = 200
HITS_PER_PAGE = 500
MAX_SPLIT_DEPTH = 10

KEY_REFRESH_MARGIN = 75

BASE_FILTERS = ["sellerName:Elkjøp"]

SPLIT_FACETS = [
    "ptLowestLevelNodeValue",
    "brand",
    "price.amount",
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
    "productTaxonomy",
]

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(
    os.environ.get("APP_DATA_DIR", str(APP_DIR))
).expanduser().resolve()

DB_FILE = DATA_DIR / "elkjop_products.sqlite3"
META_FILE = DATA_DIR / "elkjop_run_meta.json"
REPORT_FILE = DATA_DIR / "stord_mangler_elkjop.csv"
UNRESOLVED_FILE = DATA_DIR / "ulosbare_grupper.csv"
LOCK_FILE = DATA_DIR / ".analyse.lock"
STATUS_FILE = DATA_DIR / "analyse_status.json"
LOCK_MAX_AGE_SECONDS = 12 * 60 * 60

session = requests.Session()
ALGOLIA_API_KEY = None
ALGOLIA_KEY_VALID_UNTIL = None
UNRESOLVED = []


class CatalogChangedError(RuntimeError):
    """Katalogen endret seg mellom planlegging og nedlasting."""


def write_analysis_status(status, **details):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": status,
        "updated_at": time.time(),
        **details,
    }
    temporary_file = STATUS_FILE.with_suffix(".json.tmp")
    temporary_file.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temporary_file, STATUS_FILE)


def acquire_analysis_lock():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if LOCK_FILE.exists():
        try:
            if time.time() - LOCK_FILE.stat().st_mtime > LOCK_MAX_AGE_SECONDS:
                LOCK_FILE.unlink()
        except FileNotFoundError:
            pass

    token = secrets.token_hex(16)
    payload = {
        "token": token,
        "started_at": time.time(),
        "pid": os.getpid(),
    }

    try:
        descriptor = os.open(
            LOCK_FILE,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
    except FileExistsError as error:
        raise RuntimeError("En annen analyse kjører allerede.") from error

    with os.fdopen(descriptor, "w", encoding="utf-8") as lock:
        json.dump(payload, lock)

    return token


def release_analysis_lock(token):
    try:
        payload = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
        if payload.get("token") == token:
            LOCK_FILE.unlink(missing_ok=True)
    except (FileNotFoundError, OSError, ValueError, TypeError):
        pass


# ============================================================
# ALGOLIA-NØKKEL
# ============================================================

def decode_key_valid_until(key):
    try:
        padded = key + "=" * (-len(key) % 4)
        decoded = base64.b64decode(padded).decode("utf-8", errors="ignore")
        values = parse_qs(decoded, keep_blank_values=True).get("validUntil")
        return int(values[0]) if values else None
    except Exception:
        return None


def get_fresh_algolia_key():
    print("\n🔑 Henter fersk Algolia-nøkkel automatisk ...", flush=True)
    found_key = None

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )

        context = browser.new_context(
            locale="nb-NO",
            viewport={"width": 800, "height": 600},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/150.0.0.0 Safari/537.36"
            ),
        )

        def route_handler(route, request):
            if request.resource_type in {"image", "media", "font"}:
                route.abort()
            else:
                route.continue_()

        context.route("**/*", route_handler)
        page = context.new_page()

        def handle_request(request):
            nonlocal found_key

            if "algolia.net" not in request.url or "x-algolia-api-key" not in request.url:
                return

            try:
                keys = parse_qs(urlparse(request.url).query).get("x-algolia-api-key")
                if keys:
                    key = unquote(keys[0])
                    if len(key) > 50:
                        found_key = key
            except Exception:
                pass

        page.on("request", handle_request)

        for url in [
            "https://www.elkjop.no/",
            "https://www.elkjop.no/search?query=tv",
        ]:
            if found_key:
                break

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)

                for _ in range(30):
                    if found_key:
                        break
                    page.wait_for_timeout(250)

            except Exception:
                pass

        try:
            page.close()
        except Exception:
            pass

        try:
            context.close()
        except Exception:
            pass

        browser.close()

    gc.collect()

    if not found_key:
        raise RuntimeError("Fant ikke Algolia-nøkkel automatisk.")

    valid_until = decode_key_valid_until(found_key)

    if valid_until:
        left = max(0, int(valid_until - time.time()))
        print(
            f"✓ Fersk nøkkel funnet ({left // 60} min {left % 60} sek igjen)",
            flush=True,
        )
    else:
        print("✓ Fersk Algolia-nøkkel funnet.", flush=True)

    return found_key, valid_until


def refresh_algolia_key():
    global ALGOLIA_API_KEY, ALGOLIA_KEY_VALID_UNTIL
    ALGOLIA_API_KEY, ALGOLIA_KEY_VALID_UNTIL = get_fresh_algolia_key()


def ensure_valid_algolia_key():
    if not ALGOLIA_API_KEY:
        refresh_algolia_key()
        return

    if ALGOLIA_KEY_VALID_UNTIL:
        if ALGOLIA_KEY_VALID_UNTIL - time.time() <= KEY_REFRESH_MARGIN:
            print("\n♻️ Algolia-nøkkelen nærmer seg utløp.", flush=True)
            refresh_algolia_key()


def algolia_request(request_data):
    global ALGOLIA_API_KEY, ALGOLIA_KEY_VALID_UNTIL

    ensure_valid_algolia_key()

    for attempt in range(3):
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "x-algolia-application-id": ALGOLIA_APP_ID,
            "x-algolia-api-key": ALGOLIA_API_KEY,
        }

        try:
            r = session.post(
                ALGOLIA_URL,
                headers=headers,
                json={"requests": [request_data]},
                timeout=60,
            )
        except requests.RequestException as e:
            if attempt < 2:
                print(f"⚠ Nettverksfeil mot Algolia: {e}", flush=True)
                time.sleep(2)
                continue
            raise

        if r.status_code == 200:
            results = r.json().get("results", [])
            if not results:
                raise RuntimeError("Algolia returnerte ingen resultater.")
            return results[0]

        text = r.text
        lower = text.lower()

        if "validuntil" in lower or "expired" in lower or "invalid api key" in lower:
            print(
                "\n♻️ Algolia-nøkkelen er utløpt. Henter ny automatisk ...",
                flush=True,
            )
            ALGOLIA_API_KEY = None
            ALGOLIA_KEY_VALID_UNTIL = None
            refresh_algolia_key()
            continue

        if r.status_code in [429, 500, 502, 503, 504] and attempt < 2:
            time.sleep((attempt + 1) * 2)
            continue

        raise RuntimeError(f"Algolia HTTP {r.status_code}: {text[:1200]}")

    raise RuntimeError("Algolia-request feilet etter tre forsøk.")


# ============================================================
# BUTIKKER / KATEGORIER
# ============================================================

def get_stores():
    params = {
        "batch": "1",
        "input": '{"0":{"articles":["1059795"]}}',
    }

    r = session.get(STOREFINDER_URL, params=params, timeout=30)
    r.raise_for_status()
    raw = r.json()[0]["result"]["data"]["stores"]

    stores = {}

    for store in raw:
        sid = str(store.get("id", ""))
        name = store.get("displayName", "")

        if not sid:
            continue

        if any(word.lower() in name.lower() for word in EXCLUDE_STORE_WORDS):
            continue

        stores[sid] = name

    return stores


def test_algolia():
    print("Tester Algolia ...", flush=True)

    result = algolia_request({
        "indexName": INDEX_NAME,
        "query": "1059795",
        "hitsPerPage": 10,
        "page": 0,
        "attributesToRetrieve": ["articleNumber", "title", "sellerName"],
    })

    found = any(
        str(h.get("articleNumber")) == "1059795"
        for h in result.get("hits", [])
    )

    print(
        "✓ Algolia fungerer."
        if found
        else "⚠ Algolia fungerer, men test-SKU ble ikke funnet.",
        flush=True,
    )
    print(flush=True)


def get_catalog_count():
    print("Henter størrelsen på Elkjøp-katalogen ...", flush=True)

    result = algolia_request({
        "indexName": INDEX_NAME,
        "query": "",
        "hitsPerPage": 0,
        "facets": ["brand"],
        "maxValuesPerFacet": 1000,
        "analytics": False,
        "clickAnalytics": False,
        "facetFilters": BASE_FILTERS,
    })

    exhaustive = result.get("exhaustive")
    count_is_exhaustive = result.get("exhaustiveNbHits", True)

    if isinstance(exhaustive, dict):
        count_is_exhaustive = exhaustive.get(
            "nbHits",
            count_is_exhaustive,
        )

    if count_is_exhaustive is False:
        raise RuntimeError(
            "Algolia returnerte et omtrentlig katalogtall. "
            "Analysen avbrytes for å unngå hull."
        )

    catalog_count = int(result.get("nbHits", 0))
    brand_values = result.get("facets", {}).get("brand", {})
    brand_count = sum(int(count) for count in brand_values.values())

    if len(brand_values) >= 1000 or brand_count != catalog_count:
        raise RuntimeError(
            "Merke-facetten dekker ikke katalogen nøyaktig "
            f"({brand_count:,} av {catalog_count:,})."
        )

    return catalog_count


def make_facet_filter(facet_name, facet_value):
    value = str(facet_value)

    if value.startswith("-"):
        value = "\\" + value

    return f"{facet_name}:{value}"


def add_filter(filters, facet_name, facet_value):
    return list(filters) + [make_facet_filter(facet_name, facet_value)]


def add_filter_group(filters, facet_name, facet_values):
    group = [
        make_facet_filter(facet_name, value)
        for value in facet_values
    ]

    if len(group) == 1:
        return list(filters) + group

    return list(filters) + [group]


# ============================================================
# SQLITE
# ============================================================

def create_database():
    if DB_FILE.exists():
        DB_FILE.unlink()

    conn = sqlite3.connect(DB_FILE)
    configure_db(conn)

    conn.execute("""
        CREATE TABLE products (
            sku TEXT PRIMARY KEY,
            title TEXT,
            brand TEXT,
            seller TEXT,
            url TEXT,
            wholesale_status TEXT,
            retail_status TEXT,
            is_buyable INTEGER,
            stores_json TEXT,
            category TEXT,
            taxonomy_id TEXT,
            taxonomy_path TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE unresolved (
            category TEXT,
            count INTEGER,
            reason TEXT,
            UNIQUE(category, count, reason)
        )
    """)

    conn.commit()
    conn.close()


def open_database():
    conn = sqlite3.connect(DB_FILE)
    configure_db(conn)
    return conn


def configure_db(conn):
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=FILE")
    conn.execute("PRAGMA cache_size=-8000")


def db_count(conn):
    return int(conn.execute("SELECT COUNT(*) FROM products").fetchone()[0])


def save_unresolved(conn):
    if not UNRESOLVED:
        return

    conn.executemany(
        """
        INSERT OR IGNORE INTO unresolved(category, count, reason)
        VALUES (?, ?, ?)
        """,
        [
            (x["category"], int(x["count"]), x["reason"])
            for x in UNRESOLVED
        ],
    )
    conn.commit()


# ============================================================
# SPLITTING
# ============================================================

def find_best_split(filters, parent_count):
    result = algolia_request({
        "indexName": INDEX_NAME,
        "query": "",
        "hitsPerPage": 0,
        "facets": SPLIT_FACETS,
        "maxValuesPerFacet": 1000,
        "analytics": False,
        "clickAnalytics": False,
        "facetFilters": filters,
    })

    current_count = int(result.get("nbHits", parent_count))
    facets = result.get("facets", {})

    if current_count != parent_count:
        raise CatalogChangedError(
            "Katalogen endret seg under planleggingen "
            f"({parent_count:,} planlagt, {current_count:,} nå)."
        )

    exhaustive = result.get("exhaustive")
    facets_are_exhaustive = result.get("exhaustiveFacetsCount", True)

    if isinstance(exhaustive, dict):
        facets_are_exhaustive = exhaustive.get(
            "facetsCount",
            facets_are_exhaustive,
        )

    if facets_are_exhaustive is False:
        print(
            "   ⚠ Algolia returnerte omtrentlige facet-tall; "
            "splitten avvises.",
            flush=True,
        )
        return None

    candidates = []

    for facet_name in SPLIT_FACETS:
        values = facets.get(facet_name, {})

        if not isinstance(values, dict) or len(values) < 2:
            continue

        counts = [int(v) for v in values.values()]

        if not counts:
            continue

        difference = abs(sum(counts) - current_count)
        largest = max(counts)

        # Gruppering med OR-filter er bare tapsfri når facet-verdiene dekker
        # hele utvalget nøyaktig én gang. Avvis derfor både manglende verdier
        # og array-facets med overlappende tellinger.
        if difference != 0 or largest >= current_count:
            continue

        candidates.append({
            "facet": facet_name,
            "values": values,
            "largest": largest,
            "bucket_count": len(values),
            "difference": difference,
        })

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


def pack_facet_values(values):
    """Pakker gjensidig utelukkende facet-verdier i grupper under API-taket."""
    packed = []

    for facet_value, raw_count in sorted(
        values.items(),
        key=lambda item: int(item[1]),
        reverse=True,
    ):
        count = int(raw_count)

        if count <= 0 or count > PARTITION_TARGET:
            continue

        for group in packed:
            if (
                len(group["values"]) < MAX_FILTER_VALUES_PER_GROUP
                and group["count"] + count <= PARTITION_TARGET
            ):
                group["values"].append(facet_value)
                group["count"] += count
                break
        else:
            packed.append({
                "values": [facet_value],
                "count": count,
            })

    return packed


def split_partition(filters, count, description, root_category, depth=0):
    indent = "   " * depth

    if count <= MAX_RETRIEVABLE:
        return [{
            "filters": filters,
            "count": count,
            "description": description,
        }]

    if depth >= MAX_SPLIT_DEPTH:
        UNRESOLVED.append({
            "category": root_category,
            "count": count,
            "reason": "Maks splittdybde: " + description,
        })
        return []

    print(f"{indent}↳ Splitter {description} ({count:,})", flush=True)

    split = find_best_split(filters, count)

    if split is None:
        print(
            f"{indent}⚠ Fant ingen sikker split for {description} ({count:,})",
            flush=True,
        )

        UNRESOLVED.append({
            "category": root_category,
            "count": count,
            "reason": "Kunne ikke splitte: " + description,
        })

        return []

    print(
        f"{indent}   bruker facet: {split['facet']} "
        f"({split['bucket_count']} verdier, "
        f"største {split['largest']:,}, nøyaktig dekning)",
        flush=True,
    )

    final_partitions = []

    oversized_values = [
        (facet_value, int(bucket_count))
        for facet_value, bucket_count in split["values"].items()
        if int(bucket_count) > PARTITION_TARGET
    ]

    for facet_value, bucket_count in sorted(
        oversized_values,
        key=lambda item: item[1],
        reverse=True,
    ):
        child_filters = add_filter(filters, split["facet"], facet_value)

        child_description = (
            f"{description} / {split['facet']}={facet_value}"
        )

        final_partitions.extend(
            split_partition(
                child_filters,
                int(bucket_count),
                child_description,
                root_category,
                depth + 1,
            )
        )

    packed_groups = pack_facet_values(split["values"])

    for group_number, group in enumerate(packed_groups, start=1):
        group_filters = add_filter_group(
            filters,
            split["facet"],
            group["values"],
        )
        group_description = (
            f"{description} / {split['facet']}-gruppe-{group_number} "
            f"({len(group['values'])} verdier)"
        )

        final_partitions.append({
            "filters": group_filters,
            "count": group["count"],
            "description": group_description,
        })

    return final_partitions


# ============================================================
# PRODUKTER
# ============================================================

def get_stocked_store_ids(hit):
    stores = hit.get("storesWithStock")

    if isinstance(stores, list):
        return {str(x) for x in stores}

    department = hit.get("departmentStock")

    if not isinstance(department, dict):
        return set()

    return {
        str(store_id)
        for store_id, stock in department.items()
        if isinstance(stock, dict) and stock.get("inStock") is True
    }


def hit_to_row(hit):
    sku = str(hit.get("articleNumber", "")).strip()

    if not sku or hit.get("sellerName") != "Elkjøp":
        return None

    taxonomy = hit.get("productTaxonomy", [])
    category = ""
    taxonomy_id = ""
    taxonomy_names = []

    if isinstance(taxonomy, list):
        for node in taxonomy:
            if not isinstance(node, dict):
                continue

            name = str(node.get("name", "") or "").strip()
            if name:
                taxonomy_names.append(name)

        if taxonomy:
            last_node = taxonomy[-1]
            if isinstance(last_node, dict):
                category = str(last_node.get("name", "") or "").strip()
                taxonomy_id = str(last_node.get("id", "") or "").strip()

    taxonomy_path = " > ".join(taxonomy_names)

    url = str(hit.get("urlB2C", "") or "")

    if url.startswith("/"):
        url = "https://www.elkjop.no" + url

    buyable = hit.get("isBuyableInStore")
    buyable_db = 1 if buyable is True else 0 if buyable is False else None

    return (
        sku,
        str(hit.get("title", "") or ""),
        str(hit.get("brand", "") or ""),
        "Elkjøp",
        url,
        str(hit.get("wholesalesStatus", "") or ""),
        str(hit.get("retailSalesStatus", "") or ""),
        buyable_db,
        json.dumps(
            sorted(get_stocked_store_ids(hit)),
            separators=(",", ":"),
        ),
        category,
        taxonomy_id,
        taxonomy_path,
    )


def insert_hits(conn, hits):
    rows = []

    for hit in hits:
        row = hit_to_row(hit)
        if row:
            rows.append(row)

    if not rows:
        return 0

    before = conn.total_changes

    conn.executemany("""
        INSERT OR IGNORE INTO products (
            sku,
            title,
            brand,
            seller,
            url,
            wholesale_status,
            retail_status,
            is_buyable,
            stores_json,
            category,
            taxonomy_id,
            taxonomy_path
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)

    return conn.total_changes - before


def fetch_partition_to_db(conn, filters, expected_count):
    downloaded = 0
    new_skus = 0

    first = algolia_request({
        "indexName": INDEX_NAME,
        "query": "",
        "page": 0,
        "hitsPerPage": HITS_PER_PAGE,
        "attributesToRetrieve": PRODUCT_ATTRIBUTES,
        "analytics": False,
        "clickAnalytics": False,
        "facetFilters": filters,
    })

    actual_total = int(first.get("nbHits", expected_count))

    if actual_total != expected_count:
        raise CatalogChangedError(
            "Partisjonen endret størrelse under analysen "
            f"({expected_count:,} planlagt, {actual_total:,} nå)."
        )

    if actual_total > MAX_RETRIEVABLE:
        raise RuntimeError(
            f"Partisjon har nå {actual_total:,} produkter og må splittes videre."
        )

    hits = first.get("hits", [])
    downloaded += len(hits)
    new_skus += insert_hits(conn, hits)

    del hits, first

    pages = math.ceil(actual_total / HITS_PER_PAGE)

    for page in range(1, pages):
        result = algolia_request({
            "indexName": INDEX_NAME,
            "query": "",
            "page": page,
            "hitsPerPage": HITS_PER_PAGE,
            "attributesToRetrieve": PRODUCT_ATTRIBUTES,
            "analytics": False,
            "clickAnalytics": False,
            "facetFilters": filters,
        })

        hits = result.get("hits", [])
        downloaded += len(hits)
        new_skus += insert_hits(conn, hits)
        empty = not hits

        del hits, result

        if empty:
            break

    if downloaded != actual_total:
        raise CatalogChangedError(
            "Algolia leverte ikke alle produktene i partisjonen "
            f"({downloaded:,} av {actual_total:,})."
        )

    conn.commit()

    return downloaded, new_skus


# ============================================================
# KATALOGHENTING
# ============================================================

def fetch_catalog(conn, catalog_count):
    global UNRESOLVED

    UNRESOLVED = []

    print()
    print("Planlegger tapsfrie katalogpartisjoner ...", flush=True)

    partitions = split_partition(
        BASE_FILTERS,
        catalog_count,
        "hele-katalogen",
        "hele-katalogen",
    )
    planned_count = sum(
        int(partition["count"])
        for partition in partitions
    )

    print(
        f"✓ {len(partitions)} partisjoner dekker "
        f"{planned_count:,} av {catalog_count:,} produkter.",
        flush=True,
    )

    if planned_count != catalog_count:
        save_unresolved(conn)
        save_unresolved_csv(conn)
        raise RuntimeError(
            "Partisjonsplanen dekker ikke hele katalogen "
            f"({planned_count:,} av {catalog_count:,})."
        )

    for index, partition in enumerate(partitions, start=1):
        print()
        print(
            f"[{index}/{len(partitions)}] "
            f"partisjon-{index} ({partition['count']:,} produkter)",
            flush=True,
        )
        print(f"   {partition['description']}", flush=True)

        try:
            downloaded, new_skus = fetch_partition_to_db(
                conn,
                partition["filters"],
                partition["count"],
            )
        except CatalogChangedError:
            conn.rollback()
            raise
        except Exception as error:
            conn.rollback()
            print(f"   ⚠ FEIL: {error}", flush=True)

            UNRESOLVED.append({
                "category": partition["description"],
                "count": partition["count"],
                "reason": str(error),
            })
            continue

        print(f"   hentet: {downloaded:,}", flush=True)
        print(f"   nye SKU-er: {new_skus:,}", flush=True)
        print(f"   TOTALT UNIKE: {db_count(conn):,}", flush=True)

    save_unresolved(conn)

    if UNRESOLVED:
        save_unresolved_csv(conn)
        raise RuntimeError(
            f"{len(UNRESOLVED)} partisjoner kunne ikke hentes. "
            "Den forrige rapporten beholdes."
        )

    unique_count = db_count(conn)

    if unique_count != catalog_count:
        raise CatalogChangedError(
            "Kontrollen av unike SKU-er feilet "
            f"({unique_count:,} av {catalog_count:,}). "
            "Den forrige rapporten beholdes."
        )

    gc.collect()


# ============================================================
# SLUTTANALYSE
# ============================================================

def analyse_database(conn, comparison_ids):
    results = []

    other_stores = comparison_ids - {STORD_ID}
    stores_checked = len(other_stores)
    total = db_count(conn)

    cursor = conn.execute("""
        SELECT
            sku,
            title,
            brand,
            seller,
            url,
            wholesale_status,
            retail_status,
            is_buyable,
            stores_json,
            category,
            taxonomy_id,
            taxonomy_path
        FROM products
        ORDER BY rowid
    """)

    analysed = 0

    while True:
        rows = cursor.fetchmany(500)

        if not rows:
            break

        for row in rows:
            (
                sku,
                title,
                brand,
                seller,
                url,
                wholesale_status,
                retail_status,
                is_buyable,
                stores_json,
                category,
                taxonomy_id,
                taxonomy_path,
            ) = row

            analysed += 1

            if wholesale_status and wholesale_status != "ACT":
                continue

            if retail_status and retail_status != "ACT":
                continue

            if is_buyable == 0:
                continue

            try:
                stocked_ids = set(json.loads(stores_json or "[]"))
            except Exception:
                stocked_ids = set()

            if STORD_ID in stocked_ids:
                continue

            stores_with_stock = len(stocked_ids & other_stores)

            if stores_checked == 0:
                continue

            percentage = stores_with_stock / stores_checked * 100

            if stores_with_stock < MIN_STORES_WITH_STOCK:
                continue

            if percentage < MIN_PERCENT:
                continue

            priority = (
                "KRITISK"
                if percentage >= 90
                else "HØY"
                if percentage >= 80
                else "MIDDELS"
            )

            results.append({
                "priority": priority,
                "percentage": round(percentage, 1),
                "stores_with_stock": stores_with_stock,
                "stores_checked": stores_checked,
                "sku": sku,
                "title": title,
                "brand": brand,
                "seller": seller,
                "category": category,
                "taxonomy_id": taxonomy_id,
                "taxonomy_path": taxonomy_path,
                "url": url,
            })

        if analysed % 5000 < 500:
            print(
                f"Analysert {analysed:,} / {total:,}",
                flush=True,
            )

        del rows

    results.sort(
        key=lambda x: (
            x["percentage"],
            x["stores_with_stock"],
        ),
        reverse=True,
    )

    return results


def save_results(results):
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
        "taxonomy_path",
        "url",
    ]

    temporary_file = REPORT_FILE.with_suffix(".csv.tmp")

    with open(
        temporary_file,
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(results)

    # Behold den forrige rapporten helt til den nye er ferdig skrevet.
    os.replace(temporary_file, REPORT_FILE)


def save_unresolved_csv(conn):
    rows = conn.execute("""
        SELECT category, count, reason
        FROM unresolved
        ORDER BY category, count
    """).fetchall()

    if not rows:
        if UNRESOLVED_FILE.exists():
            UNRESOLVED_FILE.unlink()
        return 0

    temporary_file = UNRESOLVED_FILE.with_suffix(".csv.tmp")

    with open(
        temporary_file,
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as f:
        writer = csv.writer(f)
        writer.writerow(["category", "count", "reason"])
        writer.writerows(rows)

    os.replace(temporary_file, UNRESOLVED_FILE)

    return len(rows)


# ============================================================
# MASTER
# ============================================================

def master_main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print()
    print("=" * 80, flush=True)
    print("ELKJØP STORD - FULL LAGERANALYSE", flush=True)
    print("Kun produkter solgt av Elkjøp", flush=True)
    print("Algolia-nøkkel: AUTOMATISK", flush=True)
    print("Lagring: SQLITE", flush=True)
    print("Modus: ÉN TAPFRI KATALOGPASSERING", flush=True)
    print("=" * 80, flush=True)

    for path in [
        DB_FILE,
        META_FILE,
    ]:
        if path.exists():
            path.unlink()

    create_database()

    refresh_algolia_key()

    print("\nHenter butikklisten ...", flush=True)
    stores = get_stores()

    print(
        f"✓ Fant {len(stores)} sammenligningsbutikker.",
        flush=True,
    )

    if STORD_ID not in stores:
        raise RuntimeError("Fant ikke Elkjøp Stord.")

    print(f"✓ {STORD_ID} = {stores[STORD_ID]}\n", flush=True)

    test_algolia()
    catalog_count = get_catalog_count()

    print(
        f"✓ Fant {catalog_count:,} Elkjøp-produkter.",
        flush=True,
    )

    META_FILE.write_text(
        json.dumps(
            {
                "stores": stores,
                "catalog_count": catalog_count,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    conn = open_database()

    try:
        for attempt in range(2):
            try:
                fetch_catalog(conn, catalog_count)
                break
            except CatalogChangedError:
                if attempt == 1:
                    raise

                print()
                print(
                    "↻ Katalogen endret seg under hentingen. "
                    "Planlegger hele passeringen på nytt én gang ...",
                    flush=True,
                )

                conn.execute("DELETE FROM products")
                conn.execute("DELETE FROM unresolved")
                conn.commit()

                catalog_count = get_catalog_count()
                META_FILE.write_text(
                    json.dumps(
                        {
                            "stores": stores,
                            "catalog_count": catalog_count,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

        total_unique = db_count(conn)

        print()
        print("=" * 80, flush=True)
        print(
            f"UNIKE ELKJØP-SKU-ER: {total_unique:,}",
            flush=True,
        )
        print("=" * 80, flush=True)
        print(flush=True)

        results = analyse_database(
            conn,
            set(stores.keys()),
        )

        save_results(results)
        unresolved_count = save_unresolved_csv(conn)

    finally:
        conn.close()

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
    print("=" * 80, flush=True)
    print("FERDIG", flush=True)
    print("=" * 80, flush=True)
    print()
    print(
        f"Unike Elkjøp-SKU-er undersøkt: {total_unique:,}",
        flush=True,
    )
    print(
        f"Stord-mangler funnet: {len(results):,}",
        flush=True,
    )
    print()
    print(f"🔴 KRITISK ≥90%: {critical}", flush=True)
    print(f"🟠 HØY 80–89,9%: {high}", flush=True)
    print(f"🟡 MIDDELS 70–79,9%: {medium}", flush=True)

    print()
    print("TOPP 50 MANGLER", flush=True)
    print("-" * 80, flush=True)

    for pos, item in enumerate(results[:50], start=1):
        print(
            f"{pos:>3}. {item['percentage']:>5.1f}% | "
            f"{item['sku']} | {item['title']}",
            flush=True,
        )
        print(
            f"     {item['stores_with_stock']} av "
            f"{item['stores_checked']} andre butikker har varen",
            flush=True,
        )

    print(f"\nRapport: {REPORT_FILE}", flush=True)

    if unresolved_count:
        print(
            f"\n⚠ {unresolved_count} grupper kunne ikke hentes fullstendig.",
            flush=True,
        )
        print("Se: ulosbare_grupper.csv", flush=True)
    else:
        print(
            "\n✓ Hele Elkjøp-katalogen ble hentet fullstendig.",
            flush=True,
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    analysis_lock_token = None
    analysis_started_at = time.time()

    try:
        analysis_lock_token = acquire_analysis_lock()
        write_analysis_status(
            "running",
            started_at=analysis_started_at,
        )
        master_main()

    except KeyboardInterrupt:
        if analysis_lock_token is not None:
            write_analysis_status(
                "error",
                started_at=analysis_started_at,
                finished_at=time.time(),
                error="Avbrutt av bruker.",
            )
        print("\nAvbrutt av bruker.", flush=True)
        sys.exit(1)

    except Exception as e:
        if analysis_lock_token is not None:
            write_analysis_status(
                "error",
                started_at=analysis_started_at,
                finished_at=time.time(),
                error=str(e)[:1000],
            )
        print()
        print("=" * 80, flush=True)
        print("FEIL", flush=True)
        print("=" * 80, flush=True)
        print(repr(e), flush=True)
        sys.exit(1)

    else:
        write_analysis_status(
            "success",
            started_at=analysis_started_at,
            finished_at=time.time(),
        )

    finally:
        if analysis_lock_token is not None:
            release_analysis_lock(analysis_lock_token)
