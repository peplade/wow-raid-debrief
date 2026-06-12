#!/usr/bin/env python3
"""Nominative mechanic execution: WHO kicks, WHO switches, WHO camps AoE.

Zone-config-driven (references/zones/<zone>/execution.json, or a copy in
<workdir>/refs/execution.json which takes precedence). Per boss:
- kicks: per player (event-level), per spell; casts begun/completed per pull
  (interrupt_ability payload) — completed = casts that went THROUGH;
- add switch: damage stream raid->add segmented into spawn windows (gaps
  >15 s); per player: median first-hit latency + damage share + windows.
  Render melee separately (travel time is not a fault);
- AoE squat: per player x ground mechanic: ticks, "squats" (>=3 consecutive
  ticks <=3 s apart), damage. Render tanks separately (boss placement);
- focus conformity (council fights, KILL pull): share of each DPS's damage
  on the raid's majority target per 10 s bucket;
- friendly/priority NPC participation (npc_dps_targets / npc_heal_targets);
- trial entries (aura applies; durations unreliable — removes are sporadic);
- prison time-to-free (aura apply->remove pairs; the freer is not logged);
- personal defensives cast count per pull.

Usage: python3 execution.py [--workdir WD]
Output: <workdir>/digests/analysis/execution_nominative.json
"""
import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from statistics import median

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wcl import load_config, workdir_from_args

DEF_IDS = (871, 12975, 118038, 55694, 115203, 122783, 122470, 115176,
           48792, 48707, 55233, 61336, 22812, 106922, 108271, 30823,
           642, 498, 86659, 31850, 47585, 19236, 45438, 115610,
           104773, 110913, 31224, 5277, 1966, 19263, 6262)


def load_zone_exec(wd, cfg):
    for p in (os.path.join(wd, "refs", "execution.json"),
              os.path.join(os.path.dirname(os.path.dirname(
                  os.path.abspath(__file__))), "references", "zones",
                  cfg.get("zone_slug", "soo"), "execution.json")):
        if os.path.exists(p):
            return json.load(open(p))
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir")
    args = ap.parse_args()
    wd = workdir_from_args(args)
    cfg = load_config(wd)
    zx = load_zone_exec(wd, cfg)
    db = sqlite3.connect(os.path.join(wd, "raid.db"), timeout=30)
    db.row_factory = sqlite3.Row
    kick_names = {int(k): v for k, v in
                  (zx.get("interrupt_names") or {}).items()}

    out = {}
    for code in cfg["reports"]:
        pulls = [dict(r) for r in db.execute(
            "SELECT * FROM pull WHERE report=? ORDER BY start_time", (code,))]
        by_fight = {p["fight_id"]: p for p in pulls}
        comp = defaultdict(dict)
        for r in db.execute("""SELECT fight_id, actor_id, player_name, role
                FROM composition WHERE report=?""", (code,)):
            comp[r["fight_id"]][r["actor_id"]] = (r["player_name"], r["role"])
        actor = {r["actor_id"]: r["name"] for r in db.execute(
            "SELECT actor_id, name FROM actor_name WHERE report=?", (code,))}

        for boss in sorted({p["boss"] for p in pulls}):
            fids = [p["fight_id"] for p in pulls if p["boss"] == boss]
            ph = ",".join(map(str, fids))
            bo = out.setdefault(boss, {})

            # kicks
            kicks = bo.setdefault("kicks", defaultdict(lambda:
                                                       defaultdict(int)))
            for r in db.execute(f"""SELECT fight_id, source_id, ability_id
                    FROM deep_aura WHERE report=? AND kind='interrupt'
                    AND fight_id IN ({ph})""", (code,)):
                who = comp[r["fight_id"]].get(r["source_id"])
                if who:
                    kicks[who[0]][kick_names.get(r["ability_id"],
                                                 str(r["ability_id"]))] += 1
            cp = bo.setdefault("casts_through", [])
            for r in db.execute(f"""SELECT fight_id, ability_name, payload
                    FROM raid_event WHERE report=? AND
                    kind='interrupt_ability' AND fight_id IN ({ph})""",
                    (code,)):
                pl = json.loads(r["payload"] or "{}")
                cp.append({"pull": by_fight[r["fight_id"]]["pull_number"],
                           "kill": bool(by_fight[r["fight_id"]]["kill"]),
                           "spell": r["ability_name"],
                           "begun": pl.get("begun"),
                           "through": pl.get("completed")})

            # add switch
            windows = []
            for add in (zx.get("adds") or {}).get(boss, []):
                aids = [a for a, n in actor.items() if n == add]
                if not aids:
                    continue
                rows = db.execute(f"""SELECT fight_id, ts_rel, source_id,
                        amount FROM deep_dmg_done WHERE report=? AND
                        fight_id IN ({ph}) AND target_id IN
                        ({','.join(map(str, aids))})
                        ORDER BY fight_id, ts_rel""", (code,)).fetchall()
                cur = None
                for r in rows:
                    if (cur and r["fight_id"] == cur["fid"]
                            and r["ts_rel"] - cur["last"] <= 15000):
                        cur["last"] = r["ts_rel"]
                        cur["events"].append(r)
                    else:
                        if cur:
                            windows.append(cur)
                        cur = {"add": add, "fid": r["fight_id"],
                               "start": r["ts_rel"], "last": r["ts_rel"],
                               "events": [r]}
                if cur:
                    windows.append(cur)
            windows = [w for w in windows
                       if (w["last"] - w["start"]) >= 6000
                       and sum(e["amount"] or 0 for e in w["events"]) > 3e6]
            sw = bo.setdefault("_sw", defaultdict(lambda: {"lat": [],
                                                           "dmg": 0,
                                                           "windows": 0}))
            wsum = bo.setdefault("add_windows", [])
            for w in windows:
                first, dmg = {}, defaultdict(int)
                for e in w["events"]:
                    who = comp[w["fid"]].get(e["source_id"])
                    if not who or who[1] == "healer":
                        continue
                    nm = who[0]
                    first.setdefault(nm, e["ts_rel"])
                    dmg[nm] += e["amount"] or 0
                for nm, t in first.items():
                    sw[nm]["lat"].append((t - w["start"]) / 1000)
                    sw[nm]["dmg"] += dmg[nm]
                    sw[nm]["windows"] += 1
                top = sorted(dmg.items(), key=lambda x: -x[1])[:3]
                wsum.append({
                    "add": w["add"],
                    "pull": by_fight[w["fid"]]["pull_number"],
                    "kill": bool(by_fight[w["fid"]]["kill"]),
                    "t_s": round(w["start"] / 1000),
                    "dur_s": round((w["last"] - w["start"]) / 1000),
                    "dmg_M": round(sum(e["amount"] or 0
                                       for e in w["events"]) / 1e6, 1),
                    "top3": ["%s (%.0fM)" % (n, v / 1e6) for n, v in top],
                    "late_8s": sorted(nm for nm, t in first.items()
                                      if (t - w["start"]) / 1000 > 8)})

            # AoE squat
            squat = bo.setdefault("squat", defaultdict(
                lambda: defaultdict(lambda: {"ticks": 0, "squats": 0,
                                             "dmg": 0})))
            for aid_s, label in ((zx.get("ground_mechanics") or {})
                                 .get(boss, {})).items():
                rows = db.execute(f"""SELECT fight_id, ts_rel, target_id,
                        amount FROM deep_dmg_taken WHERE report=? AND
                        fight_id IN ({ph}) AND ability_id=? ORDER BY
                        target_id, fight_id, ts_rel""",
                        (code, int(aid_s))).fetchall()
                run, prev = [], None

                def flush(run):
                    if not run:
                        return
                    who = comp[run[0]["fight_id"]].get(run[0]["target_id"])
                    if not who:
                        return
                    s = squat[who[0]][label]
                    s["ticks"] += len(run)
                    s["dmg"] += sum(x["amount"] or 0 for x in run)
                    if len(run) >= 3:
                        s["squats"] += 1
                for r in rows:
                    if (prev and r["target_id"] == prev["target_id"]
                            and r["fight_id"] == prev["fight_id"]
                            and r["ts_rel"] - prev["ts_rel"] <= 3000):
                        run.append(r)
                    else:
                        flush(run)
                        run = [r]
                    prev = r
                flush(run)

            # focus conformity (council, kill pull)
            f3 = (zx.get("focus_bosses") or {}).get(boss)
            kf = [f for f in fids if by_fight[f]["kill"]]
            if f3 and kf:
                kf = kf[0]
                ids3 = [a for a, n in actor.items() if n in f3]
                b3 = ",".join(map(str, ids3))
                buck = defaultdict(lambda: defaultdict(int))
                pbuck = defaultdict(lambda: defaultdict(int))
                for r in db.execute(f"""SELECT ts_rel, source_id, target_id,
                        amount FROM deep_dmg_done WHERE report=? AND
                        fight_id=? AND target_id IN ({b3})""", (code, kf)):
                    bkt = r["ts_rel"] // 10000
                    buck[bkt][r["target_id"]] += r["amount"] or 0
                    who = comp[kf].get(r["source_id"])
                    if who and who[1] == "dps":
                        pbuck[(who[0], bkt)][r["target_id"]] += \
                            r["amount"] or 0
                major = {bkt: max(v, key=v.get) for bkt, v in buck.items()}
                conf = defaultdict(lambda: [0, 0])
                for (nm, bkt), v in pbuck.items():
                    conf[nm][0] += v.get(major[bkt], 0)
                    conf[nm][1] += sum(v.values())
                bo["focus_kill"] = {
                    nm: round(100 * a / b) for nm, (a, b) in
                    sorted(conf.items(),
                           key=lambda x: -(x[1][0] / max(1, x[1][1])))
                    if b > 1e6}

            # NPC participation
            for kind, table, fld in (("npc_dps", "deep_dmg_done", "amount"),
                                     ("npc_heal", "deep_heal_event",
                                      "amount")):
                names = (zx.get(kind + "_targets") or {}).get(boss)
                if not names:
                    continue
                aids = [a for a, n in actor.items() if n in names]
                if not aids:
                    continue
                agg = defaultdict(int)
                for r in db.execute(f"""SELECT fight_id, source_id,
                        SUM({fld}) s FROM {table} WHERE report=? AND
                        fight_id IN ({ph}) AND target_id IN
                        ({','.join(map(str, aids))})
                        GROUP BY fight_id, source_id""", (code,)):
                    who = comp[r["fight_id"]].get(r["source_id"])
                    if who:
                        agg[who[0]] += r["s"] or 0
                bo[kind] = {nm: round(v / 1e6, 1) for nm, v in
                            sorted(agg.items(), key=lambda x: -x[1])}

            # trial entries
            tids = (zx.get("trial_auras") or {}).get(boss)
            if tids:
                tph = ",".join(map(str, tids))
                ent = defaultdict(int)
                for r in db.execute(f"""SELECT fight_id, target_id FROM
                        deep_aura WHERE report=? AND fight_id IN ({ph})
                        AND ability_id IN ({tph}) AND
                        type='applydebuff'""", (code,)):
                    who = comp[r["fight_id"]].get(r["target_id"])
                    if who:
                        ent[who[0]] += 1
                bo["trial_entries"] = dict(sorted(ent.items(),
                                                  key=lambda x: -x[1]))

            # prisons
            pids = (zx.get("prison_auras") or {}).get(boss)
            if pids:
                pph = ",".join(map(str, pids))
                pris, opens = bo.setdefault("prisons", []), {}
                for r in db.execute(f"""SELECT fight_id, type, ts_rel,
                        target_id FROM deep_aura WHERE report=? AND
                        fight_id IN ({ph}) AND ability_id IN ({pph})
                        ORDER BY fight_id, ts_rel""", (code,)):
                    who = comp[r["fight_id"]].get(r["target_id"])
                    if not who:
                        continue
                    key = (r["fight_id"], r["target_id"])
                    if r["type"] == "applydebuff":
                        opens[key] = r["ts_rel"]
                    elif r["type"] == "removedebuff" and key in opens:
                        pris.append({
                            "pull": by_fight[r["fight_id"]]["pull_number"],
                            "player": who[0],
                            "t_s": round(opens[key] / 1000),
                            "freed_in_s": round((r["ts_rel"]
                                                 - opens.pop(key))
                                                / 1000, 1)})

            # personal defensives per pull
            dph = ",".join(map(str, DEF_IDS))
            defs = bo.setdefault("_defs", defaultdict(int))
            for r in db.execute(f"""SELECT fight_id, source_id, COUNT(*) n
                    FROM deep_cast WHERE report=? AND type='cast' AND
                    fight_id IN ({ph}) AND ability_id IN ({dph})
                    GROUP BY fight_id, source_id""", (code,)):
                who = comp[r["fight_id"]].get(r["source_id"])
                if who:
                    defs[who[0]] += r["n"]
            bo.setdefault("_npulls", 0)
            bo["_npulls"] += len(fids)

    # finalize
    for boss, bo in out.items():
        bo["kicks"] = {nm: dict(v) for nm, v in
                       sorted(bo.get("kicks", {}).items(),
                              key=lambda x: -sum(x[1].values()))}
        sw = bo.pop("_sw", {})
        bo["switch"] = {nm: {"median_latency_s": round(median(v["lat"]), 1),
                             "add_dmg_M": round(v["dmg"] / 1e6, 1),
                             "windows": v["windows"]}
                        for nm, v in sorted(sw.items(),
                                            key=lambda x: median(x[1]["lat"]))}
        bo["squat"] = {nm: {k: dict(v) for k, v in mechs.items()}
                       for nm, mechs in sorted(
                           bo.get("squat", {}).items(),
                           key=lambda x: -sum(m["ticks"]
                                              for m in x[1].values()))}
        n = max(1, bo.pop("_npulls", 1))
        bo["defensives"] = {nm: {"casts": v, "per_pull": round(v / n, 1)}
                            for nm, v in sorted(bo.pop("_defs", {}).items(),
                                                key=lambda x: -x[1])}

    path = os.path.join(wd, "digests", "analysis",
                        "execution_nominative.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("execution: %d bosses -> %s" % (len(out), path))
    for boss, bo in out.items():
        print("  %-22s kicks=%d switch=%d squat=%d windows=%d" % (
            boss, len(bo["kicks"]), len(bo["switch"]), len(bo["squat"]),
            len(bo.get("add_windows", []))))


if __name__ == "__main__":
    main()
