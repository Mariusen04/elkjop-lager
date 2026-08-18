import os
import sys
import subprocess
import re
from pathlib import Path

import pandas as pd
import streamlit as st


APP_DIR = Path(__file__).resolve().parent
BOT_FILE = APP_DIR / "stord_bot.py"
CSV_FILE = APP_DIR / "stord_mangler_elkjop.csv"
LOG_FILE = APP_DIR / "analyse.log"


st.set_page_config(
    page_title="Elkjøp Stord Lageranalyse",
    page_icon="📦",
    layout="wide",
)


# ============================================================
# AVDELING / UNDERKATEGORI
# ============================================================

def classify_product(category, title):
    """
    Returnerer (avdeling, underkategori).

    Prinsipp:
    - Elkjøps kategori brukes først og veier tyngst.
    - Produktnavnet brukes bare som fallback.
    - Hvitevarer/SDA med tydelig kategori overstyrer generelle ord som
      "lader", "batteri" osv. Dermed havner f.eks. tannbørstelader fortsatt
      under SDA, mens mobil-/PC-ladere havner under Tech.
    """

    category_text = str(category or "").strip().lower()
    title_text = str(title or "").strip().lower()

    def has(text, keyword):
        pattern = rf"(?<![a-z0-9æøå]){re.escape(keyword.lower())}(?![a-z0-9æøå])"
        return re.search(pattern, text) is not None

    def any_has(text, keywords):
        return any(has(text, keyword) for keyword in keywords)

    # ========================================================
    # 1) STERKE KATEGORIREGLER
    # ========================================================

    # -------------------------
    # HVITEVARER
    # -------------------------
    whitegoods = [
        ("Kjøl & frys", [
            "kjøleskap", "kombiskap", "fryser", "fryseskap",
            "fryseboks", "vinskap", "minikjøleskap",
        ]),
        ("Vask & tørk", [
            "vaskemaskin", "tørketrommel", "vask/tørk",
            "kombinert vask", "tørkeskap",
        ]),
        ("Oppvask", [
            "oppvaskmaskin", "oppvask",
        ]),
        ("Komfyr & ovn", [
            "komfyr", "stekeovn", "mikrobølgeovn", "mikroovn",
            "innbyggingsovn",
        ]),
        ("Platetopp", [
            "platetopp", "induksjonstopp", "koketopp",
        ]),
        ("Ventilator", [
            "ventilator", "kjøkkenvifte", "kjøkkenhette",
        ]),
    ]

    for subcategory, keywords in whitegoods:
        if any_has(category_text, keywords):
            return "Hvitevarer", subcategory

    # -------------------------
    # SDA / SMÅVARER UTENOM TECH
    # -------------------------
    sda = [
        ("Støvsuger & rengjøring", [
            "støvsuger", "robotstøvsuger", "skaftstøvsuger",
            "håndstøvsuger", "gulvvasker", "damprenser",
            "vindusvasker", "rengjøringsmaskin",
            "støvsugertilbehør", "støvsugerpose",
        ]),
        ("Kaffe", [
            "kaffemaskin", "kaffetrakter", "espressomaskin",
            "kaffekvern", "kapselmaskin", "melkeskummer",
            "kaffetilbehør",
        ]),
        ("Kjøkkenapparater", [
            "airfryer", "air fryer", "blender", "stavmikser",
            "kjøkkenmaskin", "foodprosessor", "brødrister",
            "vannkoker", "riskoker", "slow cooker",
            "multicooker", "toastjern", "vaffeljern",
            "fritøse", "juicepresse", "sitruspresse",
            "iskremmaskin", "kjøkkenvekt", "smørbrødgrill",
            "minihakker", "eggkoker", "sous vide",
        ]),
        ("Personlig pleie", [
            "barbermaskin", "trimmer", "hårklipper",
            "hårføner", "rettetang", "krølltang",
            "elektrisk tannbørste", "tannbørste", "epilator",
            "personlig pleie", "munnskyller",
        ]),
        ("Stryking & tekstil", [
            "strykejern", "tøydamper", "steamer",
            "dampstasjon", "nuppefjerner",
        ]),
        ("Inneklima", [
            "bordvifte", "gulvvifte", "luftfukter", "luftrenser",
            "avfukter", "varmeovn", "panelovn", "aircondition",
            "air conditioner", "klimaanlegg",
        ]),
    ]

    for subcategory, keywords in sda:
        if any_has(category_text, keywords):
            return "SDA", subcategory

    # -------------------------
    # TECH
    # -------------------------
    tech = [
        ("PC", [
            "bærbar pc", "laptop", "stasjonær pc", "desktop",
            "gaming pc", "chromebook", "macbook", "imac",
            "pc-skjerm", "pc skjerm", "monitor", "dataskjerm",
            "pc-komponent", "pc komponent", "grafikkort",
            "prosessor", "ram-minne", "minnebrikke", "hovedkort",
        ]),
        ("Printere & skannere", [
            "printer", "printere", "skriver", "skrivere",
            "multifunksjonsskriver", "blekkskriver", "laserskriver",
            "etikettskriver", "fotoskriver", "scanner", "skanner",
            "skannere", "blekkpatron", "blekkpatroner",
            "toner", "lasertoner", "printerblekk", "fotopapir",
        ]),
        ("Mobil", [
            "mobiltelefon", "smarttelefon", "iphone",
            "mobil tilbehør", "mobiltilbehør", "mobildeksel",
            "skjermbeskytter", "mobilholder",
        ]),
        ("Nettbrett", [
            "nettbrett", "tablet", "ipad",
        ]),
        ("Gaming", [
            "gaming", "playstation", "xbox", "nintendo",
            "spillkonsoll", "gamingmus", "gamingtastatur",
            "gaming-headset", "gaming headset", "gamingstol",
            "ratt og pedaler", "spilltilbehør",
        ]),
        ("Hodetelefoner", [
            "hodetelefon", "hodetelefoner", "ørepropper",
            "headset", "earbuds", "in-ear", "over-ear",
            "on-ear",
        ]),
        ("Lyd", [
            "lydplanke", "soundbar", "høyttaler", "høyttalere",
            "radio", "stereo", "forsterker", "subwoofer",
            "multiroom", "bluetooth-høyttaler", "platespiller",
            "mikrofon",
        ]),
        ("Foto & video", [
            "kamera", "digitalkamera", "systemkamera",
            "actionkamera", "videokamera", "objektiv",
            "fototilbehør", "kameraobjektiv", "stativ",
        ]),
        ("Smartklokke & wearables", [
            "smartklokke", "smartwatch", "aktivitetsarmbånd",
            "fitnessklokke", "apple watch",
        ]),
        ("Nettverk & smarthjem", [
            "router", "ruter", "mesh", "wifi", "wi-fi",
            "nettverk", "switch", "aksesspunkt", "access point",
            "smarthjem", "smart home", "overvåkningskamera",
            "ringeklokke", "smartpære", "smartlys",
            "nettverkskabel", "ethernet",
        ]),
        ("Lading & strøm", [
            "lader", "ladere", "mobillader", "vegglader",
            "usb-lader", "usb c lader", "usb-c lader",
            "trådløs lader", "qi-lader", "magsafe",
            "powerbank", "strømadapter", "strømforsyning",
            "grenuttak", "skjøteledning", "overspenningsvern",
            "batteri", "batterier",
        ]),
        ("Lagring & minne", [
            "ssd", "harddisk", "ekstern harddisk", "m.2",
            "minnekort", "usb-minne", "usb minne",
            "memory card", "nas",
        ]),
        ("PC-tilbehør", [
            "tastatur", "mus", "webkamera", "webcam",
            "dock", "docking", "dokkingstasjon", "usb-hub",
            "usb hub", "presenter", "tegnebrett",
        ]),
        ("Kabler & adaptere", [
            "hdmi", "displayport", "usb-kabel", "usb kabel",
            "usb-c kabel", "usb c kabel", "adapter",
            "overgang", "lydkabel", "optisk kabel",
            "antennekabel",
        ]),
        ("TV", [
            "tv", "oled tv", "qled tv", "mini-led tv",
            "miniled tv", "fjernsyn", "tv-veggfeste",
            "veggfeste til tv", "tv-tilbehør", "tv tilbehør",
        ]),
    ]

    for subcategory, keywords in tech:
        if any_has(category_text, keywords):
            return "Tech", subcategory

    # ========================================================
    # 2) FALLBACK PÅ PRODUKTNAVN
    # ========================================================
    # Her bruker vi mer konservative regler enn på kategorinavnet.
    fallback = [
        ("Tech", "PC", [
            "laptop", "notebook", "chromebook", "macbook",
            "desktop", "gaming pc",
        ]),
        ("Tech", "Printere & skannere", [
            "printer", "skriver", "laserskriver", "blekkskriver",
            "scanner", "skanner", "toner", "blekkpatron",
        ]),
        ("Tech", "Mobil", [
            "iphone", "smartphone", "mobiltelefon",
        ]),
        ("Tech", "Nettbrett", [
            "ipad", "tablet", "nettbrett",
        ]),
        ("Tech", "Gaming", [
            "playstation", "xbox", "nintendo",
        ]),
        ("Tech", "Hodetelefoner", [
            "headset", "hodetelefon", "hodetelefoner",
            "earbuds", "ørepropper", "in-ear", "over-ear",
            "on-ear",
        ]),
        ("Tech", "Nettverk & smarthjem", [
            "router", "ruter", "mesh", "wi-fi", "wifi",
        ]),
        ("Tech", "Lading & strøm", [
            "powerbank", "magsafe charger", "usb-c charger",
            "usb c charger", "vegglader",
        ]),
        ("Tech", "Lyd", [
            "soundbar", "høyttaler", "subwoofer",
        ]),
        ("Tech", "TV", [
            "oled tv", "qled tv", "mini-led tv", "smart tv",
        ]),
        ("SDA", "Støvsuger & rengjøring", [
            "støvsuger", "robotstøvsuger",
        ]),
        ("SDA", "Kaffe", [
            "kaffemaskin", "kaffetrakter", "espresso",
        ]),
    ]

    for department, subcategory, keywords in fallback:
        if any_has(title_text, keywords):
            return department, subcategory

    return "Annet", category if category_text else "Ukjent"


# ============================================================
# PASSORD
# ============================================================

def check_password():
    expected = os.environ.get("APP_PASSWORD", "").strip()

    if not expected:
        return True

    if st.session_state.get("authenticated"):
        return True

    st.title("🔐 Elkjøp Stord Lageranalyse")

    password = st.text_input(
        "Passord",
        type="password",
    )

    if st.button("Logg inn", type="primary"):
        if password == expected:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Feil passord.")

    return False


if not check_password():
    st.stop()


# ============================================================
# TOPP
# ============================================================

st.title("📦 Elkjøp Stord Lageranalyse")
st.caption(
    "Finn varer Stord mangler, sortert på avdeling og underkategori."
)


# ============================================================
# KJØR ANALYSE
# ============================================================

with st.sidebar:
    st.header("Analyse")

    if st.button(
        "▶ Kjør ny analyse",
        use_container_width=True,
        type="primary",
    ):
        if not BOT_FILE.exists():
            st.error("Fant ikke stord_bot.py.")
        else:
            LOG_FILE.write_text(
                "Starter analyse...\n",
                encoding="utf-8",
            )

            st.subheader("Status")

            progress_bar = st.progress(
                0,
                text="Starter analysen ..."
            )

            status_box = st.empty()
            detail_box = st.empty()

            live_log = st.empty()

            current_category = 0
            total_categories = 629
            current_worker = ""
            total_unique = 0
            last_lines = []

            process = subprocess.Popen(
                [
                    sys.executable,
                    str(BOT_FILE),
                ],
                cwd=str(APP_DIR),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
            )

            with open(
                LOG_FILE,
                "a",
                encoding="utf-8",
            ) as log:
                assert process.stdout is not None

                for raw_line in process.stdout:
                    line = raw_line.rstrip("\n")

                    log.write(raw_line)
                    log.flush()

                    last_lines.append(line)

                    if len(last_lines) > 18:
                        last_lines = last_lines[-18:]

                    worker_match = re.search(
                        r"WORKER\s+(\d+)-(\d+)\s+av\s+(\d+)",
                        line,
                    )

                    if worker_match:
                        current_worker = (
                            f"Worker {worker_match.group(1)}–"
                            f"{worker_match.group(2)}"
                        )

                    category_match = re.search(
                        r"\[(\d+)/(\d+)\]\s+([^\s]+)\s+\(([^)]+)\)",
                        line,
                    )

                    if category_match:
                        current_category = int(
                            category_match.group(1)
                        )

                        total_categories = int(
                            category_match.group(2)
                        )

                        category_name = (
                            category_match.group(3)
                        )

                        category_count = (
                            category_match.group(4)
                        )

                        percent = min(
                            current_category
                            / max(total_categories, 1),
                            1.0,
                        )

                        progress_bar.progress(
                            percent,
                            text=(
                                f"{current_category}/{total_categories} "
                                f"({percent * 100:.1f} %)"
                            ),
                        )

                        status_box.info(
                            f"Behandler **{category_name}** "
                            f"({category_count})"
                        )

                    unique_match = re.search(
                        r"TOTALT UNIKE:\s*([\d,\. ]+)",
                        line,
                    )

                    if unique_match:
                        raw_number = (
                            unique_match.group(1)
                            .replace(",", "")
                            .replace(".", "")
                            .replace(" ", "")
                        )

                        if raw_number.isdigit():
                            total_unique = int(raw_number)

                    details = []

                    if current_worker:
                        details.append(current_worker)

                    if total_unique:
                        details.append(
                            f"{total_unique:,} unike SKU-er"
                            .replace(",", " ")
                        )

                    if details:
                        detail_box.caption(
                            " • ".join(details)
                        )

                    live_log.code(
                        "\n".join(last_lines),
                        language=None,
                    )

            returncode = process.wait()

            with open(
                LOG_FILE,
                "a",
                encoding="utf-8",
            ) as log:
                log.write(
                    "\n\n"
                    "===== PROCESS EXIT CODE: "
                    f"{returncode} =====\n"
                )

            if returncode == 0:
                progress_bar.progress(
                    1.0,
                    text="100 % – ferdig"
                )
                status_box.success(
                    "✅ Analysen er ferdig."
                )
                st.rerun()
            else:
                status_box.error(
                    "Analysen feilet. "
                    f"Exit code: {returncode}"
                )

                if returncode in (-9, 137):
                    st.warning(
                        "Prosessen ble drept av systemet. "
                        "Dette skyldes ofte RAM-grensen."
                    )

    if LOG_FILE.exists():
        with st.expander("Vis siste analyselog"):
            try:
                log_text = LOG_FILE.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
                st.code(log_text[-30000:])
            except Exception as error:
                st.write(
                    "Kunne ikke lese logg: "
                    f"{error}"
                )

    st.divider()


# ============================================================
# LAST CSV
# ============================================================

if not CSV_FILE.exists():
    st.info(
        "Ingen rapport finnes ennå. "
        "Trykk **Kjør ny analyse** i menyen til venstre."
    )
    st.stop()


try:
    df = pd.read_csv(CSV_FILE)
except Exception as error:
    st.error(
        f"Kunne ikke lese CSV-filen: {error}"
    )
    st.stop()


required = {
    "priority",
    "percentage",
    "stores_with_stock",
    "stores_checked",
    "sku",
    "title",
    "brand",
    "category",
    "url",
}

missing = required - set(df.columns)

if missing:
    st.error(
        "CSV-filen mangler kolonner: "
        + ", ".join(sorted(missing))
    )
    st.stop()


for col in [
    "category",
    "brand",
    "priority",
    "title",
    "sku",
    "url",
]:
    df[col] = df[col].fillna("").astype(str)


df["percentage"] = pd.to_numeric(
    df["percentage"],
    errors="coerce",
).fillna(0)

df["stores_with_stock"] = pd.to_numeric(
    df["stores_with_stock"],
    errors="coerce",
).fillna(0).astype(int)

df["stores_checked"] = pd.to_numeric(
    df["stores_checked"],
    errors="coerce",
).fillna(0).astype(int)


# Legg til Avdeling + Underkategori.
classification = df.apply(
    lambda row: classify_product(
        row["category"],
        row["title"],
    ),
    axis=1,
)

df["department"] = classification.apply(lambda x: x[0])
df["subcategory"] = classification.apply(lambda x: x[1])


# ============================================================
# FILTRE
# ============================================================

with st.sidebar:
    st.header("Filtre")

    department_order = [
        "Tech",
        "Hvitevarer",
        "SDA",
        "Annet",
    ]

    available_departments = [
        dep
        for dep in department_order
        if dep in set(df["department"])
    ]

    selected_departments = st.multiselect(
        "Avdeling",
        available_departments,
        default=[
            dep
            for dep in ["Tech", "Hvitevarer", "SDA"]
            if dep in available_departments
        ],
        placeholder="Alle avdelinger",
    )

    if selected_departments:
        subcategory_source = df[
            df["department"].isin(selected_departments)
        ]
    else:
        subcategory_source = df

    subcategories = sorted(
        x
        for x in subcategory_source["subcategory"].unique()
        if str(x).strip()
    )

    selected_subcategories = st.multiselect(
        "Underkategori",
        subcategories,
        placeholder="Alle underkategorier",
    )

    brands_source = subcategory_source

    if selected_subcategories:
        brands_source = brands_source[
            brands_source["subcategory"].isin(
                selected_subcategories
            )
        ]

    brands = sorted(
        x
        for x in brands_source["brand"].unique()
        if x.strip()
    )

    selected_brands = st.multiselect(
        "Merke",
        brands,
        placeholder="Alle merker",
    )

    priorities = [
        "KRITISK",
        "HØY",
        "MIDDELS",
    ]

    selected_priorities = st.multiselect(
        "Prioritet",
        priorities,
        default=priorities,
    )

    min_percentage = st.slider(
        "Minimum % av andre butikker",
        min_value=0,
        max_value=100,
        value=70,
        step=1,
    )

    search = st.text_input(
        "Søk",
        placeholder="SKU, produkt, merke eller kategori",
    )


# ============================================================
# BRUK FILTRE
# ============================================================

filtered = df.copy()


if selected_departments:
    filtered = filtered[
        filtered["department"].isin(
            selected_departments
        )
    ]

if selected_subcategories:
    filtered = filtered[
        filtered["subcategory"].isin(
            selected_subcategories
        )
    ]

if selected_brands:
    filtered = filtered[
        filtered["brand"].isin(
            selected_brands
        )
    ]

if selected_priorities:
    filtered = filtered[
        filtered["priority"].isin(
            selected_priorities
        )
    ]
else:
    filtered = filtered.iloc[0:0]

filtered = filtered[
    filtered["percentage"] >= min_percentage
]


if search.strip():
    q = search.strip().lower()

    filtered = filtered[
        filtered["sku"]
        .str.lower()
        .str.contains(q, na=False, regex=False)
        |
        filtered["title"]
        .str.lower()
        .str.contains(q, na=False, regex=False)
        |
        filtered["brand"]
        .str.lower()
        .str.contains(q, na=False, regex=False)
        |
        filtered["category"]
        .str.lower()
        .str.contains(q, na=False, regex=False)
        |
        filtered["department"]
        .str.lower()
        .str.contains(q, na=False, regex=False)
        |
        filtered["subcategory"]
        .str.lower()
        .str.contains(q, na=False, regex=False)
    ]


filtered = filtered.sort_values(
    [
        "percentage",
        "stores_with_stock",
    ],
    ascending=[
        False,
        False,
    ],
).reset_index(drop=True)


# ============================================================
# METRICS
# ============================================================

c1, c2, c3, c4 = st.columns(4)

c1.metric(
    "Treff",
    f"{len(filtered):,}".replace(",", " "),
)

c2.metric(
    "Kritisk",
    int(
        (
            filtered["priority"]
            == "KRITISK"
        ).sum()
    ),
)

c3.metric(
    "Høy",
    int(
        (
            filtered["priority"]
            == "HØY"
        ).sum()
    ),
)

c4.metric(
    "Middels",
    int(
        (
            filtered["priority"]
            == "MIDDELS"
        ).sum()
    ),
)


# ============================================================
# TABELL
# ============================================================

st.divider()


display_df = filtered[
    [
        "department",
        "subcategory",
        "priority",
        "percentage",
        "stores_with_stock",
        "stores_checked",
        "sku",
        "title",
        "brand",
        "category",
        "url",
    ]
].copy()


display_df.columns = [
    "Avdeling",
    "Underkategori",
    "Prioritet",
    "%",
    "Butikker med varen",
    "Butikker sjekket",
    "SKU",
    "Produkt",
    "Merke",
    "Elkjøp-kategori",
    "Produktlenke",
]


st.dataframe(
    display_df,
    use_container_width=True,
    hide_index=True,
    column_config={
        "%": st.column_config.NumberColumn(
            format="%.1f %%"
        ),
        "Produktlenke": st.column_config.LinkColumn(
            "Åpne produkt",
            display_text="Åpne",
        ),
    },
)


csv_bytes = filtered.to_csv(
    index=False
).encode("utf-8-sig")


st.download_button(
    "⬇ Last ned filtrert CSV",
    data=csv_bytes,
    file_name="stord_mangler_filtrert.csv",
    mime="text/csv",
)
