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

def check_password():
    expected = os.environ.get("APP_PASSWORD", "").strip()

    if not expected:
        return True

    if st.session_state.get("authenticated"):
        return True

    st.title("🔐 Elkjøp Stord Lageranalyse")
    password = st.text_input("Passord", type="password")

    if st.button("Logg inn", type="primary"):
        if password == expected:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Feil passord.")

    return False


if not check_password():
    st.stop()


st.title("📦 Elkjøp Stord Lageranalyse")
st.caption(
    "Filtrer varer Stord mangler etter kategori, merke, "
    "prioritet og butikkandel."
)

with st.sidebar:
    st.header("Analyse")

    if st.button("▶ Kjør ny analyse", use_container_width=True, type="primary"):
        if not BOT_FILE.exists():
            st.error("Fant ikke stord_bot.py.")
        else:
            with st.spinner(
                "Kjører lageranalysen. Dette kan ta noen minutter ..."
            ):
                with open(LOG_FILE, "w", encoding="utf-8") as log:
                    process = subprocess.run(
                        [sys.executable, str(BOT_FILE)],
                        cwd=str(APP_DIR),
                        text=True,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                    )

          with open(LOG_FILE, "a", encoding="utf-8") as log:
    log.write(
        f"\n\n===== PROCESS EXIT CODE: {process.returncode} =====\n"
    )

if process.returncode == 0:
    st.success("Analysen er ferdig.")
    st.rerun()
else:
    st.error(
        f"Analysen feilet. Exit code: {process.returncode}"
    )

    if process.returncode in (-9, 137):
        st.warning(
            "Prosessen ble sannsynligvis drept pga. for lite RAM."
        )

    if LOG_FILE.exists():
        with st.expander("Vis siste analyselog"):
            try:
                log_text = LOG_FILE.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
                st.code(log_text[-20000:])
            except Exception as e:
                st.write(f"Kunne ikke lese logg: {e}")

    st.divider()

if not CSV_FILE.exists():
    st.info(
        "Ingen rapport finnes ennå. Trykk **Kjør ny analyse** "
        "i menyen til venstre."
    )
    st.stop()

try:
    df = pd.read_csv(CSV_FILE)
except Exception as e:
    st.error(f"Kunne ikke lese CSV-filen: {e}")
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


with st.sidebar:
    st.header("Filtre")

    categories = sorted(
        x
        for x in df["category"].unique()
        if x.strip()
    )

    selected_categories = st.multiselect(
        "Kategori",
        categories,
        placeholder="Alle kategorier",
    )

    brands = sorted(
        x
        for x in df["brand"].unique()
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


filtered = df.copy()

if selected_categories:
    filtered = filtered[
        filtered["category"].isin(
            selected_categories
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

st.divider()

display_df = filtered[
    [
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
    "Prioritet",
    "%",
    "Butikker med varen",
    "Butikker sjekket",
    "SKU",
    "Produkt",
    "Merke",
    "Kategori",
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
