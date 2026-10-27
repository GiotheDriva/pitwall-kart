from __future__ import annotations

import html
import io
import ipaddress
import random
import re
import socket
import time
from datetime import datetime
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup


st.set_page_config(
    page_title="Pit Wall | Go-Kart Endurance",
    page_icon="🏁",
    layout="wide",
    initial_sidebar_state="auto",
)

st.markdown(
    """
    <style>
    .pit-log-box {
        height: 320px;
        overflow-y: auto;
        padding: 0.8rem 1rem;
        border: 1px solid rgba(128, 128, 128, 0.28);
        border-radius: 0.6rem;
        background: rgba(128, 128, 128, 0.06);
    }
    .pit-log-entry {
        padding: 0.55rem 0;
        border-bottom: 1px solid rgba(128, 128, 128, 0.16);
        line-height: 1.4;
    }
    .pit-log-entry:last-child {
        border-bottom: 0;
    }
    .pit-log-time {
        color: #9aa4b2;
        font-size: 0.78rem;
        margin-right: 0.55rem;
        white-space: nowrap;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


PILOT_COLUMN_PATTERN = re.compile(
    r"\b(name|driver|pilot|pilota|racer|competitor|participant|team|"
    r"equipaggio|entrant|entry|kart)\b",
    re.IGNORECASE,
)
NON_NAME_COLUMN_PATTERN = re.compile(
    r"\b(position|pos|rank|lap|time|gap|dist|point|class|category|"
    r"number|no\.?|grid|speed|best|last|average|status)\b",
    re.IGNORECASE,
)
NON_NAME_VALUES = {
    "driver",
    "pilota",
    "team",
    "name",
    "nome",
    "total",
    "totale",
    "n/a",
    "none",
    "nan",
    "unknown",
    "ritirato",
    "dns",
    "dnf",
}
MAX_HTML_BYTES = 2_000_000
MAX_REDIRECTS = 4


def validate_public_url(url: str) -> str:
    """Allow public HTTP(S) pages only, to avoid local-network requests."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Inserisci un URL pubblico che inizi con http:// o https://.")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("L'URL non è valido.")

    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise ValueError("Gli indirizzi locali non sono consentiti.")

    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, None)}
    except socket.gaierror as exc:
        raise ValueError("Impossibile raggiungere il dominio indicato.") from exc

    if not addresses:
        raise ValueError("Il dominio non ha indirizzi pubblici raggiungibili.")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise ValueError("Il dominio ha restituito un indirizzo non valido.") from exc
        if not ip.is_global:
            raise ValueError("Per sicurezza sono ammessi solo siti pubblicamente accessibili.")

    return url.strip()


def fetch_public_html(url: str) -> bytes:
    """Fetch a small HTML page while checking every redirect destination."""
    current_url = validate_public_url(url)
    headers = {
        "User-Agent": "PitWallKartTiming/1.0 (+public timing table reader)",
        "Accept": "text/html,application/xhtml+xml",
    }

    for redirect_count in range(MAX_REDIRECTS + 1):
        with requests.get(
            current_url,
            headers=headers,
            timeout=(5, 12),
            allow_redirects=False,
            stream=True,
        ) as response:
            if response.is_redirect or response.is_permanent_redirect:
                if redirect_count >= MAX_REDIRECTS:
                    raise ValueError("La pagina ha troppi reindirizzamenti.")
                location = response.headers.get("Location")
                if not location:
                    raise ValueError("Il reindirizzamento della pagina non è valido.")
                current_url = validate_public_url(urljoin(current_url, location))
                continue

            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").lower()
            if content_type and not any(
                kind in content_type for kind in ("text/html", "application/xhtml+xml")
            ):
                raise ValueError("L'URL non sembra contenere una pagina HTML.")

            body = bytearray()
            for chunk in response.iter_content(chunk_size=32_768):
                body.extend(chunk)
                if len(body) > MAX_HTML_BYTES:
                    raise ValueError("La pagina supera il limite di 2 MB.")
            return bytes(body)

    raise ValueError("Non è stato possibile caricare la pagina.")


def clean_name(value: object) -> str | None:
    if pd.isna(value):
        return None
    name = re.sub(r"\s+", " ", str(value)).strip(" \t\r\n#")
    if not name or name.casefold() in NON_NAME_VALUES:
        return None
    if len(name) > 70 or not re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", name):
        return None
    if re.fullmatch(r"[\d\s.,:+\-/%]+", name):
        return None
    if re.fullmatch(r"(?:lap|giro)\s*\d+", name, flags=re.IGNORECASE):
        return None
    return name


def extract_pilots(html_bytes: bytes) -> list[str]:
    """Read driver/team-like columns from HTML tables, with a text fallback."""
    html_text = html_bytes.decode("utf-8", errors="replace")
    pilots: list[str] = []
    seen: set[str] = set()
    soup = BeautifulSoup(html_text, "lxml")

    # Parse each table separately: a malformed/empty table must not prevent
    # pandas from reading other valid timing tables on the same page.
    for html_table in soup.find_all("table"):
        try:
            tables = pd.read_html(io.StringIO(str(html_table)), flavor="lxml")
        except (AssertionError, ImportError, IndexError, TypeError, ValueError):
            continue

        for table in tables:
            if table.empty:
                continue
            candidate_columns = []
            for column_index, column in enumerate(table.columns):
                label_parts = column if isinstance(column, tuple) else (column,)
                label = " ".join(map(str, label_parts))
                if (
                    PILOT_COLUMN_PATTERN.search(label)
                    and not NON_NAME_COLUMN_PATTERN.search(label)
                ):
                    candidate_columns.append(column_index)

            for column_index in candidate_columns:
                for value in table.iloc[:, column_index].tolist():
                    name = clean_name(value)
                    if name and name.casefold() not in seen:
                        seen.add(name.casefold())
                        pilots.append(name)

    if pilots:
        return pilots

    # Some timing pages expose a table without useful headers. Use its first
    # text-heavy column rather than mistaking lap/time/position values for names.
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        parsed_rows = [
            [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
            for row in rows
        ]
        parsed_rows = [row for row in parsed_rows if row]
        if len(parsed_rows) < 2:
            continue
        width = max(map(len, parsed_rows))
        for index in range(width):
            values = [
                row[index]
                for row in parsed_rows[1:]
                if len(row) > index
            ]
            valid = [clean_name(value) for value in values]
            valid = [value for value in valid if value]
            if len(valid) >= 2 and len(valid) / max(len(values), 1) >= 0.5:
                for name in valid:
                    if name.casefold() not in seen:
                        seen.add(name.casefold())
                        pilots.append(name)
                break

    return pilots


def add_event(message: str) -> None:
    events = st.session_state.setdefault("events", [])
    events.insert(
        0,
        {
            "time": datetime.now().strftime("%H:%M:%S"),
            "message": message,
        },
    )
    del events[50:]


def start_live_session(pilot: str) -> None:
    st.session_state["_session_pilot"] = pilot
    st.session_state["_stint_started_at"] = time.time()
    st.session_state["_last_lap"] = random.uniform(44.2, 49.8)
    st.session_state["_best_lap"] = st.session_state["_last_lap"] - random.uniform(0.1, 1.0)
    st.session_state["_position"] = random.randint(3, 12)
    st.session_state["_gap"] = random.uniform(1.2, 8.5)
    st.session_state["_last_sim_update"] = time.time()
    st.session_state["_next_random_event"] = time.time() + random.randint(8, 16)
    st.session_state["_five_minute_logged"] = False
    st.session_state["_stint_end_logged"] = False
    st.session_state["events"] = []
    add_event(f"🟢 Gara iniziata · {pilot} al volante")


def update_simulation(max_stint_minutes: int) -> float:
    now = time.time()
    elapsed_since_update = now - st.session_state.get("_last_sim_update", now)
    if elapsed_since_update >= 1.5:
        previous_best = st.session_state["_best_lap"]
        lap = random.uniform(44.0, 49.5)
        st.session_state["_last_lap"] = lap
        if lap < previous_best:
            st.session_state["_best_lap"] = lap
            add_event(f"⏱️ Nuovo best lap · {lap:.3f} s")
        else:
            st.session_state["_position"] = max(
                1,
                min(20, st.session_state["_position"] + random.choice([-1, 0, 0, 0, 1])),
            )
            st.session_state["_gap"] = max(
                0.2,
                min(35.0, st.session_state["_gap"] + random.uniform(-0.45, 0.65)),
            )
        st.session_state["_last_sim_update"] = now

    remaining = max(
        0.0,
        max_stint_minutes * 60 - (now - st.session_state["_stint_started_at"]),
    )

    if (
        max_stint_minutes > 5
        and remaining <= 300
        and not st.session_state["_five_minute_logged"]
    ):
        add_event("🚨 Scadenza stint tra 5 min · preparare il cambio")
        st.session_state["_five_minute_logged"] = True
    if remaining <= 0 and not st.session_state["_stint_end_logged"]:
        add_event("🔴 Stint completato · rientro ai box consigliato")
        st.session_state["_stint_end_logged"] = True

    if now >= st.session_state["_next_random_event"]:
        if random.random() < 0.35:
            add_event("📻 Radio box · ritmo regolare, pista libera")
        else:
            add_event("🟡 Strategia · controllare distacco e finestra pit")
        st.session_state["_next_random_event"] = now + random.randint(8, 16)

    return remaining


for key, default in {
    "pilots": [],
    "events": [],
    "selected_pilot": None,
    "_was_live": False,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default


with st.sidebar:
    st.title("🏁 Pit Wall")
    st.caption("Strategia endurance · Go-Kart")
    timing_url = st.text_input(
        "URL del Live Timing",
        placeholder="https://…",
        help="Inserisci una pagina pubblica che espone i dati in tabelle HTML.",
    )

    if st.button("Cerca Piloti in pista", use_container_width=True):
        if not timing_url.strip():
            st.error("Inserisci prima l'URL del Live Timing.")
        else:
            try:
                with st.spinner("Lettura delle tabelle del timing…"):
                    page = fetch_public_html(timing_url)
                    discovered_pilots = extract_pilots(page)
                st.session_state["pilots"] = discovered_pilots
                if st.session_state.get("selected_pilot") not in discovered_pilots:
                    st.session_state.pop("selected_pilot", None)
                if discovered_pilots:
                    st.success(f"Trovati {len(discovered_pilots)} piloti/team.")
                else:
                    st.warning(
                        "Nessun nome trovato nelle tabelle HTML. "
                        "La pagina potrebbe caricare i dati solo con JavaScript."
                    )
            except requests.Timeout:
                st.error("Il timing non ha risposto entro il tempo previsto.")
            except requests.RequestException as exc:
                st.error(f"Errore durante la richiesta: {exc}")
            except (ValueError, OSError) as exc:
                st.error(str(exc))

    pilot_options = st.session_state["pilots"]
    selected_pilot = st.selectbox(
        "Chi sono io",
        options=pilot_options,
        index=None,
        placeholder="Seleziona un pilota o team",
        disabled=not pilot_options,
    )

    max_stint = st.number_input(
        "Max Stint (minuti)",
        min_value=1,
        max_value=300,
        value=45,
        step=1,
    )
    pit_stop_seconds = st.number_input(
        "Tempo Pit Stop (secondi)",
        min_value=0,
        max_value=600,
        value=120,
        step=5,
    )
    live_timing = st.toggle("Avvia Live Timing", value=False)

    if not pilot_options:
        st.caption("Cerca i piloti per compilare la selezione.")


st.title("Dashboard strategia box")
st.caption("Monitoraggio endurance in tempo reale · telemetria simulata")

if live_timing and selected_pilot:
    if (
        not st.session_state.get("_was_live", False)
        or st.session_state.get("_session_pilot") != selected_pilot
    ):
        start_live_session(selected_pilot)

    remaining_seconds = update_simulation(int(max_stint))
    st.session_state["_was_live"] = True

    telemetry_col, strategy_col, events_col = st.columns(3, gap="large")

    with telemetry_col:
        st.subheader("Telemetria")
        st.metric("Posizione", f"P{st.session_state['_position']}")
        st.metric("Ultimo giro", f"{st.session_state['_last_lap']:.3f} s")
        st.metric("Miglior giro", f"{st.session_state['_best_lap']:.3f} s")
        st.metric("Distacco", f"+{st.session_state['_gap']:.1f} s")
        st.caption("Valori simulati · non provengono dal Live Timing.")

    with strategy_col:
        st.subheader("Strategia")
        minutes, seconds = divmod(int(remaining_seconds), 60)
        elapsed_fraction = min(
            1.0,
            max(0.0, 1 - remaining_seconds / (int(max_stint) * 60)),
        )
        st.progress(
            elapsed_fraction,
            text=f"Stint · {minutes:02d}:{seconds:02d} al cambio",
        )

        if remaining_seconds > 600:
            st.success("FINESTRA VERDE · stint regolare")
        elif remaining_seconds > 300:
            st.warning("FINESTRA GIALLA · preparare il prossimo pilota")
        else:
            st.error("FINESTRA ROSSA · cambio pilota imminente")

        estimated_laps_lost = max(1, round(int(pit_stop_seconds) / 46))
        predicted_position = min(
            20,
            st.session_state["_position"] + estimated_laps_lost,
        )
        st.markdown("**Rientro stimato dopo il pit stop**")
        st.metric("Posizione prevista", f"P{predicted_position}")
        st.caption(
            f"Stima semplice: +{estimated_laps_lost} posizioni "
            f"(pit stop {int(pit_stop_seconds)} s)."
        )

    with events_col:
        st.subheader("Log Eventi Box")
        event_items = []
        for event in st.session_state["events"]:
            safe_time = html.escape(event["time"])
            safe_message = html.escape(event["message"])
            event_items.append(
                '<div class="pit-log-entry">'
                f'<span class="pit-log-time">{safe_time}</span>'
                f"<span>{safe_message}</span></div>"
            )
        st.markdown(
            '<div class="pit-log-box">'
            + "".join(event_items)
            + "</div>",
            unsafe_allow_html=True,
        )
else:
    st.session_state["_was_live"] = False
    st.info(
        "Per aprire la dashboard, cerca i piloti nella barra laterale, "
        "seleziona il tuo nome e attiva **Avvia Live Timing**."
    )


if live_timing and selected_pilot:
    time.sleep(2)
    st.rerun()
