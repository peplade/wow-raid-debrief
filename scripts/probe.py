#!/usr/bin/env python3
"""Pre-publication probes on generated pages. NO LLM judgement — mechanical
checks only. Exit code 1 on any failure: this is a binary gate.

Checks per page:
  * forbidden tokens: "undefined", "NaN", "null,", "TODO", "PLACEHOLDER",
    "__BOSS_TABLE__", "__PLAYERS_LINKS__", "__PACING__", "{wcl}", "None</",
    "[object Object]"
  * every <canvas id=...> has a matching tlChart('<id>' call
  * every internal href resolves to an existing file
  * non-empty <main>
  * extra forbidden tokens from --forbid a,b,c (e.g. internal codenames)

CLI:
    python3 probe.py            # probe <workdir>/pages/
    python3 probe.py --forbid secretword,internalname
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wcl import workdir_from_args

FORBIDDEN = ["undefined", "NaN", "TODO", "PLACEHOLDER", "__BOSS_TABLE__",
             "__PLAYERS_LINKS__", "__PACING__", "{wcl}", ">None<",
             "[object Object]", "None</td>", "None</b>"]


def probe_page(path, forbid):
    errs = []
    htmlc = open(path, encoding="utf-8").read()
    for tok in FORBIDDEN + forbid:
        # NaN check must not flag e.g. "NaNga" words: token boundaries.
        for m in re.finditer(re.escape(tok), htmlc):
            a, b = m.start(), m.end()
            if tok == "NaN":
                before = htmlc[a - 1:a]
                after = htmlc[b:b + 1]
                if (before.isalnum() or after.isalnum()):
                    continue
            errs.append(f"forbidden token '{tok}' at offset {a}")
            break
    canvases = re.findall(r'<canvas id="([^"]+)"', htmlc)
    for cid in canvases:
        if f"tlChart('{cid}'" not in htmlc:
            errs.append(f"canvas '{cid}' has no tlChart() call (chart never drawn)")
    base = os.path.dirname(path)
    for href in re.findall(r'href="([^"#]+?)/?"', htmlc):
        if href.startswith(("http", "mailto", "//")):
            continue
        tgt = os.path.normpath(os.path.join(base, href))
        if not (os.path.exists(tgt) or os.path.exists(os.path.join(tgt, "index.html"))
                or os.path.exists(tgt + ".html")):
            errs.append(f"dead internal link: {href}")
    m = re.search(r"<main>(.*?)</main>", htmlc, re.S)
    if m and len(m.group(1).strip()) < 200:
        errs.append("near-empty <main>")
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--forbid", default="",
                    help="comma-separated extra forbidden tokens (case-sensitive)")
    args = ap.parse_args()
    wd = workdir_from_args(args)
    pages_dir = os.path.join(wd, "pages")
    forbid = [t for t in args.forbid.split(",") if t.strip()]
    pages = []
    for root, _, files in os.walk(pages_dir):
        for f in files:
            if f.endswith(".html"):
                pages.append(os.path.join(root, f))
    if not pages:
        print(f"FAIL: no pages found under {pages_dir}")
        sys.exit(1)
    n_err = 0
    for p in sorted(pages):
        errs = probe_page(p, forbid)
        rel = os.path.relpath(p, pages_dir)
        if errs:
            n_err += len(errs)
            print(f"[FAIL] {rel}")
            for e in errs:
                print(f"    - {e}")
        else:
            print(f"[PASS] {rel}")
    print(f"PROBE: {len(pages)} pages, {n_err} errors")
    sys.exit(1 if n_err else 0)


if __name__ == "__main__":
    main()
