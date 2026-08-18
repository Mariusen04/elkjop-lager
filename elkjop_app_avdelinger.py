import os
import sys
import subprocess
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

    Kategorinavnet fra Elkjøp brukes først.
    Produktnavnet brukes som ekstra hjelp hvis kategorien er uklar.
    """
    text = f"{category} {title}".lower()

    # -------------------------
    # TECH
    # -------------------------
    tech_rules = [
        (
            "TV",
            [
                "tv", "oled", "qled", "miniled", "mini-led",
                "fjernsyn", "tv-veggfeste", "veggfeste til tv",
            ],
        ),
        (
            "Mobil",
            [
                "mobiltelefon", "smarttelefon", "iphone",
                "androidtelefon", "mobil tilbehør", "mobildeksel",
                "skjermbeskytter", "mobillader",
            ],
        ),
        (
            "PC",
            [
                "bærbar pc", "laptop", "desktop", "stasjonær pc",
                "gaming pc", "chromebook", "macbook", "imac",
                "pc-skjerm", "monitor", "dataskjerm",
            ],
        ),
        (
            "Nettbrett",
            [
                "nettbrett", "tablet", "ipad",
            ],
        ),
        (
            "Lyd",
            [
                "lydplanke", "soundbar", "høyttaler", "høyttalere",
                "hodetelefon", "ørepropper", "headset", "radio",
                "stereo", "forsterker", "subwoofer", "multiroom",
                "sonos", "bluetooth-høyttaler",
            ],
        ),
        (
            "Gaming",
            [
                "gaming", "playstation", "xbox", "nintendo",
                "spillkonsoll", "gamingmus", "gamingtastatur",
                "gaming-headset", "gamingstol", "ratt og pedaler",
            ],
        ),
        (
            "Foto & video",
            [
                "kamera", "digitalkamera", "systemkamera",
                "actionkamera", "videokamera", "objektiv",
                "fototilbehør",
            ],
        ),
        (
            "Smartklokke & wearables",
            [
                "smartklokke", "smartwatch", "aktivitetsarmbånd",
                "fitnessklokke", "apple watch",
            ],
        ),
        (
            "Nettverk & smarthjem",
            [
                "router", "ruter", "mesh", "wifi", "wi-fi",
                "nettverk", "switch", "aksesspunkt", "access point",
                "smarthjem", "smart home", "overvåkningskamera",
                "ringeklokke", "smartpære", "smart lys",
            ],
        ),
        (
            "Tech-tilbehør",
            [
                "hdmi", "usb", "usb-c", "kabel", "adapter",
                "minnekort", "harddisk", "ssd", "m.2",
                "powerbank", "lader", "dock", "docking",
                "tastatur", "mus", "webkamera",
            ],
        ),
    ]

    # -------------------------
    # HVITEVARER
    # -------------------------
    whitegoods_rules = [
        (
            "Kjøl & frys",
            [
                "kjøleskap", "kombiskap", "fryser", "fryseskap",
                "fryseboks", "vinskap", "kjøl", "frys",
            ],
        ),
        (
            "Vask & tørk",
            [
                "vaskemaskin", "tørketrommel", "kombinert vask",
                "vask/tørk", "tørkeskap",
            ],
        ),
        (
            "Oppvask",
            [
                "oppvaskmaskin", "oppvask",
            ],
        ),
        (
            "Komfyr & ovn",
            [
                "komfyr", "stekeovn", "ovn", "mikrobølgeovn",
                "mikroovn",
            ],
        ),
        (
            "Platetopp",
            [
                "platetopp", "induksjonstopp", "koketopp",
            ],
        ),
        (
            "Ventilator",
            [
                "ventilator", "kjøkkenvifte", "hette",
            ],
        ),
    ]

    # -------------------------
    # SDA = småvarer utenom Tech
    # -------------------------
    sda_rules = [
        (
            "Støvsuger & rengjøring",
            [
                "støvsuger", "robotstøvsuger", "skaftstøvsuger",
                "håndstøvsuger", "gulvvasker", "damprenser",
                "vindusvasker", "rengjøring",
            ],
        ),
        (
            "Kaffe",
            [
                "kaffemaskin", "kaffetrakter", "espressomaskin",
                "kaffekvern", "kapselmaskin", "melkeskummer",
            ],
        ),
        (
            "Kjøkkenapparater",
            [
                "airfryer", "air fryer", "blender", "stavmikser",
                "kjøkkenmaskin", "foodprosessor", "brødrister",
                "vannkoker", "riskoker", "slow cooker",
                "multicooker", "toastjern", "vaffeljern",
                "grill", "fritøse", "juicepresse", "sitruspresse",
                "iskremmaskin", "kjøkkenvekt",
            ],
        ),
        (
            "Personlig pleie",
            [
                "barbermaskin", "trimmer", "hårklipper",
                "hårføner", "rettetang", "krølltang",
                "elektrisk tannbørste", "tannbørste",
                "personlig pleie", "epilator",
            ],
        ),
        (
            "Stryking & tekstil",
            [
                "strykejern", "tøydamper", "steamer",
                "dampstasjon",
            ],
        ),
        (
            "Inneklima",
            [
                "vifte", "bordvifte", "gulvvifte",
                "luftfukter", "luftrenser", "avfukter",
                "varmeovn", "panelovn", "aircondition",
                "air conditioner",
            ],
        ),
    ]

    # Hvitevarer først for å unngå at f.eks. "ovn" havner i SDA.
    for subcategory, keywords in whitegoods_rules:
        if any(keyword in text for keyword in keywords):
            return "Hvitevarer", subcategory

    for subcategory, keywords in tech_rules:
        if any(keyword in text for keyword in keywords):
            return "Tech", subcategory

    for subcategory, keywords in sda_rules:
        if any(keyword in text for keyword in keywords):
            return "SDA", subcategory

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

            with st.spinner(
                "Kjører lageranalysen. Dette kan ta noen minutter ..."
            ):
                with open(
                    LOG_FILE,
                    "a",
                    encoding="utf-8",
                ) as log:
                    process = subprocess.run(
                        [
                            sys.executable,
                            str(BOT_FILE),
                        ],
                        cwd=str(APP_DIR),
                        text=True,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                    )

                    log.write(
                        "\n\n"
                        "===== PROCESS EXIT CODE: "
                        f"{process.returncode} =====\n"
                    )

            if process.returncode == 0:
                st.success("Analysen er ferdig.")
                st.rerun()
            else:
                st.error(
                    "Analysen feilet. "
                    f"Exit code: {process.returncode}"
                )

                if process.returncode in (-9, 137):
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
