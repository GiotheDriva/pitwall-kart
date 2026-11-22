"""Modalità semplice per la famiglia.

Il muretto pubblica ogni pochi secondi un feed (record «__mamma__» nell'archivio) con il pilota in pista,
il tempo allo stint, il prossimo pilota, la telemetria del nostro kart, il registro e le notifiche.
Questa vista lo legge ogni secondo: si apre con ?vista=facile oppure dal pulsante nel muretto.
"""
from __future__ import annotations

import html
import json
import os
import time
from pathlib import Path

import streamlit as st

import piano

DATA_FILE = Path(__file__).with_name("dati_gare.json")
MAMMA_ID = "__mamma__"

PUBBLICA_OGNI_S = 4.0   # il muretto scrive il feed al massimo ogni 4 secondi
DATI_FERMI_S = 45       # oltre questo tempo senza aggiornamenti la vista avvisa

CSS = """
<style>
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"] { display: none; }
.mf-card { background: #131c31; border: 1px solid rgba(148,163,184,.24); border-radius: 14px;
           padding: 16px 18px; margin-bottom: 14px; }
.mf-title { font-size: .78rem; text-transform: uppercase; letter-spacing: .08em; color: #94a3b8;
            font-weight: 700; margin-bottom: 8px; }
.mf-hero { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; flex-wrap: wrap; }
.mf-hero-r { text-align: right; }
.mf-driver { font-size: 2rem; font-weight: 800; color: #f8fafc; }
.mf-driver-sm { font-size: 1.3rem; margin-right: 12px; }
.mf-timer { font-size: 3.6rem; font-weight: 800; font-family: ui-monospace, Consolas, monospace; margin-top: 4px; line-height: 1.1; }
.mf-big { font-size: 2.2rem; font-weight: 800; font-family: ui-monospace, Consolas, monospace; }
.mf-sub { color: #94a3b8; font-size: .88rem; margin-top: 4px; }
.mf-next { display: flex; align-items: baseline; flex-wrap: wrap; gap: 6px; }
.mf-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }
.mf-metric { background: rgba(11,16,32,.65); border: 1px solid rgba(148,163,184,.16); border-radius: 10px; padding: 10px 12px; }
.mf-metric .k { font-size: .74rem; color: #94a3b8; }
.mf-metric .v { font-size: 1.25rem; font-weight: 700; color: #f1f5f9; margin-top: 2px; }
.mf-log { max-height: 360px; overflow-y: auto; }
.mf-ev { padding: 9px 0; border-bottom: 1px solid rgba(148,163,184,.14); line-height: 1.4; }
.mf-ev:last-child { border-bottom: 0; }
.mf-ev-t { color: #94a3b8; font-size: .85rem; margin-right: 8px; font-family: ui-monospace, Consolas, monospace; }
</style>
"""


def _hms(secondi: float) -> str:
    s = max(0, int(secondi))
    h, resto = divmod(s, 3600)
    m, s = divmod(resto, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# --------------------------------------------------------------------------- #
# Archivio del feed (autonomo, non richiede modifiche a gare.py)
# --------------------------------------------------------------------------- #
@st.cache_resource(show_spinner=False)
def _client_facile():
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
    except Exception:
        return None
    if not url or not key:
        return None
    from supabase import create_client
    return create_client(url, key)


def _leggi_file() -> list[dict]:
    try:
        dati = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        return dati if isinstance(dati, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def load_mamma_feed() -> dict:
    """Feed della modalità semplice, sempre fresco (senza cache)."""
    client = _client_facile()
    if client is not None:
        righe = client.table("gare").select("dati").eq("id", MAMMA_ID).execute().data or []
        dati = righe[0].get("dati") if righe else None
        return dict(dati.get("feed") or {}) if isinstance(dati, dict) else {}
    for r in _leggi_file():
        if r.get("id") == MAMMA_ID:
            return dict(r.get("feed") or {})
    return {}


def save_mamma_feed(feed: dict) -> None:
    record = {"id": MAMMA_ID, "feed": json.loads(json.dumps(feed, default=str))}
    client = _client_facile()
    if client is not None:
        client.table("gare").upsert([{"id": MAMMA_ID, "dati": record}]).execute()
        return
    altri = [r for r in _leggi_file() if r.get("id") != MAMMA_ID]
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(altri + [record], ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, DATA_FILE)


def set_mamma_gara(gara: dict | None) -> None:
    """Sceglie la gara mostrata nella modalità semplice (None = interrompe la condivisione)."""
    if gara is None:
        save_mamma_feed({})
        return
    save_mamma_feed({
        "attiva": True,
        "gara_id": gara["id"],
        "gara_nome": gara.get("nome") or "",
        "updated_at": time.time(),
    })


# --------------------------------------------------------------------------- #
# Lato muretto: pubblica il feed
# --------------------------------------------------------------------------- #
def pubblica(gara: dict | None, apex_row: dict | None, race_remaining: float | None, stint_ora) -> None:
    """Scrive il feed per la modalità semplice, solo se questa gara è quella condivisa.

    stint_ora: funzione senza argomenti che restituisce (secondi al cambio, ai box) del timer del muretto.
    """
    if not gara:
        return
    adesso = time.time()
    if adesso - float(st.session_state.get("_facile_ultimo", 0.0)) < PUBBLICA_OGNI_S:
        return
    st.session_state["_facile_ultimo"] = adesso

    precedente = load_mamma_feed()
    if not precedente.get("attiva") or precedente.get("gara_id") != gara.get("id"):
        return

    stint_rem_s, in_sosta = stint_ora()
    pilota = prossimo = stint_n = inizio_stint = None
    in_attesa = False
    finita = race_remaining is not None and race_remaining <= 0.5
    fine_gara_s = race_remaining

    piano_g = gara.get("piano") or {}
    durata_s = float(piano_g.get("ore") or gara.get("ore") or 0) * 3600
    if gara.get("tipo") == "Endurance" and "stint_min" in piano_g and durata_s > 0:
        apex_el = durata_s - race_remaining if race_remaining is not None else None
        elapsed = piano.tempo_gara(gara, apex_el)
        in_attesa = (not finita) and elapsed <= 1.0
        if fine_gara_s is None:
            fine_gara_s = max(0.0, durata_s - elapsed)
        per_pit, pendenti = piano.extra_penalita(gara)
        stints = piano.simula(
            durata_s,
            float(piano_g["stint_min"]) * 60,
            float(piano_g["pit_sec"]),
            piano_g.get("piloti", []),
            [float(t) for t in gara.get("pit_eseguiti", [])],
            per_pit,
            pendenti,
            elapsed,
            piano_g.get("durate_stint"),
            piano_g.get("piloti_stint"),
        )
        attuale = next((s for s in stints if s["inizio"] <= elapsed < s["fine"]), None)
        if attuale:
            pilota = attuale["pilota"]
            stint_n = attuale["n"]
            stint_rem_s = max(0.0, attuale["fine"] - elapsed)
            inizio_stint = adesso - max(0.0, elapsed - attuale["inizio"])
            seguente = next((s for s in stints if s["inizio"] > elapsed), None)
            prossimo = seguente["pilota"] if seguente else None
        elif elapsed >= durata_s:
            finita = True
        else:
            seguente = next((s for s in stints if s["inizio"] > elapsed), None)
            prossimo = seguente["pilota"] if seguente else None

    if not pilota:
        pilota = st.session_state.get("_session_pilot")

    r = apex_row or {}
    giri = r.get("laps_completed")
    feed = {
        "attiva": True,
        "gara_id": gara["id"],
        "gara_nome": gara.get("nome") or "",
        "updated_at": adesso,
        "gara_rem_s": fine_gara_s,
        "gara_rem_at": adesso,
        "in_pista": {
            "pilota": pilota or "—",
            "stint": stint_n,
            "remaining_s": stint_rem_s,
            "remaining_at": adesso,
            "in_sosta": bool(in_sosta),
            "in_attesa": bool(in_attesa),
            "finita": bool(finita),
            "prossimo": prossimo,
            "stint_inizio_at": inizio_stint,
        },
        "telemetria": {
            "position": r.get("position"),
            "kart": r.get("kart") or "",
            "last_lap": r.get("last_lap") or "",
            "best_lap": r.get("best_lap") or "",
            "gap": "Leader" if r.get("position") == 1 else (r.get("gap") or ""),
            "laps_completed": giri,
            "current_lap": (giri + 1) if giri is not None else None,
            "pits": r.get("pits"),
            "last_pit": r.get("last_pit") or "",
            "on_track": r.get("on_track") or "",
        },
        "events": [
            {"time": e.get("time", ""), "message": e.get("message", "")}
            for e in list(st.session_state.get("events", []))[:50]
        ],
        "notifiche": [
            {"id": n.get("id"), "time": n.get("time", ""), "text": n.get("text", ""), "kind": n.get("kind", "info")}
            for n in list(st.session_state.get("notifiche", []))[:60]
        ],
    }
    save_mamma_feed(feed)


# --------------------------------------------------------------------------- #
# Lato famiglia: la vista
# --------------------------------------------------------------------------- #
def _esci() -> None:
    st.session_state["modalita_facile"] = False
    if "vista" in st.query_params:
        del st.query_params["vista"]


def render() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    _corpo()


@st.fragment(run_every=1)
def _corpo() -> None:
    feed = load_mamma_feed()
    notifiche = feed.get("notifiche") or []
    chiave = lambda n: f"{n.get('id')}|{n.get('time')}|{n.get('text')}"  # noqa: E731
    nascoste = st.session_state.setdefault("_mf_nascoste", set())

    # Le notifiche già presenti all'apertura non vengono ripetute: compaiono solo le nuove.
    viste = st.session_state.get("_mf_viste")
    if viste is None:
        st.session_state["_mf_viste"] = {chiave(n) for n in notifiche}
    else:
        for n in reversed(notifiche):
            k = chiave(n)
            if k not in viste and k not in nascoste:
                viste.add(k)
                st.toast(str(n.get("text", "")), icon="🔔")
    attive = [n for n in notifiche if chiave(n) not in nascoste]

    c_tit, c_bell, c_exit = st.columns([5, 1.2, 1.5])
    c_tit.title(f"🏁 {feed.get('gara_nome') or 'Telemetria live'}")
    with c_bell:
        with st.popover(f"🔔 {len(attive)}"):
            h1, h2 = st.columns([3, 2])
            h1.markdown("**Notifiche**")
            if h2.button("Svuota", key="mf_svuota", disabled=not attive, use_container_width=True):
                nascoste.update(chiave(n) for n in attive)
                st.rerun()
            if not attive:
                st.caption("Nessuna notifica.")
            for n in attive[:60]:
                st.markdown(f"`{n.get('time', '')}` · {n.get('text', '')}")
    if c_exit.button("🔧 Muretto", use_container_width=True, help="Torna alla vista completa", key="mf_muretto"):
        _esci()
        st.rerun()

    if not feed.get("attiva"):
        st.info(
            "Nessuna gara condivisa al momento. Dal muretto vai in **⚙️ Impostazioni** "
            "e premi **📱 Condividi in modalità semplice**."
        )
        return

    adesso = time.time()
    upd = feed.get("updated_at")
    fermo = bool(upd) and (adesso - float(upd)) > DATI_FERMI_S
    if upd:
        st.caption(f"Ultimo aggiornamento dal muretto: {time.strftime('%H:%M:%S', time.localtime(float(upd)))}")
    if fermo:
        st.warning(
            "⚠️ Nessun aggiornamento da oltre 45 secondi: il muretto non risulta aperto oppure la connessione "
            "si è interrotta. I dati sotto potrebbero non essere attuali."
        )

    p = feed.get("in_pista") or {}
    pilota = html.escape(str(p.get("pilota") or "—"))
    if p.get("finita"):
        timer, colore, sub = "GARA CONCLUSA", "#38bdf8", ""
    elif p.get("in_attesa"):
        timer, colore, sub = "IN ATTESA DEL VIA", "#f59e0b", "Il conto parte al via della gara"
    elif p.get("in_sosta"):
        timer, colore, sub = "AI BOX", "#f59e0b", "Cambio pilota / sosta in corso"
    elif isinstance(p.get("remaining_s"), (int, float)):
        rem = max(0.0, float(p["remaining_s"]) - (adesso - float(p.get("remaining_at") or upd or adesso)))
        timer = _hms(rem)
        colore = "#ef4444" if rem <= 300 else "#22c55e"
        sub = f"Stint {p['stint']} · tempo residuo alla guida" if p.get("stint") else "Tempo residuo alla guida"
    else:
        timer, colore, sub = "—", "#94a3b8", ""
    if fermo:
        colore = "#94a3b8"

    rem_gara = feed.get("gara_rem_s")
    if isinstance(rem_gara, (int, float)):
        rg = max(0.0, float(rem_gara) - (adesso - float(feed.get("gara_rem_at") or upd or adesso)))
        fine_txt, fine_col = ("Terminata", "#38bdf8") if rg <= 0.5 else (_hms(rg), "#e2e8f0")
    else:
        fine_txt, fine_col = "—", "#94a3b8"

    st.markdown(
        '<div class="mf-card"><div class="mf-hero">'
        f'<div><div class="mf-title">In pista ora</div><div class="mf-driver">{pilota}</div>'
        f'<div class="mf-timer" style="color:{colore}">{timer}</div>'
        f'<div class="mf-sub">{html.escape(sub)}</div></div>'
        '<div class="mf-hero-r"><div class="mf-title">Fine gara tra</div>'
        f'<div class="mf-big" style="color:{fine_col}">{html.escape(fine_txt)}</div></div>'
        "</div></div>",
        unsafe_allow_html=True,
    )

    if not p.get("finita"):
        inizio = p.get("stint_inizio_at")
        if inizio and not p.get("in_sosta") and not p.get("in_attesa"):
            info = f"Stint iniziato {max(0, int((adesso - float(inizio)) // 60))} min fa"
        else:
            info = ""
        st.markdown(
            '<div class="mf-card"><div class="mf-title">Prossimo pilota</div><div class="mf-next">'
            f'<span class="mf-driver mf-driver-sm">{html.escape(str(p.get("prossimo") or "—"))}</span>'
            f'<span class="mf-sub">{html.escape(info)}</span></div></div>',
            unsafe_allow_html=True,
        )

    t = feed.get("telemetria") or {}
    voci = [
        ("Posizione", f"P{t['position']}" if t.get("position") else "—"),
        ("Kart", f"#{t['kart']}" if t.get("kart") else "—"),
        ("Ultimo giro", f"{t['last_lap']} s" if t.get("last_lap") else "—"),
        ("Miglior giro", f"{t['best_lap']} s" if t.get("best_lap") else "—"),
        ("Distacco", t.get("gap") or "—"),
        ("Giri completati", t.get("laps_completed") if t.get("laps_completed") is not None else "—"),
        ("Giro in corso", t.get("current_lap") if t.get("current_lap") is not None else "—"),
        ("Pit stop", t.get("pits") if t.get("pits") is not None else "—"),
        ("Ultimo pit", t.get("last_pit") or "—"),
        ("In pista", t.get("on_track") or "—"),
    ]
    celle = "".join(
        f'<div class="mf-metric"><div class="k">{html.escape(k)}</div><div class="v">{html.escape(str(v))}</div></div>'
        for k, v in voci
    )
    st.markdown(
        f'<div class="mf-card"><div class="mf-title">Telemetria del nostro kart</div><div class="mf-grid">{celle}</div></div>',
        unsafe_allow_html=True,
    )

    eventi = feed.get("events") or []
    righe = "".join(
        f'<div class="mf-ev"><span class="mf-ev-t">{html.escape(str(e.get("time", "")))}</span>'
        f'{html.escape(str(e.get("message", "")))}</div>'
        for e in eventi
    ) or '<div class="mf-sub">Nessun evento registrato ancora.</div>'
    st.markdown(
        f'<div class="mf-card"><div class="mf-title">Registro attività</div><div class="mf-log">{righe}</div></div>',
        unsafe_allow_html=True,
    )
