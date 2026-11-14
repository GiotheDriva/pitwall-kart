"""Gara simulata per le gare TEST.

I piloti/team inseriti nelle impostazioni sono i kart della gara (uno per riga: kart 1, 2, ...).
Il kart scelto in "Chi sono io" è il nostro: su di lui finiscono i pit reali e le penalità reali.
Tutti gli altri, e anche il nostro per il ritmo, sono simulati con la stessa logica:

- ritmo che sale e scende nel tempo (gomme, benzina, forma del pilota a ogni stint);
- traffico, errori, qualche penalità per infrazioni varie;
- soste programmate ogni stint, più soste extra per guasti o ritorni ai box;
- soste che durano troppo o troppo poco: in quei casi il kart prende una penalità da scontare alla sosta successiva.

Nessun ritmo è fisso: sorpassi, distacchi e giri di ritardo emergono dal caso e non seguono uno schema.
Lo snapshot ha la stessa forma di quello del live timing Apex, quindi classifica, sorpassi,
soste e notifiche funzionano senza modifiche al resto del muretto.

Tutto è deterministico per id gara, nomi e seed: ricaricando la pagina si ritrova la stessa gara.

Eventi manuali (gara["test_eventi"]), ognuno con kart, tipo, t (s di gara), valore e motivo:
- "ritardo": secondi persi nel giro in corso (sorpasso subito, penalità su un bot)
- "giro":    il giro in corso dura esattamente `valore` secondi (miglior giro)
- "sosta":   pit di `valore` secondi nel giro in corso (pit forzato)
"""
from __future__ import annotations

import bisect
import random
from itertools import accumulate

NUMERO_NOSTRO = "1"

PREDEFINITI = {
    "giro_veloce_s": 55.5,   # ritmo del kart più veloce
    "giro_lento_s": 58.5,    # ritmo del kart più lento
    "stint_min_min": 10.0,
    "stint_max_min": 40.0,
    "pit_max_s": 180.0,
}

MOTIVI_PENALITA = [
    "Traiettoria",
    "Velocità in pit lane",
    "Partenza anticipata",
    "Bandiera blu ignorata",
    "Contatto con altro kart",
]


def impostazioni(test: dict | None) -> dict:
    """Parametri della gara TEST, con valori di riserva e fasce sempre in ordine."""
    t = {**PREDEFINITI, **(test or {})}
    veloce, lento = sorted((float(t["giro_veloce_s"]), float(t["giro_lento_s"])))
    stint_min, stint_max = sorted((float(t["stint_min_min"]) * 60, float(t["stint_max_min"]) * 60))
    return {
        "giro_veloce_s": max(20.0, veloce),
        "giro_lento_s": max(20.0, lento),
        "stint_min_s": max(60.0, stint_min),
        "stint_max_s": max(60.0, stint_max),
        "pit_max_s": max(0.0, float(t["pit_max_s"])),
    }


def durata_gara_s(gara: dict) -> float:
    piano = gara.get("piano") or {}
    return float(piano.get("ore") or gara.get("ore") or 0) * 3600


def _passi(gid: str, imp: dict, n: int) -> list[float]:
    """Ritmo base di ogni kart, distribuito tra il più veloce e il più lento, in ordine casuale ma fisso per gara."""
    if n <= 0:
        return []
    base = random.Random(f"{gid}:passi")
    quote = [(i + base.random()) / n for i in range(n)]
    base.shuffle(quote)
    veloce, lento = imp["giro_veloce_s"], imp["giro_lento_s"]
    return [veloce + (lento - veloce) * q for q in quote]


def _eventi_di(eventi: list[dict] | None, numero: str) -> list[tuple[float, str, float, str]]:
    """Eventi manuali di un kart come (t, tipo, valore, motivo), ordinati per t."""
    return sorted(
        (float(e["t"]), str(e["tipo"]), float(e["valore"]), str(e.get("motivo") or ""))
        for e in eventi or []
        if str(e.get("kart")) == numero
    )


def _sosta_casuale(rng: random.Random, pit_max: float) -> tuple[float, str]:
    """Durata di una sosta di un bot e, se è andata male, il motivo della penalità."""
    r = rng.random()
    if r < 0.12:
        return rng.uniform(1.1, 1.8) * pit_max, "Sosta oltre il massimo in pit"
    if r < 0.20:
        return rng.uniform(0.2, 0.55) * pit_max, "Sosta troppo breve: lavoro incompleto"
    return rng.uniform(0.7, 0.95) * pit_max, ""


def _percorso(rng: random.Random, durata_s: float, passo_s: float, eventi, auto_pit: bool, imp: dict):
    """Gara di un kart: giri, soste e penalità.

    eventi: [(t, tipo, valore, motivo)] ordinati per t (manuali, o soste reali del nostro kart).
    auto_pit: True per i bot, che fanno le proprie soste; False per il nostro, che fa solo quelle reali.
    Restituisce (fine di ogni giro, durata di ogni giro, soste (ingresso, durata), penalità (t, s, motivo)).
    """
    pit_max = imp["pit_max_s"]
    fine: list[float] = []
    tempi: list[float] = []
    soste: list[tuple[float, float]] = []
    penalita: list[tuple[float, float, str]] = []
    t = 0.0
    i = 0
    deriva = 0.0            # ritmo che sale e scende: gomme, benzina, traffico ricorrente
    forma = 0.0             # forma del pilota nello stint in corso
    extra_prossimo = 0.0    # secondi di sosta da pagare nel giro successivo
    in_attesa = 0.0         # penalità da scontare alla prossima sosta
    inizio_stint = 0.0
    ultima_sosta = -1e9
    lunghezza = rng.uniform(imp["stint_min_s"], imp["stint_max_s"]) if auto_pit else 0.0

    def penalizza(istante: float, secondi: float, motivo: str) -> None:
        nonlocal in_attesa
        penalita.append((istante, secondi, motivo))
        in_attesa += secondi

    def sosta(istante: float, durata: float) -> float:
        nonlocal in_attesa, inizio_stint, ultima_sosta, forma
        totale = durata + in_attesa  # le penalità in attesa si scontano qui
        in_attesa = 0.0
        soste.append((istante, totale))
        inizio_stint = istante
        ultima_sosta = istante
        forma = rng.gauss(0, 0.006)  # a ogni sosta il ritmo riparte da una forma diversa
        return totale

    while t < durata_s:
        deriva = 0.9 * deriva + rng.gauss(0, 0.004)
        tempo = passo_s * (1 + deriva + forma) * (1 + rng.gauss(0, 0.004)) + extra_prossimo
        extra_prossimo = 0.0
        if rng.random() < 0.07:
            tempo += rng.uniform(0.5, 3.0)     # traffico
        if rng.random() < 0.02:
            tempo += rng.uniform(1.5, 6.0)     # errore
        if rng.random() < 0.0015:
            tempo += rng.uniform(12.0, 40.0)   # incidente o testacoda

        # Eventi manuali che cadono in questo giro.
        fine_base = t + tempo
        in_giro = []
        while i < len(eventi) and eventi[i][0] < fine_base:
            in_giro.append(eventi[i])
            i += 1
        for ev_t, tipo, valore, _motivo in in_giro:
            if tipo == "giro":
                tempo = max(valore, ev_t - t + 0.001)
        for ev_t, tipo, valore, _motivo in in_giro:
            if tipo == "ritardo":
                tempo += valore
            elif tipo == "sosta":
                tempo += sosta(max(ev_t, t), valore)

        t += tempo
        fine.append(t)
        tempi.append(tempo)

        # Infrazioni occasionali: la penalità si sconta alla prossima sosta.
        if rng.random() < 0.008:
            penalizza(t, rng.choice([5.0, 10.0, 10.0, 20.0]), rng.choice(MOTIVI_PENALITA))

        # Soste dei bot: programmata a fine stint, oppure un guasto che obbliga a rientrare.
        if auto_pit and t < durata_s:
            programmata = t - inizio_stint >= lunghezza
            guasto = (t - ultima_sosta > 300) and rng.random() < 0.004
            if programmata or guasto:
                durata, motivo = _sosta_casuale(rng, pit_max)
                extra_prossimo = sosta(t, durata)
                if motivo:
                    penalizza(t, 10.0, motivo)  # si paga alla sosta successiva
                lunghezza = rng.uniform(imp["stint_min_s"], imp["stint_max_s"])
    return fine, tempi, soste, penalita


def _kart(numero: str, team: str, percorso) -> dict:
    fine, tempi, soste, penalita = percorso
    return {
        "numero": numero,
        "team": team,
        "fine": fine,                          # fine di ogni giro (s dall'inizio gara)
        "tempi": tempi,                        # durata di ogni giro
        "best": list(accumulate(tempi, min)),  # miglior giro fino a ogni giro
        "soste": soste,                        # (ingresso ai box, durata sosta)
        "penalita": penalita,                  # (t, secondi, motivo)
    }


def genera_bot(gid: str, imp: dict, durata_s: float, eventi: list[dict] | None,
               nomi: list[str], numero_nostro: str) -> list[dict]:
    """Tutti i kart tranne il nostro. Ogni kart ha il suo ritmo e la sua storia di gara."""
    passi = _passi(gid, imp, len(nomi))
    bot = []
    for i, nome in enumerate(nomi):
        numero = str(i + 1)
        if numero == numero_nostro:
            continue
        rng = random.Random(f"{gid}:bot:{numero}")
        percorso = _percorso(rng, durata_s, passi[i], _eventi_di(eventi, numero), True, imp)
        bot.append(_kart(numero, nome, percorso))
    return bot


def _seme(gara: dict) -> str:
    return f"{gara['id']}:{gara.get('test_seme') or ''}"


def genera_campo(gara: dict, numero_nostro: str, pit_nostri: list[tuple[float, float]]) -> list[dict]:
    """Tutti i kart della gara TEST (bot + nostro), pronti per snapshot()."""
    imp = impostazioni(gara.get("test"))
    nomi = gara.get("piloti") or []
    bot = genera_bot(_seme(gara), imp, durata_gara_s(gara), gara.get("test_eventi"), nomi, numero_nostro)
    return campo(gara, bot, pit_nostri, numero_nostro)


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
    passi = _passi(_seme(gara), imp, len(nomi))
    passo_nostro = passi[idx] if 0 <= idx < len(passi) else imp["giro_lento_s"]
    ev = sorted(
        [(float(t), "sosta", float(d), "") for t, d in pit_nostri]
        + _eventi_di(gara.get("test_eventi"), numero_nostro),
        key=lambda e: e[0],
    )
    percorso = _percorso(
        random.Random(f"{_seme(gara)}:nostro:{numero_nostro}"), durata_s, passo_nostro, ev, False, imp
    )
    return [_kart(numero_nostro, nome_nostro, percorso)] + bot


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


def _commenti(karts: list[dict], t: float) -> list[dict]:
    """Penalità emesse fino al tempo t, scritte come i commenti di Apex."""
    eventi = []
    for k in karts:
        for istante, secondi, motivo in k["penalita"]:
            if istante <= t:
                testo = f"Penalità +{secondi:g} s · Kart {k['numero']} {k['team']} · {motivo}"
                eventi.append((istante, {"time": _mmss(istante), "text": testo}))
    eventi.sort(key=lambda e: e[0])
    return [item for _, item in eventi]


def snapshot(gara: dict, karts: list[dict], t: float, ora: float) -> dict:
    """Stato della gara al tempo `t` (s), nella forma del live timing Apex."""
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
        "comments": _commenti(karts, t),
        "debug": {"sample": []},
    }
