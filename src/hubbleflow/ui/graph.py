"""A standalone HTML view of a knowledge bundle.

Terminal graph layout stops being readable somewhere around twenty nodes, and a
bundle is usually bigger than that, so the tree lives in the transcript and the
node-edge view goes to a file the browser can open. Everything is inlined --
no CDN, no fonts, nothing to fetch -- so the file works offline and keeps
working when the bundle it describes is on a machine with no network.
"""

from __future__ import annotations

import html
import math
from pathlib import Path

from hubbleflow.knowledge import Bundle
from hubbleflow.ui.theme import ACCENT, DANGER, FAINT, MUTED, SURFACE, WARN

_W, _H = 1400, 900
_R = 7


def write(bundle: Bundle, destination: Path) -> Path:
    """Render the bundle to a self-contained page and return its path."""
    nodes = _positions(bundle)
    destination.write_text(_page(bundle, nodes), encoding="utf-8")
    return destination


def _positions(bundle: Bundle) -> dict[str, tuple[float, float, str]]:
    """Group by type around a circle, spreading each group along its own arc.

    Type is the one field OKF guarantees, so it is the only grouping every
    bundle can be laid out by.
    """
    by_type: dict[str, list] = {}
    for concept in bundle.concepts:
        by_type.setdefault(concept.type or "untyped", []).append(concept)

    placed: dict[str, tuple[float, float, str]] = {}
    groups = sorted(by_type.items())
    for index, (type_name, concepts) in enumerate(groups):
        centre = 2 * math.pi * index / max(len(groups), 1)
        spread = min(2 * math.pi / max(len(groups), 1) * 0.8, 1.2)
        for j, concept in enumerate(concepts):
            offset = 0 if len(concepts) == 1 else (j / (len(concepts) - 1) - 0.5) * spread
            angle = centre + offset
            radius = 200 + (j % 5) * 55
            placed[concept.path] = (
                _W / 2 + radius * math.cos(angle),
                _H / 2 + radius * math.sin(angle) * 0.62,
                type_name,
            )
    return placed


def _page(bundle: Bundle, nodes: dict[str, tuple[float, float, str]]) -> str:
    edges = "".join(
        f'<line x1="{nodes[a][0]:.0f}" y1="{nodes[a][1]:.0f}" '
        f'x2="{nodes[b][0]:.0f}" y2="{nodes[b][1]:.0f}" class="edge"/>'
        for a, b in bundle.edges()
        if a in nodes and b in nodes
    )

    circles = []
    for concept in bundle.concepts:
        if concept.path not in nodes:
            continue
        x, y, _ = nodes[concept.path]
        suspect = concept.suspect()
        css = "stale" if concept.stale() else ("draft" if concept.status == "draft" else "")
        css = "deprecated" if concept.status == "deprecated" else css
        label = html.escape(concept.name)
        tip = html.escape(" · ".join(filter(None, (concept.path, concept.type, suspect))))
        circles.append(
            f'<g class="node {css}"><title>{tip}</title>'
            f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{_R}"/>'
            f'<text x="{x:.0f}" y="{y - 12:.0f}">{label}</text></g>'
        )

    legend = "".join(
        f'<li><span class="swatch" style="background:{colour}"></span>{name}</li>'
        for name, colour in (
            ("current", ACCENT), ("draft", WARN), ("stale", DANGER), ("deprecated", MUTED)
        )
    )
    types = "".join(
        f"<li><b>{html.escape(t)}</b><span>{n}</span></li>" for t, n in bundle.types().items()
    )
    return f"""<title>Knowledge graph</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; background:#10131A; color:#C9CDD6;
         font:14px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }}
  header {{ padding:20px 28px 8px; }}
  h1 {{ margin:0; font-size:15px; letter-spacing:.14em; text-transform:uppercase; color:{ACCENT}; }}
  .sub {{ color:{MUTED}; margin-top:4px; }}
  .wrap {{ display:flex; gap:24px; padding:0 28px 28px; align-items:flex-start; flex-wrap:wrap; }}
  svg {{ background:{SURFACE}; border:1px solid #23262E; border-radius:8px; flex:1 1 720px; max-width:100%; }}
  .edge {{ stroke:{FAINT}; stroke-width:1; opacity:.55; }}
  .node circle {{ fill:{ACCENT}; }}
  .node text {{ fill:{MUTED}; font-size:10px; text-anchor:middle; pointer-events:none; }}
  .node:hover circle {{ r:11; }}
  .node:hover text {{ fill:#fff; }}
  .node.draft circle {{ fill:{WARN}; }}
  .node.stale circle {{ fill:{DANGER}; }}
  .node.deprecated circle {{ fill:{MUTED}; }}
  aside {{ flex:0 0 220px; }}
  ul {{ list-style:none; margin:0 0 22px; padding:0; }}
  aside li {{ display:flex; justify-content:space-between; gap:10px; padding:3px 0; color:{MUTED}; }}
  aside b {{ color:#C9CDD6; font-weight:600; }}
  h2 {{ font-size:11px; letter-spacing:.12em; text-transform:uppercase; color:{FAINT}; margin:0 0 8px; }}
  .swatch {{ width:9px; height:9px; border-radius:50%; display:inline-block; margin-right:8px; }}
  .legend li {{ justify-content:flex-start; }}
</style>
<header>
  <h1>Knowledge graph</h1>
  <div class="sub">{len(bundle.concepts)} concepts · {len(bundle.edges())} links · {html.escape(str(bundle.root))}</div>
</header>
<div class="wrap">
  <svg viewBox="0 0 {_W} {_H}" role="img" aria-label="Knowledge bundle graph">{edges}{"".join(circles)}</svg>
  <aside>
    <h2>Types</h2><ul>{types}</ul>
    <h2>Status</h2><ul class="legend">{legend}</ul>
  </aside>
</div>
"""
