---
name: Streamlit preview routing
description: How this pnpm artifact workspace exposes a Streamlit app in its root preview.
---

In this workspace, the root `replit.dev` preview resolves registered web artifacts. A standalone Streamlit workflow can start and return HTML without making that root preview available; the app needs a root web artifact whose service runs Streamlit.

**Why:** the Streamlit workflow was healthy, but the root preview showed “no previewable artifacts” until a web artifact was registered.

**How to apply:** when adding Streamlit here, verify a web artifact exists at `/`, route its service to Streamlit, and check the service working directory so `main.py` resolves correctly.
