---
name: Apex live-timing stream
description: Why Apex Timing's live page must be read from its WebSocket rather than its static HTML tables.
---

Apex live-timing pages use their static HTML mostly as a shell; the populated standings arrive through the WebSocket configured in the page's `javascript/config.js`. The configuration provides the host and base port; the HTTPS page connects securely on base port +3. Frames contain newline-separated commands, including a `grid` HTML table. Map each row by the header cells' `data-type` order; `tlp` is completed laps and `dyn1` with type `countdown` supplies the race timer in milliseconds.

**Why:** the HTML tables can be empty or contain UI placeholders, while the actual stream provides the team's current rank and lap data.

**How to apply:** read the page's config instead of guessing ports, allow only the known Apex data host, parse the live `grid` frame, and show unavailable values as unavailable rather than inventing them.
