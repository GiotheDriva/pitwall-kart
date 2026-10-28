"""Strumenti professionali per il muretto: pianificatore stint, lavagne e fogli dati.

Tutto il modulo è indipendente dal flusso Live Timing: lavora su dati inseriti
dall'utente e su fogli disegnabili che restano salvati nel browser.
"""
from __future__ import annotations

import json
import math
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components


PALETTE = ["#f4c542", "#72b7ff", "#70e0a4", "#ff7a59", "#c48bff", "#4fd1d9", "#ffa8d2"]


# --------------------------------------------------------------------------- #
# Stile comune
# --------------------------------------------------------------------------- #
def plot_layout(fig: go.Figure, height: int = 360) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=40, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e8edf5", family="system-ui, sans-serif"),
        legend=dict(orientation="h", y=1.12, x=0),
        hoverlabel=dict(bgcolor="#171c24", font_color="#f5f7fa"),
    )
    fig.update_xaxes(gridcolor="rgba(128,128,128,0.18)", zerolinecolor="rgba(128,128,128,0.25)")
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.18)", zerolinecolor="rgba(128,128,128,0.25)")
    return fig


def section_header(eyebrow: str, title: str, subtitle: str = "") -> None:
    st.markdown(
        f"""
        <div class="pw-section">
          <div class="pitwall-eyebrow">{eyebrow}</div>
          <div class="pw-title">{title}</div>
          {f'<div class="pitwall-note">{subtitle}</div>' if subtitle else ''}
        </div>
        """,
        unsafe_allow_html=True,
    )


def kpi_row(items: list[tuple[str, str, str]]) -> None:
    """items: (etichetta, valore, nota)."""
    cards = "".join(
        f'<div class="pw-kpi"><div class="pw-kpi-label">{label}</div>'
        f'<div class="pw-kpi-value">{value}</div>'
        f'<div class="pw-kpi-note">{note}</div></div>'
        for label, value, note in items
    )
    st.markdown(f'<div class="pw-kpis">{cards}</div>', unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# 1. Pianificatore strategia endurance
# --------------------------------------------------------------------------- #
def build_stints(race_minutes: int, stint_minutes: int, pit_seconds: int) -> list[dict]:
    """Ritorna gli stint con inizio/fine in minuti di gara, e le soste fra loro."""
    stints: list[dict] = []
    cursor = 0.0
    index = 1
    while cursor < race_minutes - 1e-6:
        end = min(cursor + stint_minutes, race_minutes)
        stints.append({"stint": index, "inizio": cursor, "fine": end, "durata": end - cursor})
        cursor = end + pit_seconds / 60
        index += 1
        if index > 500:  # protezione da input assurdi
            break
    return stints


def render_planner(
    gare_list: list[dict] | None = None,
    default_id: str | None = None,
    on_save=None,
) -> None:
    section_header(
        "Strategia",
        "Pianificatore stint e soste",
        "Scegli la gara: durata, piloti e pit minimo arrivano dalla scheda gara. "
        "Il piano si ricalcola in tempo reale.",
    )

    endurance = [g for g in (gare_list or []) if g.get("tipo") == "Endurance"]
    gara = None
    if endurance:
        ids = [g["id"] for g in endurance]
        index = ids.index(default_id) if default_id in ids else 0
        gara = st.selectbox(
            "Gara",
            endurance,
            index=index,
            format_func=lambda g: f"{g['nome']} · {g.get('team', '')} · {g.get('ore')} h",
            key="pl_gara",
        )
    else:
        st.info("Nessuna gara endurance nel calendario: puoi comunque pianificare a mano qui sotto.")

    gid = gara["id"] if gara else "manuale"
    c1, c2, c3, c4, c5 = st.columns(5)
    race_hours = c1.number_input(
        "Durata gara (ore)", 0.5, 24.0,
        float(gara["ore"]) if gara and gara.get("ore") else 4.0, 0.5, key=f"pl_hours_{gid}",
    )
    stint_min = c2.number_input("Durata stint (min)", 5, 240, 35, 1, key="pl_stint")
    pit_sec = c3.number_input("Tempo di sosta (s)", 0, 600, 90, 5, key="pl_pit")
    min_stint_min = c4.number_input("Minimo pilota (min)", 0, 240, 10, 1, key="pl_min")
    pit_min = c5.number_input(
        "Pit minimo obbligatorio", 0, 50,
        int(gara.get("pit_minimo") or 0) if gara else 0, 1, key=f"pl_pitmin_{gid}",
        help="Numero minimo di soste obbligatorie previste dal regolamento.",
    )

    roster_default = "\n".join(gara.get("piloti", [])) if gara and gara.get("piloti") else "Pilota 1\nPilota 2\nPilota 3"
    roster_text = st.text_area(
        "Piloti (uno per riga, ruotati in ordine sugli stint)",
        value=roster_default,
        height=110,
        key=f"pl_roster_{gid}",
    )
    roster = [r.strip() for r in roster_text.splitlines() if r.strip()] or ["Pilota 1"]

    race_min = race_hours * 60
    stints = build_stints(int(race_min), int(stint_min), int(pit_sec))
    if not stints:
        st.info("Imposta una durata valida.")
        return

    rows = []
    for s in stints:
        driver = roster[(s["stint"] - 1) % len(roster)]
        rows.append(
            {
                "Stint": s["stint"],
                "Pilota": driver,
                "Inizio (min)": round(s["inizio"], 1),
                "Fine (min)": round(s["fine"], 1),
                "Durata (min)": round(s["durata"], 1),
                "Sosta dopo": "Sì" if s["stint"] < len(stints) else "—",
                "Note": "",
                "_ok": s["durata"] >= min_stint_min,
            }
        )
    plan = pd.DataFrame(rows)

    planned_pits = len(stints) - 1
    if planned_pits >= pit_min:
        pit_status = "✅ Rispettato"
        pit_note = f"{planned_pits} previsti su {pit_min} obbligatori"
    else:
        pit_status = "⚠️ Non rispettato"
        pit_note = f"{planned_pits} previsti, ne servono {pit_min}"
    kpi_row(
        [
            ("Stint totali", str(len(stints)), f"{planned_pits} soste"),
            ("Pit obbligatori", pit_status, pit_note),
            ("Tempo sosta totale", f"{planned_pits * pit_sec / 60:.1f} min", "somma delle soste"),
            ("Pilota con più tempo", plan.groupby("Pilota")["Durata (min)"].sum().idxmax(),
             f"{plan.groupby('Pilota')['Durata (min)'].sum().max():.0f} min totali"),
        ]
    )

    if pit_min > 0 and planned_pits < pit_min:
        # Stint massimo che garantisce almeno pit_min soste.
        stints_needed = pit_min + 1
        max_stint = (race_min - pit_min * pit_sec / 60) / stints_needed
        st.warning(
            f"Per fare almeno {pit_min} pit servono {stints_needed} stint: "
            f"la durata massima di ciascuno è {max_stint:.1f} min."
        )

    if gara and on_save:
        piano_salvato = {
            "ore": float(race_hours),
            "stint_min": float(stint_min),
            "pit_sec": float(pit_sec),
            "min_stint": float(min_stint_min),
            "pit_minimo": int(pit_min),
            "piloti": roster,
        }
        st.button(
            "📌 Usa questo piano per il muretto",
            type="primary",
            key=f"pl_usa_{gid}",
            on_click=on_save,
            args=(gid, piano_salvato),
            help="Il muretto mostrerà chi è in pista e il tempo al prossimo cambio.",
        )

    fig = go.Figure()
    for i, driver in enumerate(roster):
        sub = plan[plan["Pilota"] == driver]
        fig.add_trace(
            go.Bar(
                x=sub["Durata (min)"],
                y=[driver] * len(sub),
                base=sub["Inizio (min)"],
                orientation="h",
                name=driver,
                marker=dict(color=PALETTE[i % len(PALETTE)], line=dict(width=0)),
                text=[f"S{n}" for n in sub["Stint"]],
                textposition="inside",
                hovertemplate="%{y}<br>%{base:.0f}–%{x:.0f} min<extra></extra>",
            )
        )
    fig.update_layout(barmode="overlay", title="Gantt piloti")
    fig.update_xaxes(title="Minuti di gara", range=[0, race_min])
    st.plotly_chart(plot_layout(fig, 90 + 46 * len(roster)), use_container_width=True)

    st.markdown("**Piano stint** — modifica pilota e note direttamente in tabella")
    edited = st.data_editor(
        plan.drop(columns=["_ok"]),
        hide_index=True,
        use_container_width=True,
        disabled=["Stint", "Inizio (min)", "Fine (min)", "Durata (min)", "Sosta dopo"],
        column_config={
            "Pilota": st.column_config.SelectboxColumn("Pilota", options=roster, required=True),
            "Note": st.column_config.TextColumn("Note", width="large"),
        },
        key=f"pl_editor_{gid}",
    )
    short = plan.loc[plan["_ok"] == False, "Stint"].tolist()  # noqa: E712
    if short:
        st.warning(f"Stint sotto il minimo pilota: {', '.join(map(str, short))}.")

    per_driver = (
        edited.groupby("Pilota")["Durata (min)"].sum().reset_index()
        .rename(columns={"Durata (min)": "Minuti al volante"})
    )
    per_driver["Ore"] = (per_driver["Minuti al volante"] / 60).round(2)
    fig2 = go.Figure(
        go.Bar(
            x=per_driver["Pilota"],
            y=per_driver["Minuti al volante"],
            marker=dict(color=[PALETTE[i % len(PALETTE)] for i in range(len(per_driver))]),
            text=per_driver["Minuti al volante"].round(0),
            textposition="outside",
        )
    )
    fig2.update_layout(title="Minuti al volante per pilota")
    c_left, c_right = st.columns([3, 2])
    c_left.plotly_chart(plot_layout(fig2, 320), use_container_width=True)
    c_right.dataframe(per_driver, hide_index=True, use_container_width=True)

    csv = edited.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Scarica piano stint (CSV)",
        data=csv,
        file_name=f"piano-stint-{datetime.now():%Y%m%d-%H%M}.csv",
        mime="text/csv",
    )


# --------------------------------------------------------------------------- #
# 2. Tabelle dati giri (fogli vuoti da compilare)
# --------------------------------------------------------------------------- #
def render_lap_sheet() -> None:
    section_header(
        "Dati",
        "Foglio giri",
        "Foglio vuoto: inserisci i giri a mano (o incolla da un foglio di calcolo). "
        "Il grafico e le statistiche si aggiornano da soli.",
    )
    if "lap_sheet" not in st.session_state:
        st.session_state["lap_sheet"] = pd.DataFrame(
            {
                "Giro": pd.Series(dtype="Int64"),
                "Pilota": pd.Series(dtype="string"),
                "Tempo (s)": pd.Series(dtype="float"),
                "Settore 1": pd.Series(dtype="float"),
                "Settore 2": pd.Series(dtype="float"),
                "Settore 3": pd.Series(dtype="float"),
                "Pit": pd.Series(dtype="bool"),
                "Note": pd.Series(dtype="string"),
            }
        )
    sheet = st.data_editor(
        st.session_state["lap_sheet"],
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        key="lap_editor",
        column_config={
            "Tempo (s)": st.column_config.NumberColumn("Tempo (s)", format="%.3f", min_value=0),
            "Settore 1": st.column_config.NumberColumn(format="%.3f"),
            "Settore 2": st.column_config.NumberColumn(format="%.3f"),
            "Settore 3": st.column_config.NumberColumn(format="%.3f"),
        },
    )
    st.session_state["lap_sheet"] = sheet

    valid = sheet.dropna(subset=["Tempo (s)"])
    valid = valid[valid["Tempo (s)"] > 0]
    if valid.empty:
        st.info("Inserisci almeno un tempo giro per vedere grafico e statistiche.")
        return

    best = valid["Tempo (s)"].min()
    avg = valid["Tempo (s)"].mean()
    consistency = valid["Tempo (s)"].std(ddof=0) if len(valid) > 1 else 0.0
    kpi_row(
        [
            ("Giri registrati", str(len(valid)), f"{int(valid['Pit'].fillna(False).sum())} con pit"),
            ("Miglior giro", f"{best:.3f} s", "minimo"),
            ("Media", f"{avg:.3f} s", "tutti i giri validi"),
            ("Costanza", f"±{consistency:.3f} s", "deviazione standard"),
        ]
    )

    x = valid["Giro"].fillna(pd.Series(range(1, len(valid) + 1), index=valid.index))
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=x, y=valid["Tempo (s)"], mode="lines+markers", name="Tempo giro",
            line=dict(color="#72b7ff", width=2.5), marker=dict(size=7),
        )
    )
    fig.add_hline(y=best, line=dict(color="#70e0a4", dash="dot"), annotation_text="best",
                  annotation_font_color="#70e0a4")
    fig.add_hline(y=avg, line=dict(color="#f4c542", dash="dash"), annotation_text="media",
                  annotation_font_color="#f4c542")
    fig.update_layout(title="Andamento tempi giro")
    fig.update_xaxes(title="Giro")
    fig.update_yaxes(title="Secondi")
    st.plotly_chart(plot_layout(fig, 380), use_container_width=True)

    if valid["Pilota"].notna().any():
        by = valid.groupby("Pilota", dropna=True)["Tempo (s)"].agg(["min", "mean", "count"]).reset_index()
        by.columns = ["Pilota", "Best (s)", "Media (s)", "Giri"]
        st.dataframe(by.round(3), hide_index=True, use_container_width=True)

    st.download_button(
        "Scarica foglio giri (CSV)",
        data=sheet.to_csv(index=False).encode("utf-8"),
        file_name=f"giri-{datetime.now():%Y%m%d-%H%M}.csv",
        mime="text/csv",
    )


# --------------------------------------------------------------------------- #
# 3. Lavagne disegnabili (fogli bianchi)
# --------------------------------------------------------------------------- #
def canvas_board(key: str, title: str, grid: bool, height: int = 560) -> None:
    """Foglio disegnabile con penna, gomma, forme, testo, zoom e salvataggio locale."""
    key_js = json.dumps(f"pitwall-board-{key}")
    grid_js = "true" if grid else "false"
    html_doc = f"""
<!doctype html><html lang="it"><head><meta charset="utf-8">
<style>
 * {{ box-sizing: border-box; }}
 body {{ margin:0; background:#0e1117; color:#e8edf5; font:13px/1.4 system-ui,sans-serif; }}
 .bar {{ display:flex; flex-wrap:wrap; gap:6px; align-items:center; padding:8px; background:#171c24;
        border:1px solid #2a3545; border-radius:10px 10px 0 0; }}
 button, select, input {{ border:1px solid #38475b; border-radius:7px; padding:6px 9px; background:#1d2633;
        color:#e8edf5; font-weight:600; cursor:pointer; }}
 button:hover {{ border-color:#72b7ff; }}
 button.on {{ background:#264a73; border-color:#72b7ff; }}
 input[type=color] {{ width:38px; height:32px; padding:2px; }}
 input[type=range] {{ padding:0; width:90px; }}
 .sep {{ width:1px; height:24px; background:#2a3545; margin:0 4px; }}
 .st {{ margin-left:auto; color:#70e0a4; font-size:12px; }}
 .wrap {{ position:relative; overflow:hidden; border:1px solid #2a3545; border-top:0; border-radius:0 0 10px 10px;
        background:#fbfcff; height:{height - 60}px; }}
 canvas {{ width:100%; height:100%; display:block; touch-action:none; cursor:crosshair; }}
 .hint {{ color:#9aa8ba; font-size:11px; padding:6px 2px 0; }}
</style></head><body>
<div class="bar">
  <strong style="padding:0 4px">{title}</strong><span class="sep"></span>
  <button id="t-pen" class="on" data-t="pen">✎ Penna</button>
  <button id="t-line" data-t="line">╱ Linea</button>
  <button id="t-rect" data-t="rect">▭ Rettangolo</button>
  <button id="t-circ" data-t="circ">◯ Cerchio</button>
  <button id="t-arrow" data-t="arrow">➜ Freccia</button>
  <button id="t-text" data-t="text">T Testo</button>
  <button id="t-erase" data-t="erase">⌫ Gomma</button>
  <span class="sep"></span>
  <input id="col" type="color" value="#142b4a" title="Colore">
  <input id="size" type="range" min="1" max="24" value="3" title="Spessore">
  <span class="sep"></span>
  <button id="undo">↶ Annulla</button>
  <button id="redo">↷ Ripeti</button>
  <button id="clear">Pulisci</button>
  <button id="png">Esporta PNG</button>
  <span id="st" class="st">Pronto</span>
</div>
<div class="wrap"><canvas id="c"></canvas></div>
<div class="hint">Trascina per disegnare · Doppio clic per scrivere testo · Il foglio si salva automaticamente in questo browser.</div>
<script>
(function() {{
  const KEY = {key_js};
  const GRID = {grid_js};
  const cv = document.getElementById('c');
  const ctx = cv.getContext('2d');
  const stat = document.getElementById('st');
  let W = 1600, H = 900, dpr = 1;
  let items = [];      // stroke / shape / text objects
  let redoStack = [];
  let cur = null, tool = 'pen', start = null;

  function safe(fn) {{ try {{ return fn(); }} catch (e) {{ return null; }} }}
  function load() {{
    const raw = safe(() => localStorage.getItem(KEY));
    if (raw) {{ const d = safe(() => JSON.parse(raw)); if (Array.isArray(d)) items = d; }}
  }}
  function save() {{
    safe(() => localStorage.setItem(KEY, JSON.stringify(items)));
    stat.textContent = 'Salvato ' + new Date().toLocaleTimeString('it-IT', {{hour:'2-digit', minute:'2-digit'}});
  }}
  function resize() {{
    const r = cv.getBoundingClientRect();
    dpr = window.devicePixelRatio || 1;
    cv.width = Math.max(300, r.width * dpr);
    cv.height = Math.max(200, r.height * dpr);
    W = cv.width / dpr; H = cv.height / dpr;
    draw();
  }}
  function drawGrid() {{
    if (!GRID) return;
    ctx.save(); ctx.strokeStyle = 'rgba(70,110,160,0.18)'; ctx.lineWidth = 1;
    const step = 40;
    for (let x = 0; x <= W; x += step) {{ ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke(); }}
    for (let y = 0; y <= H; y += step) {{ ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke(); }}
    ctx.restore();
  }}
  function drawItem(it) {{
    ctx.save();
    ctx.strokeStyle = it.c; ctx.fillStyle = it.c; ctx.lineWidth = it.w;
    ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    if (it.t === 'pen' || it.t === 'erase') {{
      if (it.t === 'erase') {{ ctx.globalCompositeOperation = 'destination-out'; ctx.strokeStyle = '#000'; }}
      ctx.beginPath();
      it.p.forEach((q, i) => i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1]));
      ctx.stroke();
    }} else if (it.t === 'line') {{
      ctx.beginPath(); ctx.moveTo(it.a[0], it.a[1]); ctx.lineTo(it.b[0], it.b[1]); ctx.stroke();
    }} else if (it.t === 'arrow') {{
      const [x1,y1] = it.a, [x2,y2] = it.b, ang = Math.atan2(y2-y1, x2-x1), hd = 12 + it.w*2;
      ctx.beginPath(); ctx.moveTo(x1,y1); ctx.lineTo(x2,y2); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(x2,y2);
      ctx.lineTo(x2 - hd*Math.cos(ang-0.4), y2 - hd*Math.sin(ang-0.4));
      ctx.lineTo(x2 - hd*Math.cos(ang+0.4), y2 - hd*Math.sin(ang+0.4));
      ctx.closePath(); ctx.fill();
    }} else if (it.t === 'rect') {{
      ctx.strokeRect(it.a[0], it.a[1], it.b[0]-it.a[0], it.b[1]-it.a[1]);
    }} else if (it.t === 'circ') {{
      const rx = Math.abs(it.b[0]-it.a[0])/2, ry = Math.abs(it.b[1]-it.a[1])/2;
      ctx.beginPath(); ctx.ellipse((it.a[0]+it.b[0])/2, (it.a[1]+it.b[1])/2, Math.max(rx,1), Math.max(ry,1), 0, 0, Math.PI*2); ctx.stroke();
    }} else if (it.t === 'text') {{
      ctx.font = (14 + it.w*2) + 'px system-ui, sans-serif'; ctx.textBaseline = 'top';
      ctx.fillText(it.s, it.a[0], it.a[1]);
    }}
    ctx.restore();
  }}
  function draw() {{
    ctx.setTransform(dpr,0,0,dpr,0,0);
    ctx.clearRect(0,0,W,H);
    ctx.fillStyle = '#fbfcff'; ctx.fillRect(0,0,W,H);
    drawGrid();
    items.forEach(drawItem);
    if (cur) drawItem(cur);
  }}
  function pos(e) {{
    const r = cv.getBoundingClientRect();
    const p = e.touches ? e.touches[0] : e;
    return [p.clientX - r.left, p.clientY - r.top];
  }}
  function down(e) {{
    if (tool === 'text') return;
    e.preventDefault();
    const p = pos(e); start = p;
    if (tool === 'pen' || tool === 'erase') cur = {{t: tool, p: [p], c: colorVal(), w: sizeVal()}};
    else cur = {{t: tool, a: p, b: p, c: colorVal(), w: sizeVal()}};
    draw();
  }}
  function move(e) {{
    if (!cur) return;
    e.preventDefault();
    const p = pos(e);
    if (cur.p) cur.p.push(p); else cur.b = p;
    draw();
  }}
  function up() {{
    if (!cur) return;
    items.push(cur); redoStack = []; cur = null; start = null; save(); draw();
  }}
  function colorVal() {{ return document.getElementById('col').value; }}
  function sizeVal() {{ return +document.getElementById('size').value; }}

  cv.addEventListener('mousedown', down);
  cv.addEventListener('mousemove', move);
  window.addEventListener('mouseup', up);
  cv.addEventListener('touchstart', down, {{passive:false}});
  cv.addEventListener('touchmove', move, {{passive:false}});
  cv.addEventListener('touchend', up);
  cv.addEventListener('dblclick', e => {{
    const p = pos(e);
    const s = prompt('Testo da inserire:');
    if (s) {{ items.push({{t:'text', a:p, s:s, c:colorVal(), w:sizeVal()}}); redoStack = []; save(); draw(); }}
  }});

  document.querySelectorAll('[data-t]').forEach(b => b.addEventListener('click', () => {{
    tool = b.dataset.t;
    document.querySelectorAll('[data-t]').forEach(x => x.classList.toggle('on', x === b));
    cv.style.cursor = tool === 'text' ? 'text' : (tool === 'erase' ? 'cell' : 'crosshair');
  }}));
  document.getElementById('undo').onclick = () => {{ if (items.length) {{ redoStack.push(items.pop()); save(); draw(); }} }};
  document.getElementById('redo').onclick = () => {{ if (redoStack.length) {{ items.push(redoStack.pop()); save(); draw(); }} }};
  document.getElementById('clear').onclick = () => {{
    if (confirm('Cancellare tutto il foglio?')) {{ items = []; redoStack = []; save(); draw(); }}
  }};
  document.getElementById('png').onclick = () => {{
    const a = document.createElement('a');
    a.download = 'foglio-' + Date.now() + '.png';
    a.href = cv.toDataURL('image/png'); a.click();
  }};
  window.addEventListener('resize', resize);
  load(); resize();
  stat.textContent = items.length ? 'Caricato' : 'Foglio vuoto';
}})();
</script></body></html>
"""
    components.html(html_doc, height=height, scrolling=False)


def render_boards() -> None:
    section_header(
        "Fogli",
        "Lavagne e fogli vuoti",
        "Disegna forme, frecce e note. Ogni foglio resta salvato nel browser.",
    )
    b1, b2 = st.tabs(["🗒️ Lavagna strategia", "🗺️ Schizzo pista"])
    with b1:
        canvas_board("strategy", "Lavagna strategia", grid=True)
    with b2:
        canvas_board("track", "Schizzo pista / box", grid=False)


# --------------------------------------------------------------------------- #
# Stile
# --------------------------------------------------------------------------- #
PRO_CSS = """
<style>
.pw-section { margin: 0.4rem 0 0.9rem; }
.pw-title { font-size: 1.35rem; font-weight: 750; letter-spacing: -0.01em; color: #f5f7fa; }
.pw-kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 0.7rem; margin: 0.4rem 0 1rem; }
.pw-kpi {
  padding: 0.85rem 1rem; border-radius: 0.8rem; border: 1px solid rgba(86,156,255,0.22);
  background: linear-gradient(160deg, rgba(30,58,95,0.35), rgba(18,24,34,0.6));
  box-shadow: 0 6px 18px rgba(0,0,0,0.18);
}
.pw-kpi-label { color: #9aa4b2; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 700; }
.pw-kpi-value { color: #f4c542; font-size: 1.45rem; font-weight: 750; margin: 0.15rem 0; }
.pw-kpi-note { color: #7d8a9c; font-size: 0.78rem; }
.stTabs [data-baseweb="tab-list"] { gap: 0.3rem; }
.stTabs [data-baseweb="tab"] { border-radius: 0.6rem 0.6rem 0 0; padding: 0.5rem 0.9rem; }
</style>
"""


def inject_style() -> None:
    st.markdown(PRO_CSS, unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# 4. Classifica viva: sorpassi, best lap, colori e animazioni
# --------------------------------------------------------------------------- #
import html as _html  # noqa: E402
import re as _re  # noqa: E402


def lap_to_seconds(value: str | None) -> float | None:
    """Accetta '36.604' oppure '1:02.345'."""
    text = (value or "").strip().replace(",", ".")
    if not text:
        return None
    match = _re.fullmatch(r"(?:(\d+):)?(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    minutes = int(match.group(1) or 0)
    return minutes * 60 + float(match.group(2))


def track_field(rows: list[dict], add_event) -> dict:
    """Confronta la classifica con l'ultimo aggiornamento.

    Registra sorpassi, pit, nuovi best lap (di sessione e personali) e
    restituisce i flash da animare e il trend dell'ultimo giro (up/down).
    """
    prev = st.session_state.get("_field_prev", {})
    personal = st.session_state.setdefault("_field_personal", {})
    trends = st.session_state.setdefault("_field_trend", {})
    session_best = st.session_state.get("_field_session_best")
    flashes: dict[str, set[str]] = {}
    current: dict[str, dict] = {}

    for row in rows:
        kart = row["kart"]
        current[kart] = row
        old = prev.get(kart)
        changed: set[str] = set()
        if old is None:
            # Primo aggiornamento del kart: solo inizializzazione, nessun evento.
            blp = lap_to_seconds(row["best_lap"])
            if blp is not None:
                personal[kart] = blp
                if session_best is None or blp < session_best:
                    session_best = blp
            continue

        if row["position"] is not None and old["position"] is not None:
            if row["position"] != old["position"]:
                changed.add("position")
            if row["position"] < old["position"]:
                add_event(
                    f"🔀 Sorpasso · Kart {kart} {row['team']} "
                    f"P{old['position']} → P{row['position']}"
                )

        if row["pits"] is not None and old["pits"] is not None and row["pits"] > old["pits"]:
            add_event(f"🛠️ Pit · Kart {kart} {row['team']} (totale {row['pits']})")

        if row["last_lap"] and row["last_lap"] != old["last_lap"]:
            changed.add("last_lap")
            new_lap = lap_to_seconds(row["last_lap"])
            old_lap = lap_to_seconds(old["last_lap"])
            if new_lap is not None and old_lap is not None:
                trends[kart] = "down" if new_lap < old_lap else ("up" if new_lap > old_lap else trends.get(kart))

        blp = lap_to_seconds(row["best_lap"])
        if blp is not None:
            known = personal.get(kart)
            if known is None or blp < known:
                personal[kart] = blp
                if session_best is None or blp < session_best:
                    session_best = blp
                    add_event(f"🟣 Miglior giro di sessione · Kart {kart} {row['team']} · {row['best_lap']} s")
                elif known is not None:
                    add_event(f"🟢 Miglior giro personale · Kart {kart} {row['team']} · {row['best_lap']} s")

        if changed:
            flashes[kart] = changed

    st.session_state["_field_prev"] = current
    st.session_state["_field_session_best"] = session_best
    return {"flashes": flashes, "trends": trends, "session_best": session_best}


PRO_TABLE_CSS = """
<style>
.pw-table-wrap { overflow-x: auto; border: 1px solid rgba(128,128,128,0.28); border-radius: 0.7rem; }
.pw-table { width: 100%; border-collapse: collapse; font-size: 0.92rem; color: #e8edf5; }
.pw-table th { text-align: left; padding: 0.55rem 0.7rem; font-size: 0.7rem; letter-spacing: 0.08em;
  text-transform: uppercase; color: #9aa4b2; background: rgba(128,128,128,0.10); }
.pw-table td { padding: 0.5rem 0.7rem; border-top: 1px solid rgba(128,128,128,0.14); white-space: nowrap; }
.pw-table tr:hover td { background: rgba(114,183,255,0.08); }
.pw-lap-purple { color: #b26bff; font-weight: 800; }
.pw-lap-green { color: #70e0a4; font-weight: 700; }
.pw-lap-yellow { color: #f4c542; font-weight: 700; }
.pw-kart { display: inline-block; min-width: 2.2rem; text-align: center; padding: 0.1rem 0.45rem;
  border-radius: 0.4rem; background: #1e3a5f; color: #f5f7fa; font-weight: 700; }
.pw-flash-pos td.pw-pos { animation: pwFlashPos 1.1s ease-out; }
.pw-flash-lap td.pw-lap { animation: pwFlashLap 1.1s ease-out; }
@keyframes pwFlashPos { 0% { background: rgba(244,197,66,0.45); } 100% { background: transparent; } }
@keyframes pwFlashLap { 0% { background: rgba(112,224,164,0.40); transform: scale(1.04); } 100% { background: transparent; transform: scale(1); } }
.pw-leader td { background: rgba(178,107,255,0.07); }
</style>
"""


def standings_html(rows: list[dict], state: dict) -> str:
    flashes: dict[str, set[str]] = state.get("flashes", {})
    trends: dict[str, str] = state.get("trends", {})
    session_best = state.get("session_best")
    body = []
    for row in rows:
        kart = row["kart"]
        flash = flashes.get(kart, set())
        row_class = " ".join(
            c for c in (
                "pw-leader" if row["position"] == 1 else "",
                "pw-flash-pos" if "position" in flash else "",
                "pw-flash-lap" if "last_lap" in flash else "",
            ) if c
        )
        lap_secs = lap_to_seconds(row["last_lap"])
        if lap_secs is not None and session_best is not None and lap_secs <= session_best + 1e-9:
            lap_class = "pw-lap-purple"
        elif trends.get(kart) == "down":
            lap_class = "pw-lap-green"
        elif trends.get(kart) == "up":
            lap_class = "pw-lap-yellow"
        else:
            lap_class = ""
        gap = "Leader" if row["position"] == 1 else (row["gap"] or "—")
        body.append(
            f'<tr class="{row_class}">'
            f'<td class="pw-pos"><strong>{_html.escape(str(row["position"] or "—"))}</strong></td>'
            f'<td><span class="pw-kart">{_html.escape(row["kart"] or "—")}</span></td>'
            f'<td>{_html.escape(row["team"])}</td>'
            f'<td class="pw-lap"><span class="{lap_class}">{_html.escape(row["last_lap"] or "—")}</span></td>'
            f'<td>{_html.escape(gap)}</td>'
            f'<td>{_html.escape(row["best_lap"] or "—")}</td>'
            f'<td>{_html.escape(str(row["laps_completed"] if row["laps_completed"] is not None else "—"))}</td>'
            f'<td>{_html.escape(str(row["pits"] if row["pits"] is not None else "—"))}</td>'
            f'<td>{_html.escape(row["on_track"] or "—")}</td>'
            "</tr>"
        )
    head = (
        "<tr><th>Pos</th><th>Kart</th><th>Pilota / team</th><th>Ultimo giro</th>"
        "<th>Distacco</th><th>Miglior giro</th><th>Giri</th><th>Pit</th><th>In pista</th></tr>"
    )
    return (
        PRO_TABLE_CSS
        + '<div class="pw-table-wrap"><table class="pw-table">'
        + f"<thead>{head}</thead><tbody>{''.join(body)}</tbody></table></div>"
        + '<div class="pitwall-note" style="margin-top:0.4rem">'
        "🟣 miglior giro di sessione · 🟢 ultimo giro migliorato · 🟡 ultimo giro peggiorato"
        "</div>"
    )
