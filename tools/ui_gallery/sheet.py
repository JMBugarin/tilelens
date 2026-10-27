"""index.html: every rendered state side by side, per backdrop and appearance."""

from __future__ import annotations

import html
import os
from pathlib import Path
from urllib.parse import quote

CSS = """
:root { color-scheme: light dark; --bg: #f4f4f6; --fg: #16181d; --mut: #6a6f7a; --card: #fff;
        --line: #d8dbe2; --zoom: 0.5; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #14151a; --fg: #eceef3; --mut: #9096a3; --card: #1d1f26; --line: #2c2f38; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
       font: 14px/1.45 -apple-system, "Segoe UI", system-ui, sans-serif; }
header { position: sticky; top: 0; z-index: 2; display: flex; gap: 16px; align-items: center;
         flex-wrap: wrap; padding: 10px 16px; background: var(--card);
         border-bottom: 1px solid var(--line); }
header h1 { font-size: 15px; margin: 0 8px 0 0; }
header input[type=search] { padding: 5px 9px; border-radius: 8px; border: 1px solid var(--line);
                            background: var(--bg); color: var(--fg); min-width: 200px; }
header label { color: var(--mut); display: flex; gap: 8px; align-items: center; }
main { padding: 8px 16px 48px; }
h2 { font-size: 13px; letter-spacing: .06em; text-transform: uppercase; color: var(--mut);
     margin: 28px 0 8px; }
article { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
          padding: 12px 14px 14px; margin: 0 0 12px; }
article h3 { font-size: 14px; margin: 0; }
article h3 span { color: var(--mut); font-weight: 400; margin-left: 8px; }
article p { margin: 2px 0 8px; color: var(--mut); }
.row { display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-start; }
figure { margin: 0; }
figcaption { color: var(--mut); font-size: 12px; margin: 0 0 4px; }
figure img { display: block; width: calc(var(--w) * var(--zoom) * 1px); max-width: 100%;
             height: auto; border-radius: 6px; border: 1px solid var(--line); }
.pair { display: flex; flex-direction: column; gap: 6px; }
.fail { color: #c0392b; font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px;
        white-space: pre-wrap; }
.hidden { display: none; }
article h3 span.warn { color: #b9770e; cursor: help; }
.notes { border: 1px solid #b9770e; border-radius: 12px; padding: 10px 14px; margin: 16px 0 0;
         color: #b9770e; font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px;
         white-space: pre-wrap; }
"""

JS = """
const zoom = document.getElementById('zoom');
zoom.addEventListener('input', () =>
  document.documentElement.style.setProperty('--zoom', zoom.value));
document.documentElement.style.setProperty('--zoom', zoom.value);
document.getElementById('q').addEventListener('input', e => {
  const q = e.target.value.trim().toLowerCase();
  document.querySelectorAll('article').forEach(a =>
    a.classList.toggle('hidden', q && !a.dataset.name.includes(q)));
});
"""


def _url(path: Path, out: Path) -> str:
    """A relative URL from the index to path; an absolute file URL when none exists (another
    drive on Windows)."""
    try:
        return quote(Path(os.path.relpath(path, out)).as_posix())
    except ValueError:
        return path.resolve().as_uri()


def _earlier(rec: dict, compare: Path) -> Path | None:
    """The earlier render of the same image; a run of one appearance is also compared with an
    earlier run that never forced one."""
    exact = compare / rec["file"]
    if exact.is_file():
        return exact
    if rec["scheme"] != "default":
        plain = compare / rec["file"].replace(f"__{rec['scheme']}.png", ".png")
        if plain.is_file():
            return plain
    return None


def _figure(rec: dict, out: Path, compare: Path | None) -> str:
    label = rec["backdrop"] + (f" · {rec['scheme']}" if rec["scheme"] != "default" else "")
    width = rec["size_pt"][0]
    src = html.escape(quote(rec["file"]))
    after = f'<img loading="lazy" style="--w:{width}" src="{src}" alt="">'
    if compare is None:
        body = f'<a href="{src}">{after}</a>'
    else:
        before = _earlier(rec, compare)
        if before is not None:
            href = html.escape(_url(before, out))
            old = f'<img loading="lazy" style="--w:{width}" src="{href}" alt="">'
            body = f'<div class="pair"><div><figcaption>before</figcaption>{old}</div>'
            body += f"<div><figcaption>after</figcaption>{after}</div></div>"
        else:
            body = f"<figcaption>no earlier render</figcaption>{after}"
    return f"<figure><figcaption>{html.escape(label)}</figcaption>{body}</figure>"


def write_index(
    out: Path,
    records: list[dict],
    failures: list[dict],
    compare: Path | None = None,
    notes: list[str] | None = None,
) -> Path:
    by_state: dict[str, list[dict]] = {}
    for rec in records:
        by_state.setdefault(rec["state"], []).append(rec)
    parts = [
        "<!doctype html><html lang=en><head><meta charset=utf-8>",
        '<meta name=viewport content="width=device-width,initial-scale=1">',
        f"<title>UI gallery</title><style>{CSS}</style></head><body>",
        "<header><h1>UI gallery</h1>",
        '<input id=q type=search placeholder="Filter states">',
        "<label>Size <input id=zoom type=range min=0.2 max=1.5 step=0.05 value=0.5></label>",
        f'<span style="color:var(--mut)">{len(by_state)} states, {len(records)} images, '
        f"{len(failures)} failed</span></header><main>",
    ]
    if notes:
        text = "\n".join(html.escape(n) for n in notes)
        parts.append(f"<div class=notes>Qt warnings outside any state:\n{text}</div>")
    surface = None
    for name in sorted(by_state):
        recs = by_state[name]
        if name.split("/")[0] != surface:
            surface = name.split("/")[0]
            parts.append(f"<h2>{html.escape(surface)}</h2>")
        first = recs[0]
        doc = f"<p>{html.escape(first['doc'])}</p>" if first.get("doc") else ""
        notes = [m for r in recs for m in r.get("qt", [])]
        warn = ""
        if notes:
            tip = html.escape("\n".join(dict.fromkeys(notes)), quote=True)
            warn = f'<span class=warn title="{tip}">{len(set(notes))} Qt message(s)</span>'
        parts.append(
            f'<article data-name="{html.escape(name)}"><h3>{html.escape(name)}'
            f"<span>{html.escape(first['kind'])}</span>{warn}</h3>{doc}<div class=row>"
        )
        parts += [_figure(r, out, compare) for r in recs]
        parts.append("</div></article>")
    if failures:
        parts.append("<h2>could not be rendered</h2>")
        for fail in failures:
            parts.append(
                f'<article data-name="{html.escape(fail["state"])}">'
                f"<h3>{html.escape(fail['state'])}"
                f"<span>{html.escape(fail['scheme'])}</span></h3>"
                f'<div class="fail">{html.escape(fail["error"])}</div></article>'
            )
    parts.append(f"</main><script>{JS}</script></body></html>")
    path = out / "index.html"
    path.write_text("".join(parts), encoding="utf-8")
    return path
