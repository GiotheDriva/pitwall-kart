"""Piano gara vivo: cronometro, chi è in pista, tempo al cambio, pit ora.

Il piano viene simulato a partire dagli stint salvati nel Pianificatore.
Ogni "Pit ora" registrato sposta gli stint successivi: lo stint in corso
finisce adesso, il pilota successivo della rotazione entra dopo il tempo
di sosta, e lo stint riparte con la durata pianificata.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st


def simula(
    durata_s: float,
    stint_s: float,
    pit_s: float,
    piloti: list[str],
    pit_manuali: list[float],
) -> list[dict]:
    """Stint in secondi dall'inizio gara, con i pit manuali inseriti."""
    piloti = piloti or ["Pilota 1"]
    manuali = sorted(t for t in pit_manuali if 0 < t < durata_s)
    stints: list[dict] = []
    t = 0.0
    while t < durata_s - 1e-6 and len(stints) < 500:
        while manuali and manuali[0] < t:
            manuali.pop(0)
        fine = min(t + stint_s, durata_s)
        if manuali and t <= manuali[0] < fine:
            fine = manuali.pop(0)
        stints.append(
            {
                "n": len(stints) + 1,
                "pilota": piloti[len(stints) % len(piloti)],
                "inizio": t,
                "fine": fine,
            }
        )
        if fine >= durata_s - 1e-6:
            break
        t = fine + pit_s
    return stints


def tempo_gara(gara: dict, apex_elapsed: float | None) -> float:
    """Secondi trascorsi dall'inizio gara: cronometro del muretto oppure Apex."""
    cron = gara.get("cronometro") or {}
    if apex_elapsed is not None and (cron.get("usa_apex") or apex_elapsed > 0):
        # Appena il countdown di Apex parte, la strategia parte insieme alla gara.
        return apex_elapsed
    offset = float(cron.get("offset_s") or 0.0)
    if cron.get("stato") == "in_corso" and cron.get("avvio"):
        offset += datetime.now().timestamp() - float(cron["avvio"])
    return offset


def _mmss(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _hhmmss(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def render_card(gara: dict, race_remaining: float | None, azioni) -> None:
    """Scheda live nel muretto. `azioni` è il modulo gare con le funzioni di controllo."""
    gid = gara["id"]
    piano = gara.get("piano")
    durata_s = float(
        (piano or {}).get("ore") or gara.get("ore") or 0
    ) * 3600
    apex_elapsed = (
        durata_s - race_remaining
        if race_remaining is not None and durata_s > 0
        else None
    )
    elapsed = tempo_gara(gara, apex_elapsed)
    cron = gara.get("cronometro") or {}
    in_corso = cron.get("stato") == "in_corso"

    st.markdown("#### Strategia live")
    c1, c2 = st.columns([2, 5])
    if apex_elapsed is not None and apex_elapsed > 0:
        etichetta = "da Apex"
    else:
        etichetta = "in corso" if in_corso else "fermo"
    c1.metric("Tempo di gara", _hhmmss(elapsed), etichetta)
    with c2:
        b1, b2, b3 = st.columns(3)
        if in_corso:
            b1.button("⏸ Pausa", key=f"cron_pausa_{gid}", on_click=azioni.cron_pausa, args=(gid,))
        else:
            b1.button("▶ Avvia gara", type="primary", key=f"cron_avvia_{gid}",
                      on_click=azioni.cron_avvia, args=(gid,))
        b2.button("⟲ Azzera", key=f"cron_azzera_{gid}", on_click=azioni.cron_azzera, args=(gid,))
        b3.toggle(
            "Usa il tempo di Apex",
            value=bool(cron.get("usa_apex")),
            key=f"usa_apex_{gid}",
            on_change=azioni.cron_usa_apex,
            args=(gid,),
            help="Se attivo, il tempo di gara arriva dal conto alla rovescia di Apex.",
        )

    if not piano or durata_s <= 0:
        st.info(
            "Imposta la strategia nel **Pianificatore** e premi "
            "**📌 Usa questo piano per il muretto** per vedere chi è in pista."
        )
        _controlli(gara, elapsed, durata_s, None, azioni)
        return

    pit_manuali = [float(t) for t in gara.get("pit_eseguiti", [])]
    stints = simula(durata_s, float(piano["stint_min"]) * 60, float(piano["pit_sec"]),
                    piano.get("piloti", []), pit_manuali)
    pit_totali = len(stints) - 1
    pit_minimo = int(piano.get("pit_minimo") or 0)

    attuale = next((s for s in stints if s["inizio"] <= elapsed < s["fine"]), None)
    prossimo = next((s for s in stints if s["inizio"] > elapsed), None)

    if elapsed >= durata_s:
        st.success("Gara conclusa.")
    elif attuale:
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("In pista ora", attuale["pilota"], f"stint {attuale['n']}")
        k2.metric("Cambio tra", _mmss(attuale["fine"] - elapsed), "secondo la strategia")
        k3.metric(
            "Prossimo pilota",
            prossimo["pilota"] if prossimo else "—",
            f"tra {_mmss(prossimo['inizio'] - elapsed)}" if prossimo else "",
        )
        k4.metric(
            "Pit",
            f"{len(pit_manuali)} fatti",
            f"{pit_totali} previsti · minimo {pit_minimo}" + (" ✅" if pit_totali >= pit_minimo else " ⚠️"),
        )
    else:
        k1, k2 = st.columns(2)
        ultimo_pit = st.session_state.get("_ultimo_pit_apex") or ""
        k1.metric(
            "In sosta",
            "Pit stop in corso",
            f"ultimo pit Apex: {ultimo_pit}" if ultimo_pit else "ultimo pit Apex: —",
        )
        k2.metric("Prossimo pilota", prossimo["pilota"] if prossimo else "—")

    b1, b2, _ = st.columns([1.3, 1.6, 4])
    b1.button(
        "🛑 Pit ora",
        type="primary",
        key=f"pit_ora_{gid}",
        help="Registra l'ingresso in pit adesso: la strategia si ricalcola da qui.",
        on_click=azioni.registra_pit,
        args=(gid, elapsed),
        disabled=elapsed >= durata_s,
    )
    b2.button(
        "↩ Annulla ultimo pit",
        key=f"annulla_pit_{gid}",
        disabled=not pit_manuali,
        on_click=azioni.annulla_pit,
        args=(gid,),
    )

    righe = [
        {
            "Stint": s["n"],
            "Pilota": s["pilota"],
            "Inizio": _mmss(s["inizio"]),
            "Fine": _mmss(s["fine"]),
            "Durata": f"{(s['fine'] - s['inizio']) / 60:.1f} min",
            "Stato": "▶️ in corso" if s is attuale else ("✔️" if s["fine"] <= elapsed else "in arrivo"),
        }
        for s in stints
        if s["fine"] > elapsed - 1
    ][:8]
    if righe:
        st.caption("Prossimi stint (tempo di gara, mm:ss dall'inizio)")
        st.dataframe(pd.DataFrame(righe), hide_index=True)

    _controlli(gara, elapsed, durata_s, stints, azioni)
    st.divider()


def _controlli(gara: dict, elapsed: float, durata_s: float, stints, azioni) -> None:
    """Box di controllo: tempo, stint attuale e tempo di sosta modificabili."""
    gid = gara["id"]
    piano = gara.get("piano") or {}
    with st.expander("⚙️ Controlli: tempo, stint e sosta", expanded=False):
        st.caption("Modifica il tempo di gara, vai a uno stint o cambia il tempo di sosta.")
        c1, c2, c3 = st.columns([1, 1, 1.2])
        minuti = c1.number_input("Imposta tempo: minuti", 0, 1440, int(elapsed // 60), 1, key=f"ctl_min_{gid}")
        secondi = c2.number_input("secondi", 0, 59, int(elapsed % 60), 1, key=f"ctl_sec_{gid}")
        c3.write("")
        c3.button("Imposta tempo", key=f"ctl_tempo_{gid}", on_click=azioni.cron_imposta,
                  args=(gid, minuti * 60 + secondi))

        if stints:
            d1, d2 = st.columns([1, 1.2])
            n = d1.number_input("Vai allo stint numero", 1, len(stints), 1, 1, key=f"ctl_stint_{gid}")
            d2.write("")
            d2.button(
                "Vai a questo stint",
                key=f"ctl_vai_stint_{gid}",
                on_click=azioni.cron_imposta,
                args=(gid, stints[int(n) - 1]["inizio"]),
                help="Porta il cronometro all'inizio dello stint scelto.",
            )

        e1, e2 = st.columns([1, 1.2])
        pit_nuovo = e1.number_input("Tempo di sosta (s)", 0, 900, int(piano.get("pit_sec") or 90), 5,
                                    key=f"ctl_pit_{gid}")
        e2.write("")
        e2.button("Salva tempo di sosta", key=f"ctl_salva_pit_{gid}", on_click=azioni.imposta_pit_sec,
                  args=(gid, int(pit_nuovo)))
