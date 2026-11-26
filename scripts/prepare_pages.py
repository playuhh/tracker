"""Copy only explicitly approved public files into a fresh Pages directory."""

from __future__ import annotations

import argparse
import html
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from privacy_audit import PUBLIC_ALLOWLIST


def dashboard(report: str) -> str:
    """Keep the existing report intact; never read private portfolio data."""
    buttons = "".join(
        f'<button type="button" role="tab" id="tab-{i}" aria-controls="panel-{i}" aria-selected="{str(i == 0).lower()}" tabindex="{0 if i == 0 else -1}">Building {letter}</button>'
        for i, letter in enumerate("ABCD")
    )
    panels = (
        '<section role="tabpanel" id="panel-0" aria-labelledby="tab-0">'
        '<iframe title="Building A dashboard" srcdoc="' + html.escape(report, quote=True) + '"></iframe></section>'
    )
    for i in range(1, 4):
        panels += f'<section role="tabpanel" id="panel-{i}" aria-labelledby="tab-{i}" hidden><h2>Building {"ABCD"[i]}</h2><p>Collecting history — no approved public observations yet.</p></section>'
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Apartment tracker</title>
<style>body{margin:0;font:15px system-ui,sans-serif;color:#182230;background:#f7f8fa}
nav{display:flex;gap:8px;padding:12px;overflow:auto;background:white;border-bottom:1px solid #ddd}
button{white-space:nowrap;padding:10px 16px;border:1px solid #ccd2da;border-radius:8px;background:white;color:inherit;cursor:pointer}
button[aria-selected=true]{background:#182230;color:white}section{padding:16px}#panel-0{padding:0}
iframe{display:block;border:0;width:100%;height:calc(100dvh - 70px)}[hidden]{display:none!important}</style></head><body>
<nav role="tablist" aria-label="Buildings">""" + buttons + "</nav>" + panels + """<script>
const tabs=[...document.querySelectorAll('[role=tab]')];
function activate(i){tabs.forEach((t,j)=>{t.setAttribute('aria-selected',String(i===j));t.tabIndex=i===j?0:-1;document.getElementById('panel-'+j).hidden=i!==j;});}
tabs.forEach((t,i)=>{t.addEventListener('click',()=>activate(i));t.addEventListener('keydown',e=>{let j=({ArrowRight:(i+1)%tabs.length,ArrowLeft:(i+tabs.length-1)%tabs.length,Home:0,End:tabs.length-1})[e.key];if(j!==undefined){e.preventDefault();activate(j);tabs[j].focus();}});});
</script></body></html>"""


def prepare(root: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    names = {Path("data/report.html"): "index.html"}
    for relative in PUBLIC_ALLOWLIST:
        source = root / relative
        if not source.is_file():
            raise RuntimeError(f"Missing allowlisted public artifact: {relative}")
        (destination / names[relative]).write_text(dashboard(source.read_text(encoding="utf-8")), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--destination", type=Path, default=Path("site"))
    args = parser.parse_args()
    prepare(args.root.resolve(), args.destination.resolve())


if __name__ == "__main__":
    main()
