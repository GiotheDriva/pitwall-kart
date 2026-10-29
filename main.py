from __future__ import annotations

import asyncio
import copy
import hashlib
import html
import io
import ipaddress
import random
import re
import socket
import threading
import time
from datetime import datetime
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
import streamlit as st
import websockets
from bs4 import BeautifulSoup
import streamlit.components.v1 as components

import pro_tools
import gare
import piano


st.set_page_config(
    page_title="Pit Wall | Go-Kart Endurance",
    page_icon="🏁",
    layout="wide",
    initial_sidebar_state="auto",
)

st.markdown(
    """
    <style>
    .pitwall-banner {
        padding: 0.8rem 1rem;
        margin: 0.2rem 0 1rem;
        border: 1px solid rgba(86, 156, 255, 0.25);
        border-radius: 0.75rem;
        background: linear-gradient(110deg, rgba(24, 71, 119, 0.28), rgba(22, 32, 49, 0.32));
    }
    .pitwall-eyebrow {
        color: #72b7ff;
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.12em;
        text-transform: uppercase;
    }
    .pitwall-status {
        display: inline-flex;
        align-items: center;
        gap: 0.45rem;
        padding: 0.3rem 0.65rem;
        border-radius: 999px;
        background: rgba(40, 167, 100, 0.14);
        color: #70e0a4;
        font-size: 0.8rem;
        font-weight: 700;
    }
    .pitwall-status.stale {
        background: rgba(224, 163, 45, 0.15);
        color: #f0c36a;
    }
    .pit-log-box {
        height: 360px;
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
    .pitwall-note {
        color: #9aa4b2;
        font-size: 0.82rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


gare.route()


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


def parse_apex_comments(value: str) -> list[dict]:
    """Estrae i messaggi della sezione Commenti (campo 'com' di Apex, HTML con <p>)."""
    if not value or not value.strip():
        return []
    soup = BeautifulSoup(value, "lxml")
    comments: list[dict] = []
    for p in soup.find_all("p"):
        ora = p.b.get_text(strip=True) if p.b else ""
        testo = p.get_text(" ", strip=True)
        if ora and testo.startswith(ora):
            testo = testo[len(ora):].strip()
        if testo:
            comments.append({"time": ora, "text": testo})
    if not soup.find("p"):
        testo = soup.get_text(" ", strip=True)
        if testo:
            comments.append({"time": "", "text": testo})
    return comments


def parse_apex_frame(frame: str, state: dict | None = None) -> dict:
    """Apply one Apex WebSocket message to the persistent standings state.

    Apex sends the full grid once, then only the cells that change, as lines
    like ``r110096c6|tn|52.716``. ``state`` keeps the whole classification
    between messages so every update is merged, not replaced.
    """
    if state is None:
        state = {"header": {}, "cells": {}}
    header: dict[str, str] = state["header"]
    cells: dict[str, dict[str, str]] = state["cells"]
    fields: dict[str, str] = {}
    cell_key = re.compile(r"^(r\d+)(c\d+)$")

    for line in frame.splitlines():
        parts = line.split("|", 2)
        if len(parts) < 3:
            continue
        key, value_type, value = parts
        if key == "grid":
            grid = BeautifulSoup(value, "lxml")
            head = grid.select_one("tr.head")
            if head:
                header.clear()
                cells.clear()  # griglia completa: elimina le righe delle sessioni precedenti
                for index, td in enumerate(head.find_all("td", recursive=False), start=1):
                    tipo = (td.get("data-type") or "").strip().lower()
                    testo = td.get_text(" ", strip=True).lower()
                    if not tipo and "tempo pit" in testo:
                        tipo = "tpit"  # colonna "Tempo Pit": Apex non le assegna un data-type
                    header[f"c{index}"] = tipo
            for tr in grid.select("tr[data-id]"):
                if "head" in tr.get("class", []):
                    continue
                row_id = tr.get("data-id", "")
                values = {
                    f"c{index}": td.get_text(" ", strip=True)
                    for index, td in enumerate(tr.find_all("td", recursive=False), start=1)
                }
                cells[row_id] = values
        elif cell_key.match(key):
            row_id, col = cell_key.match(key).groups()
            cells.setdefault(row_id, {})[col] = value.strip()
        elif key == "com":
            fields["com"] = value
        elif key in {"title1", "title2"}:
            fields[key] = value.strip()
        elif key == "dyn1":
            fields["timer_type"] = value_type.strip().lower()
            fields["timer_value"] = value.strip()

    rows: list[dict] = []
    debug_sample: list[tuple[str, str]] = []
    for row_id, values in cells.items():
        by_type = {header[col]: text for col, text in values.items() if header.get(col)}
        if not debug_sample and by_type:
            debug_sample = [(header.get(col, ""), text) for col, text in values.items()]
        team = clean_apex_name(by_type.get("dr", ""))
        position = parse_apex_int(by_type.get("rk", ""))
        kart = by_type.get("no", "").strip()
        if not team or (position is None and not kart):
            continue
        rows.append(
            {
                "id": row_id,
                "position": position,
                "kart": kart,
                "team": team,
                "last_lap": by_type.get("llp", "").strip(),
                "gap": by_type.get("gap", "").strip(),
                "interval": "",
                "best_lap": by_type.get("blp", "").strip(),
                "laps_completed": parse_apex_int(by_type.get("tlp", "")),
                "pits": parse_apex_int(by_type.get("pit", "")),
                "last_pit": by_type.get("tpit", "").strip(),
                "on_track": by_type.get("otr", "").strip(),
            }
        )

    timer_seconds = None
    if fields.get("timer_type") in {"countdown", "countdown_text"}:
        timer_value = fields.get("timer_value", "").split("_", 1)[0].strip()
        try:
            timer_seconds = max(0.0, float(timer_value) / 1000)
        except ValueError:
            timer_seconds = None

    rows.sort(key=lambda item: (item["position"] is None, item["position"] or 0, item["kart"]))
    return {
        "event_title": " · ".join(
            value for value in (fields.get("title1"), fields.get("title2")) if value
        ),
        "rows": rows,
        "remaining_seconds": timer_seconds,
        "received_at": time.time(),
        "remaining_received_at": time.time() if timer_seconds is not None else None,
        "standings_received_at": time.time() if rows else None,
        "pilots": list(dict.fromkeys(row["team"] for row in rows)),
        "comments": parse_apex_comments(fields.get("com", "")),
        "debug": {"sample": [list(item) for item in debug_sample]},
    }


@st.cache_resource(show_spinner=False)
def apex_feed_registry() -> dict:
    """Keep one persistent WebSocket per Apex page across Streamlit reruns."""
    return {"lock": threading.RLock(), "feeds": {}}


def _set_feed_state(feed_key: str, **updates) -> None:
    registry = apex_feed_registry()
    with registry["lock"]:
        entry = registry["feeds"].get(feed_key)
        if entry is not None:
            entry.update(updates)


def _run_apex_feed(feed_key: str, websocket_url: str, stop_event: threading.Event) -> None:
    """Maintain the timing socket outside Streamlit's rerun cycle."""
    registry = apex_feed_registry()
    grid_state: dict = {"header": {}, "cells": {}}

    async def listen() -> None:
        retry_delay = 0.4
        while not stop_event.is_set():
            try:
                async with websockets.connect(
                    websocket_url,
                    open_timeout=8,
                    close_timeout=3,
                    max_size=MAX_HTML_BYTES * 4,
                    ping_interval=20,
                    ping_timeout=20,
                ) as connection:
                    _set_feed_state(feed_key, connected=True, last_error=None)
                    retry_delay = 0.4
                    while not stop_event.is_set():
                        try:
                            message = await asyncio.wait_for(connection.recv(), timeout=2)
                        except TimeoutError:
                            with registry["lock"]:
                                entry = registry["feeds"].get(feed_key)
                                last_access = entry.get("last_access", 0) if entry else 0
                            if time.monotonic() - last_access > 90:
                                return
                            continue

                        with registry["lock"]:
                            entry = registry["feeds"].get(feed_key)
                            last_access = entry.get("last_access", 0) if entry else 0
                        if time.monotonic() - last_access > 90:
                            return

                        frame = (
                            message.decode("utf-8", errors="replace")
                            if isinstance(message, bytes)
                            else message
                        )
                        update = parse_apex_frame(frame, grid_state)
                        now = time.time()
                        with registry["lock"]:
                            entry = registry["feeds"].get(feed_key)
                            if entry is None:
                                return
                            # Ogni frame conferma che il socket è vivo, anche senza classifica.
                            entry["last_message_at"] = now
                            entry["connected"] = True
                            snapshot = entry.get("snapshot") or {
                                "event_title": "",
                                "rows": [],
                                "remaining_seconds": None,
                                "remaining_received_at": None,
                                "standings_received_at": None,
                                "pilots": [],
                            }
                            # Conserva l'ultimo campione con righe: i frame solo-timer non lo cancellano.
                            if (update.get("debug") or {}).get("sample"):
                                snapshot["debug"] = update["debug"]
                            if update["rows"]:
                                snapshot["rows"] = update["rows"]
                                snapshot["pilots"] = update["pilots"]
                                snapshot["standings_received_at"] = now
                            if update["event_title"]:
                                snapshot["event_title"] = update["event_title"]
                            if update.get("comments"):
                                commenti = snapshot.setdefault("comments", [])
                                noti = {(c["time"], c["text"]) for c in commenti}
                                for item in update["comments"]:
                                    if (item["time"], item["text"]) not in noti:
                                        commenti.append(item)
                                        noti.add((item["time"], item["text"]))
                                del commenti[:-200]
                            if update["remaining_seconds"] is not None:
                                snapshot["remaining_seconds"] = update["remaining_seconds"]
                                snapshot["remaining_received_at"] = now
                            if (
                                update["rows"]
                                or update["event_title"]
                                or update["remaining_seconds"] is not None
                                or update.get("comments")
                            ):
                                snapshot["received_at"] = now
                                entry["snapshot"] = snapshot
                                entry["last_message_at"] = now
                                entry["connected"] = True
                                entry["last_error"] = None
                                if update["rows"]:
                                    entry["ready"].set()
            except Exception as exc:
                _set_feed_state(feed_key, connected=False, last_error=str(exc)[:240])
                if stop_event.is_set():
                    return
                await asyncio.sleep(retry_delay)
                retry_delay = min(8, retry_delay * 1.7)

    try:
        asyncio.run(listen())
    finally:
        _set_feed_state(feed_key, connected=False)


def fetch_apex_snapshot(page_url: str, wait_seconds: float = 7) -> dict:
    """Return the latest buffered frame, starting the socket only when needed."""
    page_url = page_url.strip()
    websocket_url = apex_websocket_endpoint(page_url)
    registry = apex_feed_registry()
    with registry["lock"]:
        entry = registry["feeds"].get(page_url)
        if entry is None or not entry["thread"].is_alive():
            stop_event = threading.Event()
            entry = {
                "snapshot": None,
                "connected": False,
                "last_error": None,
                "last_access": time.monotonic(),
                "last_message_at": None,
                "ready": threading.Event(),
                "stop_event": stop_event,
                "thread": None,
            }
            thread = threading.Thread(
                target=_run_apex_feed,
                args=(page_url, websocket_url, stop_event),
                name="apex-live-feed",
                daemon=True,
            )
            entry["thread"] = thread
            registry["feeds"][page_url] = entry
            thread.start()
        else:
            entry["last_access"] = time.monotonic()
        ready = entry["ready"]

    if wait_seconds > 0:
        ready.wait(wait_seconds)

    with registry["lock"]:
        entry = registry["feeds"].get(page_url)
        if entry is None:
            raise RuntimeError("Il collegamento live Apex non è disponibile.")
        entry["last_access"] = time.monotonic()
        snapshot = copy.deepcopy(entry["snapshot"])
        connected = entry["connected"]
        error = entry["last_error"]
        last_message_at = entry["last_message_at"]

    if not snapshot or not snapshot.get("rows"):
        if error:
            raise RuntimeError(f"Connessione Apex non riuscita: {error}")
        raise RuntimeError("Apex Timing non ha ancora inviato la classifica live.")
    snapshot["connected"] = connected
    snapshot["last_error"] = error
    snapshot["last_message_at"] = last_message_at
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


def format_live_duration(seconds: float | None) -> str:
    """Format a countdown with tenths so its progress is visible between frames."""
    if seconds is None:
        return "—"
    total = max(0.0, float(seconds))
    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    remainder = total % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{remainder:04.1f}"
    return f"{minutes:02d}:{remainder:04.1f}"


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
    st.session_state["_seen_apex_comments"] = []
    st.session_state["apex_history"] = []
    st.session_state["simulation_history"] = []
    st.session_state["_position_delta"] = None
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


def update_strategy_timer(max_stint_minutes: int, alert_minutes: int = 5) -> float:
    now = time.time()
    remaining = max(
        0.0,
        max_stint_minutes * 60 - (now - st.session_state["_stint_started_at"]),
    )

    if (
        max_stint_minutes > alert_minutes
        and remaining <= alert_minutes * 60
        and not st.session_state["_five_minute_logged"]
    ):
        add_event(
            f"🚨 Scadenza stint tra {alert_minutes} min · preparare il cambio"
        )
        st.session_state["_five_minute_logged"] = True
    if remaining <= 0 and not st.session_state["_stint_end_logged"]:
        add_event("🔴 Stint completato · rientro ai box consigliato")
        st.session_state["_stint_end_logged"] = True

    return remaining


def update_simulation(max_stint_minutes: int, alert_minutes: int = 5) -> float:
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
        history = st.session_state.setdefault("simulation_history", [])
        history.append(
            {
                "orario": datetime.now().strftime("%H:%M:%S"),
                "giro": len(history) + 1,
                "tempo_giro_s": round(lap, 3),
                "posizione": st.session_state["_position"],
            }
        )
        del history[:-200]

    if now >= st.session_state["_next_random_event"]:
        if random.random() < 0.35:
            add_event("📻 Radio box simulata · ritmo regolare, pista libera")
        else:
            add_event("🟡 Strategia simulata · controllare distacco e finestra pit")
        st.session_state["_next_random_event"] = now + random.randint(8, 16)

    return update_strategy_timer(max_stint_minutes, alert_minutes)


def record_apex_history(row: dict, previous: dict | None) -> None:
    changed_lap = (
        previous is None
        or row.get("laps_completed") != previous.get("laps_completed")
        or row.get("last_lap") != previous.get("last_lap")
    )
    changed_position = previous is not None and row.get("position") != previous.get("position")
    if not changed_lap and not changed_position:
        return

    history = st.session_state.setdefault("apex_history", [])
    history.append(
        {
            "orario": datetime.now().strftime("%H:%M:%S"),
            "giro": row.get("laps_completed"),
            "tempo_giro_s": lap_seconds(row.get("last_lap", "")),
            "posizione": row.get("position"),
            "distacco_s": gap_seconds(row.get("gap", ""), row.get("position", 0)),
        }
    )
    del history[:-300]


PENALTY_WORDS = ("penal", "sanzion", "ammonizion", "avviso", "warning", "attenzione", "stop and go", "drive through")


def is_penalty_text(text: str) -> bool:
    low = text.lower()
    return any(word in low for word in PENALTY_WORDS)


def is_mine(text: str, mine: dict) -> bool:
    kart = str(mine.get("kart") or "").strip()
    team = str(mine.get("team") or "").strip()
    if kart and re.search(rf"(?<!\d){re.escape(kart)}(?!\d)", text):
        return True
    return bool(team) and team.lower() in text.lower()


def log_apex_comments(snapshot: dict) -> None:
    """Scrive nel Registro eventi ogni messaggio della sezione Commenti, una sola volta."""
    seen = st.session_state.setdefault("_seen_apex_comments", [])
    for item in snapshot.get("comments", []):
        key = f"{item['time']}|{item['text']}"
        if key in seen:
            continue
        seen.append(key)
        icona = "⚠️" if is_penalty_text(item["text"]) else "💬"
        ora = f"{item['time']} · " if item["time"] else ""
        add_event(f"{icona} Apex {ora}{item['text']}")
    del seen[:-500]


def update_apex_events(row: dict, snapshot: dict) -> None:
    st.session_state["_apex_mine"] = {"kart": row.get("kart", ""), "team": row.get("team", "")}
    st.session_state["_apex_comments"] = list(snapshot.get("comments", []))
    log_apex_comments(snapshot)
    previous = st.session_state.get("_last_apex_row")
    remaining = snapshot.get("remaining_seconds")
    previous_remaining = st.session_state.get("_last_apex_remaining")

    if previous:
        if (
            row["position"] is not None
            and previous["position"] is not None
            and row["position"] != previous["position"]
        ):
            st.session_state["_position_delta"] = row["position"] - previous["position"]
            add_event(f"📊 Posizione live · P{previous['position']} → P{row['position']}")
        if (
            row["laps_completed"] is not None
            and previous["laps_completed"] is not None
            and row["laps_completed"] > previous["laps_completed"]
        ):
            add_event(f"🏎️ Giro completato · {row['laps_completed']} giri")
        if row["pits"] is not None and previous["pits"] is not None and row["pits"] > previous["pits"]:
            durata = row.get("last_pit") or "—"
            add_event(f"🛠️ Pit stop registrato · totale {row['pits']} · durata {durata}")
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

    record_apex_history(row, previous)
    st.session_state["_last_apex_row"] = row.copy()
    st.session_state["_last_apex_remaining"] = remaining


def estimate_apex_rejoin_position(row: dict, rows: list[dict], pit_stop_seconds: int) -> int | None:
    if row.get("position") is None:
        return None
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


def render_notebook(pilot: str, timing_url: str) -> None:
    """Render a browser-local engineering notebook with autosaved notes and sketch."""
    storage_key = hashlib.sha256(
        f"{timing_url.strip()}|{pilot}".encode("utf-8")
    ).hexdigest()[:24]
    notebook_html = r"""
    <!doctype html>
    <html lang="it">
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
          * { box-sizing: border-box; }
          body { margin: 0; background: #10141c; color: #e8edf5; font: 14px/1.45 system-ui, sans-serif; }
          .wrap { padding: 12px; border: 1px solid #2a3545; border-radius: 12px; background: #151b25; }
          .toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 10px; }
          button, select, input { border: 1px solid #38475b; border-radius: 7px; padding: 8px 10px; color: #e8edf5; background: #1d2633; }
          button { cursor: pointer; font-weight: 650; }
          button:hover { border-color: #69aaff; background: #26364b; }
          input[type=color] { width: 42px; height: 36px; padding: 3px; }
          input[type=range] { padding: 0; width: 110px; }
          .canvas-wrap { overflow: hidden; width: 100%; border: 1px solid #718096; border-radius: 8px; background: #f9fbff; touch-action: none; }
          canvas { display: block; width: 100%; height: auto; cursor: crosshair; touch-action: none; }
          textarea { display: block; width: 100%; min-height: 150px; margin: 12px 0; resize: vertical; padding: 12px; border: 1px solid #38475b; border-radius: 8px; background: #0e131b; color: #edf2fa; font: 14px/1.5 ui-monospace, monospace; }
          .label { color: #a9b5c6; font-size: 12px; }
          .status { margin-left: auto; color: #6fe0a1; font-size: 12px; }
          .help { color: #9aa8ba; font-size: 12px; margin: 5px 1px 0; }
          @media (max-width: 650px) { .status { width: 100%; margin-left: 0; } }
        </style>
      </head>
      <body>
        <div class="wrap">
          <div class="toolbar">
            <label class="label" for="tool">Strumento</label>
            <select id="tool"><option value="pen">Penna</option><option value="eraser">Gomma</option></select>
            <label class="label" for="color">Colore</label>
            <input id="color" type="color" value="#142b4a" aria-label="Colore penna">
            <label class="label" for="size">Tratto</label>
            <input id="size" type="range" min="1" max="18" value="3">
            <button id="undo" type="button">↶ Annulla</button>
            <button id="clear" type="button">Cancella disegno</button>
            <button id="save" type="button">Salva ora</button>
            <button id="png" type="button">Esporta PNG</button>
            <button id="txt" type="button">Esporta note</button>
            <span id="status" class="status">Caricamento taccuino…</span>
          </div>
          <div class="canvas-wrap"><canvas id="sheet" width="1600" height="620" aria-label="Foglio per schizzi tecnici"></canvas></div>
          <p class="help">Foglio a griglia: disegna con mouse, penna o touch. Gli appunti sono salvati in questo browser, separati per pilota e Live Timing.</p>
          <textarea id="notes" placeholder="Note box, pressioni gomme, rapporti, traffico, setup, strategia pit, messaggi radio…"></textarea>
        </div>
        <script>
          const STORAGE_KEY = "__STORAGE_KEY__";
          const canvas = document.getElementById("sheet");
          const ctx = canvas.getContext("2d");
          const notes = document.getElementById("notes");
          const status = document.getElementById("status");
          const tool = document.getElementById("tool");
          const color = document.getElementById("color");
          const size = document.getElementById("size");
          const undoStack = [];
          let drawing = false;
          let lastPoint = null;
          let saveTimer = null;
          let storageAvailable = true;

          function paintGrid() {
            ctx.save();
            ctx.globalCompositeOperation = "source-over";
            ctx.fillStyle = "#fbfcff";
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            ctx.strokeStyle = "#dce5f0";
            ctx.lineWidth = 1;
            for (let x = 0; x <= canvas.width; x += 32) {
              ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, canvas.height); ctx.stroke();
            }
            for (let y = 0; y <= canvas.height; y += 32) {
              ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(canvas.width, y); ctx.stroke();
            }
            ctx.strokeStyle = "#b9c9dc";
            for (let x = 0; x <= canvas.width; x += 160) {
              ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, canvas.height); ctx.stroke();
            }
            for (let y = 0; y <= canvas.height; y += 160) {
              ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(canvas.width, y); ctx.stroke();
            }
            ctx.restore();
          }

          function setStatus(message, error = false) {
            status.textContent = message;
            status.style.color = error ? "#f0a7a7" : "#6fe0a1";
          }

          function save() {
            try {
              localStorage.setItem(STORAGE_KEY, JSON.stringify({
                notes: notes.value,
                image: canvas.toDataURL("image/png"),
                savedAt: new Date().toISOString()
              }));
              storageAvailable = true;
              setStatus("Salvato in questo browser");
            } catch (error) {
              storageAvailable = false;
              setStatus("Storage browser non disponibile", true);
            }
          }

          function load() {
            paintGrid();
            try {
              const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
              notes.value = saved.notes || "";
              if (saved.image) {
                const image = new Image();
                image.onload = () => { ctx.drawImage(image, 0, 0, canvas.width, canvas.height); };
                image.src = saved.image;
              }
              setStatus(saved.savedAt ? "Note recuperate · autosalvataggio attivo" : "Pronto · autosalvataggio attivo");
            } catch (error) {
              storageAvailable = false;
              setStatus("Salvataggio locale non disponibile", true);
            }
          }

          function pointFromEvent(event) {
            const bounds = canvas.getBoundingClientRect();
            return {
              x: (event.clientX - bounds.left) * canvas.width / bounds.width,
              y: (event.clientY - bounds.top) * canvas.height / bounds.height
            };
          }

          function queueSave() {
            window.clearTimeout(saveTimer);
            saveTimer = window.setTimeout(save, 450);
          }

          canvas.addEventListener("pointerdown", event => {
            event.preventDefault();
            drawing = true;
            canvas.setPointerCapture(event.pointerId);
            undoStack.push(canvas.toDataURL("image/png"));
            if (undoStack.length > 12) undoStack.shift();
            lastPoint = pointFromEvent(event);
            ctx.beginPath();
            ctx.moveTo(lastPoint.x, lastPoint.y);
          });
          canvas.addEventListener("pointermove", event => {
            if (!drawing) return;
            event.preventDefault();
            const point = pointFromEvent(event);
            ctx.lineCap = "round";
            ctx.lineJoin = "round";
            ctx.lineWidth = Number(size.value);
            ctx.strokeStyle = tool.value === "eraser" ? "#fbfcff" : color.value;
            ctx.beginPath();
            ctx.moveTo(lastPoint.x, lastPoint.y);
            ctx.lineTo(point.x, point.y);
            ctx.stroke();
            lastPoint = point;
          });
          function finishStroke() {
            if (!drawing) return;
            drawing = false;
            lastPoint = null;
            save();
          }
          canvas.addEventListener("pointerup", finishStroke);
          canvas.addEventListener("pointercancel", finishStroke);
          notes.addEventListener("input", queueSave);
          document.getElementById("save").addEventListener("click", save);
          document.getElementById("undo").addEventListener("click", () => {
            const imageData = undoStack.pop();
            if (!imageData) return;
            const image = new Image();
            image.onload = () => { ctx.clearRect(0, 0, canvas.width, canvas.height); ctx.drawImage(image, 0, 0); save(); };
            image.src = imageData;
          });
          document.getElementById("clear").addEventListener("click", () => {
            undoStack.push(canvas.toDataURL("image/png"));
            paintGrid();
            save();
          });
          document.getElementById("png").addEventListener("click", () => {
            const link = document.createElement("a");
            link.download = "pit-wall-schizzo.png";
            link.href = canvas.toDataURL("image/png");
            link.click();
          });
          document.getElementById("txt").addEventListener("click", () => {
            const blob = new Blob([notes.value], {type: "text/plain;charset=utf-8"});
            const link = document.createElement("a");
            link.href = URL.createObjectURL(blob);
            link.download = "pit-wall-note.txt";
            link.click();
            URL.revokeObjectURL(link.href);
          });
          load();
        </script>
      </body>
    </html>
    """.replace("__STORAGE_KEY__", storage_key)
    components.html(notebook_html, height=700, scrolling=True)


for key, default in {
    "pilots": [],
    "events": [],
    "selected_pilot": None,
    "timing_provider": None,
    "timing_url": "https://www.apex-timing.com/live-timing/firstkartinn/",
    "apex_pilot_identities": {},
    "apex_snapshot": None,
    "_was_live": False,
    "apex_history": [],
    "simulation_history": [],
    "_position_delta": None,
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
        key="max_stint_minutes",
    )
    pit_stop_seconds = st.number_input(
        "Tempo Pit Stop (secondi)",
        min_value=0,
        max_value=600,
        value=120,
        step=5,
        key="pit_stop_seconds",
    )
    live_timing = st.toggle("Avvia Live Timing", value=False, key="live_timing")

    with st.expander("Personalizza muretto", expanded=False):
        refresh_interval = st.slider(
            "Aggiornamento interfaccia",
            min_value=0.15,
            max_value=1.0,
            value=0.25,
            step=0.05,
            format="%.2f s",
            help="Il WebSocket Apex resta sempre in ascolto. Questo intervallo regola solo il ridisegno della dashboard.",
        )
        alert_minutes = st.number_input(
            "Avviso stint prima (min)",
            min_value=1,
            max_value=30,
            value=5,
            step=1,
            help="A questo punto il countdown passa alla finestra rossa.",
        )
        history_limit = st.slider(
            "Giri da mostrare nei grafici",
            min_value=5,
            max_value=100,
            value=30,
            step=5,
        )
        next_driver = st.text_input(
            "Prossimo pilota (facoltativo)",
            placeholder="Nome per il prossimo stint",
            key="next_driver",
        )

    if not pilot_options:
        st.caption("Cerca i piloti per compilare la selezione.")
    elif st.session_state.get("timing_provider") == "apex":
        st.caption("Classifica, tempi e giri provengono dal flusso live Apex.")
    else:
        st.caption("Per questo sito sono disponibili i nomi; la telemetria resta simulata.")

    st.caption(
        f"Ridisegno ogni {refresh_interval:.2f} s · feed live indipendente"
    )


def render_live_dashboard(
    provider: str | None,
    pilot: str,
    timing_url: str,
    max_stint_minutes: int,
    pit_stop_seconds: int,
    alert_minutes: int,
) -> None:
    snapshot = None
    apex_row = None
    feed_age = None
    race_remaining = None

    if provider == "apex":
        try:
            snapshot = fetch_apex_snapshot(timing_url, wait_seconds=0)
            st.session_state["apex_snapshot"] = snapshot
        except Exception as exc:
            snapshot = st.session_state.get("apex_snapshot")
            if not snapshot:
                st.error(f"Connessione Apex in attesa: {exc}")
                return

        identity = st.session_state["apex_pilot_identities"].get(pilot)
        apex_row = find_apex_row(snapshot, identity)
        if not apex_row:
            st.warning("Kart selezionato non presente nell’ultimo aggiornamento Apex.")
            return

        update_apex_events(apex_row, snapshot)
        record_fleet(snapshot)
        remaining_seconds = update_strategy_timer(max_stint_minutes, alert_minutes)
        # Lo stato dipende dall'ultimo frame ricevuto dal socket, non dall'ultima
        # classifica: Apex invia la classifica solo quando cambia qualcosa.
        last_frame_at = snapshot.get("last_message_at") or snapshot.get(
            "received_at", time.time()
        )
        feed_age = max(0.0, time.time() - last_frame_at)
        connected = bool(snapshot.get("connected")) and feed_age < 30
        status_class = "" if connected else "stale"
        status_label = "LIVE · CONNESSO" if connected else "LIVE · RICONNESSIONE"
        event_title = html.escape(snapshot.get("event_title") or "Sessione Apex")
        st.markdown(
            '<div class="pitwall-banner">'
            '<span class="pitwall-eyebrow">Pit wall · endurance</span> &nbsp; '
            f'<span class="pitwall-status {status_class}">● {status_label}</span>'
            f'<span style="float:right;color:#aab6c6">{event_title}</span>'
            "</div>",
            unsafe_allow_html=True,
        )
        st.caption(
            f"Classifica aggiornata {feed_age:.1f} s fa · "
            "timer e stime box locali"
        )
        if not connected and snapshot.get("last_error"):
            st.caption(f"Ultimo stato rete: {snapshot['last_error']}")

        countdown_anchor = snapshot.get(
            "remaining_received_at",
            snapshot.get("received_at", time.time()),
        )
        if snapshot.get("remaining_seconds") is not None:
            race_remaining = max(
                0.0,
                snapshot["remaining_seconds"] - (time.time() - countdown_anchor),
            )
    else:
        remaining_seconds = update_simulation(max_stint_minutes, alert_minutes)
        st.markdown(
            '<div class="pitwall-banner">'
            '<span class="pitwall-eyebrow">Pit wall · modalità simulata</span>'
            '<span class="pitwall-status stale">● DATI DIMOSTRATIVI</span>'
            "</div>",
            unsafe_allow_html=True,
        )

    gara_aperta = gare.trova(gare.load_gare(), st.session_state.get(gare.VISTA))
    if gara_aperta and gara_aperta.get("tipo") == "Endurance":
        piano.render_card(gara_aperta, race_remaining, gare)

    noleggio = st.session_state.get("_tipo_gara") == "Noleggio"
    if noleggio:
        telemetry_col, events_col = st.columns([1.2, 1.2], gap="large")
        strategy_col = None
    else:
        telemetry_col, strategy_col, events_col = st.columns(
            [1.2, 1.0, 1.2], gap="large"
        )

    with telemetry_col:
        st.subheader("Telemetria")
        if apex_row:
            first_row, second_row = st.columns(2, gap="small")
            position_delta = (
                st.session_state.get("_position_delta")
                if apex_row["position"] is not None
                else None
            )
            delta_text = (
                f"{-position_delta:+d} pos"
                if position_delta not in (None, 0)
                else None
            )
            first_row.metric(
                "Posizione live",
                f"P{apex_row['position']}"
                if apex_row["position"] is not None
                else "—",
                delta=delta_text,
                delta_color="inverse",
            )
            second_row.metric(
                "Ultimo giro",
                f"{apex_row['last_lap']} s" if apex_row["last_lap"] else "—",
            )
            first_row.metric(
                "Miglior giro",
                f"{apex_row['best_lap']} s" if apex_row["best_lap"] else "—",
            )
            second_row.metric(
                "Distacco",
                "Leader"
                if apex_row["position"] == 1
                else apex_row["gap"] or "—",
            )
            first_row.metric(
                "Giri completati",
                apex_row["laps_completed"]
                if apex_row["laps_completed"] is not None
                else "—",
            )
            current_lap = (
                apex_row["laps_completed"] + 1
                if apex_row["laps_completed"] is not None and apex_row["on_track"]
                else "—"
            )
            second_row.metric("Giro in corso", current_lap)
            st.session_state["_ultimo_pit_apex"] = apex_row.get("last_pit") or ""
            st.caption(
                f"Kart {apex_row['kart'] or '—'} · pit stop "
                f"{apex_row['pits'] if apex_row['pits'] is not None else '—'} · "
                f"ultimo pit {apex_row.get('last_pit') or '—'} · "
                f"tempo in pista {apex_row['on_track'] or '—'}"
            )
            if race_remaining is not None:
                st.metric("Tempo gara residuo", format_live_duration(race_remaining))
        else:
            st.metric("Posizione simulata", f"P{st.session_state['_position']}")
            st.metric("Ultimo giro simulato", f"{st.session_state['_last_lap']:.3f} s")
            st.metric("Miglior giro simulato", f"{st.session_state['_best_lap']:.3f} s")
            st.metric("Distacco simulato", f"+{st.session_state['_gap']:.1f} s")
            st.caption("Valori dimostrativi · non provengono dal Live Timing.")

    if not noleggio:
        with strategy_col:
            st.subheader("Strategia")
            stint_total = max(1, max_stint_minutes * 60)
            elapsed_fraction = min(1.0, max(0.0, 1 - remaining_seconds / stint_total))
            st.progress(
                elapsed_fraction,
                text=f"Stint · {format_live_duration(remaining_seconds)} al cambio",
            )

            if remaining_seconds <= alert_minutes * 60:
                st.error("FINESTRA ROSSA · cambio pilota imminente")
            elif remaining_seconds <= (alert_minutes + 5) * 60:
                st.warning("FINESTRA GIALLA · preparare il prossimo pilota")
            else:
                st.success("FINESTRA VERDE · stint regolare")

            st.markdown("**Rientro stimato dopo il pit stop**")
            if apex_row and snapshot:
                predicted_position = estimate_apex_rejoin_position(
                    apex_row,
                    snapshot["rows"],
                    pit_stop_seconds,
                )
                st.metric(
                    "Posizione prevista",
                    f"P{predicted_position}" if predicted_position is not None else "Non stimabile",
                )
                st.caption(
                    f"Stima dai distacchi live · pit impostato a {pit_stop_seconds} s. "
                    "Non include traffico o kart a un giro di distanza."
                )
            else:
                estimated_laps_lost = max(1, round(pit_stop_seconds / 46))
                predicted_position = min(
                    20,
                    st.session_state["_position"] + estimated_laps_lost,
                )
                st.metric("Posizione prevista", f"P{predicted_position}")
                st.caption(
                    f"Stima simulata: +{estimated_laps_lost} posizioni "
                    f"(pit stop {pit_stop_seconds} s)."
                )
            st.caption(f"Avviso personalizzato a {alert_minutes} min dal cambio.")

    with events_col:
        st.subheader("Registro eventi")
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

    with st.expander("Comandi rapidi box", expanded=False):
        pit_col, flag_col, traffic_col, issue_col = st.columns(4)
        if pit_col.button("🛠️ PIT IN", use_container_width=True):
            st.session_state["_stint_started_at"] = time.time()
            st.session_state["_five_minute_logged"] = False
            st.session_state["_stint_end_logged"] = False
            next_driver = st.session_state.get("next_driver", "").strip()
            add_event(
                f"🛠️ PIT IN manuale · nuovo stint"
                f"{' · prossimo: ' + next_driver if next_driver else ''}"
            )
        if flag_col.button("🟡 Bandiera gialla", use_container_width=True):
            add_event("🟡 Bandiera gialla · valutare ritmo e traffico")
        if traffic_col.button("🚦 Traffico", use_container_width=True):
            add_event("🚦 Traffico segnalato · monitorare distacco")
        if issue_col.button("⚠️ Problema kart", use_container_width=True):
            add_event("⚠️ Problema kart segnalato · verificare al prossimo pit")
        note_col, note_button_col = st.columns([4, 1])
        quick_note = note_col.text_input(
            "Nota radio / evento",
            placeholder="Es. pressione gomme, vibrazione, strategia",
            key="quick_box_note",
        )
        if note_button_col.button("Registra", use_container_width=True):
            if quick_note.strip():
                add_event(f"📻 {quick_note.strip()}")
                st.session_state["quick_box_note"] = ""
            else:
                st.warning("Scrivi una nota prima di registrarla.")

def record_fleet(snapshot: dict) -> None:
    """Storico giro per giro di tutti i kart in pista, usato da Analisi e Confronto."""
    fleet = st.session_state.setdefault("fleet_laps", {})
    now = datetime.now().strftime("%H:%M:%S")
    for row in snapshot.get("rows", []):
        key = str(row.get("kart") or row.get("id") or "").strip()
        if not key:
            continue
        f = fleet.setdefault(
            key,
            {"kart": key, "team": "", "giri": [], "soste": [], "prev_giri": None, "prev_soste": None},
        )
        f["team"] = row.get("team", "") or f["team"]
        laps = row.get("laps_completed")
        if laps is not None:
            if f["prev_giri"] is not None and laps > f["prev_giri"]:
                f["giri"].append(
                    {
                        "giro": laps,
                        "tempo_s": lap_seconds(row.get("last_lap", "")),
                        "posizione": row.get("position"),
                        "distacco_s": gap_seconds(row.get("gap", ""), row.get("position", 0)),
                        "orario": now,
                    }
                )
                del f["giri"][:-400]
            f["prev_giri"] = laps
        pits = row.get("pits")
        if pits is not None:
            if f["prev_soste"] is not None and pits > f["prev_soste"]:
                f["soste"].append(
                    {"giro": laps, "totale": pits, "durata": row.get("last_pit", ""), "orario": now}
                )
                del f["soste"][:-100]
            f["prev_soste"] = pits


def _mio_kart() -> str:
    return str((st.session_state.get("_apex_mine") or {}).get("kart") or "")


def count_penalita_kart(kart: str) -> int:
    commenti = st.session_state.get("_apex_comments", [])
    return sum(
        1 for c in commenti
        if is_penalty_text(c["text"]) and is_mine(c["text"], {"kart": kart, "team": ""})
    )


def render_analisi_kart() -> None:
    """Analisi del proprio kart: giro per giro, passo del team, distacco e soste."""
    fleet = st.session_state.get("fleet_laps", {})
    mio = _mio_kart()
    f = fleet.get(mio)
    st.divider()
    st.markdown(f"#### Analisi del kart {mio or '—'}")
    if not f or not f["giri"]:
        st.caption("Nessun giro completato ancora.")
        return

    giri = pd.DataFrame(f["giri"])
    st.markdown("**Giro per giro**")
    st.dataframe(giri, hide_index=True, use_container_width=True)

    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("**Distacco dal leader**")
        serie = giri.dropna(subset=["distacco_s"])
        if len(serie):
            st.line_chart(serie.set_index("giro")[["distacco_s"]], y_label="Secondi", height=260)
        else:
            st.caption("Distacco non disponibile.")
    with right:
        st.markdown("**Passo medio dei kart del team**")
        team = f.get("team", "")
        passi = []
        for k, v in fleet.items():
            tempi = [g["tempo_s"] for g in v["giri"] if g.get("tempo_s")]
            if team and v.get("team") == team and tempi:
                passi.append({"Kart": k, "Passo medio (s)": round(sum(tempi) / len(tempi), 3)})
        if passi:
            st.bar_chart(pd.DataFrame(passi).set_index("Kart"), y_label="Secondi", height=260)
        else:
            st.caption("Nessun altro kart del team con giri registrati.")

    st.markdown("**Pit e tempo in sosta**")
    if f["soste"]:
        st.dataframe(pd.DataFrame(f["soste"]), hide_index=True, use_container_width=True)
    else:
        st.caption("Nessuna sosta registrata.")


@st.fragment(run_every=5)
def render_confronto() -> None:
    """Confronto tra tutti i kart in pista: giri, giro più veloce, soste e penalità."""
    fleet = st.session_state.get("fleet_laps", {})
    st.subheader("Confronto kart")
    if not fleet:
        st.info("Avvia il Live Timing: qui compaiono i giri di tutti i kart in pista.")
        return

    opzioni = {k: f"Kart {k} · {v.get('team') or 'pilota singolo'}" for k, v in fleet.items()}
    mio = _mio_kart()
    default = [mio] if mio in opzioni else list(opzioni)[:2]
    scelti = st.multiselect(
        "Kart da confrontare",
        list(opzioni),
        default=default,
        format_func=lambda k: opzioni[k],
        key="confronto_kart",
    )
    if not scelti:
        st.caption("Seleziona almeno un kart.")
        return

    righe = []
    for k in scelti:
        v = fleet[k]
        tempi = [g["tempo_s"] for g in v["giri"] if g.get("tempo_s")]
        righe.append(
            {
                "Kart": k,
                "Team / pilota": v.get("team") or "—",
                "Giri": v.get("prev_giri") or 0,
                "Giro più veloce (s)": round(min(tempi), 3) if tempi else None,
                "Giro medio (s)": round(sum(tempi) / len(tempi), 3) if tempi else None,
                "Ultimo giro (s)": round(tempi[-1], 3) if tempi else None,
                "Soste": v.get("prev_soste") or 0,
                "Ultima sosta": v["soste"][-1]["durata"] if v["soste"] else "—",
                "Penalità Apex": count_penalita_kart(k),
            }
        )
    st.markdown("**Riepilogo**")
    st.dataframe(pd.DataFrame(righe), hide_index=True, use_container_width=True)

    serie: dict[int, dict[str, float]] = {}
    for k in scelti:
        for g in fleet[k]["giri"]:
            if g.get("tempo_s"):
                serie.setdefault(g["giro"], {})[f"Kart {k}"] = g["tempo_s"]
    st.markdown("**Giri affiancati (secondi per giro)**")
    if serie:
        tabella = pd.DataFrame.from_dict(serie, orient="index").sort_index()
        tabella.index.name = "Giro"
        st.line_chart(tabella, y_label="Secondi", height=320)
        with st.expander("Tabella giro per giro"):
            st.dataframe(tabella.reset_index(), hide_index=True, use_container_width=True)
    else:
        st.caption("In attesa del primo giro completato.")

    soste = [{"Kart": k, **s} for k in scelti for s in fleet[k]["soste"]]
    st.markdown("**Pit e tempo in sosta**")
    if soste:
        st.dataframe(pd.DataFrame(soste), hide_index=True, use_container_width=True)
    else:
        st.caption("Nessuna sosta registrata.")


def render_analysis(provider: str | None, history_limit: int) -> None:
    st.subheader("Analisi stint")
    if provider == "apex":
        history = st.session_state.get("apex_history", [])
        if not history:
            st.info("I grafici si popolano dal momento in cui avvii il Live Timing.")
            return
        data = pd.DataFrame(history).tail(history_limit)
        lap_data = data.dropna(subset=["tempo_giro_s"])
        position_data = data.dropna(subset=["posizione"])
        lap_col, position_col = st.columns(2, gap="large")
        with lap_col:
            st.markdown("#### Andamento tempi sul giro")
            if len(lap_data) >= 1:
                st.line_chart(
                    lap_data.set_index("giro")[["tempo_giro_s"]],
                    y_label="Secondi",
                    height=300,
                )
            else:
                st.caption("In attesa del primo giro completato.")
        with position_col:
            st.markdown("#### Evoluzione posizione")
            if len(position_data) >= 1:
                chart_spec = {
                    "mark": {"type": "line", "point": True},
                    "encoding": {
                        "x": {"field": "orario", "type": "ordinal", "title": "Orario"},
                        "y": {
                            "field": "posizione",
                            "type": "quantitative",
                            "scale": {"reverse": True},
                            "title": "Posizione · P1 in alto",
                        },
                        "tooltip": [
                            {"field": "orario", "type": "ordinal", "title": "Orario"},
                            {"field": "posizione", "type": "quantitative", "title": "Posizione"},
                            {"field": "giro", "type": "quantitative", "title": "Giri"},
                        ],
                    },
                }
                st.vega_lite_chart(
                    position_data,
                    chart_spec,
                    use_container_width=True,
                )
            else:
                st.caption("In attesa dei cambi di posizione.")
        st.caption(
            "Storico raccolto da quando hai avviato questa sessione; "
            "le classifiche precedenti non vengono inventate."
        )
        st.dataframe(data, hide_index=True, width="stretch")
        st.download_button(
            "Esporta analisi CSV",
            data=data.to_csv(index=False).encode("utf-8"),
            file_name="pit-wall-analisi-apex.csv",
            mime="text/csv",
        )
        render_analisi_kart()
    elif provider == "html":
        history = st.session_state.get("simulation_history", [])
        if not history:
            st.info("Avvia la dashboard per raccogliere i dati dimostrativi.")
            return
        data = pd.DataFrame(history).tail(history_limit)
        left_chart, right_chart = st.columns(2, gap="large")
        with left_chart:
            st.markdown("#### Simulazione tempi sul giro")
            st.line_chart(
                data.set_index("giro")[["tempo_giro_s"]],
                y_label="Secondi",
                height=300,
            )
        with right_chart:
            st.markdown("#### Simulazione posizione")
            st.line_chart(
                data.set_index("orario")[["posizione"]],
                y_label="Posizione",
                height=300,
            )
        st.caption("Questi grafici sono dimostrativi, non dati timing del sito.")
        st.download_button(
            "Esporta simulazione CSV",
            data=data.to_csv(index=False).encode("utf-8"),
            file_name="pit-wall-simulazione.csv",
            mime="text/csv",
        )
    else:
        st.info("Cerca un Live Timing per iniziare una sessione di analisi.")


@st.fragment(run_every=5)
def render_classifica_sotto() -> None:
    """Classifica live sotto le tab: resta aggiornata in qualunque sezione."""
    snapshot = st.session_state.get("apex_snapshot")
    if not snapshot or not snapshot.get('rows'):
        return
    st.divider()
    st.subheader("Classifica live")
    field_state = pro_tools.track_field(snapshot["rows"], add_event)
    st.markdown(
        pro_tools.standings_html(snapshot["rows"], field_state),
        unsafe_allow_html=True,
    )
    with st.expander("Diagnostica feed Apex", expanded=False):
        st.caption(
            "Celle ricevute per la prima riga: tipo colonna → testo. "
            "Se la classifica mostra trattini, questo elenco mostra quale colonna non viene letta."
        )
        sample = (snapshot.get("debug") or {}).get("sample") or []
        if sample:
            st.dataframe(
                pd.DataFrame(sample, columns=["Colonna", "Valore"]),
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.info("Nessuna riga ricevuta ancora.")


st.title("🏁 Pit Wall Engineering")
timing_provider = st.session_state.get("timing_provider")
st.caption(
    "Centro operativo endurance · "
    "Apex live quando disponibile · strategie e stime sempre identificate"
)

pro_tools.inject_style()
_gara_aperta_tab = gare.trova(gare.load_gare(), st.session_state.get(gare.VISTA))
st.session_state["_tipo_gara"] = (_gara_aperta_tab or {}).get("tipo")
if st.session_state["_tipo_gara"] == "Noleggio":
    # Noleggio: niente penalità né pianificatore, solo muretto, analisi e fogli.
    pit_tab, analysis_tab, confronto_tab, sheets_tab, notebook_tab = st.tabs(
        ["🏎️ Pit wall", "📈 Analisi", "🆚 Confronto", "📊 Fogli dati", "📝 Taccuino tecnico"]
    )
    penalty_tab = planner_tab = None
else:
    pit_tab, penalty_tab, analysis_tab, confronto_tab, planner_tab, sheets_tab, notebook_tab = st.tabs(
        [
            "🏎️ Pit wall",
            "⚠️ Penalità e avvisi",
            "📈 Analisi",
            "🆚 Confronto",
            "🧭 Pianificatore",
            "📊 Fogli dati",
            "📝 Taccuino tecnico",
        ]
    )


@st.fragment(run_every=5)
def render_penalties() -> None:
    gid = st.session_state.get(gare.VISTA)
    gara = gare.trova(gare.load_gare(), gid) if gid and gid != "home" else None
    if gara:
        titolo, bottone = st.columns([4, 1.6])
        titolo.subheader("Penalità assegnate dal team")
        bottone.write("")
        if bottone.button("＋ Aggiungi penalità", type="primary", key=f"btn_pen_{gara['id']}"):
            gare.dialog_penalita(gara["id"], gara.get("piloti", []))
        manuali = list(reversed(gara.get("penalita", [])))
        if not manuali:
            st.caption("Nessuna penalità assegnata.")
        for p in manuali:
            with st.container(border=True):
                testo, cestino = st.columns([5, 1])
                testo.markdown(
                    f"**{p.get('pilota', '')}** · {pro_tools.descrivi_penalita(p)} · {p.get('motivo', '')}  \n"
                    f"{p.get('ora', '')}"
                )
                cestino.button(
                    "🗑️",
                    key=f"del_pen_{p['id']}",
                    help="Elimina penalità",
                    on_click=gare.elimina_penalita,
                    args=(gara["id"], p["id"]),
                )
        st.divider()

    mine = st.session_state.get("_apex_mine") or {}
    items = [
        c for c in reversed(st.session_state.get("_apex_comments", []))
        if is_penalty_text(c["text"])
    ]
    miei = [c for c in items if is_mine(c["text"], mine)]
    altri = [c for c in items if c not in miei]

    def riga(c: dict) -> str:
        return f"{c['time'] + ' · ' if c['time'] else ''}{c['text']}"

    st.subheader("Le mie penalità e avvisi")
    if not miei:
        st.caption("Nessuna penalità o avviso per il tuo kart.")
    for c in miei:
        st.error(riga(c))

    st.subheader("Tutte le altre penalità e avvisi")
    if not altri:
        st.caption("Nessun altro avviso.")
    for c in altri:
        st.markdown(f"- {riga(c)}")


if penalty_tab is not None:
    with penalty_tab:
        render_penalties()

if planner_tab is not None:
    with planner_tab:
        pro_tools.render_planner(
            gare.load_gare(),
            st.session_state.get(gare.VISTA),
            on_save=gare.salva_piano,
        )

with sheets_tab:
    sheet_lap, sheet_boards = st.tabs(["⏱️ Giri", "🎨 Lavagne e schizzi"])
    with sheet_lap:
        pro_tools.render_lap_sheet()
    with sheet_boards:
        pro_tools.render_boards()

with pit_tab:
    if live_timing and selected_pilot:
        if (
            not st.session_state.get("_was_live", False)
            or st.session_state.get("_session_pilot") != selected_pilot
        ):
            if timing_provider == "apex":
                start_apex_session(selected_pilot)
            else:
                start_live_session(selected_pilot)
        st.session_state["_was_live"] = True
        live_fragment = st.fragment(run_every=float(refresh_interval))(
            render_live_dashboard
        )
        live_fragment(
            timing_provider,
            selected_pilot,
            timing_url,
            int(max_stint),
            int(pit_stop_seconds),
            int(alert_minutes),
        )
    else:
        st.session_state["_was_live"] = False
        st.info(
            "Cerca i piloti nella barra laterale, seleziona il tuo kart "
            "e attiva **Avvia Live Timing**."
        )

with analysis_tab:
    analysis_fragment = st.fragment(
        run_every=max(1.0, float(refresh_interval))
    )(render_analysis)
    analysis_fragment(timing_provider, int(history_limit))

with confronto_tab:
    render_confronto()

with notebook_tab:
    st.subheader("Taccuino tecnico")
    notebook_target = selected_pilot or "Generale"
    st.caption(
        f"Spazio di lavoro per {notebook_target}. Note e schizzi si salvano "
        "nel browser in uso, separati per pilota e Live Timing."
    )
    render_notebook(notebook_target, timing_url)


render_classifica_sotto()
