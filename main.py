from __future__ import annotations

import asyncio
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
import websockets
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


def fetch_public_resource(url: str, accepted_content_types: tuple[str, ...]) -> bytes:
    """Fetch a small public resource while checking every redirect destination."""
    current_url = validate_public_url(url)
    headers = {
        "User-Agent": "PitWallKartTiming/1.0 (+public timing table reader)",
        "Accept": ", ".join(accepted_content_types),
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
            if content_type and not any(kind in content_type for kind in accepted_content_types):
                raise ValueError("La risorsa ricevuta non ha il formato previsto.")

            body = bytearray()
            for chunk in response.iter_content(chunk_size=32_768):
                body.extend(chunk)
                if len(body) > MAX_HTML_BYTES:
                    raise ValueError("La pagina supera il limite di 2 MB.")
            return bytes(body)

    raise ValueError("Non è stato possibile caricare la pagina.")


def fetch_public_html(url: str) -> bytes:
    return fetch_public_resource(url, ("text/html", "application/xhtml+xml"))


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


APEX_DATA_HOST = "live-data.apex-timing.com"


def is_apex_timing_url(url: str) -> bool:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme in {"http", "https"}
        and (host == "apex-timing.com" or host.endswith(".apex-timing.com"))
        and "/live-timing/" in parsed.path.lower()
    )


@st.cache_data(ttl=900, show_spinner=False)
def apex_websocket_endpoint(page_url: str) -> str:
    """Read Apex's public timing host and port from the page's config.js."""
    page_url = validate_public_url(page_url)
    page = BeautifulSoup(fetch_public_html(page_url), "lxml")
    config_script = page.find(
        "script",
        src=re.compile(r"(?:^|/)config\.js(?:\?|$)", re.IGNORECASE),
    )
    if not config_script or not config_script.get("src"):
        raise ValueError("La pagina Apex non espone il file di configurazione live.")

    config_url = urljoin(page_url, config_script["src"])
    if urlparse(config_url).hostname != urlparse(page_url).hostname:
        raise ValueError("Il file di configurazione Apex proviene da un host inatteso.")
    config_js = fetch_public_resource(
        config_url,
        ("application/javascript", "text/javascript", "application/x-javascript"),
    ).decode("utf-8", errors="replace")

    host_match = re.search(
        r"\bconfigHost\s*=\s*['\"]([^'\"]+)['\"]",
        config_js,
    )
    port_match = re.search(r"\bconfigPort\s*=\s*(\d+)", config_js)
    if not host_match or not port_match:
        raise ValueError("Configurazione Live Timing Apex incompleta.")

    data_host = host_match.group(1).strip().lower()
    if data_host != APEX_DATA_HOST:
        raise ValueError("Host dati Apex non riconosciuto; collegamento non avviato.")
    base_port = int(port_match.group(1))
    if not 1 <= base_port <= 65531:
        raise ValueError("Porta dati Apex non valida.")
    validate_public_url(f"https://{data_host}/")

    if urlparse(page_url).scheme == "https":
        return f"wss://{data_host}:{base_port + 3}/"
    return f"ws://{data_host}:{base_port + 2}/"


def clean_apex_name(value: str) -> str:
    name = re.sub(r"\s+", " ", html.unescape(value)).strip()
    return re.sub(r"\s+\[\d{1,3}:\d{2}\]\s*$", "", name).strip()


def parse_apex_int(value: str) -> int | None:
    match = re.search(r"-?\d+", value or "")
    return int(match.group()) if match else None


def parse_apex_frame(frame: str) -> dict:
    """Convert Apex's live WebSocket messages into a current standings snapshot."""
    fields: dict[str, str] = {}
    grid_html = ""
    for line in frame.splitlines():
        parts = line.split("|", 2)
        if len(parts) < 3:
            continue
        key, value_type, value = parts
        if key == "grid":
            grid_html = value
        elif key in {"title1", "title2"}:
            fields[key] = value.strip()
        elif key == "dyn1":
            fields["timer_type"] = value_type.strip().lower()
            fields["timer_value"] = value.strip()

    rows: list[dict] = []
    if grid_html:
        grid = BeautifulSoup(grid_html, "lxml")
        header_row = grid.select_one("tr.head")
        header_types = (
            [cell.get("data-type", "").strip().lower() for cell in header_row.find_all("td", recursive=False)]
            if header_row
            else []
        )
        for row in grid.select("tr[data-id]"):
            if "head" in row.get("class", []):
                continue
            cells = row.find_all("td", recursive=False)
            values = [cell.get_text(" ", strip=True) for cell in cells]
            row_fields = {
                header_types[index]: value
                for index, value in enumerate(values)
                if index < len(header_types) and header_types[index]
            }
            team = clean_apex_name(row_fields.get("dr", ""))
            position = parse_apex_int(row_fields.get("rk", ""))
            if not team or position is None:
                continue
            rows.append(
                {
                    "id": row.get("data-id", ""),
                    "position": position,
                    "kart": row_fields.get("no", "").strip(),
                    "team": team,
                    "last_lap": row_fields.get("llp", "").strip(),
                    "gap": row_fields.get("gap", "").strip(),
                    "interval": row_fields.get("int", "").strip(),
                    "best_lap": row_fields.get("blp", "").strip(),
                    "laps_completed": parse_apex_int(row_fields.get("tlp", "")),
                    "pits": parse_apex_int(row_fields.get("pit", "")),
                    "on_track": row_fields.get("otr", "").strip(),
                }
            )

    timer_seconds = None
    if fields.get("timer_type") in {"countdown", "countdown_text"}:
        timer_value = fields.get("timer_value", "").split("_", 1)[0].strip()
        try:
            timer_seconds = max(0.0, float(timer_value) / 1000)
        except ValueError:
            timer_seconds = None

    rows.sort(key=lambda item: item["position"])
    return {
        "event_title": " · ".join(
            value for value in (fields.get("title1"), fields.get("title2")) if value
        ),
        "rows": rows,
        "remaining_seconds": timer_seconds,
        "received_at": time.time(),
        "pilots": list(dict.fromkeys(row["team"] for row in rows)),
    }


async def receive_apex_frame(websocket_url: str) -> str:
    messages: list[str] = []
    async with websockets.connect(
        websocket_url,
        open_timeout=8,
        close_timeout=2,
        max_size=MAX_HTML_BYTES * 4,
        ping_interval=None,
    ) as connection:
        for _ in range(4):
            message = await asyncio.wait_for(connection.recv(), timeout=6)
            messages.append(
                message.decode("utf-8", errors="replace")
                if isinstance(message, bytes)
                else message
            )
            if any(line.startswith("grid|") for line in messages[-1].splitlines()):
                break
    return "\n".join(messages)


@st.cache_data(ttl=1, show_spinner=False)
def fetch_apex_snapshot(page_url: str) -> dict:
    websocket_url = apex_websocket_endpoint(page_url)
    frame = asyncio.run(receive_apex_frame(websocket_url))
    snapshot = parse_apex_frame(frame)
    if not snapshot["rows"]:
        raise ValueError("Apex Timing ha aperto il collegamento ma non ha inviato la classifica.")
    return snapshot


def apex_pilot_options(snapshot: dict) -> tuple[list[str], dict[str, dict[str, str]]]:
    options: list[str] = []
    identities: dict[str, dict[str, str]] = {}
    for row in snapshot.get("rows", []):
        kart = row.get("kart", "").strip()
        label = f"Kart {kart} · {row['team']}" if kart else row["team"]
        if label not in identities:
            options.append(label)
            identities[label] = {"id": row.get("id", ""), "kart": kart}
    return options, identities


def find_apex_row(snapshot: dict, identity: dict[str, str] | None) -> dict | None:
    if not identity:
        return None
    row_id = identity.get("id", "")
    kart = identity.get("kart", "")
    rows = snapshot.get("rows", [])
    return (
        next((row for row in rows if row.get("id") == row_id), None)
        or next((row for row in rows if row.get("kart") == kart), None)
    )


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def lap_seconds(value: str) -> float | None:
    cleaned = (value or "").strip().replace(",", ".")
    if not cleaned:
        return None
    try:
        parts = [float(part) for part in cleaned.split(":")]
    except ValueError:
        return None
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return None


def gap_seconds(value: str, position: int) -> float | None:
    cleaned = (value or "").strip().replace(",", ".")
    if position == 1:
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return None


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


def start_strategy_session(pilot: str, start_message: str) -> None:
    st.session_state["_session_pilot"] = pilot
    st.session_state["_stint_started_at"] = time.time()
    st.session_state["_five_minute_logged"] = False
    st.session_state["_stint_end_logged"] = False
    st.session_state["events"] = []
    add_event(start_message)


def start_live_session(pilot: str) -> None:
    start_strategy_session(pilot, f"🟡 Telemetria simulata · {pilot} al volante")
    st.session_state["_last_lap"] = random.uniform(44.2, 49.8)
    st.session_state["_best_lap"] = st.session_state["_last_lap"] - random.uniform(0.1, 1.0)
    st.session_state["_position"] = random.randint(3, 12)
    st.session_state["_gap"] = random.uniform(1.2, 8.5)
    st.session_state["_last_sim_update"] = time.time()
    st.session_state["_next_random_event"] = time.time() + random.randint(8, 16)


def start_apex_session(pilot: str) -> None:
    start_strategy_session(pilot, f"🟢 Live Apex collegato · {pilot}")
    st.session_state["_last_apex_row"] = None
    st.session_state["_last_apex_remaining"] = None


def update_strategy_timer(max_stint_minutes: int) -> float:
    now = time.time()
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

    return remaining


def update_simulation(max_stint_minutes: int) -> float:
    now = time.time()
    elapsed_since_update = now - st.session_state.get("_last_sim_update", now)
    if elapsed_since_update >= 1.5:
        previous_best = st.session_state["_best_lap"]
        lap = random.uniform(44.0, 49.5)
        st.session_state["_last_lap"] = lap
        if lap < previous_best:
            st.session_state["_best_lap"] = lap
            add_event(f"⏱️ Nuovo best lap simulato · {lap:.3f} s")
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

    if now >= st.session_state["_next_random_event"]:
        if random.random() < 0.35:
            add_event("📻 Radio box simulata · ritmo regolare, pista libera")
        else:
            add_event("🟡 Strategia simulata · controllare distacco e finestra pit")
        st.session_state["_next_random_event"] = now + random.randint(8, 16)

    return update_strategy_timer(max_stint_minutes)


def update_apex_events(row: dict, snapshot: dict) -> None:
    previous = st.session_state.get("_last_apex_row")
    remaining = snapshot.get("remaining_seconds")
    previous_remaining = st.session_state.get("_last_apex_remaining")

    if previous:
        if row["position"] != previous["position"]:
            st.session_state["_position_delta"] = row["position"] - previous["position"]
            add_event(f"📊 Posizione live · P{previous['position']} → P{row['position']}")
        if (
            row["laps_completed"] is not None
            and previous["laps_completed"] is not None
            and row["laps_completed"] > previous["laps_completed"]
        ):
            add_event(f"🏎️ Giro completato · {row['laps_completed']} giri")
        if row["pits"] is not None and previous["pits"] is not None and row["pits"] > previous["pits"]:
            add_event(f"🛠️ Pit stop registrato · totale {row['pits']}")
        old_best = lap_seconds(previous["best_lap"])
        new_best = lap_seconds(row["best_lap"])
        if new_best is not None and (old_best is None or new_best < old_best):
            add_event(f"⏱️ Best lap Apex · {row['best_lap']} s")

    if (
        remaining is not None
        and remaining <= 300
        and (previous_remaining is None or previous_remaining > 300)
    ):
        add_event("🚨 Gara nella finestra finale · meno di 5 min")

    st.session_state["_last_apex_row"] = row.copy()
    st.session_state["_last_apex_remaining"] = remaining


def estimate_apex_rejoin_position(row: dict, rows: list[dict], pit_stop_seconds: int) -> int | None:
    selected_gap = gap_seconds(row.get("gap", ""), row["position"])
    if selected_gap is None:
        return None
    projected_gap = selected_gap + pit_stop_seconds
    same_lap_rows = [
        other
        for other in rows
        if gap_seconds(other.get("gap", ""), other["position"]) is not None
    ]
    return min(
        len(rows),
        1
        + sum(
            1
            for other in same_lap_rows
            if other.get("id") != row.get("id")
            and gap_seconds(other.get("gap", ""), other["position"]) <= projected_gap
        ),
    )


for key, default in {
    "pilots": [],
    "events": [],
    "selected_pilot": None,
    "timing_provider": None,
    "timing_url": "https://www.apex-timing.com/live-timing/firstkartinn/",
    "apex_pilot_identities": {},
    "apex_snapshot": None,
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
        key="timing_url",
        help="Apex Timing usa il flusso live; gli altri siti richiedono tabelle HTML pubbliche.",
    )

    if st.button("Cerca Piloti in pista", use_container_width=True):
        if not timing_url.strip():
            st.error("Inserisci prima l'URL del Live Timing.")
        else:
            try:
                with st.spinner("Lettura del Live Timing…"):
                    if is_apex_timing_url(timing_url):
                        snapshot = fetch_apex_snapshot(timing_url.strip())
                        discovered_pilots, identities = apex_pilot_options(snapshot)
                        provider = "apex"
                    else:
                        page = fetch_public_html(timing_url)
                        discovered_pilots = extract_pilots(page)
                        snapshot = None
                        identities = {}
                        provider = "html"

                st.session_state["pilots"] = discovered_pilots
                st.session_state["timing_provider"] = provider
                st.session_state["apex_pilot_identities"] = identities
                st.session_state["apex_snapshot"] = snapshot
                if st.session_state.get("selected_pilot") not in discovered_pilots:
                    st.session_state["selected_pilot"] = None
                if discovered_pilots:
                    st.success(
                        f"Trovati {len(discovered_pilots)} piloti/team "
                        f"{'sul Live Timing Apex' if provider == 'apex' else 'nelle tabelle HTML'}."
                    )
                else:
                    st.warning(
                        "Nessun nome trovato nelle tabelle HTML. "
                        "La pagina potrebbe caricare i dati solo con JavaScript."
                    )
            except Exception as exc:
                st.session_state["pilots"] = []
                st.session_state["selected_pilot"] = None
                st.session_state["timing_provider"] = None
                st.session_state["apex_pilot_identities"] = {}
                st.session_state["apex_snapshot"] = None
                st.error(f"Impossibile leggere il Live Timing: {exc}")

    pilot_options = st.session_state["pilots"]
    selected_pilot = st.selectbox(
        "Chi sono io",
        options=pilot_options,
        index=None,
        placeholder="Seleziona un pilota o team",
        disabled=not pilot_options,
        key="selected_pilot",
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
    elif st.session_state.get("timing_provider") == "apex":
        st.caption("Classifica, tempi e giri provengono dal flusso live Apex.")
    else:
        st.caption("Per questo sito sono disponibili i nomi; la telemetria resta simulata.")


st.title("Dashboard strategia box")
timing_provider = st.session_state.get("timing_provider")
if timing_provider == "apex":
    st.caption("Classifica e giri reali da Apex Timing · timer stint e stime pit locali")
else:
    st.caption("Monitoraggio endurance · telemetria simulata se il sito non espone dati live")

if live_timing and selected_pilot:
    if (
        not st.session_state.get("_was_live", False)
        or st.session_state.get("_session_pilot") != selected_pilot
    ):
        if timing_provider == "apex":
            start_apex_session(selected_pilot)
        else:
            start_live_session(selected_pilot)

    apex_row = None
    snapshot = None
    dashboard_ready = False
    if timing_provider == "apex":
        try:
            snapshot = fetch_apex_snapshot(st.session_state["timing_url"])
            st.session_state["apex_snapshot"] = snapshot
        except Exception as exc:
            snapshot = st.session_state.get("apex_snapshot")
            if snapshot and time.time() - snapshot.get("received_at", 0) <= 20:
                st.warning(f"Collegamento Apex in ritardo; mostro l’ultimo dato ricevuto. {exc}")
            else:
                st.error(f"Live Timing Apex temporaneamente non disponibile: {exc}")

        if snapshot:
            identity = st.session_state["apex_pilot_identities"].get(selected_pilot)
            apex_row = find_apex_row(snapshot, identity)
            if apex_row:
                update_apex_events(apex_row, snapshot)
                remaining_seconds = update_strategy_timer(int(max_stint))
                dashboard_ready = True
            else:
                st.warning("Kart/pilota selezionato non presente nell’ultimo aggiornamento Apex.")
    else:
        remaining_seconds = update_simulation(int(max_stint))
        dashboard_ready = True

    st.session_state["_was_live"] = True

    if dashboard_ready:
        telemetry_col, strategy_col, events_col = st.columns(3, gap="large")

        with telemetry_col:
            st.subheader("Telemetria")
            if apex_row:
                st.metric("Posizione live", f"P{apex_row['position']}")
                st.metric(
                    "Ultimo giro",
                    f"{apex_row['last_lap']} s" if apex_row["last_lap"] else "—",
                )
                st.metric(
                    "Miglior giro",
                    f"{apex_row['best_lap']} s" if apex_row["best_lap"] else "—",
                )
                st.metric(
                    "Distacco",
                    "Leader"
                    if apex_row["position"] == 1
                    else apex_row["gap"] or "—",
                )
                st.metric(
                    "Giri completati",
                    apex_row["laps_completed"]
                    if apex_row["laps_completed"] is not None
                    else "—",
                )
                race_remaining = snapshot.get("remaining_seconds")
                if race_remaining is not None:
                    race_remaining = max(
                        0.0,
                        race_remaining - (time.time() - snapshot.get("received_at", time.time())),
                    )
                st.metric("Tempo gara residuo", format_duration(race_remaining))
                st.caption(
                    f"Apex · Kart {apex_row['kart'] or '—'} · "
                    f"pits {apex_row['pits'] if apex_row['pits'] is not None else '—'} · "
                    f"on track {apex_row['on_track'] or '—'}"
                )
            else:
                st.metric("Posizione", f"P{st.session_state['_position']}")
                st.metric("Ultimo giro", f"{st.session_state['_last_lap']:.3f} s")
                st.metric("Miglior giro", f"{st.session_state['_best_lap']:.3f} s")
                st.metric("Distacco", f"+{st.session_state['_gap']:.1f} s")
                st.caption("Valori simulati · non provengono dal Live Timing.")

        with strategy_col:
            st.subheader("Strategia")
            elapsed_fraction = min(
                1.0,
                max(0.0, 1 - remaining_seconds / (int(max_stint) * 60)),
            )
            st.progress(
                elapsed_fraction,
                text=f"Stint · {format_duration(remaining_seconds)} al cambio",
            )

            if remaining_seconds > 600:
                st.success("FINESTRA VERDE · stint regolare")
            elif remaining_seconds > 300:
                st.warning("FINESTRA GIALLA · preparare il prossimo pilota")
            else:
                st.error("FINESTRA ROSSA · cambio pilota imminente")

            st.markdown("**Rientro stimato dopo il pit stop**")
            if apex_row:
                predicted_position = estimate_apex_rejoin_position(
                    apex_row,
                    snapshot["rows"],
                    int(pit_stop_seconds),
                )
                st.metric(
                    "Posizione prevista",
                    f"P{predicted_position}" if predicted_position is not None else "Non stimabile",
                )
                st.caption(
                    "Stima basata sui distacchi live confrontabili; non include traffico "
                    "o kart a un giro di distanza."
                )
            else:
                estimated_laps_lost = max(1, round(int(pit_stop_seconds) / 46))
                predicted_position = min(
                    20,
                    st.session_state["_position"] + estimated_laps_lost,
                )
                st.metric("Posizione prevista", f"P{predicted_position}")
                st.caption(
                    f"Stima simulata: +{estimated_laps_lost} posizioni "
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
