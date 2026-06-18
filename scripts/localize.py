#!/usr/bin/env python3
"""Spell-name localization via Wowhead tooltips (official client strings).
Builds/extends <workdir>/refs/spell_names.json = {spell_id: localized_name}
for the report language (raid.json "lang"). Idempotent: only missing ids are
fetched; the cache grows across nights.

For lang=en nothing needs fetching (combat-log names are already English):
the file is still written (empty or pass-through) so pages.py has one code path.

CLI:
    python3 localize.py spells          # collect ids from db+refs, fetch missing
    python3 localize.py spells --ids 143436,144359   # ad-hoc additions
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wcl import Backend, load_config, load_env, workdir_from_args

# Wowhead locale ids (tooltip endpoint ?locale=N).
WOWHEAD_LOCALE = {"en": 0, "ko": 1, "fr": 2, "de": 3, "zh": 4, "es": 6,
                  "ru": 8, "pt": 10, "it": 11}
TOOLTIP_URL = "https://nether.wowhead.com/tooltip/spell/{sid}?locale={loc}"


def fetch_name(sid, loc):
    url = TOOLTIP_URL.format(sid=sid, loc=loc)
    req = urllib.request.Request(url, headers={"User-Agent": "wow-raid-debrief"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.load(r)
        name = data.get("name") or ""
        # Strip wowhead quality prefix markers if any.
        return re.sub(r"^\[(.*)\]$", r"\1", name).strip() or None
    except Exception:
        return None


def collect_ids(be, workdir):
    """Every spell id the report pages may display."""
    ids = set()
    for table, col in (("player_ability", "ability_id"), ("death", "ability_id"),
                       ("deep_dmg_taken", "ability_id"), ("deep_dmg_done", "ability_id"),
                       ("deep_aura", "ability_id"),
                       ("deep_heal_ability", "ability_id"), ("raid_event", "ability_id")):
        for r in be.con.execute(f"SELECT DISTINCT {col} i FROM {table} "
                                f"WHERE {col} IS NOT NULL AND {col} > 1"):
            ids.add(r["i"])
    # refs (mechanics + spec kpis): walk for "id" ints.
    for fn in ("mechanics_ref.json", "spec_kpis.json"):
        p = os.path.join(workdir, "refs", fn)
        if not os.path.exists(p):
            continue

        def walk(o):
            if isinstance(o, dict):
                v = o.get("id")
                if isinstance(v, int) and v > 1:
                    ids.add(v)
                for x in o.values():
                    walk(x)
            elif isinstance(o, list):
                for x in o:
                    walk(x)
        walk(json.load(open(p, encoding="utf-8")))
    return ids


def cmd_spells(be, cfg, args):
    lang = (cfg.get("lang") or "en").lower()
    loc = WOWHEAD_LOCALE.get(lang)
    out_p = os.path.join(be.workdir, "refs", "spell_names.json")
    cache = {}
    if os.path.exists(out_p):
        cache = {int(k): v for k, v in
                 json.load(open(out_p, encoding="utf-8")).items() if v}
    if loc is None:
        print(f"unknown lang '{lang}' — supported: {sorted(WOWHEAD_LOCALE)}")
        sys.exit(1)
    ids = collect_ids(be, be.workdir)
    if args.ids:
        ids |= {int(x) for x in args.ids.split(",") if x.strip()}
    if lang == "en":
        # Log names are already EN; write the file for the single code path.
        with open(out_p, "w", encoding="utf-8") as f:
            json.dump({str(k): v for k, v in cache.items()}, f,
                      ensure_ascii=False, indent=0)
        print(f"lang=en: nothing to fetch ({len(ids)} ids seen) -> {out_p}")
        return
    missing = sorted(i for i in ids if i not in cache)
    print(f"{len(ids)} ids referenced, {len(missing)} missing in {lang} cache")
    n_ok = 0
    for i, sid in enumerate(missing, 1):
        name = fetch_name(sid, loc)
        if name:
            cache[sid] = name
            n_ok += 1
        time.sleep(0.15)               # be nice to wowhead
        if i % 50 == 0 or i == len(missing):
            with open(out_p, "w", encoding="utf-8") as f:
                json.dump({str(k): v for k, v in cache.items()}, f,
                          ensure_ascii=False, indent=0)
            print(f"  {i}/{len(missing)} fetched ({n_ok} named)", flush=True)
    with open(out_p, "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in cache.items()}, f,
                  ensure_ascii=False, indent=0)
    print(f"-> {out_p} ({len(cache)} names)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("spells")
    sp.add_argument("--ids", default="")
    args = ap.parse_args()
    wd = workdir_from_args(args)
    load_env(wd)
    be = Backend(wd)
    cfg = load_config(wd)
    cmd_spells(be, cfg, args)


if __name__ == "__main__":
    main()
