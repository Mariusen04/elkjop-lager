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

def parse_taxonomy_path(value, fallback_category=""):
    """
    Bruker Elkjøps egen productTaxonomy direkte.
    Ingen gjetting basert på produktnavn.
    """
    raw = str(value or "").strip()

    if raw:
        levels = [
            part.strip()
            for part in raw.split(">")
            if part.strip()
        ]
    else:
        fallback = str(fallback_category or "").strip()
        levels = [fallback] if fallback else []

    return levels


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
    "Bruker Elkjøps eget productTaxonomy-hierarki – ingen gjetting på produktnavn."
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

if "taxonomy_path" not in df.columns:
    # Gammel rapport: bruk siste Elkjøp-kategori som midlertidig fallback.
    # Kjør en ny analyse for å få hele kategorihierarkiet.
    df["taxonomy_path"] = df["category"]
else:
    df["taxonomy_path"] = df["taxonomy_path"].fillna("").astype(str)


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


# ============================================================
# ELKJØPS EGET KATEGORIHIERARKI
# ============================================================

taxonomy_levels = df.apply(
    lambda row: parse_taxonomy_path(
        row["taxonomy_path"],
        row["category"],
    ),
    axis=1,
)

max_depth = max(
    (len(levels) for levels in taxonomy_levels),
    default=0,
)

# Vi viser opptil 5 faktiske nivåer fra Elkjøp.
level_count = min(max_depth, 5)

for level_index in range(level_count):
    column_name = f"taxonomy_level_{level_index + 1}"

    df[column_name] = taxonomy_levels.apply(
        lambda levels, i=level_index:
            levels[i] if i < len(levels) else ""
    )

# Siste nivå er fortsatt den konkrete Elkjøp-kategorien.
df["elkjoep_category"] = df["category"]


# ============================================================
# FILTRE
# ============================================================

with st.sidebar:
    st.header("Filtre")

    selected_taxonomy = {}

    # Dynamiske, kaskaderende filtre basert på Elkjøps faktiske taxonomy.
    taxonomy_source = df.copy()

    level_labels = [
        "Hovedkategori",
        "Kategori",
        "Underkategori",
        "Detaljkategori",
        "Nivå 5",
    ]

    for level_index in range(level_count):
        column_name = f"taxonomy_level_{level_index + 1}"
        label = level_labels[level_index]

        options = sorted(
            value
            for value in taxonomy_source[column_name].unique()
            if str(value).strip()
        )

        selected = st.multiselect(
            label,
            options,
            placeholder=f"Alle – {label.lower()}",
            key=f"taxonomy_filter_{level_index + 1}",
        )

        selected_taxonomy[column_name] = selected

        if selected:
            taxonomy_source = taxonomy_source[
                taxonomy_source[column_name].isin(selected)
            ]

    brands = sorted(
        x
        for x in taxonomy_source["brand"].unique()
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
        placeholder="SKU, produkt, merke eller Elkjøp-kategori",
    )


# ============================================================
# BRUK FILTRE
# ============================================================

filtered = df.copy()


for column_name, selected in selected_taxonomy.items():
    if selected:
        filtered = filtered[
            filtered[column_name].isin(selected)
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
        filtered["taxonomy_path"]
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


table_columns = [
    "priority",
    "percentage",
    "stores_with_stock",
    "stores_checked",
    "sku",
    "title",
    "brand",
]

table_headers = [
    "Prioritet",
    "%",
    "Butikker med varen",
    "Butikker sjekket",
    "SKU",
    "Produkt",
    "Merke",
]

for level_index in range(level_count):
    column_name = f"taxonomy_level_{level_index + 1}"
    table_columns.append(column_name)

    labels = [
        "Hovedkategori",
        "Kategori",
        "Underkategori",
        "Detaljkategori",
        "Nivå 5",
    ]
    table_headers.append(labels[level_index])

table_columns.extend([
    "taxonomy_path",
    "url",
])

table_headers.extend([
    "Elkjøp-kategoristi",
    "Produktlenke",
])

display_df = filtered[table_columns].copy()
display_df.columns = table_headers


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
