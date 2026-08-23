import os
import sys
import subprocess
import re
import json
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd
import streamlit as st
import extra_streamlit_components as stx

from auth_utils import create_remember_token, validate_remember_token


APP_DIR = Path(__file__).resolve().parent
BOT_FILE = APP_DIR / "stord_bot.py"
DATA_DIR = Path(
    os.environ.get("APP_DATA_DIR", str(APP_DIR))
).expanduser().resolve()
CSV_FILE = DATA_DIR / "stord_mangler_elkjop.csv"
LOG_FILE = DATA_DIR / "analyse.log"
LOCK_FILE = DATA_DIR / ".analyse.lock"
STATUS_FILE = DATA_DIR / "analyse_status.json"
LOCK_MAX_AGE_SECONDS = 12 * 60 * 60
APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "Europe/Oslo")
AUTH_COOKIE_NAME = "elkjop_lager_auth"
AUTH_COOKIE_SECRET = os.environ.get("APP_AUTH_SECRET", "").strip()

try:
    AUTH_COOKIE_DAYS = min(
        365,
        max(1, int(os.environ.get("APP_AUTH_COOKIE_DAYS", "30"))),
    )
except ValueError:
    AUTH_COOKIE_DAYS = 30


st.set_page_config(
    page_title="Elkjøp Stord Lageranalyse",
    page_icon="📦",
    layout="wide",
)

cookie_controller = stx.CookieManager(key="elkjop_auth_cookies")

DATA_DIR.mkdir(parents=True, exist_ok=True)

st.markdown(
    """
    <style>
    [data-testid="stMetric"] {
        border: 1px solid rgba(128, 128, 128, 0.22);
        border-radius: 0.75rem;
        padding: 0.8rem 1rem;
    }
    [data-testid="stSidebar"] button[kind="primary"] {
        font-weight: 700;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def format_timestamp(timestamp):
    try:
        timezone = ZoneInfo(APP_TIMEZONE)
    except ZoneInfoNotFoundError:
        timezone = datetime.now().astimezone().tzinfo

    return datetime.fromtimestamp(
        timestamp,
        tz=timezone,
    ).strftime("%d.%m.%Y kl. %H:%M")


def read_analysis_lock():
    if not LOCK_FILE.exists():
        return None

    try:
        age = time.time() - LOCK_FILE.stat().st_mtime

        if age > LOCK_MAX_AGE_SECONDS:
            LOCK_FILE.unlink(missing_ok=True)
            return None

        return json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"started_at": None}


def read_analysis_log():
    if not LOG_FILE.exists():
        return ""

    try:
        return LOG_FILE.read_text(
            encoding="utf-8",
            errors="replace",
        )[-30000:]
    except OSError:
        return ""


def read_analysis_status():
    if not STATUS_FILE.exists():
        return {}

    try:
        status = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        return status if isinstance(status, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def get_analysis_progress(log_text):
    partition_matches = list(re.finditer(
        r"\[(\d+)/(\d+)\]\s+partisjon-\d+\s+\(([^)]+)\)",
        log_text,
    ))
    unique_matches = list(re.finditer(
        r"TOTALT UNIKE:\s*([\d,. ]+)",
        log_text,
    ))

    progress = {
        "current": 0,
        "total": 0,
        "partition_size": "",
        "unique": 0,
    }

    if partition_matches:
        latest = partition_matches[-1]
        progress["current"] = int(latest.group(1))
        progress["total"] = int(latest.group(2))
        progress["partition_size"] = latest.group(3)

    if unique_matches:
        raw_number = re.sub(r"[^0-9]", "", unique_matches[-1].group(1))
        progress["unique"] = int(raw_number) if raw_number else 0

    return progress


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

def set_login_cookie(password):
    token = create_remember_token(
        password,
        AUTH_COOKIE_SECRET,
        AUTH_COOKIE_DAYS,
    )
    cookie_controller.set(
        cookie=AUTH_COOKIE_NAME,
        val=token,
        key="elkjop_auth_cookie_set",
        path="/",
        expires_at=datetime.now() + timedelta(days=AUTH_COOKIE_DAYS),
        max_age=AUTH_COOKIE_DAYS * 24 * 60 * 60,
        secure=True,
        same_site="strict",
    )


def forget_login_cookie():
    # En utløpt cookie med Max-Age=0 fungerer også når komponentens lokale
    # cookie-cache ennå ikke er ferdig lastet.
    cookie_controller.set(
        cookie=AUTH_COOKIE_NAME,
        val="",
        key="elkjop_auth_cookie_forget",
        path="/",
        expires_at=datetime.now() - timedelta(days=1),
        max_age=0,
        secure=True,
        same_site="strict",
    )


def request_logout():
    st.session_state["logout_requested"] = True


def check_password():
    expected = os.environ.get("APP_PASSWORD", "").strip()

    if not expected:
        return True

    if st.session_state.pop("logout_requested", False):
        forget_login_cookie()
        st.session_state["authenticated"] = False

    # st.context.cookies viser cookiene fra da nettleserøkten ble opprettet.
    # Les derfor bare remember-tokenet én gang per Streamlit-økt. Det hindrer
    # at en bruker blir logget inn igjen av en gammel verdi rett etter logout.
    if "authenticated" not in st.session_state:
        remembered_token = st.context.cookies.get(AUTH_COOKIE_NAME)
        cookie_is_valid = validate_remember_token(
            remembered_token,
            expected,
            AUTH_COOKIE_SECRET,
        )
        st.session_state["authenticated"] = cookie_is_valid

        if remembered_token and not cookie_is_valid:
            forget_login_cookie()

    if st.session_state.get("authenticated"):
        return True

    login_placeholder = st.empty()

    with login_placeholder.container():
        st.title("🔐 Elkjøp Stord Lageranalyse")

        with st.form("login_form"):
            password = st.text_input(
                "Passord",
                type="password",
            )
            remember_login = st.checkbox(
                f"Husk meg på denne enheten i {AUTH_COOKIE_DAYS} dager",
                value=bool(AUTH_COOKIE_SECRET),
                disabled=not bool(AUTH_COOKIE_SECRET),
            )
            submitted = st.form_submit_button(
                "Logg inn",
                type="primary",
            )

        if not AUTH_COOKIE_SECRET:
            st.caption(
                "Fast innlogging er ikke aktivert på serveren. "
                "Vanlig innlogging virker fortsatt."
            )

    if submitted:
        if secrets.compare_digest(password, expected):
            st.session_state["authenticated"] = True

            if remember_login and AUTH_COOKIE_SECRET:
                set_login_cookie(expected)
            else:
                forget_login_cookie()

            login_placeholder.empty()
            st.toast("Innlogget")
            return True
        else:
            st.error("Feil passord.")

    return False


if not check_password():
    st.stop()


if os.environ.get("APP_PASSWORD", "").strip():
    with st.sidebar:
        st.button(
            "↪ Logg ut og glem denne enheten",
            width="stretch",
            on_click=request_logout,
        )
        st.divider()


# ============================================================
# TOPP
# ============================================================

st.title("📦 Elkjøp Stord Lageranalyse")

report_status = (
    f"Sist oppdatert {format_timestamp(CSV_FILE.stat().st_mtime)}"
    if CSV_FILE.exists()
    else "Ingen rapport er opprettet ennå"
)

st.caption(
    "Finn varer Stord mangler, men som er tilgjengelige i andre Elkjøp-butikker. "
    f"• {report_status}"
)


# ============================================================
# KJØR ANALYSE
# ============================================================

with st.sidebar:
    st.header("Analyse")

    lock_info = read_analysis_lock()
    analysis_log = read_analysis_log()
    analysis_status = read_analysis_status()

    if lock_info:
        started_at = lock_info.get("started_at")
        started_text = (
            format_timestamp(started_at)
            if isinstance(started_at, (int, float))
            else "ukjent tidspunkt"
        )
        st.info(f"En analyse kjører. Startet {started_text}.")

        progress = get_analysis_progress(analysis_log)

        if progress["total"]:
            ratio = min(
                max(progress["current"] - 1, 0) / progress["total"],
                1.0,
            )
            st.progress(
                ratio,
                text=(
                    f"Partisjon {progress['current']} av "
                    f"{progress['total']} ({ratio * 100:.0f} % ferdig)"
                ),
            )

            details = [
                f"{progress['partition_size']} i aktiv partisjon"
            ]

            if progress["unique"]:
                details.append(
                    f"{progress['unique']:,} unike SKU-er hentet"
                    .replace(",", " ")
                )

            st.caption(" • ".join(details))

        if st.button("🔄 Oppdater status", width="stretch"):
            st.rerun()
    elif analysis_status.get("status") == "error":
        finished_at = analysis_status.get("finished_at")
        finished_text = (
            format_timestamp(finished_at)
            if isinstance(finished_at, (int, float))
            else "ukjent tidspunkt"
        )
        error_text = str(
            analysis_status.get("error")
            or "Ukjent feil. Se analyseloggen."
        )[:300]
        st.error(
            f"Siste analyse feilet {finished_text}. "
            f"Forrige rapport er beholdt. {error_text}"
        )
    elif analysis_status.get("status") == "success":
        finished_at = analysis_status.get("finished_at")
        finished_text = (
            format_timestamp(finished_at)
            if isinstance(finished_at, (int, float))
            else "ukjent tidspunkt"
        )
        st.success(f"Siste analyse fullført {finished_text}.")

    if st.button(
        "▶ Kjør ny analyse",
        width="stretch",
        type="primary",
        disabled=bool(lock_info),
        help=(
            "Vent til analysen som kjører er ferdig."
            if lock_info
            else "Hent ferske lagerdata fra Elkjøp."
        ),
    ):
        if not BOT_FILE.exists():
            st.error("Fant ikke stord_bot.py.")
        else:
            LOG_FILE.write_text(
                "Starter analyse...\n",
                encoding="utf-8",
            )

            with open(
                LOG_FILE,
                "a",
                encoding="utf-8",
            ) as log:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        str(BOT_FILE),
                    ],
                    cwd=str(APP_DIR),
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )

            # Gi boten et kort øyeblikk til å opprette låsefilen. Selve
            # analysen fortsetter frakoblet nettleserøkten.
            for _ in range(20):
                if LOCK_FILE.exists() or process.poll() is not None:
                    break
                time.sleep(0.1)

            if process.poll() is None:
                st.toast(
                    "Analysen er startet og fortsetter selv om siden lukkes."
                )
                st.rerun()
            else:
                st.error(
                    "Analysen kunne ikke starte. Se analyseloggen under."
                )

    if LOG_FILE.exists():
        with st.expander("Vis siste analyselog"):
            st.code(analysis_log or read_analysis_log())

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

st.caption(
    f"Viser {len(filtered):,} av {len(df):,} produkter i rapporten."
    .replace(",", " ")
)

if not filtered.empty:
    with st.expander("📊 Se fordeling i utvalget"):
        category_column = (
            "taxonomy_level_1"
            if "taxonomy_level_1" in filtered.columns
            else "category"
        )
        chart_left, chart_right = st.columns(2)

        with chart_left:
            st.markdown("**Største kategorier**")
            category_counts = (
                filtered[category_column]
                .replace("", "Ukjent")
                .value_counts()
                .head(10)
                .rename_axis("Kategori")
                .reset_index(name="Antall")
            )
            st.bar_chart(
                category_counts,
                x="Kategori",
                y="Antall",
                horizontal=True,
            )

        with chart_right:
            st.markdown("**Største merker**")
            brand_counts = (
                filtered["brand"]
                .replace("", "Ukjent")
                .value_counts()
                .head(10)
                .rename_axis("Merke")
                .reset_index(name="Antall")
            )
            st.bar_chart(
                brand_counts,
                x="Merke",
                y="Antall",
                horizontal=True,
            )


# ============================================================
# TABELL
# ============================================================

st.divider()
st.subheader("Produkter")


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
display_df.insert(0, "Velg", False)
display_df.insert(1, "Antall", 1)

# Et filterbytte kan endre hvilke produkter som ligger på hver rad. Nullstill da
# gamle avkrysninger, slik at et valg aldri flyttes til feil SKU.
filter_signature = hash(tuple(display_df["SKU"].tolist()))

if st.session_state.get("order_table_filter_signature") != filter_signature:
    st.session_state.pop("order_table", None)
    st.session_state["order_table_filter_signature"] = filter_signature

editable_columns = {"Velg", "Antall"}
read_only_columns = [
    column
    for column in display_df.columns
    if column not in editable_columns
]

edited_df = st.data_editor(
    display_df,
    width="stretch",
    hide_index=True,
    disabled=read_only_columns,
    key="order_table",
    column_config={
        "Velg": st.column_config.CheckboxColumn(
            "Velg",
            help="Ta med produktet i eksport av valgte varer.",
            default=False,
        ),
        "Antall": st.column_config.NumberColumn(
            "Antall",
            help="Antallet som skal sendes til bestillingsprogrammet.",
            min_value=1,
            step=1,
            format="%d",
        ),
        "%": st.column_config.NumberColumn(
            format="%.1f %%"
        ),
        "Produktlenke": st.column_config.LinkColumn(
            "Åpne produkt",
            display_text="Åpne",
        ),
    },
)


# ============================================================
# BESTILLINGSEKSPORT
# ============================================================

st.subheader("Bestillingsliste")
st.caption(
    "Format: SKU, tabulatortegn og antall – én vare per linje. "
    "Listen kan limes direkte inn i Mass Entry."
)

export_scope = st.radio(
    "Produkter som skal eksporteres",
    [
        "Valgte produkter",
        "Alle filtrerte produkter",
    ],
    horizontal=True,
)

if export_scope == "Alle filtrerte produkter":
    order_df = edited_df.copy()
else:
    order_df = edited_df[edited_df["Velg"].fillna(False)].copy()

order_lines = []

for sku, quantity in order_df[["SKU", "Antall"]].itertuples(
    index=False,
    name=None,
):
    sku_text = str(sku).strip()
    numeric_quantity = pd.to_numeric(quantity, errors="coerce")

    if not sku_text or pd.isna(numeric_quantity):
        continue

    order_lines.append(f"{sku_text}\t{max(1, int(numeric_quantity))}")

order_text = "\n".join(order_lines)

if order_text:
    st.text_area(
        "Kopier til Mass Entry",
        value=order_text,
        height=180,
        help="Klikk i feltet, velg alt og kopier.",
    )
    st.caption(
        (
            f"{len(order_lines):,} varelinjer. Klikk i feltet og bruk "
            "Ctrl+A / Ctrl+C eller ⌘A / ⌘C."
        ).replace(",", " ")
    )
    st.download_button(
        "⬇ Last ned bestillingsliste",
        data=order_text.encode("utf-8"),
        file_name="bestillingsliste.txt",
        mime="text/plain",
    )
else:
    st.info(
        "Huk av produkter i tabellen, eller velg "
        "**Alle filtrerte produkter**."
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
