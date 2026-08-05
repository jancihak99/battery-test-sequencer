"""Classic battery-test HTML/JSON report with embedded SVG charts (offline)."""
from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class TracePoint:
    t_s: float
    pack_v: float | None = None
    pack_i: float | None = None
    ea_v: float | None = None
    ea_i: float | None = None
    soc: float | None = None
    t_max_c: float | None = None
    ah: float | None = None


@dataclass
class StepTrace:
    step_id: str
    step_type: str
    points: list[TracePoint] = field(default_factory=list)
    capacity_ah: float | None = None
    capacity_key: str | None = None
    dcir_mohm: float | None = None
    note: str = ""


def _f(v: Any, default: float | None = None) -> float | None:
    if v is None or v == "":
        return default
    try:
        x = float(v)
        if x != x:  # NaN
            return default
        return x
    except (TypeError, ValueError):
        return default


def downsample(points: list[TracePoint], max_n: int = 900) -> list[TracePoint]:
    if len(points) <= max_n:
        return points
    step = len(points) / max_n
    out: list[TracePoint] = []
    i = 0.0
    while int(i) < len(points) and len(out) < max_n:
        out.append(points[int(i)])
        i += step
    if out[-1] is not points[-1]:
        out.append(points[-1])
    return out


def _svg_polyline(
    xs: list[float],
    ys: list[float],
    *,
    title: str,
    y_label: str,
    color: str,
    width: int = 760,
    height: int = 240,
) -> str:
    if not xs or not ys or len(xs) != len(ys):
        return f"<p class='muted'>{html.escape(title)}: žádná data</p>"
    pad_l, pad_r, pad_t, pad_b = 54, 18, 28, 36
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    if abs(xmax - xmin) < 1e-9:
        xmax = xmin + 1.0
    # Pad Y a bit
    ypad = (ymax - ymin) * 0.08 if ymax > ymin else max(0.1, abs(ymax) * 0.05 or 0.1)
    ymin -= ypad
    ymax += ypad
    if abs(ymax - ymin) < 1e-9:
        ymax = ymin + 1.0

    def sx(x: float) -> float:
        return pad_l + (x - xmin) / (xmax - xmin) * plot_w

    def sy(y: float) -> float:
        return pad_t + (1.0 - (y - ymin) / (ymax - ymin)) * plot_h

    pts = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in zip(xs, ys))
    # Grid / axes
    y_ticks = [ymin + (ymax - ymin) * i / 4 for i in range(5)]
    x_ticks = [xmin + (xmax - xmin) * i / 4 for i in range(5)]
    grid = []
    for yt in y_ticks:
        yy = sy(yt)
        grid.append(
            f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{width-pad_r}" y2="{yy:.1f}" '
            f'stroke="#e6ebf0" stroke-width="1"/>'
            f'<text x="{pad_l-6}" y="{yy+4:.1f}" text-anchor="end" class="tick">{yt:.2f}</text>'
        )
    for xt in x_ticks:
        xx = sx(xt)
        grid.append(
            f'<line x1="{xx:.1f}" y1="{pad_t}" x2="{xx:.1f}" y2="{height-pad_b}" '
            f'stroke="#eef2f5" stroke-width="1"/>'
            f'<text x="{xx:.1f}" y="{height-12}" text-anchor="middle" class="tick">{xt:.0f}s</text>'
        )
    return f"""
<figure class="chart">
  <figcaption>{html.escape(title)}</figcaption>
  <svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="{html.escape(title)}">
    <rect x="0" y="0" width="{width}" height="{height}" fill="#fafbfc" rx="8"/>
    {''.join(grid)}
    <polyline fill="none" stroke="{color}" stroke-width="2.2" stroke-linejoin="round"
              stroke-linecap="round" points="{pts}"/>
    <text x="12" y="18" class="ylabel">{html.escape(y_label)}</text>
  </svg>
</figure>
"""


def traces_from_csv_rows(rows: list[dict[str, Any]], step_types: dict[str, str] | None = None) -> list[StepTrace]:
    """Group recorded CSV rows into per-step traces (relative t_s per step)."""
    step_types = step_types or {}
    by_id: dict[str, StepTrace] = {}
    step_t0: dict[str, float | None] = {}
    for row in rows:
        sid = str(row.get("step") or "step")
        if sid not in by_id:
            by_id[sid] = StepTrace(step_id=sid, step_type=step_types.get(sid, ""))
            step_t0[sid] = None
        # Prefer explicit t_s; else parse ISO and use run-relative later
        t_s = _f(row.get("t_s"))
        if t_s is None:
            # fallback: sequential index
            t_s = float(len(by_id[sid].points))
        if step_t0[sid] is None:
            step_t0[sid] = t_s
        rel = max(0.0, t_s - float(step_t0[sid] or 0.0))
        # Prefer EA for power path; fall back to pack
        ea_v = _f(row.get("ea_v"))
        if ea_v is None:
            ea_v = _f(row.get("psi_v") if abs(_f(row.get("psi_i") or 0) or 0) >= abs(_f(row.get("el_i") or 0) or 0) else row.get("el_v"))
        ea_i = _f(row.get("ea_i"))
        if ea_i is None:
            psi_i = abs(_f(row.get("psi_i") or 0) or 0)
            el_i = abs(_f(row.get("el_i") or 0) or 0)
            ea_i = psi_i if psi_i >= el_i else el_i
            if ea_i == 0:
                ea_i = abs(_f(row.get("pack_i") or 0) or 0)
        by_id[sid].points.append(
            TracePoint(
                t_s=rel,
                pack_v=_f(row.get("pack_v")),
                pack_i=_f(row.get("pack_i")),
                ea_v=ea_v if ea_v else _f(row.get("pack_v")),
                ea_i=ea_i,
                soc=_f(row.get("soc")),
                t_max_c=_f(row.get("t_max")),
                ah=_f(row.get("ah")),
            )
        )
    return list(by_id.values())


def build_report_payload(
    *,
    program: str,
    profile: str,
    serial: str,
    started_at: str | None,
    finished_at: str | None,
    measurements: dict[str, Any],
    extras: dict[str, Any],
    include: list[str],
    traces: list[StepTrace],
    csv_name: str | None = None,
    activity_name: str | None = None,
    cells: list[float] | None = None,
    temps: list[float] | None = None,
    dtc_level: int | None = None,
    active_dtcs: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "program": program,
        "profile": profile,
        "serial": serial,
        "started_at": started_at,
        "finished_at": finished_at,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "results": {},
        "traces": [],
        "files": {"csv": csv_name, "activity": activity_name},
    }
    results = payload["results"]
    for key in include:
        if key in extras and extras[key] is not None:
            results[key] = extras[key]
        elif key == "capacity_ah" and measurements.get("capacity_ah") is not None:
            results["capacity_ah"] = measurements["capacity_ah"]
        elif key == "dcir_mohm" and measurements.get("dcir_mohm") is not None:
            results["dcir_mohm"] = measurements["dcir_mohm"]
    # Always surface known capacity_* / dcir in results even if not in include
    for k, v in extras.items():
        if (k.startswith("capacity_") or k == "dcir_mohm") and v is not None:
            results.setdefault(k, v)
    if measurements.get("capacity_ah") is not None:
        results.setdefault("capacity_ah", measurements["capacity_ah"])
    if measurements.get("dcir_mohm") is not None:
        results.setdefault("dcir_mohm", measurements["dcir_mohm"])

    if cells is not None:
        payload["cell_voltages"] = cells
    if temps is not None:
        payload["temperatures_c"] = temps
    if dtc_level is not None:
        payload["dtc_level"] = dtc_level
    if active_dtcs is not None:
        payload["active_dtcs"] = active_dtcs

    for tr in traces:
        pts = downsample(tr.points)
        payload["traces"].append(
            {
                "step_id": tr.step_id,
                "step_type": tr.step_type,
                "n_points": len(tr.points),
                "duration_s": pts[-1].t_s if pts else 0.0,
                "capacity_ah": tr.capacity_ah,
                "capacity_key": tr.capacity_key,
                "dcir_mohm": tr.dcir_mohm,
                "samples": [
                    {
                        "t_s": p.t_s,
                        "v": p.ea_v if p.ea_v is not None else p.pack_v,
                        "i": p.ea_i if p.ea_i is not None else (abs(p.pack_i) if p.pack_i is not None else None),
                        "soc": p.soc,
                        "t_max_c": p.t_max_c,
                        "ah": p.ah,
                    }
                    for p in pts
                ],
            }
        )
    return payload


def render_html(payload: dict[str, Any]) -> str:
    results = payload.get("results") or {}
    cards = []
    labels = {
        "capacity_ah": "Kapacita",
        "capacity_1c_ah": "Kapacita 1C",
        "capacity_3c_ah": "Kapacita 3C",
        "dcir_mohm": "DCIR",
    }
    units = {
        "capacity_ah": "Ah",
        "capacity_1c_ah": "Ah",
        "capacity_3c_ah": "Ah",
        "dcir_mohm": "mΩ",
    }
    for key, val in results.items():
        if val is None:
            continue
        label = labels.get(key, key)
        unit = units.get(key, "")
        if isinstance(val, float):
            txt = f"{val:.3f} {unit}".strip()
        else:
            txt = f"{val} {unit}".strip()
        cards.append(f'<div class="card"><div class="k">{html.escape(label)}</div><div class="v">{html.escape(txt)}</div></div>')

    charts = []
    for tr in payload.get("traces") or []:
        samples = tr.get("samples") or []
        xs = [float(s["t_s"]) for s in samples]
        vs = [float(s["v"]) for s in samples if s.get("v") is not None]
        xs_v = [float(s["t_s"]) for s in samples if s.get("v") is not None]
        i_s = [float(s["i"]) for s in samples if s.get("i") is not None]
        xs_i = [float(s["t_s"]) for s in samples if s.get("i") is not None]
        head = f"<h3>Krok {html.escape(tr['step_id'])} <span class='muted'>({html.escape(tr.get('step_type') or '')} · {tr.get('n_points', 0)} vzorků · {tr.get('duration_s', 0):.0f}s)</span></h3>"
        meta = []
        if tr.get("capacity_ah") is not None:
            meta.append(f"Kapacita ({tr.get('capacity_key') or 'Ah'}): <b>{tr['capacity_ah']:.3f} Ah</b>")
        if tr.get("dcir_mohm") is not None:
            meta.append(f"DCIR: <b>{tr['dcir_mohm']:.3f} mΩ</b>")
        meta_html = ("<p>" + " · ".join(meta) + "</p>") if meta else ""
        charts.append(head + meta_html)
        if xs_v and vs:
            charts.append(
                _svg_polyline(xs_v, vs, title=f"Napětí — {tr['step_id']}", y_label="V", color="#1f8a5b")
            )
        if xs_i and i_s:
            charts.append(
                _svg_polyline(xs_i, i_s, title=f"Proud — {tr['step_id']}", y_label="A", color="#c47a3a")
            )
        xs_soc = [float(s["t_s"]) for s in samples if s.get("soc") is not None]
        soc_s = [float(s["soc"]) for s in samples if s.get("soc") is not None]
        if xs_soc and soc_s:
            charts.append(
                _svg_polyline(xs_soc, soc_s, title=f"SOC — {tr['step_id']}", y_label="%", color="#008FC1")
            )

    cells = payload.get("cell_voltages") or []
    cell_rows = ""
    if cells:
        finite = [(i + 1, float(v)) for i, v in enumerate(cells) if isinstance(v, (int, float)) and v == v and v > 0.05]
        if finite:
            cell_rows = "<table class='grid'><tr><th>Článek</th><th>V</th></tr>" + "".join(
                f"<tr><td>{i}</td><td>{v:.4f}</td></tr>" for i, v in finite
            ) + "</table>"

    temps = payload.get("temperatures_c") or []
    temp_txt = ", ".join(f"{float(t):.1f}°C" for t in temps if isinstance(t, (int, float)) and t == t) if temps else "—"

    files = payload.get("files") or {}
    file_bits = []
    if files.get("csv"):
        file_bits.append(f"CSV: {html.escape(str(files['csv']))}")
    if files.get("activity"):
        file_bits.append(f"Activity: {html.escape(str(files['activity']))}")

    return f"""<!DOCTYPE html>
<html lang="cs"><head>
<meta charset="utf-8"/>
<title>BTS Report — {html.escape(str(payload.get('program') or ''))}</title>
<style>
  :root {{ --accent:#008FC1; --ok:#1f8a5b; --text:#1a222c; --dim:#667484; --border:#d5dde5; --bg:#f4f6f8; }}
  body {{ font-family:"Segoe UI",system-ui,sans-serif; margin:0; background:var(--bg); color:var(--text); }}
  header {{ background:#fff; border-bottom:1px solid var(--border); padding:1.25rem 2rem; }}
  header h1 {{ margin:0 0 .35rem; font-size:1.45rem; }}
  header .meta {{ color:var(--dim); font-size:.95rem; }}
  main {{ max-width:960px; margin:0 auto; padding:1.5rem 1.25rem 3rem; }}
  .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:.75rem; margin:1rem 0 1.5rem; }}
  .card {{ background:#fff; border:1px solid var(--border); border-radius:10px; padding:1rem 1.1rem; }}
  .card .k {{ color:var(--dim); font-size:.8rem; text-transform:uppercase; letter-spacing:.04em; }}
  .card .v {{ font-size:1.35rem; font-weight:700; margin-top:.25rem; }}
  h2 {{ font-size:1.15rem; margin:1.75rem 0 .75rem; }}
  h3 {{ font-size:1.02rem; margin:1.4rem 0 .4rem; }}
  .muted {{ color:var(--dim); font-weight:500; font-size:.9rem; }}
  .chart {{ background:#fff; border:1px solid var(--border); border-radius:10px; padding:.6rem .8rem 0; margin:.6rem 0 1rem; }}
  .chart figcaption {{ font-size:.85rem; color:var(--dim); margin:0 0 .2rem .2rem; }}
  .tick {{ font-size:10px; fill:var(--dim); }}
  .ylabel {{ font-size:11px; fill:var(--dim); }}
  table.grid {{ border-collapse:collapse; background:#fff; width:100%; max-width:320px; }}
  table.grid th, table.grid td {{ border:1px solid var(--border); padding:5px 10px; text-align:left; }}
  footer {{ color:var(--dim); font-size:.85rem; margin-top:2rem; }}
</style>
</head><body>
<header>
  <h1>{html.escape(str(payload.get('program') or 'BTS Report'))}</h1>
  <div class="meta">
    Profil: {html.escape(str(payload.get('profile') or '—'))}
    · Sériové č.: {html.escape(str(payload.get('serial') or '—'))}
    · Start: {html.escape(str(payload.get('started_at') or '—'))}
    · Konec: {html.escape(str(payload.get('finished_at') or '—'))}
  </div>
</header>
<main>
  <h2>Výsledky</h2>
  <div class="cards">{''.join(cards) if cards else '<p class="muted">Žádné naměřené výsledky (capacity / DCIR).</p>'}</div>

  <h2>Průběhy (V / I v čase)</h2>
  {''.join(charts) if charts else '<p class="muted">Žádné zaznamenané stopy — u kroků zapni „Zaznamenat stopu“.</p>'}

  <h2>Teploty / články / DTC</h2>
  <p>Teploty: {html.escape(temp_txt)}</p>
  <p>DTC level: {html.escape(str(payload.get('dtc_level') if payload.get('dtc_level') is not None else '—'))}
     · Active: {html.escape(', '.join(payload.get('active_dtcs') or []) or '—')}</p>
  {cell_rows}

  <footer>
    Vygenerováno {html.escape(str(payload.get('generated_at') or ''))}
    {' · ' + ' · '.join(file_bits) if file_bits else ''}
  </footer>
</main>
</body></html>
"""


def write_report_bundle(
    runs_dir: Path,
    base_name: str,
    payload: dict[str, Any],
) -> tuple[Path, Path]:
    runs_dir.mkdir(parents=True, exist_ok=True)
    json_path = runs_dir / f"{base_name}.json"
    html_path = runs_dir / f"{base_name}.html"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    html_path.write_text(render_html(payload), encoding="utf-8")
    return html_path, json_path
