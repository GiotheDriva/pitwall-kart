"""Home del Pit Wall: centro gare del team.

Usa le funzioni di gare.py (archivio, sponsor, fatture, dialog) e mostra:
- hero con la prossima gara e il conto alla rovescia;
- indicatori del team (gare, da pagare, bilancio sponsor, avvisi);
- gare divise per categoria (Endurance, Sprint, Noleggio, TEST) con card colorate;
- tab Fatture e Sponsor già esistenti.
"""
from __future__ import annotations

import calendar
import html
import uuid
from datetime import date, datetime

import streamlit as st

import gare

CATEGORIE = [
    ("Endurance", "🏁", "#2563eb", "Gare a squadre con cambi pilota e soste"),
    ("Sprint", "⚡", "#e11d48", "Gare individuali veloci"),
    ("Noleggio", "🎟️", "#0d9488", "Turni liberi al kartodromo"),
]
COLORE_TEST = "#a855f7"
COLORE_CONCLUSA = "#64748b"

CSS = """
<style>
.hm-hero {
  border-radius: 18px; padding: 22px 24px; margin: 4px 0 18px;
  background: radial-gradient(120% 140% at 0% 0%, #1e3a8a 0%, #0f172a 55%, #0b1020 100%);
  border: 1px solid rgba(96,165,250,.25); box-shadow: 0 16px 40px rgba(0,0,0,.35);
}
.hm-hero-top { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; flex-wrap: wrap; }
.hm-brand { font-size: 1.9rem; font-weight: 800; letter-spacing: -.02em; color: #f8fafc; }
.hm-brand-sub { color: #93c5fd; font-size: .92rem; margin-top: 2px; }
.hm-date { color: #cbd5e1; font-size: .85rem; background: rgba(15,23,42,.6); border: 1px solid rgba(148,163,184,.25);
           padding: 6px 12px; border-radius: 999px; }
.hm-kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-top: 18px; }
.hm-kpi { background: rgba(15,23,42,.55); border: 1px solid rgba(148,163,184,.2); border-radius: 14px; padding: 12px 14px; }
.hm-kpi .k { font-size: .72rem; text-transform: uppercase; letter-spacing: .08em; color: #94a3b8; font-weight: 700; }
.hm-kpi .v { font-size: 1.45rem; font-weight: 800; color: #f8fafc; margin-top: 4px; }
.hm-kpi .n { font-size: .78rem; color: #94a3b8; margin-top: 2px; }
.hm-next { border-radius: 18px; padding: 20px 22px; margin-bottom: 18px;
           background: linear-gradient(120deg, rgba(234,179,8,.16), rgba(15,23,42,.9) 60%);
           border: 1px solid rgba(250,204,21,.35); }
.hm-next-tag { color: #facc15; font-size: .75rem; text-transform: uppercase; letter-spacing: .1em; font-weight: 800; }
.hm-next-name { font-size: 1.5rem; font-weight: 800; color: #f8fafc; margin: 4px 0 2px; }
.hm-next-meta { color: #cbd5e1; font-size: .92rem; }
.hm-count { font-size: 2.1rem; font-weight: 800; color: #facc15; font-family: ui-monospace, Consolas, monospace; }
.hm-section { display: flex; align-items: baseline; justify-content: space-between; gap: 12px;
              margin: 22px 0 10px; padding-bottom: 8px; border-bottom: 1px solid rgba(148,163,184,.2); }
.hm-section h3 { margin: 0; font-size: 1.2rem; color: #f1f5f9; }
.hm-section .c { color: #94a3b8; font-size: .85rem; }
.hm-card { border-radius: 14px; padding: 14px 16px 10px; background: rgba(15,23,42,.6);
           border: 1px solid rgba(148,163,184,.18); border-left-width: 6px; }
.hm-card-meta { font-size: .76rem; color: #94a3b8; text-transform: uppercase; letter-spacing: .06em; font-weight: 700; }
.hm-card-name { font-size: 1.12rem; font-weight: 800; color: #f8fafc; margin: 4px 0 2px; }
.hm-card-sub { font-size: .86rem; color: #cbd5e1; }
.hm-badges { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.hm-badge { font-size: .74rem; font-weight: 700; padding: 3px 9px; border-radius: 999px;
            background: rgba(148,163,184,.14); color: #e2e8f0; border: 1px solid rgba(148,163,184,.25); }
.hm-ok { background: rgba(34,197,94,.15); color: #86efac; border-color: rgba(34,197,94,.35); }
.hm-warn { background: rgba(245,158,11,.15); color: #fcd34d; border-color: rgba(245,158,11,.35); }
.hm-bad { background: rgba(239,68,68,.15); color: #fca5a5; border-color: rgba(239,68,68,.35); }
.hm-spacer { height: 14px; }
.hm-empty { color: #94a3b8; font-size: .9rem; padding: 10px 2px; }
@media (max-width: 640px) {
  .hm-hero { padding: 16px; }
  .hm-brand { font-size: 1.5rem; }
  .hm-count { font-size: 1.6rem; }
}
</style>
"""


@st.dialog("Nuova gara")
def dialog_nuova_gara() -> None:
    """Creazione gara (come in gare.py)."""
    nome = st.text_input("Nome della gara *", key="ng_nome", placeholder="Es. 4 ore di Pomposa")
    c1, c2 = st.columns(2)
    tipo = c1.radio("Tipo di gara", ["Endurance", "Sprint", "Noleggio", "TEST"], horizontal=True, key="ng_tipo")
    es_test = tipo == "TEST"
    if tipo == "Noleggio":
        team = ""
    elif es_test:
        team = "TEST"
        c2.caption("Gara di prova: nessun dato reale. Tutto si imposta nelle Impostazioni.")
    else:
        team = c2.text_input("Nome del team *", key="ng_team")

    c3, c4 = st.columns(2)
    minuti = None
    ore = None
    giri = None
    if tipo == "Noleggio":
        minuti = c3.number_input("Durata (minuti)", min_value=5, max_value=600, value=20, step=5, key="ng_minuti")
    elif es_test:
        ore = 1.0
    elif tipo == "Endurance":
        ore = c3.number_input("Durata (ore)", min_value=0.5, max_value=24.0, value=4.0, step=0.5, key="ng_ore")
    else:
        giri = c3.number_input("Numero di giri", min_value=1, max_value=500, value=20, step=1, key="ng_giri")
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

    if st.button("Crea gara", type="primary", key="ng_crea", use_container_width=True):
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
            gara["test"] = {
                "stint_min_min": 10.0, "stint_max_min": 40.0, "pit_max_s": 180.0,
                "giro_veloce_s": 55.5, "giro_lento_s": 58.5,
            }
        archivio = gare.load_gare()
        archivio.append(gara)
        gare.save_gare(archivio)
        gare.apri_gara(gara["id"])
        st.rerun()


def _data(g: dict) -> date | None:
    try:
        return date.fromisoformat(g.get("data") or "")
    except ValueError:
        return None


def _aggiungi_mesi(d: date, mesi: int) -> date:
    m = d.month - 1 + mesi
    anno, mese = d.year + m // 12, m % 12 + 1
    giorno = min(d.day, calendar.monthrange(anno, mese)[1])
    return date(anno, mese, giorno)


def _scomposizione(da: date, a: date) -> tuple[int, int, int]:
    """Mesi, settimane e giorni tra due date (mesi di calendario)."""
    mesi = (a.year - da.year) * 12 + (a.month - da.month)
    if mesi > 0 and a.day < da.day:
        mesi -= 1
    ancora = _aggiungi_mesi(da, max(0, mesi))
    resto = (a - ancora).days
    return max(0, mesi), resto // 7, resto % 7


def _parti(mesi: int, settimane: int, giorni: int) -> str:
    parti = []
    for n, singolare, plurale in ((mesi, "mese", "mesi"), (settimane, "settimana", "settimane"), (giorni, "giorno", "giorni")):
        if n:
            parti.append(f"{n} {singolare if n == 1 else plurale}")
    if not parti:
        return "oggi"
    if len(parti) == 1:
        return parti[0]
    return ", ".join(parti[:-1]) + " e " + parti[-1]


def _quando(d: date | None, oggi: date) -> str:
    if d is None:
        return "senza data"
    if d == oggi:
        return "oggi"
    if d > oggi:
        return "tra " + _parti(*_scomposizione(oggi, d))
    return _parti(*_scomposizione(d, oggi)) + " fa"


def _prossima(gare_list: list[dict], oggi: date) -> dict | None:
    future = [g for g in gare_list if g.get("stato") != "conclusa" and _data(g) and _data(g) >= oggi]
    return min(future, key=_data) if future else None


def _avvisi(gare_list: list[dict], oggi: date) -> list[tuple[str, str]]:
    """Cose da sistemare, come (livello, testo): per il team manager."""
    reali = [g for g in gare_list if not g.get("test")]
    out: list[tuple[str, str]] = []
    scadute = [g["nome"] for g in reali if g.get("stato") != "conclusa" and _data(g) and _data(g) < oggi]
    if scadute:
        out.append(("bad", f"Da chiudere: {', '.join(scadute)}"))
    senza_piloti = [g["nome"] for g in reali if g.get("tipo") != "Noleggio" and not g.get("piloti")]
    if senza_piloti:
        out.append(("warn", f"Senza piloti: {', '.join(senza_piloti)}"))
    senza_link = [g["nome"] for g in reali if g.get("stato") != "conclusa" and not g.get("link")]
    if senza_link:
        out.append(("warn", f"Senza link Live Timing: {', '.join(senza_link)}"))
    da_pagare = [g["nome"] for g in reali if not g.get("pagata") and gare._noi(g) > 0]
    if da_pagare:
        out.append(("warn", f"Da pagare: {', '.join(da_pagare)}"))
    return out


def _hero(gare_list: list[dict], oggi: date) -> None:
    reali = [g for g in gare_list if not g.get("test")]
    prossima = _prossima(reali, oggi)
    sponsor = gare.load_sponsor()
    disponibile = sum(gare.residuo_sponsor(s, gare_list) for s in sponsor)
    da_pagare = sum(gare._noi(g) for g in reali if not g.get("pagata"))
    conclusa = sum(1 for g in reali if g.get("stato") == "conclusa")

    st.markdown(
        '<div class="hm-hero"><div class="hm-hero-top">'
        '<div><div class="hm-brand">🏁 Centro gare del team</div>'
        '<div class="hm-brand-sub">Calendario, costi, sponsor e muretto in un posto solo</div></div>'
        f'<div class="hm-date">{html.escape(oggi.strftime("%d/%m/%Y"))}</div></div>'
        '<div class="hm-kpis">'
        f'<div class="hm-kpi"><div class="k">Gare</div><div class="v">{len(reali)}</div>'
        f'<div class="n">{conclusa} concluse</div></div>'
        f'<div class="hm-kpi"><div class="k">Da pagare</div><div class="v">{html.escape(gare.euro(da_pagare))}</div>'
        '<div class="n">quota a carico del team</div></div>'
        f'<div class="hm-kpi"><div class="k">Bilancio sponsor</div><div class="v">{html.escape(gare.euro(disponibile))}</div>'
        f'<div class="n">{len(sponsor)} sponsor</div></div>'
        f'<div class="hm-kpi"><div class="k">Prossima gara</div><div class="v">{html.escape(prossima["nome"]) if prossima else "—"}</div>'
        f'<div class="n">{html.escape(_quando(_data(prossima), oggi)) if prossima else "nessuna in programma"}</div></div>'
        "</div></div>",
        unsafe_allow_html=True,
    )


def _prossima_card(gara: dict, oggi: date) -> None:
    d = _data(gara)
    giorni = (d - oggi).days if d else 0
    testo_durata = _quando(d, oggi).capitalize() + " alla partenza"
    piloti = len(gara.get("piloti") or [])
    st.markdown(
        '<div class="hm-next">'
        '<div class="hm-next-tag">⭐ Prossima gara</div>'
        f'<div class="hm-next-name">{html.escape(gara["nome"])}</div>'
        f'<div class="hm-next-meta">{html.escape(gara.get("team") or "Team non indicato")} · '
        f'{html.escape(gare.formato_tipo(gara))} · {html.escape(d.strftime("%d/%m/%Y") if d else "")}'
        f' · {piloti} piloti</div>'
        f'<div style="margin-top:12px" class="hm-count">{"OGGI" if giorni == 0 else f"{giorni} giorni"}</div>'
        f'<div class="hm-next-meta">{html.escape(testo_durata)}</div></div>',
        unsafe_allow_html=True,
    )
    c1, _ = st.columns([1.3, 4])
    if c1.button("🏎️ Apri muretto", type="primary", key=f"hm_next_apri_{gara['id']}", use_container_width=True):
        gare.apri_gara(gara["id"])
        st.rerun()


def _avvisi_box(gare_list: list[dict], oggi: date) -> None:
    avvisi = _avvisi(gare_list, oggi)
    if not avvisi:
        st.markdown('<div class="hm-empty">✅ Nessuna cosa in sospeso.</div>', unsafe_allow_html=True)
        return
    chips = "".join(
        f'<span class="hm-badge hm-{liv}">{html.escape(testo)}</span>' for liv, testo in avvisi
    )
    st.markdown(f'<div class="hm-badges">{chips}</div>', unsafe_allow_html=True)


def _card(gara: dict, oggi: date, colore: str) -> None:
    d = _data(gara)
    conclusa = gara.get("stato") == "conclusa"
    stato = "Conclusa" if conclusa else _quando(d, oggi).capitalize()
    piloti = len(gara.get("piloti") or [])
    sponsor_nome = ""
    if gara.get("sponsor_id"):
        sponsor_nome = next((s["nome"] for s in gare.load_sponsor() if s.get("id") == gara["sponsor_id"]), "")

    badge = [
        (f"👥 {piloti} piloti", "hm-badge"),
        ("✅ Pagata" if gara.get("pagata") else f"⏳ Da pagare {gare.euro(gare._noi(gara))}",
         "hm-badge hm-ok" if gara.get("pagata") else "hm-badge hm-warn"),
        ("🟢 Live Timing" if gara.get("link") else "⚪ Nessun link",
         "hm-badge hm-ok" if gara.get("link") else "hm-badge"),
    ]
    if sponsor_nome:
        badge.append((f"🤝 {sponsor_nome}", "hm-badge"))
    chips = "".join(f'<span class="{c}">{html.escape(t)}</span>' for t, c in badge)
    sub = " · ".join(x for x in [gara.get("team", ""), gare.formato_tipo(gara)] if x)

    with st.container(border=True):
        st.markdown(
            f'<div class="hm-card" style="border-left-color:{colore}">'
            f'<div class="hm-card-meta">{html.escape(stato)} · {html.escape(d.strftime("%d/%m/%Y") if d else "senza data")}</div>'
            f'<div class="hm-card-name">{html.escape(gara["nome"])}</div>'
            f'<div class="hm-card-sub">{html.escape(sub)}</div>'
            f'<div class="hm-badges">{chips}</div></div>',
            unsafe_allow_html=True,
        )
        st.markdown('<div class="hm-spacer"></div>', unsafe_allow_html=True)
        azioni = st.columns([3, 1, 0.8], gap="medium")
        if conclusa:
            if azioni[0].button("ℹ️ Apri report", key=f"hm_rep_{gara['id']}", use_container_width=True):
                st.session_state[gare.VISTA] = f"report:{gara['id']}"
                st.rerun()
        elif azioni[0].button("🏎️ Apri muretto", key=f"hm_apri_{gara['id']}", type="primary", use_container_width=True):
            gare.apri_gara(gara["id"])
            st.rerun()
        if azioni[2].button("🗑️", key=f"hm_del_{gara['id']}", help="Elimina gara"):
            gare.dialog_elimina(gara["id"], gara["nome"])


def _sezione(titolo: str, icona: str, colore: str, gare_sez: list[dict], oggi: date, descrizione: str = "") -> None:
    st.markdown(
        f'<div class="hm-section"><h3>{icona} {html.escape(titolo)}</h3>'
        f'<span class="c">{len(gare_sez)} {"gara" if len(gare_sez) == 1 else "gare"}'
        f'{" · " + html.escape(descrizione) if descrizione else ""}</span></div>',
        unsafe_allow_html=True,
    )
    if not gare_sez:
        st.markdown('<div class="hm-empty">Nessuna gara in questa categoria.</div>', unsafe_allow_html=True)
        return
    colonne = st.columns(2)
    for i, g in enumerate(gare_sez):
        with colonne[i % 2]:
            _card(g, oggi, COLORE_CONCLUSA if g.get("stato") == "conclusa" else colore)


def _ordina(gare_sez: list[dict], oggi: date) -> list[dict]:
    """Prima le gare in programma (le più vicine in alto), poi le concluse e le passate."""
    avanti = sorted((g for g in gare_sez if g.get("stato") != "conclusa" and _data(g) and _data(g) >= oggi), key=_data)
    ids_avanti = {g["id"] for g in avanti}
    dopo = sorted((g for g in gare_sez if g["id"] not in ids_avanti), key=lambda g: _data(g) or date.min, reverse=True)
    return avanti + dopo


def render() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    oggi = date.today()
    gare_list = gare.load_gare()

    top_l, top_r = st.columns([5, 1.6])
    with top_r:
        if st.button("＋ Nuova gara", type="primary", key="hm_nuova", use_container_width=True):
            dialog_nuova_gara()

    _hero(gare_list, oggi)

    reali = [g for g in gare_list if not g.get("test")]
    prossima = _prossima(reali, oggi)
    if prossima:
        _prossima_card(prossima, oggi)

    with st.expander("⚠️ Da sistemare", expanded=True):
        _avvisi_box(gare_list, oggi)

    tab_gare, tab_fatture, tab_sponsor = st.tabs(["🗓️ Gare per categoria", "🧾 Fatture", "🤝 Sponsor"])
    with tab_gare:
        if not gare_list:
            st.info("Nessuna gara ancora. Premi **＋ Nuova gara** in alto a destra.")
        for tipo, icona, colore, descr in CATEGORIE:
            sez = _ordina([g for g in reali if g.get("tipo") == tipo and not g.get("test")], oggi)
            _sezione(tipo, icona, colore, sez, oggi, descr)
        test = _ordina([g for g in gare_list if g.get("test")], oggi)
        if test:
            _sezione("Gare TEST", "🧪", COLORE_TEST, test, oggi, "simulazioni del muretto")

    with tab_fatture:
        gare.render_fatture(gare_list)
    with tab_sponsor:
        gare.render_sponsor(gare_list)
