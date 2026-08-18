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

    Viktig:
    1. Elkjøp-kategorien får høyest prioritet.
    2. Produktnavnet brukes bare som fallback.
    3. PC/Mobil/Lyd osv. sjekkes før TV, så en PC ikke havner i TV
       bare fordi produktnavnet tilfeldigvis inneholder "TV".
    """

    category_text = str(category or "").lower()
    title_text = str(title or "").lower()

    def has(text, keyword):
        pattern = rf"(?<![a-z0-9æøå]){re.escape(keyword.lower())}(?![a-z0-9æøå])"
        return re.search(pattern, text) is not None

    def any_has(text, keywords):
        return any(has(text, keyword) for keyword in keywords)

    # ========================================================
    # STERKE REGLER BASERT PÅ ELKJØP-KATEGORIEN
    # ========================================================

    category_rules = [
        # TECH - PC først
        ("Tech", "PC", [
            "bærbar pc", "laptop", "stasjonær pc", "desktop",
            "gaming pc", "chromebook", "macbook", "imac",
            "pc-skjerm", "pc skjerm", "monitor", "dataskjerm",
            "pc-tilbehør", "pc tilbehør",
        ]),
        ("Tech", "Mobil", [
            "mobiltelefon", "smarttelefon", "iphone",
            "mobil tilbehør", "mobiltilbehør", "mobildeksel",
            "skjermbeskytter",
        ]),
        ("Tech", "Nettbrett", [
            "nettbrett", "tablet", "ipad",
        ]),
        ("Tech", "Gaming", [
            "gaming", "playstation", "xbox", "nintendo",
            "spillkonsoll", "gamingmus", "gamingtastatur",
            "gaming-headset", "gaming headset", "gamingstol",
            "ratt og pedaler",
        ]),
        ("Tech", "Lyd", [
            "lydplanke", "soundbar", "høyttaler", "høyttalere",
            "hodetelefon", "ørepropper", "headset", "radio",
            "stereo", "forsterker", "subwoofer", "multiroom",
            "bluetooth-høyttaler",
        ]),
        ("Tech", "Foto & video", [
            "kamera", "digitalkamera", "systemkamera",
            "actionkamera", "videokamera", "objektiv",
            "fototilbehør",
        ]),
        ("Tech", "Smartklokke & wearables", [
            "smartklokke", "smartwatch", "aktivitetsarmbånd",
            "fitnessklokke", "apple watch",
        ]),
        ("Tech", "Nettverk & smarthjem", [
            "router", "ruter", "mesh", "wifi", "wi-fi",
            "nettverk", "switch", "aksesspunkt", "access point",
            "smarthjem", "smart home", "overvåkningskamera",
            "ringeklokke",
        ]),
        # TV kommer ETTER andre Tech-kategorier
        ("Tech", "TV", [
            "tv", "oled tv", "qled tv", "mini-led tv",
            "miniled tv", "fjernsyn", "tv-veggfeste",
            "veggfeste til tv", "tv-tilbehør", "tv tilbehør",
        ]),

        # HVITEVARER
        ("Hvitevarer", "Kjøl & frys", [
            "kjøleskap", "kombiskap", "fryser", "fryseskap",
            "fryseboks", "vinskap",
        ]),
        ("Hvitevarer", "Vask & tørk", [
            "vaskemaskin", "tørketrommel", "kombinert vask",
            "vask/tørk", "tørkeskap",
        ]),
        ("Hvitevarer", "Oppvask", [
            "oppvaskmaskin", "oppvask",
        ]),
        ("Hvitevarer", "Komfyr & ovn", [
            "komfyr", "stekeovn", "mikrobølgeovn", "mikroovn",
        ]),
        ("Hvitevarer", "Platetopp", [
            "platetopp", "induksjonstopp", "koketopp",
        ]),
        ("Hvitevarer", "Ventilator", [
            "ventilator", "kjøkkenvifte",
        ]),

        # SDA
        ("SDA", "Støvsuger & rengjøring", [
            "støvsuger", "robotstøvsuger", "skaftstøvsuger",
            "håndstøvsuger", "gulvvasker", "damprenser",
            "vindusvasker",
        ]),
        ("SDA", "Kaffe", [
            "kaffemaskin", "kaffetrakter", "espressomaskin",
            "kaffekvern", "kapselmaskin", "melkeskummer",
        ]),
        ("SDA", "Kjøkkenapparater", [
            "airfryer", "air fryer", "blender", "stavmikser",
            "kjøkkenmaskin", "foodprosessor", "brødrister",
            "vannkoker", "riskoker", "slow cooker",
            "multicooker", "toastjern", "vaffeljern",
            "fritøse", "juicepresse", "sitruspresse",
            "iskremmaskin", "kjøkkenvekt",
        ]),
        ("SDA", "Personlig pleie", [
            "barbermaskin", "trimmer", "hårklipper",
            "hårføner", "rettetang", "krølltang",
            "elektrisk tannbørste", "tannbørste", "epilator",
        ]),
        ("SDA", "Stryking & tekstil", [
            "strykejern", "tøydamper", "steamer", "dampstasjon",
        ]),
        ("SDA", "Inneklima", [
            "bordvifte", "gulvvifte", "luftfukter", "luftrenser",
            "avfukter", "varmeovn", "panelovn", "aircondition",
            "air conditioner",
        ]),
    ]

    for department, subcategory, keywords in category_rules:
        if any_has(category_text, keywords):
            return department, subcategory

    # ========================================================
    # FALLBACK PÅ PRODUKTNAVN
    # ========================================================
    # Bevisst rekkefølge: PC/Mobil/etc før TV.
    title_rules = [
        ("Tech", "PC", [
            "laptop", "notebook", "chromebook", "macbook",
            "desktop", "gaming pc", "pc monitor", "pc-skjerm",
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
        ("Tech", "Lyd", [
            "soundbar", "høyttaler", "headset", "hodetelefon",
            "earbuds", "ørepropper", "subwoofer",
        ]),
        ("Tech", "Nettverk & smarthjem", [
            "router", "ruter", "mesh", "wi-fi", "wifi",
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

    for department, subcategory, keywords in title_rules:
        if any_has(title_text, keywords):
            return department, subcategory

    return "Annet", category if str(category).strip() else "Ukjent"


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
