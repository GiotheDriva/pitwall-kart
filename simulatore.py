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
