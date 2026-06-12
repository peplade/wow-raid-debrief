#!/usr/bin/env python3
"""Night pacing: combat vs idle time, repull discipline, longest gaps.

Usage: python3 pacing.py [--workdir WD] [--compare WD2 [WD3 ...]]
Output: <workdir>/digests/analysis/pacing.json
  { "self": {label, nights:[...]}, "compare": [{label, nights:[...]}, ...] }

Per night: span, combat seconds, idle seconds, combat share, repull gaps
(wipe -> repull of the SAME boss) with median, gaps >= 2 min with context,
and the full segment list (boss pulls + trash fights) for gantt rendering.
"""
import argparse
import json
import os
import sqlite3
import sys
from statistics import median

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wcl import load_config, workdir_from_args


def week(wd):
    cfg = load_config(wd)
    db = sqlite3.connect(os.path.join(wd, "raid.db"), timeout=30)
    db.row_factory = sqlite3.Row
    ph = ",".join("?" * len(cfg["reports"]))
    pulls = [dict(r) for r in db.execute(
        f"""SELECT report, fight_id, boss, difficulty, kill, boss_pct,
        duration_s, start_time, end_time, pull_number FROM pull
        WHERE report IN ({ph}) ORDER BY report, start_time""",
        cfg["reports"])]
    trash = [dict(r) for r in db.execute(
        f"""SELECT report, fight_id, name, duration_s, start_time, end_time,
        deaths FROM trash_fight WHERE report IN ({ph})
        ORDER BY report, start_time""", cfg["reports"])]

    nights = []
    for code in cfg["reports"]:
        np = [p for p in pulls if p["report"] == code]
        nt = [t for t in trash if t["report"] == code
              and (t["duration_s"] or 0) > 5]
        if not np:
            continue
        segs = sorted(
            [{"kind": "boss", "label": "%s%s" % (p["boss"][:18],
                                                 " ✓" if p["kill"] else ""),
              "boss": p["boss"], "diff": p["difficulty"],
              "kill": bool(p["kill"]), "pull": p["pull_number"],
              "start": p["start_time"], "end": p["end_time"],
              "pct": p["boss_pct"]} for p in np] +
            [{"kind": "trash", "label": t["name"][:18], "boss": None,
              "start": t["start_time"], "end": t["end_time"],
              "deaths": t["deaths"]} for t in nt],
            key=lambda s: s["start"])
        t0, t1 = segs[0]["start"], segs[-1]["end"]
        combat = sum(s["end"] - s["start"] for s in segs)
        gaps = []
        for a, b in zip(segs, segs[1:]):
            g = (b["start"] - a["end"]) / 1000
            if g > 5:
                gaps.append({"after": a["label"], "before": b["label"],
                             "gap_s": round(g), "at": a["end"]})
        repulls = []
        bp = [s for s in segs if s["kind"] == "boss"]
        for a, b in zip(bp, bp[1:]):
            if a["boss"] == b["boss"] and not a["kill"]:
                repulls.append(round((b["start"] - a["end"]) / 1000))
        nights.append({
            "report": code,
            "span_s": round((t1 - t0) / 1000),
            "combat_s": round(combat / 1000),
            "idle_s": round((t1 - t0 - combat) / 1000),
            "combat_share": round(combat / (t1 - t0), 3) if t1 > t0 else None,
            "repull_median_s": round(median(repulls)) if repulls else None,
            "repulls": repulls,
            "n_gaps_2min": sum(1 for g in gaps if g["gap_s"] >= 120),
            "top_gaps": sorted(gaps, key=lambda g: -g["gap_s"])[:6],
            "segments": segs})
    return {"label": cfg.get("label"), "nights": nights}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir")
    ap.add_argument("--compare", nargs="*", default=[],
                    help="earlier workdirs for week-over-week pacing")
    args = ap.parse_args()
    wd = workdir_from_args(args)
    out = {"self": week(wd),
           "compare": [week(os.path.abspath(os.path.expanduser(x)))
                       for x in args.compare]}
    path = os.path.join(wd, "digests", "analysis", "pacing.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    for blk in [out["self"]] + out["compare"]:
        for n in blk["nights"]:
            print("%s %s: span %dmin combat %dmin (%.0f%%) repull_med %ss "
                  "gaps>2min %d" % (blk["label"], n["report"][:8],
                                    n["span_s"] // 60, n["combat_s"] // 60,
                                    100 * (n["combat_share"] or 0),
                                    n["repull_median_s"], n["n_gaps_2min"]))
    print("->", path)


if __name__ == "__main__":
    main()
