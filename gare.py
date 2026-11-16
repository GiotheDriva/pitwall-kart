"""Calendario gare, scheda gara e fatture del Pit Wall.

Le gare sono salvate in dati_gare.json accanto a main.py, così restano
disponibili dopo ogni riavvio. Aprendo una gara si entra nel muretto
con il link Live Timing collegato alla gara.
"""
from __future__ import annotations

import json
import os
import re
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


SPONSOR_ID = "__sponsor__"  # record speciale: l'elenco sponsor, salvato accanto alle gare


def _tutti_i_record() -> list[dict]:
    if _client() is None:
        return _leggi_locale()
    gare_remote = _leggi_remoto()
    locale = DATA_FILE.exists() and _leggi_locale()
    if not gare_remote and locale:
        # Primo avvio con Supabase: importa le gare locali una sola volta.
        save_gare(locale)
        DATA_FILE.rename(DATA_FILE.with_suffix(".importato.json"))
        return locale
    return gare_remote


def load_gare() -> list[dict]:
    return [g for g in _tutti_i_record() if not str(g.get("id", "")).startswith("__")]


def save_gare(gare: list[dict]) -> None:
    client = _client()
    if client is None:
        speciali = [r for r in _leggi_locale() if str(r.get("id", "")).startswith("__")]
        tmp = DATA_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(gare + speciali, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, DATA_FILE)
        return
    if gare:
        client.table("gare").upsert([{"id": g["id"], "dati": g} for g in gare]).execute()
    ids = {g["id"] for g in gare}
    esistenti = client.table("gare").select("id").execute().data or []
    for row in esistenti:
        if row["id"] not in ids and not str(row["id"]).startswith("__"):
            client.table("gare").delete().eq("id", row["id"]).execute()
    _leggi_remoto.clear()


def load_sponsor() -> list[dict]:
    for r in _tutti_i_record():
        if r.get("id") == SPONSOR_ID:
            return list(r.get("sponsor") or [])
    return []


def save_sponsor(sponsor: list[dict]) -> None:
    record = {"id": SPONSOR_ID, "sponsor": sponsor}
    client = _client()
    if client is None:
        altri = [r for r in _leggi_locale() if r.get("id") != SPONSOR_ID]
        tmp = DATA_FILE.with_suffix(".tmp")
        gare_locali = [r for r in altri if not str(r.get("id", "")).startswith("__")]
        tmp.write_text(json.dumps(gare_locali + [record], ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, DATA_FILE)
        return
    client.table("gare").upsert([{"id": SPONSOR_ID, "dati": record}]).execute()
    _leggi_remoto.clear()


def residuo_sponsor(sponsor: dict, gare_lista: list[dict], escludi_gara: str | None = None) -> float:
    """Bilancio dello sponsor: quota versata meno quanto ha già coperto in altre gare."""
    usato = sum(
        float(g.get("quota_sponsor") or 0)
        for g in gare_lista
        if g.get("sponsor_id") == sponsor["id"] and g["id"] != escludi_gara
    )
    return float(sponsor.get("quota_versata") or 0) - usato


def trova(gare: list[dict], gara_id: str) -> dict | None:
    return next((g for g in gare if g.get("id") == gara_id), None)


def euro(value: float | int | None) -> str:
    amount = float(value or 0)
    return f"€ {amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def formato_tipo(gara: dict) -> str:
    if gara.get("test"):
        return f"TEST · Endurance · {gara.get('ore') or '—'} h"
    if gara.get("tipo") == "Endurance":
        return f"Endurance · {gara.get('ore') or '—'} h"
    if gara.get("tipo") == "Noleggio":
        return f"Noleggio · {gara.get('minuti') or '—'} min"
    return f"Sprint · {gara.get('giri') or '—'} giri"


# Stato del muretto che appartiene a una singola gara: va azzerato quando si cambia gara,
# altrimenti nomi, classifica e cache di una gara (o della modalità TEST) finiscono in un'altra.
_STATO_GARA = [
    "pilots", "selected_pilot", "timing_provider", "apex_pilot_identities", "apex_snapshot",
    "events", "apex_history", "simulation_history", "_was_live", "_campo_test",
    "_test_eventi_visti", "_apex_comments", "_apex_mine", "_seen_apex_comments",
    "_field_prev", "_field_session_best", "_last_apex_row", "_last_apex_remaining",
    "_session_pilot", "_position", "_position_delta", "_best_lap", "_gap", "_last_lap",
    "_pits_stint_visti", "_ultimo_pit_apex", "_stint_started_at", "_stint_offset_s",
    "_stint_end_logged", "_five_minute_logged", "_next_random_event", "_last_sim_update",
]


def reset_stato_gara() -> None:
    for chiave in _STATO_GARA:
        st.session_state.pop(chiave, None)
    st.session_state["live_timing"] = False


def apri_gara(gara_id: str) -> None:
    st.session_state[VISTA] = gara_id


def torna_home() -> None:
    st.session_state[VISTA] = "home"
    st.session_state[CARICATA] = None


# --------------------------------------------------------------------------- #
# Nuova gara
# --------------------------------------------------------------------------- #
# Valori di partenza di una gara TEST: nessun dato reale, tutto si imposta nelle Impostazioni.
TEST_PREDEFINITI = {
    "stint_min_min": 10.0,
    "stint_max_min": 40.0,
    "pit_max_s": 180.0,
    "giro_veloce_s": 55.5,   # giro migliore a cui puntano i kart
    "giro_lento_s": 58.5,    # giro peggiore: i kart più scarsi
}


@st.dialog("Nuova gara")
def dialog_nuova_gara() -> None:
    nome = st.text_input("Nome della gara *", key="ng_nome", placeholder="Es. 4 ore di Pomposa")
    c1, c2 = st.columns(2)
    tipo = c1.radio("Tipo di gara", ["Endurance", "Sprint", "Noleggio", "TEST"], horizontal=True, key="ng_tipo")
    es_test = tipo == "TEST"
    if tipo == "Noleggio":
        team = ""  # nel noleggio si partecipa come piloti singoli
    elif es_test:
        team = "TEST"
        c2.caption("Gara di prova: nessun dato reale. Tutto si imposta nelle Impostazioni.")
    else:
        team = c2.text_input("Nome del team *", key="ng_team")

    c3, c4 = st.columns(2)
    minuti = None
    if tipo == "Noleggio":
        minuti = c3.number_input("Durata (minuti)", min_value=5, max_value=600, value=20, step=5, key="ng_minuti")
        ore = None
        giri = None
    elif es_test:
        ore = 1.0
        giri = None
    elif tipo == "Endurance":
        ore = c3.number_input("Durata (ore)", min_value=0.5, max_value=24.0, value=4.0, step=0.5, key="ng_ore")
        giri = None
    else:
        giri = c3.number_input("Numero di giri", min_value=1, max_value=500, value=20, step=1, key="ng_giri")
        ore = None
    quota = c4.number_input("Quota di partecipazione (€)", min_value=0.0, value=0.0, step=5.0, key="ng_quota")

    data_gara = st.date_input("Data della gara", value=date.today(), key="ng_data")
    piloti_txt = "" if es_test else st.text_area("Piloti (uno per riga)", key="ng_piloti", height=110)
    pit_minimo = 0
    if tipo not in ("Noleggio", "TEST"):
        pit_minimo = st.number_input("Pit minimo obbligatorio", min_value=0, max_value=50, value=0, step=1,
                                     key="ng_pit", help="Numero minimo di soste previste dal regolamento.")
    link = st.text_input(
        "Link Live Timing Apex (facoltativo, puoi aggiungerlo dopo)",
        key="ng_link",
        placeholder="https://www.apex-timing.com/live-timing/…",
    )

    if st.button("Crea gara", type="primary", key="ng_crea"):
        if es_test and not nome.strip():
            nome = "TEST"
        if not nome.strip() or (tipo not in ("Noleggio", "TEST") and not team.strip()):
            st.error("Nome della gara e nome del team sono obbligatori.")
            return
        gara = {
            "id": uuid.uuid4().hex[:12],
            "nome": nome.strip(),
            "tipo": "Endurance" if es_test else tipo,
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
        if es_test:
            gara["piloti"] = ["Pilota 1", "Pilota 2"]
            gara["test"] = dict(TEST_PREDEFINITI)
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


# --------------------------------------------------------------------------- #
# Gara conclusa e report (sola lettura)
# --------------------------------------------------------------------------- #
_PAROLE_PENALITA = ("penal", "sanzion", "ammonizion", "avviso", "warning", "attenzione", "stop and go", "drive through")


def _copia_json(dati):
    return json.loads(json.dumps(dati, default=str))


def concludi_gara(gara_id: str) -> None:
    """Salva nella gara tutti i dati della sessione (giri, eventi, commenti, storico) e torna al calendario."""
    mio = st.session_state.get("_apex_mine") or {}
    report = {
        "chiusa_il": datetime.now().isoformat(timespec="seconds"),
        "mio_kart": str(mio.get("kart") or ""),
        "mio_team": str(mio.get("team") or ""),
        "kart": _copia_json(st.session_state.get("fleet_laps", {})),
        "eventi": _copia_json(list(st.session_state.get("events", []))[:300]),
        "commenti": _copia_json(list(st.session_state.get("_apex_comments", []))[-300:]),
        "storico": _copia_json(list(st.session_state.get("apex_history", []))[-300:]),
    }

    def fn(g):
        g["stato"] = "conclusa"
        g["report"] = report

    _aggiorna(gara_id, fn)
    torna_home()


def render_report(gara: dict) -> None:
    gid = gara["id"]
    titolo, indietro = st.columns([5, 1.4])
    titolo.title(f"📊 Report · {gara['nome']}")
    titolo.caption(" · ".join(x for x in [gara.get("team", ""), gara.get("data", ""), formato_tipo(gara)] if x))
    indietro.write("")
    if indietro.button("← Calendario", key=f"rep_indietro_{gid}"):
        torna_home()
        st.rerun()

    rep = gara.get("report")
    if not rep:
        st.info("Questa gara non è ancora conclusa. Premi «Gara conclusa» nelle Impostazioni del muretto per salvare il report.")
        return
    st.caption(f"Conclusa il {rep.get('chiusa_il', '')}")

    kart: dict = rep.get("kart") or {}
    mio = rep.get("mio_kart", "")
    t_ris, t_giri, t_ana, t_pit, t_pen, t_reg = st.tabs(
        ["Riepilogo", "Giri", "Analisi", "Pit stop", "Penalità", "Registro e note"]
    )

    with t_ris:
        righe = []
        for k, v in kart.items():
            tempi = [g["tempo_s"] for g in v.get("giri", []) if g.get("tempo_s")]
            penalita_apex = sum(
                1 for c in rep.get("commenti", [])
                if any(p in c.get("text", "").lower() for p in _PAROLE_PENALITA)
                and k and re.search(rf"(?<!\d){re.escape(str(k))}(?!\d)", c.get("text", ""))
            )
            righe.append(
                {
                    "Kart": k,
                    "Team / pilota": v.get("team") or "—",
                    "Giri": v.get("prev_giri") or 0,
                    "Giro più veloce (s)": round(min(tempi), 3) if tempi else None,
                    "Giro medio (s)": round(sum(tempi) / len(tempi), 3) if tempi else None,
                    "Soste": v.get("prev_soste") or 0,
                    "Penalità Apex": penalita_apex,
                }
            )
        if righe:
            st.dataframe(pd.DataFrame(righe), hide_index=True, use_container_width=True)
        else:
            st.caption("Nessun dato di gara salvato.")

    with t_giri:
        if not kart:
            st.caption("Nessun giro salvato.")
        else:
            opzioni = list(kart)
            default = [mio] if mio in kart else opzioni[:2]
            scelti = st.multiselect("Kart", opzioni, default=default, key=f"rep_giri_{gid}")
            serie: dict[int, dict[str, float]] = {}
            for k in scelti:
                for g in kart[k].get("giri", []):
                    if g.get("tempo_s"):
                        serie.setdefault(g["giro"], {})[f"Kart {k}"] = g["tempo_s"]
            if serie:
                tabella = pd.DataFrame.from_dict(serie, orient="index").sort_index()
                tabella.index.name = "Giro"
                st.line_chart(tabella, y_label="Secondi", height=320)
                st.dataframe(tabella.reset_index(), hide_index=True, use_container_width=True)
            else:
                st.caption("Nessun giro da mostrare.")

    with t_ana:
        v = kart.get(mio)
        if not v or not v.get("giri"):
            st.caption("Nessun giro del nostro kart salvato.")
        else:
            giri = pd.DataFrame(v["giri"])
            st.markdown(f"**Kart {mio} · giro per giro**")
            st.dataframe(giri, hide_index=True, use_container_width=True)
            serie = giri.dropna(subset=["distacco_s"])
            if len(serie):
                st.markdown("**Distacco dal leader**")
                st.line_chart(serie.set_index("giro")[["distacco_s"]], y_label="Secondi", height=260)

    with t_pit:
        soste = [{"Kart": k, **s} for k, v in kart.items() for s in v.get("soste", [])]
        if soste:
            st.dataframe(pd.DataFrame(soste), hide_index=True, use_container_width=True)
        else:
            st.caption("Nessuna sosta registrata da Apex.")
        manuali = gara.get("pit_eseguiti", [])
        if manuali:
            st.markdown("**Pit ora registrati dal muretto (tempo di gara)**")
            st.dataframe(
                pd.DataFrame({"Pit": range(1, len(manuali) + 1),
                              "Tempo di gara": [f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}" for t in manuali]}),
                hide_index=True, use_container_width=True,
            )

    with t_pen:
        penalita = gara.get("penalita", [])
        st.markdown("**Penalità assegnate dal team**")
        if penalita:
            st.dataframe(
                pd.DataFrame([
                    {"Pilota": p.get("pilota", ""), "Penalità": pro_tools.descrivi_penalita(p),
                     "Motivo": p.get("motivo", ""), "Ora": p.get("ora", "")}
                    for p in penalita
                ]),
                hide_index=True, use_container_width=True,
            )
        else:
            st.caption("Nessuna penalità assegnata dal team.")
        st.markdown("**Penalità e avvisi da Apex**")
        apex = [c for c in rep.get("commenti", []) if any(p in c.get("text", "").lower() for p in _PAROLE_PENALITA)]
        if apex:
            st.dataframe(pd.DataFrame(apex), hide_index=True, use_container_width=True)
        else:
            st.caption("Nessuna penalità o avviso da Apex.")

    with t_reg:
        eventi = rep.get("eventi", [])
        note_rapide = [e for e in eventi if str(e.get("message", "")).startswith("📻")]
        st.markdown("**Note rapide del muretto**")
        if note_rapide:
            st.dataframe(pd.DataFrame(note_rapide), hide_index=True, use_container_width=True)
        else:
            st.caption("Nessuna nota rapida.")
        st.markdown("**Registro attività**")
        if eventi:
            st.dataframe(pd.DataFrame(eventi), hide_index=True, use_container_width=True)
        else:
            st.caption("Registro vuoto.")
        st.info("Note e schizzi del Taccuino tecnico e delle lavagne non sono inclusi: restano salvati nel browser.")


def render_home() -> None:
    gare = sorted(load_gare(), key=lambda g: g.get("data", ""), reverse=True)

    titolo, bottone = st.columns([5, 1.4])
    titolo.title("🏁 Calendario gare")
    titolo.caption("Gare, team e pagamenti del muretto")
    bottone.write("")
    if bottone.button("＋ Nuova gara", type="primary", key="btn_nuova_gara"):
        dialog_nuova_gara()

    tab_gare, tab_fatture, tab_sponsor = st.tabs(["🗓️ Gare", "🧾 Fatture", "🤝 Sponsor"])

    with tab_gare:
        if not gare:
            st.info("Nessuna gara ancora. Premi **＋ Nuova gara** in alto a destra.")
        for gara in gare:
            with st.container(border=True):
                c1, c2, c3, c4, c5, c6, c7 = st.columns([3, 2.2, 2, 2, 1, 0.5, 0.5])
                sottotitolo = " · ".join(x for x in [gara.get("team", ""), gara.get("data", "")] if x)
                c1.markdown(f"**{gara['nome']}**  \n{sottotitolo}")
                c2.markdown(f"{formato_tipo(gara)}  \n{len(gara.get('piloti', []))} piloti")
                c3.markdown(
                    f"{euro(gara.get('quota'))}  \n"
                    + ("✅ Pagata" if gara.get("pagata") else "⏳ Da pagare")
                )
                if gara.get("stato") == "conclusa":
                    c4.markdown("🏁 **Gara conclusa**")
                    c5.write("")  # niente "Apri": la gara è chiusa, resta solo il report (ℹ️)
                else:
                    c4.markdown("🟢 Live Timing collegato" if gara.get("link") else "⚪ Nessun link")
                    if c5.button("Apri", key=f"apri_{gara['id']}"):
                        apri_gara(gara["id"])
                        st.rerun()
                if c6.button("ℹ️", key=f"info_{gara['id']}", help="Report della gara"):
                    st.session_state[VISTA] = f"report:{gara['id']}"
                    st.rerun()
                if c7.button("🗑️", key=f"del_{gara['id']}", help="Elimina questa gara"):
                    dialog_elimina(gara["id"], gara["nome"])

    with tab_fatture:
        render_fatture(gare)

    with tab_sponsor:
        render_sponsor(gare)


def _noi(gara: dict) -> float:
    """Quota a carico del team: costo della gara meno la parte coperta dallo sponsor."""
    return max(0.0, float(gara.get("quota") or 0) - float(gara.get("quota_sponsor") or 0))


def render_metriche_sponsor(gare_lista: list[dict]) -> None:
    """Totali degli sponsor: mostrati solo dentro le tab Fatture e Sponsor."""
    sponsor_lista = load_sponsor()
    versato = sum(float(s.get("quota_versata") or 0) for s in sponsor_lista)
    bilancio = sum(residuo_sponsor(s, gare_lista) for s in sponsor_lista)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Sponsor", len(sponsor_lista))
    m2.metric("Totale versato dagli sponsor", euro(versato))
    m3.metric("Bilancio disponibile", euro(bilancio))
    m4.metric("Gare coperte dagli sponsor", sum(1 for g in gare_lista if float(g.get("quota_sponsor") or 0) > 0))


def render_fatture(gare_lista: list[dict]) -> None:
    render_metriche_sponsor(gare_lista)
    if not gare_lista:
        st.info("Le fatture compariranno quando aggiungi una gara.")
        return

    for msg in st.session_state.pop("_fatt_avvisi", []):
        st.warning(msg)

    sponsor_lista = load_sponsor()
    per_id = {s["id"]: s for s in sponsor_lista}
    id_per_nome = {s["nome"]: s["id"] for s in sponsor_lista}

    costi = sum(float(g.get("quota") or 0) for g in gare_lista)
    coperto = sum(float(g.get("quota_sponsor") or 0) for g in gare_lista)
    noi = sum(_noi(g) for g in gare_lista)
    pagato = sum(_noi(g) for g in gare_lista if g.get("pagata"))
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Gare", len(gare_lista))
    k2.metric("Costi totali", euro(costi))
    k3.metric("Coperto dagli sponsor", euro(coperto))
    k4.metric("A carico nostro", euro(noi))
    k5.metric("Da pagare", euro(noi - pagato))

    if sponsor_lista:
        st.markdown("**Bilancio sponsor**")
        st.dataframe(
            pd.DataFrame([
                {
                    "Sponsor": s["nome"],
                    "Versato (€)": float(s.get("quota_versata") or 0),
                    "Già coperto (€)": float(s.get("quota_versata") or 0) - residuo_sponsor(s, gare_lista),
                    "Disponibile (€)": residuo_sponsor(s, gare_lista),
                }
                for s in sponsor_lista
            ]).round(2),
            hide_index=True,
        )

    st.caption(
        "Per ogni gara scegli lo sponsor e quanto copre. Il resto resta a carico nostro. "
        "Lo sponsor non può coprire più del bilancio disponibile. Spunta **Pagata** quando hai pagato la tua parte."
    )
    df = pd.DataFrame(
        [
            {
                "ID": g["id"],
                "Gara": g["nome"],
                "Team": g.get("team", ""),
                "Data": g.get("data", ""),
                "Costo (€)": float(g.get("quota") or 0),
                "Sponsor": per_id[g["sponsor_id"]]["nome"] if g.get("sponsor_id") in per_id else "",
                "Quota sponsor (€)": float(g.get("quota_sponsor") or 0),
                "A noi (€)": _noi(g),
                "Pagata": bool(g.get("pagata")),
                "Data pagamento": g.get("data_pagamento", ""),
            }
            for g in gare_lista
        ]
    )
    edited = st.data_editor(
        df,
        hide_index=True,
        key="editor_fatture",
        disabled=["ID", "Gara", "Team", "Data", "Costo (€)", "A noi (€)"],
        column_config={
            "ID": None,
            "Costo (€)": st.column_config.NumberColumn(format="€ %.2f"),
            "Sponsor": st.column_config.SelectboxColumn("Sponsor", options=[""] + list(id_per_nome.keys())),
            "Quota sponsor (€)": st.column_config.NumberColumn(format="€ %.2f", min_value=0.0),
            "A noi (€)": st.column_config.NumberColumn(format="€ %.2f"),
            "Pagata": st.column_config.CheckboxColumn("Pagata"),
            "Data pagamento": st.column_config.TextColumn(help="Esempio: 2026-10-28"),
        },
    )

    archivio = load_gare()
    changed = False
    avvisi = []
    for _, row in edited.iterrows():
        gara = trova(archivio, row["ID"])
        if not gara:
            continue
        nome_sp = row["Sponsor"] if isinstance(row["Sponsor"], str) else ""
        sp_id = id_per_nome.get(nome_sp, "")
        costo = float(gara.get("quota") or 0)
        quota_sp = float(row["Quota sponsor (€)"]) if sp_id and row["Quota sponsor (€)"] == row["Quota sponsor (€)"] else 0.0
        quota_sp = min(max(0.0, quota_sp), costo)
        if sp_id:
            disponibile = residuo_sponsor(per_id[sp_id], archivio, escludi_gara=gara["id"])
            if quota_sp > disponibile:
                avvisi.append(
                    f"{gara['nome']}: {nome_sp} ha solo {euro(max(0, disponibile))} disponibili, "
                    f"quota ridotta a quella cifra."
                )
                quota_sp = max(0.0, disponibile)
        else:
            quota_sp = 0.0
        pagata = bool(row["Pagata"])
        data_pag = str(row["Data pagamento"] or "").strip()
        if (
            (gara.get("sponsor_id") or "") != sp_id
            or float(gara.get("quota_sponsor") or 0) != quota_sp
            or bool(gara.get("pagata")) != pagata
            or data_pag != gara.get("data_pagamento", "")
        ):
            gara["sponsor_id"] = sp_id
            gara["quota_sponsor"] = quota_sp
            gara["pagata"] = pagata
            gara["data_pagamento"] = data_pag
            changed = True
    if changed:
        save_gare(archivio)
        st.session_state["_fatt_avvisi"] = avvisi
        st.rerun()

    riepilogo: dict[str, dict] = {}
    for _, row in df.iterrows():
        r = riepilogo.setdefault(row["Team"], {"Team": row["Team"], "Gare": 0,
                                               "A noi (€)": 0.0, "Pagato (€)": 0.0, "Da pagare (€)": 0.0})
        r["Gare"] += 1
        r["A noi (€)"] += row["A noi (€)"]
        r["Pagato (€)" if row["Pagata"] else "Da pagare (€)"] += row["A noi (€)"]
    per_team = pd.DataFrame(list(riepilogo.values()))
    st.markdown("**Riepilogo per team**")
    st.dataframe(per_team.round(2), hide_index=True)
    st.download_button(
        "Scarica fatture (CSV)",
        data=df.drop(columns=["ID"]).to_csv(index=False).encode("utf-8"),
        file_name=f"fatture-gare-{date.today().isoformat()}.csv",
        mime="text/csv",
    )


def render_sponsor(gare_lista: list[dict]) -> None:
    """Sponsor: nome, quota versata, durata del contratto, gare previste e altro."""
    render_metriche_sponsor(gare_lista)
    sponsor_lista = load_sponsor()

    with st.expander("➕ Aggiungi sponsor", expanded=not sponsor_lista):
        with st.form("nuovo_sponsor", clear_on_submit=True):
            c1, c2 = st.columns([2, 1])
            nome = c1.text_input("Nome sponsor *")
            quota = c2.number_input("Quota versata (€)", min_value=0.0, value=0.0, step=50.0)
            c3, c4 = st.columns([2, 1])
            durata = c3.text_input("Durata contratto", placeholder="Es. Stagione 2026, 6 mesi")
            gare_prev = c4.number_input("Gare previste", min_value=0, value=0, step=1)
            note = st.text_area("Altro (contatti, accordi, note)", height=80)
            if st.form_submit_button("Aggiungi sponsor", type="primary"):
                if not nome.strip():
                    st.warning("Scrivi il nome dello sponsor.")
                else:
                    sponsor_lista.append({
                        "id": uuid.uuid4().hex[:8], "nome": nome.strip(), "quota_versata": float(quota),
                        "durata": durata.strip(), "gare_previste": int(gare_prev), "note": note.strip(),
                    })
                    save_sponsor(sponsor_lista)
                    st.rerun()

    if not sponsor_lista:
        st.info("Nessuno sponsor ancora. Aggiungilo con il riquadro qui sopra.")
        return

    usate = {s["id"]: sum(1 for g in gare_lista if g.get("sponsor_id") == s["id"] and float(g.get("quota_sponsor") or 0) > 0)
             for s in sponsor_lista}
    df = pd.DataFrame([
        {
            "ID": s["id"],
            "Nome": s["nome"],
            "Quota versata (€)": float(s.get("quota_versata") or 0),
            "Durata contratto": s.get("durata", ""),
            "Gare previste": int(s.get("gare_previste") or 0),
            "Gare coperte": usate[s["id"]],
            "Disponibile (€)": residuo_sponsor(s, gare_lista),
            "Altro": s.get("note", ""),
        }
        for s in sponsor_lista
    ])
    st.caption("Modifica direttamente la tabella. Per eliminare uno sponsor usa il cestino a sinistra della riga.")
    edited = st.data_editor(
        df,
        hide_index=True,
        num_rows="dynamic",
        key="editor_sponsor",
        disabled=["ID", "Gare coperte", "Disponibile (€)"],
        column_config={
            "ID": None,
            "Quota versata (€)": st.column_config.NumberColumn(format="€ %.2f", min_value=0.0),
            "Gare previste": st.column_config.NumberColumn(min_value=0, step=1),
            "Disponibile (€)": st.column_config.NumberColumn(format="€ %.2f"),
            "Altro": st.column_config.TextColumn(width="large"),
        },
    )

    nuova = []
    for _, row in edited.iterrows():
        nome = str(row["Nome"] or "").strip() if isinstance(row["Nome"], str) else ""
        if not nome:
            continue
        sid = row["ID"] if isinstance(row["ID"], str) and row["ID"] else uuid.uuid4().hex[:8]
        nuova.append({
            "id": sid, "nome": nome,
            "quota_versata": float(row["Quota versata (€)"] or 0),
            "durata": str(row["Durata contratto"] or "").strip(),
            "gare_previste": int(row["Gare previste"] or 0),
            "note": str(row["Altro"] or "").strip() if isinstance(row["Altro"], str) else "",
        })

    campi = lambda s: (s["id"], s["nome"], s["quota_versata"], s["durata"], s["gare_previste"], s["note"])
    if [campi(s) for s in nuova] != [campi(s) for s in sponsor_lista]:
        sparite = {s["id"] for s in sponsor_lista} - {s["id"] for s in nuova}
        if sparite:
            archivio = load_gare()
            for g in archivio:
                if g.get("sponsor_id") in sparite:
                    g["sponsor_id"] = ""
                    g["quota_sponsor"] = 0.0
            save_gare(archivio)
        save_sponsor(nuova)
        st.rerun()


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
        st.session_state["_auto_cerca"] = not gara.get("test")
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
    if str(gara_id).startswith("report:"):
        gara_rep = trova(gare, gara_id.split(":", 1)[1])
        if gara_rep is None:
            torna_home()
            render_home()
            st.stop()
        render_report(gara_rep)
        st.stop()
    gara = trova(gare, gara_id) if gara_id != "home" else None

    if gara is None:
        if gara_id != "home":
            torna_home()
        render_home()
        st.stop()

    if st.session_state.get(CARICATA) != gara["id"]:
        reset_stato_gara()
        st.session_state["timing_url"] = gara.get("link") or ""
        st.session_state["_auto_cerca"] = bool(gara.get("link")) and not gara.get("test")
        st.session_state[CARICATA] = gara["id"]
    render_scheda(gara)


# --------------------------------------------------------------------------- #
# Piano vivo: salvataggio da Pianificatore e pit registrati dal muretto
# --------------------------------------------------------------------------- #
def salva_parametri_test(
    gara_id: str,
    piloti: list[str],
    ore: float,
    pit_minimo: int,
    stint_min_min: float,
    stint_max_min: float,
    pit_max_s: float,
    giro_veloce_s: float = 55.5,
    giro_lento_s: float = 58.5,
) -> None:
    def fn(g):
        g["piloti"] = list(piloti)
        g["ore"] = float(ore)
        g["pit_minimo"] = int(pit_minimo)
        g["test"] = {
            "stint_min_min": float(stint_min_min),
            "stint_max_min": float(stint_max_min),
            "pit_max_s": float(pit_max_s),
            "giro_veloce_s": float(giro_veloce_s),
            "giro_lento_s": float(giro_lento_s),
        }
    _aggiorna(gara_id, fn)


def salva_piloti(gara_id: str, piloti: list[str]) -> None:
    """Piloti/team che correranno nella gara TEST: diventano i kart della classifica."""
    def fn(g):
        g["piloti"] = list(piloti)
    _aggiorna(gara_id, fn)


def nuova_simulazione_test(gara_id: str) -> None:
    """Nuova gara simulata: nuovo seme (gare diverse) e via gli eventi simulati della precedente."""
    def fn(g):
        g["test_seme"] = uuid.uuid4().hex[:10]
        g["test_eventi"] = []
    _aggiorna(gara_id, fn)


def salva_piano(gara_id: str, piano: dict) -> None:
    gare_all = load_gare()
    gara = trova(gare_all, gara_id)
    if gara:
        # Orario del pin: serve a verificare che il muretto stia usando l'ultimo piano salvato.
        gara["piano"] = {**piano, "salvato_il": datetime.now().strftime("%H:%M:%S")}
        save_gare(gare_all)


def registra_pit(gara_id: str, elapsed_s: float) -> bool:
    """Registra una sosta. Ritorna False se è già registrata (stessa sosta da Apex e dal pulsante)."""
    gare_all = load_gare()
    gara = trova(gare_all, gara_id)
    if not gara:
        return False
    pits = gara.setdefault("pit_eseguiti", [])
    tempo = round(float(elapsed_s), 1)
    if pits and abs(tempo - pits[-1]) < 60:
        return False
    pits.append(tempo)
    # Le penalità ancora da scontare si pagano a questa sosta.
    for p in gara.get("penalita", []):
        if p.get("scontata_pit") is None:
            p["scontata_pit"] = len(pits)
    save_gare(gare_all)
    return True


def annulla_pit(gara_id: str) -> None:
    gare_all = load_gare()
    gara = trova(gare_all, gara_id)
    if gara and gara.get("pit_eseguiti"):
        n = len(gara["pit_eseguiti"])
        gara["pit_eseguiti"].pop()
        # Le penalità scontate in quella sosta tornano da scontare.
        for p in gara.get("penalita", []):
            if p.get("scontata_pit") == n:
                p["scontata_pit"] = None
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


def aggiungi_evento_test(gara_id: str, evento: dict) -> None:
    """Evento simulato della gara TEST: ritardo (sorpasso o penalità), giro o sosta di un bot."""
    def fn(g):
        g.setdefault("test_eventi", []).append({**evento, "id": uuid.uuid4().hex[:8]})
    _aggiorna(gara_id, fn)


def azzera_eventi_test(gara_id: str) -> None:
    def fn(g):
        g["test_eventi"] = []
    _aggiorna(gara_id, fn)


def elimina_penalita(gara_id: str, pen_id: str) -> None:
    def fn(g):
        g["penalita"] = [p for p in g.get("penalita", []) if p.get("id") != pen_id]
    _aggiorna(gara_id, fn)


def aggiungi_rettifica(gara_id: str, kart: str, tipo: str, valore: float, motivo: str, pit_base: int | None = None) -> None:
    """Penalità (+secondi) o recupero (-secondi) assegnato a un kart nella classifica."""
    def fn(g):
        g.setdefault("rettifiche", []).append(
            {
                "id": uuid.uuid4().hex[:8],
                "kart": str(kart),
                "tipo": tipo,  # "penalita" oppure "recupero"
                "valore": float(valore),
                "motivo": motivo.strip(),
                "ora": datetime.now().isoformat(timespec="seconds"),
                "pit_base": pit_base,  # pit del kart al momento della penalità: si sconta al pit successivo
            }
        )
    _aggiorna(gara_id, fn)


def allinea_pit_base(gara_id: str, pits: dict[str, int]) -> None:
    """Penalità e recuperi salvati prima del conteggio pit: si agganciano al pit attuale del kart."""
    def fn(g):
        for r in g.get("rettifiche", []):
            if "pit_base" not in r:
                r["pit_base"] = pits.get(str(r["kart"]))
    _aggiorna(gara_id, fn)


def aggiungi_rettifiche_auto(gara_id: str, nuove: list[dict]) -> int:
    """Salva le penalità lette dal live Apex: ogni penalità una sola volta (id stabile). Restituisce quante ne ha aggiunte."""
    gare_all = load_gare()
    gara = trova(gare_all, gara_id)
    if not gara:
        return 0
    lista = gara.setdefault("rettifiche", [])
    esistenti = {r.get("id") for r in lista}
    da_salvare = []
    for r in nuove:
        if r["id"] not in esistenti:
            esistenti.add(r["id"])
            da_salvare.append(r)
    if da_salvare:
        lista.extend(da_salvare)
        save_gare(gare_all)
    return len(da_salvare)


def elimina_rettifica(gara_id: str, rett_id: str) -> None:
    def fn(g):
        g["rettifiche"] = [r for r in g.get("rettifiche", []) if r.get("id") != rett_id]
    _aggiorna(gara_id, fn)


def segna_scontata(gara_id: str, pen_id: str, scontata: bool) -> None:
    """Segna a mano una penalità come scontata (o la riapre). Senza pit registrati vale 0: nessun tempo extra."""
    def fn(g):
        for p in g.get("penalita", []):
            if p.get("id") == pen_id:
                p["scontata_pit"] = len(g.get("pit_eseguiti") or []) if scontata else None
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
        piano = g.get("piano")
        # Senza un piano completo non creo un piano parziale: il muretto lo leggerebbe come valido.
        if piano and "stint_min" in piano:
            piano["pit_sec"] = float(secondi)
            g["piano"] = piano
    _aggiorna(gara_id, fn)
