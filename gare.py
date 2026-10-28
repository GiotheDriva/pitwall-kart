"""Calendario gare, scheda gara e fatture del Pit Wall.

Le gare sono salvate in dati_gare.json accanto a main.py, così restano
disponibili dopo ogni riavvio. Aprendo una gara si entra nel muretto
con il link Live Timing collegato alla gara.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

import pro_tools

DATA_FILE = Path(__file__).with_name("dati_gare.json")
VISTA = "vista_gara"          # "home" oppure id della gara aperta
CARICATA = "_gara_caricata"   # ultima gara il cui link è stato caricato nel muretto


# --------------------------------------------------------------------------- #
# Archivio
# --------------------------------------------------------------------------- #
def _client():
    """Client Supabase se i Secrets sono impostati, altrimenti None (uso locale)."""
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
    except Exception:
        return None
    return _crea_client(url, key)


@st.cache_resource(show_spinner=False)
def _crea_client(url: str, key: str):
    from supabase import create_client

    return create_client(url, key)


@st.cache_data(ttl=2, show_spinner=False)
def _leggi_remoto() -> list[dict]:
    res = _client().table("gare").select("dati").execute()
    return [row["dati"] for row in (res.data or [])]


def _leggi_locale() -> list[dict]:
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def load_gare() -> list[dict]:
    if _client() is None:
        return _leggi_locale()
    gare = _leggi_remoto()
    locale = DATA_FILE.exists() and _leggi_locale()
    if not gare and locale:
        # Primo avvio con Supabase: importa le gare locali una sola volta.
        save_gare(locale)
        DATA_FILE.rename(DATA_FILE.with_suffix(".importato.json"))
        return locale
    return gare


def save_gare(gare: list[dict]) -> None:
    client = _client()
    if client is None:
        tmp = DATA_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(gare, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, DATA_FILE)
        return
    if gare:
        client.table("gare").upsert([{"id": g["id"], "dati": g} for g in gare]).execute()
    ids = {g["id"] for g in gare}
    esistenti = client.table("gare").select("id").execute().data or []
    for row in esistenti:
        if row["id"] not in ids:
            client.table("gare").delete().eq("id", row["id"]).execute()
    _leggi_remoto.clear()


def trova(gare: list[dict], gara_id: str) -> dict | None:
    return next((g for g in gare if g.get("id") == gara_id), None)


def euro(value: float | int | None) -> str:
    amount = float(value or 0)
    return f"€ {amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def formato_tipo(gara: dict) -> str:
    if gara.get("tipo") == "Endurance":
        return f"Endurance · {gara.get('ore') or '—'} h"
    if gara.get("tipo") == "Noleggio":
        return f"Noleggio · {gara.get('minuti') or '—'} min"
    return f"Sprint · {gara.get('giri') or '—'} giri"


def apri_gara(gara_id: str) -> None:
    st.session_state[VISTA] = gara_id


def torna_home() -> None:
    st.session_state[VISTA] = "home"
    st.session_state[CARICATA] = None


# --------------------------------------------------------------------------- #
# Nuova gara
# --------------------------------------------------------------------------- #
@st.dialog("Nuova gara")
def dialog_nuova_gara() -> None:
    nome = st.text_input("Nome della gara *", key="ng_nome", placeholder="Es. 4 ore di Pomposa")
    c1, c2 = st.columns(2)
    tipo = c1.radio("Tipo di gara", ["Endurance", "Sprint", "Noleggio"], horizontal=True, key="ng_tipo")
    if tipo == "Noleggio":
        team = ""  # nel noleggio si partecipa come piloti singoli
    else:
        team = c2.text_input("Nome del team *", key="ng_team")

    c3, c4 = st.columns(2)
    minuti = None
    if tipo == "Noleggio":
        minuti = c3.number_input("Durata (minuti)", min_value=5, max_value=600, value=20, step=5, key="ng_minuti")
        ore = None
        giri = None
    elif tipo == "Endurance":
        ore = c3.number_input("Durata (ore)", min_value=0.5, max_value=24.0, value=4.0, step=0.5, key="ng_ore")
        giri = None
    else:
        giri = c3.number_input("Numero di giri", min_value=1, max_value=500, value=20, step=1, key="ng_giri")
        ore = None
    quota = c4.number_input("Quota di partecipazione (€)", min_value=0.0, value=0.0, step=5.0, key="ng_quota")

    data_gara = st.date_input("Data della gara", value=date.today(), key="ng_data")
    piloti_txt = st.text_area("Piloti (uno per riga)", key="ng_piloti", height=110)
    pit_minimo = 0
    if tipo != "Noleggio":
        pit_minimo = st.number_input("Pit minimo obbligatorio", min_value=0, max_value=50, value=0, step=1,
                                     key="ng_pit", help="Numero minimo di soste previste dal regolamento.")
    link = st.text_input(
        "Link Live Timing Apex (facoltativo, puoi aggiungerlo dopo)",
        key="ng_link",
        placeholder="https://www.apex-timing.com/live-timing/…",
    )

    if st.button("Crea gara", type="primary", key="ng_crea"):
        if not nome.strip() or (tipo != "Noleggio" and not team.strip()):
            st.error("Nome della gara e nome del team sono obbligatori.")
            return
        gara = {
            "id": uuid.uuid4().hex[:12],
            "nome": nome.strip(),
            "tipo": tipo,
            "ore": float(ore) if ore is not None else None,
            "giri": int(giri) if giri is not None else None,
            "minuti": int(minuti) if minuti is not None else None,
            "team": team.strip(),
            "piloti": [p.strip() for p in piloti_txt.splitlines() if p.strip()],
            "quota": float(quota),
            "pit_minimo": int(pit_minimo),
            "pagata": False,
            "data_pagamento": "",
            "data": data_gara.isoformat(),
            "link": link.strip(),
            "note": "",
            "creata": datetime.now().isoformat(timespec="seconds"),
        }
        gare = load_gare()
        gare.append(gara)
        save_gare(gare)
        apri_gara(gara["id"])
        st.rerun()


# --------------------------------------------------------------------------- #
# Home: calendario e fatture
# --------------------------------------------------------------------------- #
@st.dialog("Elimina gara")
def dialog_elimina(gara_id: str, nome: str) -> None:
    st.warning(f"Stai per eliminare **{nome}**. Questa operazione non si può annullare.")
    st.write("Per confermare, scrivi il nome esatto della gara:")
    scritto = st.text_input("Nome della gara", key=f"del_nome_{gara_id}", placeholder=nome)
    if st.button(
        "Elimina definitivamente",
        type="primary",
        key=f"del_ok_{gara_id}",
        disabled=scritto.strip() != nome.strip(),
    ):
        _elimina(gara_id)
        st.rerun()


def render_home() -> None:
    gare = sorted(load_gare(), key=lambda g: g.get("data", ""), reverse=True)

    titolo, bottone = st.columns([5, 1.4])
    titolo.title("🏁 Calendario gare")
    titolo.caption("Gare, team e pagamenti del muretto")
    bottone.write("")
    if bottone.button("＋ Nuova gara", type="primary", key="btn_nuova_gara"):
        dialog_nuova_gara()

    tab_gare, tab_fatture = st.tabs(["🗓️ Gare", "🧾 Fatture"])

    with tab_gare:
        if not gare:
            st.info("Nessuna gara ancora. Premi **＋ Nuova gara** in alto a destra.")
        for gara in gare:
            with st.container(border=True):
                c1, c2, c3, c4, c5, c6 = st.columns([3, 2.2, 2, 2, 1, 0.5])
                sottotitolo = " · ".join(x for x in [gara.get("team", ""), gara.get("data", "")] if x)
                c1.markdown(f"**{gara['nome']}**  \n{sottotitolo}")
                c2.markdown(f"{formato_tipo(gara)}  \n{len(gara.get('piloti', []))} piloti")
                c3.markdown(
                    f"{euro(gara.get('quota'))}  \n"
                    + ("✅ Pagata" if gara.get("pagata") else "⏳ Da pagare")
                )
                c4.markdown("🟢 Live Timing collegato" if gara.get("link") else "⚪ Nessun link")
                if c5.button("Apri", key=f"apri_{gara['id']}"):
                    apri_gara(gara["id"])
                    st.rerun()
                if c6.button("🗑️", key=f"del_{gara['id']}", help="Elimina questa gara"):
                    dialog_elimina(gara["id"], gara["nome"])

    with tab_fatture:
        render_fatture(gare)


def render_fatture(gare: list[dict]) -> None:
    if not gare:
        st.info("Le fatture compariranno quando aggiungi una gara.")
        return

    totale = sum(float(g.get("quota") or 0) for g in gare)
    pagato = sum(float(g.get("quota") or 0) for g in gare if g.get("pagata"))
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Gare", len(gare))
    k2.metric("Totale quote", euro(totale))
    k3.metric("Pagato", euro(pagato))
    k4.metric("Da pagare", euro(totale - pagato))

    st.caption("Spunta **Pagata** e indica la data di pagamento: le modifiche si salvano subito.")
    df = pd.DataFrame(
        [
            {
                "ID": g["id"],
                "Gara": g["nome"],
                "Team": g.get("team", ""),
                "Data": g.get("data", ""),
                "Quota (€)": float(g.get("quota") or 0),
                "Pagata": bool(g.get("pagata")),
                "Data pagamento": g.get("data_pagamento", ""),
            }
            for g in gare
        ]
    )
    edited = st.data_editor(
        df,
        hide_index=True,
        key="editor_fatture",
        disabled=["ID", "Gara", "Team", "Data", "Quota (€)"],
        column_config={
            "ID": None,
            "Quota (€)": st.column_config.NumberColumn(format="€ %.2f"),
            "Pagata": st.column_config.CheckboxColumn("Pagata"),
            "Data pagamento": st.column_config.TextColumn(help="Esempio: 2026-10-28"),
        },
    )

    archivio = load_gare()
    changed = False
    for _, row in edited.iterrows():
        gara = trova(archivio, row["ID"])
        if not gara:
            continue
        if bool(row["Pagata"]) != bool(gara.get("pagata")) or str(row["Data pagamento"] or "") != gara.get("data_pagamento", ""):
            gara["pagata"] = bool(row["Pagata"])
            gara["data_pagamento"] = str(row["Data pagamento"] or "").strip()
            changed = True
    if changed:
        save_gare(archivio)
        st.rerun()

    riepilogo: dict[str, dict] = {}
    for _, row in df.iterrows():
        r = riepilogo.setdefault(row["Team"], {"Team": row["Team"], "Gare": 0,
                                               "Totale (€)": 0.0, "Pagato (€)": 0.0, "Da pagare (€)": 0.0})
        r["Gare"] += 1
        r["Totale (€)"] += row["Quota (€)"]
        r["Pagato (€)" if row["Pagata"] else "Da pagare (€)"] += row["Quota (€)"]
    per_team = pd.DataFrame(list(riepilogo.values()))
    st.markdown("**Riepilogo per team**")
    st.dataframe(per_team.round(2), hide_index=True)
    st.download_button(
        "Scarica fatture (CSV)",
        data=df.drop(columns=["ID"]).to_csv(index=False).encode("utf-8"),
        file_name=f"fatture-gare-{date.today().isoformat()}.csv",
        mime="text/csv",
    )


# --------------------------------------------------------------------------- #
# Scheda gara: link Live Timing, dettagli, elimina
# --------------------------------------------------------------------------- #
def _salva_link(gara_id: str) -> None:
    link = st.session_state.get(f"sg_link_{gara_id}", "").strip()
    gare = load_gare()
    gara = trova(gare, gara_id)
    if not gara:
        return
    gara["link"] = link
    save_gare(gare)
    if link:
        st.session_state["timing_url"] = link
        st.session_state[CARICATA] = gara_id


def _salva_dettagli(gara_id: str) -> None:
    gare = load_gare()
    gara = trova(gare, gara_id)
    if not gara:
        return
    key = lambda name: st.session_state.get(f"sg_{name}_{gara_id}")  # noqa: E731
    gara.update(
        {
            "piloti": [p.strip() for p in (key("piloti") or "").splitlines() if p.strip()],
            "quota": float(key("quota") or 0),
            "pit_minimo": int(key("pit_minimo") or 0),
            "pagata": bool(key("pagata")),
            "data_pagamento": (key("data_pagamento") or "").strip(),
            "note": (key("note") or "").strip(),
        }
    )
    save_gare(gare)


def _elimina(gara_id: str) -> None:
    save_gare([g for g in load_gare() if g.get("id") != gara_id])
    torna_home()


def render_scheda(gara: dict) -> None:
    gid = gara["id"]
    titolo, indietro = st.columns([5, 1.4])
    titolo.title(f"🏁 {gara['nome']}")
    titolo.caption(" · ".join(x for x in [gara.get("team", ""), gara.get("data", ""), formato_tipo(gara)] if x))
    indietro.write("")
    if indietro.button("← Calendario", key=f"indietro_{gid}"):
        torna_home()
        st.rerun()

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Piloti", len(gara.get("piloti", [])))
    k2.metric("Quota", euro(gara.get("quota")))
    k3.metric("Pagamento", "Pagata" if gara.get("pagata") else "Da pagare")
    k4.metric("Live Timing", "Collegato" if gara.get("link") else "Da collegare")

    st.markdown("#### Link Live Timing")
    if not gara.get("link"):
        st.info("Incolla il link della pagina Apex di questa gara: poi il muretto qui sotto si collega da solo.")
    st.text_input(
        "Link Apex Timing",
        value=gara.get("link", ""),
        key=f"sg_link_{gid}",
        placeholder="https://www.apex-timing.com/live-timing/…",
    )
    st.button("Salva link e collega il muretto", key=f"salva_link_{gid}", type="primary",
              on_click=_salva_link, args=(gid,))

    with st.expander("Dettagli gara", expanded=False):
        st.text_area("Piloti (uno per riga)", value="\n".join(gara.get("piloti", [])),
                     key=f"sg_piloti_{gid}", height=120)
        c1, c2, c3 = st.columns(3)
        st.number_input("Pit minimo obbligatorio", min_value=0, max_value=50, step=1,
                        value=int(gara.get("pit_minimo") or 0), key=f"sg_pit_minimo_{gid}")
        c1.number_input("Quota di partecipazione (€)", min_value=0.0, value=float(gara.get("quota") or 0),
                        step=5.0, key=f"sg_quota_{gid}")
        c2.checkbox("Pagata", value=bool(gara.get("pagata")), key=f"sg_pagata_{gid}")
        c3.text_input("Data pagamento", value=gara.get("data_pagamento", ""),
                      key=f"sg_data_pagamento_{gid}", placeholder="2026-10-28")
        st.text_area("Note", value=gara.get("note", ""), key=f"sg_note_{gid}", height=80)
        st.button("Salva dettagli", key=f"salva_dettagli_{gid}", on_click=_salva_dettagli, args=(gid,))
        st.divider()
        st.button("Elimina questa gara", key=f"elimina_{gid}", on_click=_elimina, args=(gid,))

    st.divider()


# --------------------------------------------------------------------------- #
# Router: chiamato all'inizio di main.py
# --------------------------------------------------------------------------- #
def route() -> None:
    pro_tools.inject_style()
    gare = load_gare()
    gara_id = st.session_state.get(VISTA, "home")
    gara = trova(gare, gara_id) if gara_id != "home" else None

    if gara is None:
        if gara_id != "home":
            torna_home()
        render_home()
        st.stop()

    if st.session_state.get(CARICATA) != gara["id"]:
        if gara.get("link"):
            st.session_state["timing_url"] = gara["link"]
        st.session_state[CARICATA] = gara["id"]
    render_scheda(gara)


# --------------------------------------------------------------------------- #
# Piano vivo: salvataggio da Pianificatore e pit registrati dal muretto
# --------------------------------------------------------------------------- #
def salva_piano(gara_id: str, piano: dict) -> None:
    gare_all = load_gare()
    gara = trova(gare_all, gara_id)
    if gara:
        gara["piano"] = piano
        save_gare(gare_all)


def registra_pit(gara_id: str, elapsed_s: float) -> None:
    gare_all = load_gare()
    gara = trova(gare_all, gara_id)
    if gara:
        gara.setdefault("pit_eseguiti", []).append(round(float(elapsed_s), 1))
        save_gare(gare_all)


def annulla_pit(gara_id: str) -> None:
    gare_all = load_gare()
    gara = trova(gare_all, gara_id)
    if gara and gara.get("pit_eseguiti"):
        gara["pit_eseguiti"].pop()
        save_gare(gare_all)


# --------------------------------------------------------------------------- #
# Cronometro gara e controlli del piano (dal muretto)
# --------------------------------------------------------------------------- #
def _aggiorna(gara_id: str, modifica) -> None:
    gare_all = load_gare()
    gara = trova(gare_all, gara_id)
    if gara:
        modifica(gara)
        save_gare(gare_all)


def _cronometro(gara: dict) -> dict:
    return gara.setdefault(
        "cronometro", {"stato": "fermo", "offset_s": 0.0, "avvio": None, "usa_apex": False}
    )


def cron_avvia(gara_id: str) -> None:
    def fn(g):
        c = _cronometro(g)
        if c["stato"] != "in_corso":
            c["avvio"] = datetime.now().timestamp()
            c["stato"] = "in_corso"
    _aggiorna(gara_id, fn)


def cron_pausa(gara_id: str) -> None:
    def fn(g):
        c = _cronometro(g)
        if c["stato"] == "in_corso" and c.get("avvio"):
            c["offset_s"] = float(c["offset_s"]) + (datetime.now().timestamp() - c["avvio"])
        c["avvio"] = None
        c["stato"] = "fermo"
    _aggiorna(gara_id, fn)


def cron_azzera(gara_id: str) -> None:
    def fn(g):
        c = _cronometro(g)
        c.update({"stato": "fermo", "offset_s": 0.0, "avvio": None})
    _aggiorna(gara_id, fn)


def cron_imposta(gara_id: str, secondi: float) -> None:
    def fn(g):
        c = _cronometro(g)
        c["offset_s"] = max(0.0, float(secondi))
        if c["stato"] == "in_corso":
            c["avvio"] = datetime.now().timestamp()
    _aggiorna(gara_id, fn)


def cron_usa_apex(gara_id: str) -> None:
    valore = bool(st.session_state.get(f"usa_apex_{gara_id}"))
    _aggiorna(gara_id, lambda g: _cronometro(g).__setitem__("usa_apex", valore))


# --------------------------------------------------------------------------- #
# Penalità assegnate dal team (gestite dalla tab "Penalità e avvisi")
# --------------------------------------------------------------------------- #
def aggiungi_penalita(gara_id: str, pilota: str, tipo: str, valore: float, motivo: str) -> None:
    def fn(g):
        g.setdefault("penalita", []).append(
            {
                "id": uuid.uuid4().hex[:8],
                "pilota": pilota,
                "tipo": tipo,  # "secondi" oppure "giri"
                "valore": float(valore),
                "motivo": motivo.strip(),
                "ora": datetime.now().isoformat(timespec="seconds"),
            }
        )
    _aggiorna(gara_id, fn)


def elimina_penalita(gara_id: str, pen_id: str) -> None:
    def fn(g):
        g["penalita"] = [p for p in g.get("penalita", []) if p.get("id") != pen_id]
    _aggiorna(gara_id, fn)


@st.dialog("Aggiungi penalità")
def dialog_penalita(gara_id: str, piloti: list[str]) -> None:
    if not piloti:
        st.error("Prima aggiungi i piloti nella scheda gara.")
        return
    pilota = st.selectbox("Pilota colpevole", piloti, key=f"pen_pilota_{gara_id}")
    c1, c2 = st.columns(2)
    tipo_label = c1.radio("Penalità in", ["Secondi", "Giri"], horizontal=True, key=f"pen_tipo_{gara_id}")
    valore = c2.number_input("Valore", min_value=1.0, max_value=3600.0, value=10.0, step=1.0, key=f"pen_valore_{gara_id}")
    motivo = st.text_input("Motivo *", key=f"pen_motivo_{gara_id}", placeholder="Es. taglio di curva, partenza anticipata")
    if st.button("Salva penalità", type="primary", key=f"pen_salva_{gara_id}"):
        if not motivo.strip():
            st.error("Scrivi il motivo della penalità.")
            return
        aggiungi_penalita(gara_id, pilota, "giri" if tipo_label == "Giri" else "secondi", valore, motivo)
        st.rerun()


def imposta_pit_sec(gara_id: str, secondi: int) -> None:
    def fn(g):
        piano = g.get("piano") or {}
        piano["pit_sec"] = float(secondi)
        g["piano"] = piano
    _aggiorna(gara_id, fn)
