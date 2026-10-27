---
name: Resilient timing-table parsing
description: Avoiding pandas parser failures on malformed live-timing HTML.
---

`pandas.read_html` can raise `IndexError` on an empty or malformed table and abort parsing the entire page, including valid sibling tables. Isolate parsing per HTML table and skip malformed ones; keep a text-based fallback for pages without useful headers.

**Why:** the timing-page lookup failed on a malformed table before reaching valid driver rows.

**How to apply:** when adding or changing live-timing extraction, preserve per-table error handling and test a page containing both malformed and valid tables.
