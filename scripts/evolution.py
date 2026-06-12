#!/usr/bin/env python3
"""Week-over-week guild evolution dataset: percentiles, gear/ilvl, roster.

Usage: python3 evolution.py <workdir_week1> <workdir_week2> [...more weeks]
Output (in the LAST workdir):
  digests/analysis/evolution.json   — per-week raid stats + per-player
      median percentile trajectory, ilvl, deaths, presence
  digests/analysis/gear_evolution.json — per-player ilvl delta + new items
      between the last two weeks (combatantinfo diff), localized item names
      via wowhead (cache digests/item_names.json)

Prerequisite: percentiles.py has run in EVERY workdir (kill percentiles).
Percentile is the cross-difficulty comparable; raw DPS is only comparable
same boss + same difficulty.
"""
import json
import os
import sqlite3
import sys
import urllib.request
from collections import defaultdict
from statistics import median

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wcl import load_config

SLOT_IDX_SKIP = (3, 17)        # shirt, tabard
WOWHEAD = "https://nether.wowhead.com/mop-classic/%s/tooltip/item/%d"


def week_data(wd):
    cfg = load_config(wd)
    own = tuple(cfg["reports"])
    ph = ",".join("?" * len(own))
    db = sqlite3.connect(os.path.join(wd, "raid.db"), timeout=30)
    db.row_factory = sqlite3.Row

    pulls = [dict(r) for r in db.execute(
        f"SELECT * FROM pull WHERE report IN ({ph}) ORDER BY start_time", own)]
    kills = [p for p in pulls if p["kill"]]
    wipes = [p for p in pulls if not p["kill"]]

    roster = {}
    for r in db.execute(
            f"SELECT player_name, MAX(class) cls, MAX(spec) spec,"
            f" MAX(role) role, ROUND(AVG(NULLIF(item_level,0)),1) il,"
            f" COUNT(*) n FROM composition WHERE report IN ({ph})"
            f" GROUP BY player_name", own):
        roster[r["player_name"]] = {
            "class": r["cls"], "spec": r["spec"], "role": r["role"],
            "ilvl": r["il"], "fights": r["n"]}

    deaths_by = {r[0]: r[1] for r in db.execute(
        f"SELECT player_name, COUNT(*) FROM death WHERE report IN ({ph}) "
        f"GROUP BY player_name", own)}
    pp = db.execute(f"SELECT SUM(prepot), COUNT(*) FROM conso "
                    f"WHERE report IN ({ph})", own).fetchone()
    prepot_rate = round(pp[0] / pp[1], 3) if pp and pp[1] else None

    perc_path = os.path.join(wd, "digests", "percentiles.json")
    perc = json.load(open(perc_path)) if os.path.exists(perc_path) else {}
    by_player = defaultdict(list)
    for fk, f in perc.items():
        for p in f["players"]:
            by_player[p["name"]].append({
                "boss": f["boss"], "difficulty": f["difficulty"],
                "metric": p["metric"], "role": p["role"],
                "amount": round(p["amount"] or 0),
                "percentile": p["rank_percent"], "ilvl": p["ilvl"]})

    return {
        "label": cfg.get("label"), "reports": list(own),
        "raid": {
            "n_pulls": len(pulls), "n_kills": len(kills),
            "n_wipes": len(wipes),
            "kills": [{"boss": p["boss"], "difficulty": p["difficulty"],
                       "duration_s": p["duration_s"]} for p in kills],
            "prepot_rate": prepot_rate,
            "morts_total": sum(deaths_by.values())},
        "roster": roster,
        "deaths_by_player": deaths_by,
        "parses": dict(by_player)}


def last_gear_by_player(wd):
    cfg = load_config(wd)
    own = tuple(cfg["reports"])
    ph = ",".join("?" * len(own))
    db = sqlite3.connect(os.path.join(wd, "raid.db"), timeout=30)
    db.row_factory = sqlite3.Row
    fight_ts = {(r["report"], r["fight_id"]): r["start_time"]
                for r in db.execute(f"SELECT report,fight_id,start_time "
                                    f"FROM pull WHERE report IN ({ph})", own)}
    name_of = {}
    for r in db.execute(
            f"SELECT report,fight_id,actor_id,player_name FROM composition "
            f"WHERE report IN ({ph})", own):
        name_of[(r["report"], r["fight_id"], r["actor_id"])] = r["player_name"]
    best = {}
    for r in db.execute(
            f"SELECT report,fight_id,source_id,payload FROM raid_event "
            f"WHERE kind='combatantinfo' AND report IN ({ph})", own):
        nm = name_of.get((r["report"], r["fight_id"], r["source_id"]))
        ts = fight_ts.get((r["report"], r["fight_id"]))
        if not nm or ts is None or (nm in best and best[nm][2] >= ts):
            continue
        gear = json.loads(r["payload"]).get("gear") or []
        ils = [g["itemLevel"] for i, g in enumerate(gear)
               if g.get("itemLevel", 0) >= 400 and i not in SLOT_IDX_SKIP]
        if ils:
            best[nm] = (gear, round(sum(ils) / len(ils), 1), ts)
    return best


def item_name(item_id, lang, cache, cache_path):
    if str(item_id) in cache:
        return cache[str(item_id)]
    try:
        with urllib.request.urlopen(WOWHEAD % (lang, item_id),
                                    timeout=10) as r:
            name = json.load(r).get("name") or str(item_id)
    except Exception:
        name = str(item_id)
    cache[str(item_id)] = name
    with open(cache_path, "w") as fh:
        json.dump(cache, fh, ensure_ascii=False, indent=0)
    return name


def main():
    wds = [os.path.abspath(os.path.expanduser(x)) for x in sys.argv[1:]]
    if len(wds) < 2:
        print("usage: evolution.py <workdir_week1> <workdir_week2> [...]")
        sys.exit(1)
    weeks = [week_data(w) for w in wds]
    last = wds[-1]
    out_dir = os.path.join(last, "digests", "analysis")
    os.makedirs(out_dir, exist_ok=True)

    players = sorted({p for w in weeks for p in w["roster"]})
    evolution = {}
    for nm in players:
        ent = {"weeks": []}
        for w in weeks:
            r = w["roster"].get(nm)
            parses = w["parses"].get(nm, [])
            pcts = [x["percentile"] for x in parses
                    if x["percentile"] is not None]
            ent["weeks"].append({
                "label": w["label"], "present": bool(r),
                "role": r and r["role"], "spec": r and r["spec"],
                "ilvl": r and r["ilvl"],
                "median_percentile": round(median(pcts), 1) if pcts else None,
                "n_parses": len(pcts),
                "deaths": w["deaths_by_player"].get(nm, 0),
                "parses": parses})
        evolution[nm] = ent
    with open(os.path.join(out_dir, "evolution.json"), "w") as fh:
        json.dump({"weeks": [{"label": w["label"], "raid": w["raid"]}
                             for w in weeks],
                   "players": evolution}, fh, ensure_ascii=False, indent=1)

    # gear diff between the last two weeks
    cfg = load_config(last)
    lang = cfg.get("lang", "en")
    g1 = last_gear_by_player(wds[-2])
    g2 = last_gear_by_player(last)
    cache_path = os.path.join(last, "digests", "item_names.json")
    cache = (json.load(open(cache_path))
             if os.path.exists(cache_path) else {})
    gear = {}
    for nm, (gear2, il2, _) in sorted(g2.items()):
        ent = {"ilvl_prev": None, "ilvl_now": il2, "delta": None,
               "new_items": [], "absent_prev": nm not in g1}
        if nm in g1:
            gear1, il1, _ = g1[nm]
            ent["ilvl_prev"], ent["delta"] = il1, round(il2 - il1, 1)
            ids1 = {g["id"] for g in gear1}
            for i, g in enumerate(gear2):
                if i in SLOT_IDX_SKIP or g.get("itemLevel", 0) < 400:
                    continue
                if g["id"] not in ids1:
                    ent["new_items"].append({
                        "id": g["id"], "ilvl": g["itemLevel"], "slot": i,
                        "name": item_name(g["id"], lang, cache, cache_path)})
        gear[nm] = ent
    gear["__departed__"] = sorted(set(g1) - set(g2))
    with open(os.path.join(out_dir, "gear_evolution.json"), "w") as fh:
        json.dump(gear, fh, ensure_ascii=False, indent=1)
    print("evolution: %d weeks, %d players; gear diff %s -> %s"
          % (len(weeks), len(evolution),
             weeks[-2]["label"], weeks[-1]["label"]))


if __name__ == "__main__":
    main()
