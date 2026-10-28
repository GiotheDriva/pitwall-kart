---
name: Go-kart endurance pit-wall product
description: Product scope and distinction between timing-page names and simulated race data.
---

The product is an Italian-language pit-wall strategy dashboard for go-kart endurance events. When using Apex Timing, standings, lap times, completed laps, and race countdown must come from its live feed, not random values. Stint timing and pit-return projections remain local estimates. Unsupported timing sources may still show clearly labeled simulated telemetry.

**Why:** the user tested the app against First Kart Inn and explicitly asked for real team/position values plus current lap totals or remaining race time.

**How to apply:** for Apex, never replace a missing live field with random telemetry; label locally computed strategy separately. Keep simulations clearly marked for sources without a supported live feed.
