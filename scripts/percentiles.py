#!/usr/bin/env python3
"""Per-player WCL percentiles on kill fights (report.rankings).

Percentiles are computed by WCL per (spec, encounter, difficulty, size), so
they ARE comparable across difficulties — the right metric for week-over-week
tracking when the raid moves from Normal to Heroic.

Usage: python3 percentiles.py [--workdir WD]
Output: <workdir>/digests/percentiles.json
  { "<report>:<fight_id>": { encounter_id, boss, difficulty, duration_ms,
      players: [ {name, class, spec, role, metric, amount, rank_percent,
                  best_percent, bracket_percent, ilvl} ] } }
Metric: dps for dps/tanks, hps for healers (two queries per report).
Costs ~2 API calls per report; safe to re-run.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wcl import Backend, gql, load_config, load_env, workdir_from_args


def fetch_rankings(be, code, metric):
    q = ('{ reportData { report(code:"%s") { rankings(playerMetric:%s, '
         'compare:Parses) } } }' % (code, metric))
    data = gql(be, q)
    r = data["data"]["reportData"]["report"]["rankings"]
    if isinstance(r, str):
        r = json.loads(r)
    return r.get("data", [])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir")
    args = ap.parse_args()
    wd = workdir_from_args(args)
    load_env(wd)
    cfg = load_config(wd)
    be = Backend(wd)

    out = {}
    for code in cfg["reports"]:
        for metric in ("dps", "hps"):
            for f in fetch_rankings(be, code, metric):
                key = "%s:%s" % (code, f["fightID"])
                ent = out.setdefault(key, {
                    "encounter_id": f.get("encounter", {}).get("id"),
                    "boss": f.get("encounter", {}).get("name"),
                    "difficulty": f.get("difficulty"),
                    "duration_ms": f.get("duration"),
                    "players": []})
                for role_key, role in (f.get("roles") or {}).items():
                    if metric == "dps" and role_key == "healers":
                        continue
                    if metric == "hps" and role_key != "healers":
                        continue
                    for c in role.get("characters", []):
                        ent["players"].append({
                            "name": c.get("name"),
                            "class": c.get("class"),
                            "spec": c.get("spec"),
                            "role": role_key,
                            "metric": metric,
                            "amount": c.get("amount"),
                            "rank_percent": c.get("rankPercent"),
                            "best_percent": c.get("bestPercent"),
                            "bracket_percent": c.get("bracketPercent"),
                            "ilvl": c.get("bracketData")})
    dig = os.path.join(wd, "digests")
    os.makedirs(dig, exist_ok=True)
    path = os.path.join(dig, "percentiles.json")
    with open(path, "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("percentiles: %d ranked kill fights -> %s" % (len(out), path))


if __name__ == "__main__":
    main()
