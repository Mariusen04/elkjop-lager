import requests, csv, time, math, sys, base64, sqlite3, json, gc
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote
from playwright.sync_api import sync_playwright

STORD_ID = "1273"
MIN_PERCENT = 70.0
MIN_STORES_WITH_STOCK = 10
EXCLUDE_STORE_WORDS = ["Phonehouse", "Outlet"]

ALGOLIA_APP_ID = "Z0FL7R8UBH"
INDEX_NAME = "commerce_b2c_OCNOELK"
ALGOLIA_URL = "https://z0fl7r8ubh-dsn.algolia.net/1/indexes/*/queries"
STOREFINDER_URL = "https://www.elkjop.no/api/trpc/location.getStoreFinderStores"

MAX_RETRIEVABLE = 1500
HITS_PER_PAGE = 500
MAX_SPLIT_DEPTH = 10
KEY_REFRESH_MARGIN = 90
SPLIT_TOLERANCE_PERCENT = 0.005
SPLIT_TOLERANCE_MIN = 10

BASE_FILTERS = ["sellerName:Elkjøp"]

SPLIT_FACETS = [
    "ptLowestLevelNodeValue", "brand", "sellerName",
    "retailItemCategoryGroup", "articleRole", "articleType",
    "retailSalesStatus", "mainLogisticalFlow", "stockGrade", "displayGrade",
]

PRODUCT_ATTRIBUTES = [
    "articleNumber", "title", "brand", "sellerName", "urlB2C",
    "storesWithStock", "departmentStock", "wholesalesStatus",
    "retailSalesStatus", "isBuyableInStore", "productTaxonomy",
]

DB_FILE = Path("elkjop_products.sqlite3")
REPORT_FILE = Path("stord_mangler_elkjop.csv")
UNRESOLVED_FILE = Path("ulosbare_grupper.csv")

session = requests.Session()
ALGOLIA_API_KEY = None
ALGOLIA_KEY_VALID_UNTIL = None
UNRESOLVED_SPLITS = []


def decode_key_valid_until(key):
    try:
        padded = key + "=" * (-len(key) % 4)
        decoded = base64.b64decode(padded).decode("utf-8", errors="ignore")
        values = parse_qs(decoded, keep_blank_values=True).get("validUntil")
        return int(values[0]) if values else None
    except Exception:
        return None


def get_fresh_algolia_key():
    print("\n🔑 Henter fersk Algolia-nøkkel automatisk ...")
    found_key = None

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
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

        browser.close()

    gc.collect()

    if not found_key:
        raise RuntimeError("Fant ikke Algolia-nøkkel automatisk.")

    valid_until = decode_key_valid_until(found_key)
    if valid_until:
        left = max(0, int(valid_until - time.time()))
        print(f"✓ Fersk nøkkel funnet ({left // 60} min {left % 60} sek igjen)")
    else:
        print("✓ Fersk Algolia-nøkkel funnet.")

    return found_key, valid_until


def refresh_algolia_key():
    global ALGOLIA_API_KEY, ALGOLIA_KEY_VALID_UNTIL
    ALGOLIA_API_KEY, ALGOLIA_KEY_VALID_UNTIL = get_fresh_algolia_key()


def ensure_valid_algolia_key():
    if not ALGOLIA_API_KEY:
        refresh_algolia_key()
    elif ALGOLIA_KEY_VALID_UNTIL and ALGOLIA_KEY_VALID_UNTIL - time.time() <= KEY_REFRESH_MARGIN:
        print("\n♻️ Algolia-nøkkelen nærmer seg utløp.")
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
                print(f"⚠ Nettverksfeil mot Algolia: {e}")
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
            print("\n♻️ Algolia-nøkkelen er utløpt. Henter ny automatisk ...")
            ALGOLIA_API_KEY = None
            ALGOLIA_KEY_VALID_UNTIL = None
            refresh_algolia_key()
            continue

        if r.status_code in [429, 500, 502, 503, 504] and attempt < 2:
            time.sleep((attempt + 1) * 2)
            continue

        raise RuntimeError(f"Algolia HTTP {r.status_code}: {text[:1200]}")

    raise RuntimeError("Algolia-request feilet etter tre forsøk.")


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
    print("Tester Algolia ...")
    result = algolia_request({
        "indexName": INDEX_NAME,
        "query": "1059795",
        "hitsPerPage": 10,
        "page": 0,
        "attributesToRetrieve": ["articleNumber", "title", "sellerName"],
    })
    found = any(str(h.get("articleNumber")) == "1059795" for h in result.get("hits", []))
    print("✓ Algolia fungerer." if found else "⚠ Algolia fungerer, men test-SKU ble ikke funnet.")
    print()


def get_taxonomy_facets():
    print("Henter produktkategorier for Elkjøp-selger ...")
    result = algolia_request({
        "indexName": INDEX_NAME,
        "query": "",
        "hitsPerPage": 0,
        "facets": ["productTaxonomy.id"],
        "maxValuesPerFacet": 1000,
        "analytics": False,
        "clickAnalytics": False,
        "facetFilters": BASE_FILTERS,
    })
    facets = result.get("facets", {}).get("productTaxonomy.id", {})
    cats = [{"id": k, "count": int(v)} for k, v in facets.items()]
    cats.sort(key=lambda x: x["count"])
    return cats


def make_category_filters(taxonomy_id):
    return BASE_FILTERS + [f"productTaxonomy.id:{taxonomy_id}"]


def add_filter(filters, facet_name, facet_value):
    return list(filters) + [f"{facet_name}:{facet_value}"]


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
    allowed = max(SPLIT_TOLERANCE_MIN, int(current_count * SPLIT_TOLERANCE_PERCENT))
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

        if difference > allowed or largest >= current_count:
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

    candidates.sort(key=lambda x: (x["largest"], x["difference"], -x["bucket_count"]))
    return candidates[0]


def split_partition(filters, count, description, root_category, depth=0):
    indent = "   " * depth

    if count <= MAX_RETRIEVABLE:
        return [{"filters": filters, "count": count, "description": description}]

    if depth >= MAX_SPLIT_DEPTH:
        UNRESOLVED_SPLITS.append({
            "category": root_category,
            "count": count,
            "reason": "Maks splittdybde: " + description,
        })
        return []

    print(f"{indent}↳ Splitter {description} ({count:,})")
    split = find_best_split(filters, count)

    if split is None:
        print(f"{indent}⚠ Fant ingen sikker split for {description} ({count:,})")
        UNRESOLVED_SPLITS.append({
            "category": root_category,
            "count": count,
            "reason": "Kunne ikke splitte: " + description,
        })
        return []

    print(
        f"{indent}   bruker facet: {split['facet']} "
        f"({split['bucket_count']} grupper, største {split['largest']:,}, "
        f"avvik {split['difference']})"
    )

    result = []
    for facet_value, bucket_count in split["values"].items():
        child_filters = add_filter(filters, split["facet"], facet_value)
        child_desc = f"{description} / {split['facet']}={facet_value}"
        result.extend(
            split_partition(
                child_filters,
                int(bucket_count),
                child_desc,
                root_category,
                depth + 1,
            )
        )
    return result


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


def open_database():
    if DB_FILE.exists():
        DB_FILE.unlink()

    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=FILE")
    conn.execute("PRAGMA cache_size=-12000")
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
            taxonomy_id TEXT
        )
    """)
    conn.commit()
    return conn


def hit_to_row(hit):
    sku = str(hit.get("articleNumber", "")).strip()
    if not sku or hit.get("sellerName") != "Elkjøp":
        return None

    taxonomy = hit.get("productTaxonomy", [])
    category = ""
    taxonomy_id = ""
    if isinstance(taxonomy, list) and taxonomy and isinstance(taxonomy[-1], dict):
        category = str(taxonomy[-1].get("name", "") or "")
        taxonomy_id = str(taxonomy[-1].get("id", "") or "")

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
        json.dumps(sorted(get_stocked_store_ids(hit)), separators=(",", ":")),
        category,
        taxonomy_id,
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
            sku, title, brand, seller, url, wholesale_status,
            retail_status, is_buyable, stores_json, category, taxonomy_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)
    conn.commit()
    return conn.total_changes - before


def db_count(conn):
    return int(conn.execute("SELECT COUNT(*) FROM products").fetchone()[0])


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
    if actual_total > MAX_RETRIEVABLE:
        raise RuntimeError(
            f"Partisjon har nå {actual_total:,} produkter og må splittes videre."
        )

    hits = first.get("hits", [])
    downloaded += len(hits)
    new_skus += insert_hits(conn, hits)

    del hits, first
    gc.collect()

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
        if page % 2 == 0:
            gc.collect()

        if empty:
            break

        time.sleep(0.02)

    return downloaded, new_skus


def analyse_database(conn, comparison_ids):
    results = []
    other_stores = comparison_ids - {STORD_ID}
    stores_checked = len(other_stores)
    total = db_count(conn)

    cursor = conn.execute("""
        SELECT sku, title, brand, seller, url, wholesale_status,
               retail_status, is_buyable, stores_json, category, taxonomy_id
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
                sku, title, brand, seller, url, wholesale_status,
                retail_status, is_buyable, stores_json, category, taxonomy_id
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

            if stores_with_stock < MIN_STORES_WITH_STOCK or percentage < MIN_PERCENT:
                continue

            priority = "KRITISK" if percentage >= 90 else "HØY" if percentage >= 80 else "MIDDELS"

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
                "url": url,
            })

        if analysed % 5000 < 500:
            print(f"Analysert {analysed:,} / {total:,}")

        del rows

    results.sort(
        key=lambda x: (x["percentage"], x["stores_with_stock"]),
        reverse=True,
    )
    return results


def save_results(results):
    columns = [
        "priority", "percentage", "stores_with_stock", "stores_checked",
        "sku", "title", "brand", "seller", "category", "taxonomy_id", "url",
    ]

    with open(REPORT_FILE, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(results)


def save_unresolved():
    if UNRESOLVED_FILE.exists():
        UNRESOLVED_FILE.unlink()

    if not UNRESOLVED_SPLITS:
        return

    unique = {}
    for item in UNRESOLVED_SPLITS:
        unique[(item["category"], item["count"], item["reason"])] = item

    with open(UNRESOLVED_FILE, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["category", "count", "reason"])
        writer.writeheader()
        writer.writerows(unique.values())


def main():
    print()
    print("=" * 80)
    print("ELKJØP STORD - FULL LAGERANALYSE")
    print("Kun produkter solgt av Elkjøp")
    print("Algolia-nøkkel: AUTOMATISK")
    print("Lagring: SQLITE / LAVT RAM-FORBRUK")
    print("=" * 80)
    print()

    if REPORT_FILE.exists():
        REPORT_FILE.unlink()

    conn = open_database()

    try:
        refresh_algolia_key()

        print("\nHenter butikklisten ...")
        stores = get_stores()
        print(f"✓ Fant {len(stores)} sammenligningsbutikker.")

        if STORD_ID not in stores:
            raise RuntimeError("Fant ikke Elkjøp Stord.")

        print(f"✓ {STORD_ID} = {stores[STORD_ID]}\n")
        test_algolia()

        categories = get_taxonomy_facets()
        oversized = sum(1 for c in categories if c["count"] > MAX_RETRIEVABLE)

        print(f"✓ Fant {len(categories)} kategorier for Elkjøp-produkter.")
        print(f"✓ {oversized} kategorier må splittes.\n")

        for n, category in enumerate(categories, start=1):
            taxonomy_id = category["id"]
            count = category["count"]

            print(f"\n[{n}/{len(categories)}] {taxonomy_id} ({count:,} produkter)")
            filters = make_category_filters(taxonomy_id)

            if count <= MAX_RETRIEVABLE:
                partitions = [{"filters": filters, "count": count, "description": taxonomy_id}]
            else:
                partitions = split_partition(filters, count, taxonomy_id, taxonomy_id)

            downloaded = 0
            new_skus = 0

            for partition in partitions:
                try:
                    d, new = fetch_partition_to_db(
                        conn,
                        partition["filters"],
                        partition["count"],
                    )
                    downloaded += d
                    new_skus += new
                except Exception as e:
                    print("   ⚠ FEIL:", e)
                    UNRESOLVED_SPLITS.append({
                        "category": taxonomy_id,
                        "count": partition["count"],
                        "reason": str(e),
                    })

            print(f"   hentet: {downloaded:,}")
            print(f"   nye SKU-er: {new_skus:,}")
            print(f"   TOTALT UNIKE: {db_count(conn):,}")
            gc.collect()

        total_unique = db_count(conn)

        print("\n" + "=" * 80)
        print(f"UNIKE ELKJØP-SKU-ER: {total_unique:,}")
        print("=" * 80 + "\n")

        results = analyse_database(conn, set(stores.keys()))
        save_results(results)
        save_unresolved()

        critical = sum(1 for x in results if x["priority"] == "KRITISK")
        high = sum(1 for x in results if x["priority"] == "HØY")
        medium = sum(1 for x in results if x["priority"] == "MIDDELS")

        print("\n" + "=" * 80)
        print("FERDIG")
        print("=" * 80 + "\n")
        print(f"Unike Elkjøp-SKU-er undersøkt: {total_unique:,}")
        print(f"Stord-mangler funnet: {len(results):,}\n")
        print(f"🔴 KRITISK ≥90%: {critical}")
        print(f"🟠 HØY 80–89,9%: {high}")
        print(f"🟡 MIDDELS 70–79,9%: {medium}\n")
        print("TOPP 50 MANGLER")
        print("-" * 80)

        for pos, item in enumerate(results[:50], start=1):
            print(
                f"{pos:>3}. {item['percentage']:>5.1f}% | "
                f"{item['sku']} | {item['title']}"
            )
            print(
                f"     {item['stores_with_stock']} av "
                f"{item['stores_checked']} andre butikker har varen"
            )

        print(f"\nRapport: {REPORT_FILE}")

        if UNRESOLVED_SPLITS:
            unique_count = len({
                (x["category"], x["count"], x["reason"])
                for x in UNRESOLVED_SPLITS
            })
            print(f"\n⚠ {unique_count} grupper kunne ikke hentes fullstendig.")
            print("Se: ulosbare_grupper.csv")
        else:
            print("\n✓ Alle Elkjøp-kategorier ble hentet fullstendig.")

    finally:
        conn.close()
        gc.collect()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAvbrutt av bruker.")
        sys.exit(1)
    except Exception as e:
        print("\n" + "=" * 80)
        print("FEIL")
        print("=" * 80)
        print(repr(e))
        sys.exit(1)
