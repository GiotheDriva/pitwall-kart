"""Campo di gara simulato per le gare TEST.

I piloti/team inseriti nelle impostazioni sono i kart della gara (uno per riga, kart 1, 2, ...).
Il kart scelto in "Chi sono io" è il nostro: su di lui finiscono i pit e le penalità reali.
Gli altri sono bot con passo tra il giro migliore e il peggiore.

Lo snapshot ha la stessa forma di quello del live timing Apex: classifica, sorpassi, soste e
notifiche funzionano senza modifiche al resto del muretto.

Tutto è deterministico: stessi parametri, stessi nomi, stessi eventi danno sempre gli stessi giri.

Eventi simulati (gara["test_eventi"]), ognuno con kart, tipo, t (s di gara) e valore:
- "ritardo": secondi persi nel giro in corso (sorpasso subito, penalità su un bot)
- "giro":    il giro in corso dura esattamente `valore` secondi (miglior giro)
- "sosta":   pit di `valore` secondi nel giro in corso (pit forzato di un bot)
"""
from __future__ import annotations

import bisect
import random
from itertools import accumulate

NUMERO_NOSTRO = "1"

PREDEFINITI = {
    "giro_veloce_s": 55.0,   # giro migliore a cui puntano i bot
    "giro_lento_s": 68.0,    # giro peggiore: i bot più scarsi
    "giro_nostro_s": 60.0,   # passo del nostro kart nella simulazione
    "stint_min_min": 10.0,
    "stint_max_min": 40.0,
    "pit_max_s": 180.0,
}


def impostazioni(test: dict | None) -> dict:
    """Parametri della gara TEST, con valori di riserva e fasce sempre in ordine."""
    t = {**PREDEFINITI, **(test or {})}
    veloce, lento = sorted((float(t["giro_veloce_s"]), float(t["giro_lento_s"])))
    stint_min, stint_max = sorted((float(t["stint_min_min"]) * 60, float(t["stint_max_min"]) * 60))
    return {
        "giro_veloce_s": max(20.0, veloce),
        "giro_lento_s": max(20.0, lento),
        "giro_nostro_s": max(20.0, float(t["giro_nostro_s"])),
        "stint_min_s": max(60.0, stint_min),
        "stint_max_s": max(60.0, stint_max),
        "pit_max_s": max(0.0, float(t["pit_max_s"])),
    }


def durata_gara_s(gara: dict) -> float:
    piano = gara.get("piano") or {}
    return float(piano.get("ore") or gara.get("ore") or 0) * 3600


def _passo(rng: random.Random, passo_s: float) -> float:
    """Tempo di un giro: passo del kart, piccola variazione e ogni tanto un errore."""
    tempo = passo_s * (1 + rng.gauss(0, 0.004))
    if rng.random() < 0.02:  # uscita di pista o traffico
        tempo += rng.uniform(1.5, 6.0)
    return tempo


def _eventi_di(eventi: list[dict] | None, numero: str) -> list[tuple[float, str, float]]:
    """Eventi di un kart come (t, tipo, valore), ordinati per t."""
    return sorted(
        (float(e["t"]), str(e["tipo"]), float(e["valore"]))
        for e in eventi or []
        if str(e.get("kart")) == numero
    )


def _percorso(rng: random.Random, durata_s: float, passo_s: float, eventi, stint=None):
    """Giri di un kart: (fine di ogni giro, durata di ogni giro, soste (ingresso, durata)).

    eventi: [(t, tipo, valore)] ordinati per t.
    stint: (min_s, max_s, pit_max_s) per le soste automatiche dei bot; None per il nostro kart.
    """
    fine: list[float] = []
    tempi: list[float] = []
    soste: list[tuple[float, float]] = []
    t = 0.0
    i = 0
    extra_auto = 0.0
    inizio_stint = 0.0
    lunghezza = rng.uniform(stint[0], stint[1]) if stint else 0.0
    while t < durata_s:
        tempo = _passo(rng, passo_s) + extra_auto
        extra_auto = 0.0
        in_giro = []
        while i < len(eventi) and eventi[i][0] < t + tempo:
            in_giro.append(eventi[i])
            i += 1
        for ev_t, tipo, valore in in_giro:
            if tipo == "giro":
                tempo = max(valore, ev_t - t + 0.001)
        for ev_t, tipo, valore in in_giro:
            if tipo == "ritardo":
                tempo += valore
            elif tipo == "sosta":
                tempo += valore
                soste.append((max(ev_t, t), valore))
        t += tempo
        fine.append(t)
        tempi.append(tempo)
        if stint and t < durata_s and t - inizio_stint >= lunghezza:
            sosta = rng.uniform(0.6 * stint[2], stint[2])
            soste.append((t, sosta))
            extra_auto = sosta  # la sosta allunga il giro successivo
            inizio_stint = t
            lunghezza = rng.uniform(stint[0], stint[1])
    return fine, tempi, soste


def _kart(numero: str, team: str, fine, tempi, soste) -> dict:
    return {
        "numero": numero,
        "team": team,
        "fine": fine,                          # fine di ogni giro (s dall'inizio gara)
        "tempi": tempi,                        # durata di ogni giro
        "best": list(accumulate(tempi, min)),  # miglior giro fino a ogni giro
        "soste": soste,                        # (ingresso ai box, durata sosta)
    }


def genera_bot(gid: str, imp: dict, durata_s: float, eventi: list[dict] | None,
               nomi: list[str], numero_nostro: str) -> list[dict]:
    """Tutti i kart tranne il nostro. Il passo dipende dalla posizione in lista, quindi resta stabile
    anche quando cambi il kart scelto in 'Chi sono io'."""
    n = len(nomi)
    if n == 0:
        return []
    base = random.Random(f"{gid}:passi")
    # Passi distribuiti su tutta la fascia, in ordine casuale: ci sono bot quasi perfetti e bot scarsi.
    quote = [(i + base.random()) / n for i in range(n)]
    base.shuffle(quote)
    bot = []
    for i, nome in enumerate(nomi):
        numero = str(i + 1)
        if numero == numero_nostro:
            continue
        rng = random.Random(f"{gid}:bot:{numero}")
        passo = imp["giro_veloce_s"] + (imp["giro_lento_s"] - imp["giro_veloce_s"]) * quote[i]
        fine, tempi, soste = _percorso(
            rng, durata_s, passo, _eventi_di(eventi, numero),
            (imp["stint_min_s"], imp["stint_max_s"], imp["pit_max_s"]),
        )
        bot.append(_kart(numero, nome, fine, tempi, soste))
    return bot


def campo(gara: dict, bot: list[dict], pit_nostri: list[tuple[float, float]], numero_nostro: str) -> list[dict]:
    """Tutti i kart della gara TEST, il nostro per primo.

    bot: risultato di genera_bot (può essere tenuto in cache).
    pit_nostri: (ingresso ai box, durata sosta) delle soste reali del nostro kart.
    """
    imp = impostazioni(gara.get("test"))
    durata_s = durata_gara_s(gara)
    nomi = gara.get("piloti") or []
    idx = int(numero_nostro) - 1
    nome_nostro = nomi[idx] if 0 <= idx < len(nomi) else "Nostro kart"
    ev = sorted(
        [(float(t), "sosta", float(d)) for t, d in pit_nostri]
        + _eventi_di(gara.get("test_eventi"), numero_nostro),
        key=lambda e: e[0],
    )
    fine, tempi, soste = _percorso(
        random.Random(f"{gara['id']}:nostro:{numero_nostro}"), durata_s, imp["giro_nostro_s"], ev
    )
    nostro = _kart(numero_nostro, nome_nostro, fine, tempi, soste)
    return [nostro] + bot


def _ordine(karts: list[dict], t: float) -> list[tuple[dict, int, float]]:
    """Ordine al tempo t: più giri completati, poi chi ha chiuso l'ultimo giro per primo."""
    stato = []
    for k in karts:
        n = bisect.bisect_right(k["fine"], t)
        ultimo = k["fine"][n - 1] if n else float("inf")
        stato.append((k, n, ultimo))
    stato.sort(key=lambda s: (-s[1], s[2], s[0]["numero"]))
    return stato


def pianifica_sorpasso(karts: list[dict], numero: str, t: float) -> tuple[str | None, float, str]:
    """Ritardo da dare al kart davanti perché `numero` lo superi alla linea successiva.

    Restituisce (numero del kart davanti, secondi di ritardo, messaggio d'errore).
    """
    ordine = _ordine(karts, t)
    pos = next((i for i, s in enumerate(ordine) if s[0]["numero"] == numero), None)
    if pos is None:
        return None, 0.0, "Kart non trovato."
    if pos == 0:
        return None, 0.0, "È già in testa: non c'è nessuno da superare."
    io, n_io, _ = ordine[pos]
    davanti, n_dav, _ = ordine[pos - 1]
    if n_dav != n_io:
        return None, 0.0, "Il kart davanti è più avanti di giri: il sorpasso simulato vale solo nello stesso giro."
    if n_io >= len(io["fine"]) or n_dav >= len(davanti["fine"]):
        return None, 0.0, "Gara terminata per questi kart."
    secondi = io["fine"][n_io] - davanti["fine"][n_io] + 0.5
    return davanti["numero"], secondi, ""


def _tempo(secondi: float | None) -> str:
    """Formato dei giri come li scrive Apex: 58.123 oppure 1:02.345."""
    if secondi is None:
        return ""
    if secondi < 60:
        return f"{secondi:.3f}"
    return f"{int(secondi // 60)}:{secondi % 60:06.3f}"


def _mmss(secondi: float) -> str:
    secondi = max(0, int(secondi))
    return f"{secondi // 60:02d}:{secondi % 60:02d}"


def _distacco(rif: dict, n_rif: int, k: dict, n_k: int) -> str:
    """Distacco di k da rif: secondi se hanno completato lo stesso numero di giri, altrimenti giri di ritardo."""
    if n_k == 0:
        return ""
    if n_rif > n_k:
        giri = n_rif - n_k
        return f"+{giri} giro" if giri == 1 else f"+{giri} giri"
    return f"{k['fine'][n_k - 1] - rif['fine'][n_k - 1]:.3f}"


def snapshot(gara: dict, karts: list[dict], t: float, ora: float) -> dict:
    """Stato del campo al tempo di gara `t` (s), nella forma del live timing Apex."""
    durata_s = durata_gara_s(gara)
    stato = _ordine(karts, t)
    rows = []
    for pos, (k, n, _) in enumerate(stato, start=1):
        passate = [s for s in k["soste"] if s[0] <= t]
        in_box = bool(passate) and passate[-1][0] <= t < passate[-1][0] + passate[-1][1]
        uscita = passate[-1][0] + passate[-1][1] if passate else 0.0
        if pos == 1:
            gap = intervallo = ""
        else:
            leader = stato[0]
            davanti = stato[pos - 2]
            gap = _distacco(leader[0], leader[1], k, n)
            intervallo = _distacco(davanti[0], davanti[1], k, n)
        rows.append(
            {
                "id": f"sim-{k['numero']}",
                "position": pos,
                "kart": k["numero"],
                "team": k["team"],
                "last_lap": _tempo(k["tempi"][n - 1]) if n else "",
                "gap": gap,
                "interval": intervallo,
                "best_lap": _tempo(k["best"][n - 1]) if n else "",
                "laps_completed": n,
                "pits": len(passate),
                "last_pit": _tempo(passate[-1][1]) if passate else "",
                "on_track": "ai box" if in_box else _mmss(t - uscita),
            }
        )

    return {
        "event_title": f"TEST · {gara.get('nome') or 'Gara di prova'}",
        "rows": rows,
        "remaining_seconds": max(0.0, durata_s - t),
        "received_at": ora,
        "remaining_received_at": ora,
        "last_message_at": ora,
        "standings_received_at": ora,
        "connected": True,
        "last_error": None,
        "pilots": list(dict.fromkeys(r["team"] for r in rows)),
        "comments": [],
        "debug": {"sample": []},
    }
