---
name: Go-kart endurance pit-wall product
description: Product scope and distinction between timing-page names and simulated race data.
---

The product is an Italian-language pit-wall strategy dashboard for go-kart endurance events. It may read driver or team names from a user-provided public live-timing HTML page, while telemetry, race events, and pit-return estimates are simulated rather than authoritative.

**Why:** the user defined this scope and requested simulated telemetry; future changes should not imply that mock metrics are live race data.

**How to apply:** keep sourced names clearly separate from simulated metrics and estimates unless the user changes the requirement.
