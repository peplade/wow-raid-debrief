#!/usr/bin/env python3
"""Per-pull dossiers: chronology + POSSIBLE REACTIONS at the timing.

The core idea: at every critical moment of every wipe, compute which raid
CDs and personal defensives were AVAILABLE and not used. Availability is
tracked on the ABSOLUTE night timeline — a CD burned late in pull N is still
on cooldown at the next repull (repulls are ~2 min) — so "they had Barrier"
claims are honest. CD durations are indicative MoP values; talents are not
verifiable from logs (say so when rendering).

Usage: python3 dossiers.py [--workdir WD]
Output: <workdir>/digests/analysis/dossiers.json
Per pull: merged chronology (deaths, signature enemy casts from the zone's
execution.json, raid CDs, battle-rezzes deduped, lust), critical moments
(first death + clusters >=3 deaths/10 s) each with: CDs posted +-10 s, CDs
available-but-not-posted, victims that had a personal defensive in reserve;
inter-pull delta (fixed vs repeated killers, repull gap).
"""
import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wcl import load_config, workdir_from_args

# Raid CDs: id -> (name, cooldown_s). Indicative MoP durations.
RAID_CDS = {
    62618: ("Power Word: Barrier", 180), 33206: ("Pain Suppression", 180),
    47788: ("Guardian Spirit", 180), 64843: ("Divine Hymn", 480),
    15286: ("Vampiric Embrace", 180), 31821: ("Devotion Aura", 180),
    633: ("Lay on Hands", 600), 6940: ("Hand of Sacrifice", 120),
    740: ("Tranquility", 480), 102342: ("Ironbark", 90),
    98008: ("Spirit Link Totem", 180), 108280: ("Healing Tide Totem", 180),
    16190: ("Mana Tide Totem", 180), 108281: ("Ancestral Guidance", 120),
    115310: ("Revival", 180), 116849: ("Life Cocoon", 120),
    97462: ("Rallying Cry", 180), 114203: ("Demoralizing Banner", 180),
    114207: ("Skull Banner", 180), 76577: ("Smoke Bomb", 180),
    51052: ("Anti-Magic Zone", 120), 120668: ("Stormlash Totem", 300),
    2825: ("Bloodlust", 600), 32182: ("Heroism", 600),
}
LUST = {2825, 32182, 80353, 90355}
# who CAN post what (class, spec) -> raid CD ids (MoP, majors only)
ROSTER_RAID_CDS = {
    ("Priest", "Discipline"): [62618, 33206],
    ("Priest", "Holy"): [47788, 64843],
    ("Priest", "Shadow"): [15286],
    ("Paladin", "Retribution"): [31821, 633, 6940],
    ("Paladin", "Holy"): [31821, 633, 6940],
    ("Paladin", "Protection"): [31821, 633, 6940],
    ("Druid", "Restoration"): [740, 102342],
    ("Druid", "Balance"): [740],
    ("Shaman", "Restoration"): [98008, 108280, 16190, 2825],
    ("Shaman", "Elemental"): [108281, 120668, 2825],
    ("Shaman", "Enhancement"): [120668, 2825],
    ("Monk", "Mistweaver"): [115310, 116849],
    ("Warrior", "Fury"): [97462, 114207],
    ("Warrior", "Arms"): [97462, 114207],
    ("Warrior", "Protection"): [97462, 114207],
    ("Rogue", "Combat"): [76577], ("Rogue", "Assassination"): [76577],
    ("Rogue", "Subtlety"): [76577],
    ("DeathKnight", "Blood"): [51052],
    ("Mage", "Frost"): [80353], ("Mage", "Fire"): [80353],
    ("Mage", "Arcane"): [80353],
    ("Hunter", "BeastMastery"): [90355],
}
# Personal defensives: id -> (name, indicative cd_s)
PERSONALS = {
    871: ("Shield Wall", 300), 12975: ("Last Stand", 180),
    118038: ("Die by the Sword", 120), 55694: ("Enraged Regeneration", 60),
    115203: ("Fortifying Brew", 180), 122783: ("Diffuse Magic", 90),
    122470: ("Touch of Karma", 90), 115176: ("Zen Meditation", 180),
    48792: ("Icebound Fortitude", 180), 48707: ("Anti-Magic Shell", 45),
    55233: ("Vampiric Blood", 60), 49028: ("Dancing Rune Weapon", 90),
    61336: ("Survival Instincts", 180), 22812: ("Barkskin", 60),
    106922: ("Might of Ursoc", 180), 108271: ("Astral Shift", 90),
    30823: ("Shamanistic Rage", 60), 642: ("Divine Shield", 300),
    498: ("Divine Protection", 60),
    86659: ("Guardian of Ancient Kings", 300),
    31850: ("Ardent Defender", 180), 1022: ("Hand of Protection", 300),
    47585: ("Dispersion", 120), 19236: ("Desperate Prayer", 120),
    45438: ("Ice Block", 300), 115610: ("Temporal Shield", 90),
    104773: ("Unending Resolve", 180), 110913: ("Dark Bargain", 180),
    31224: ("Cloak of Shadows", 60), 5277: ("Evasion", 180),
    1966: ("Feint", 15), 19263: ("Deterrence", 120),
    6262: ("Healthstone", 120),
}
CLASS_PERSONALS = {
    "Warrior": [118038, 55694, 97462], "Monk": [115203, 122783, 122470],
    "DeathKnight": [48792, 48707], "Druid": [22812],
    "Shaman": [108271, 30823], "Paladin": [642, 498],
    "Priest": [19236, 47585], "Mage": [45438, 115610],
    "Warlock": [104773, 110913], "Rogue": [31224, 5277, 1966],
    "Hunter": [19263],
}
BREZ = {20484: "Rebirth", 61999: "Raise Ally"}
HEALING_CDS_FOR_GAPS = (62618, 33206, 98008, 108280, 740, 64843, 115310,
                        31821, 97462, 51052, 108281)


def load_signatures(wd, cfg):
    """Optional zone execution config: signature enemy casts to timeline."""
    for base in (os.path.join(wd, "refs"),
                 os.path.join(os.path.dirname(os.path.dirname(
                     os.path.abspath(__file__))), "references", "zones",
                     cfg.get("zone_slug", "soo"))):
        p = os.path.join(base, "execution.json")
        if os.path.exists(p):
            ex = json.load(open(p))
            return {b: {int(k): v for k, v in m.items()}
                    for b, m in (ex.get("signature_casts") or {}).items()}
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir")
    args = ap.parse_args()
    wd = workdir_from_args(args)
    cfg = load_config(wd)
    db = sqlite3.connect(os.path.join(wd, "raid.db"), timeout=30)
    db.row_factory = sqlite3.Row
    sig_by_boss = load_signatures(wd, cfg)

    out = {"pulls": []}
    for code in cfg["reports"]:
        pulls = [dict(r) for r in db.execute(
            "SELECT * FROM pull WHERE report=? ORDER BY start_time", (code,))]
        comp = defaultdict(dict)
        for r in db.execute("""SELECT fight_id, actor_id, player_name, class,
                spec, role FROM composition WHERE report=?""", (code,)):
            comp[r["fight_id"]][r["actor_id"]] = (
                r["player_name"], r["class"], r["spec"], r["role"])

        # absolute usage history (per report — nights don't share CDs)
        use_raid, owners = defaultdict(list), defaultdict(set)
        for p in pulls:
            for nm, cls, spec, role in comp[p["fight_id"]].values():
                for cid in ROSTER_RAID_CDS.get((cls, spec), []):
                    owners[cid].add(nm)
        for r in db.execute("""SELECT fight_id, timestamp, source_name,
                ability_id FROM raid_event WHERE report=? AND
                kind='cd_cast'""", (code,)):
            p = next(x for x in pulls if x["fight_id"] == r["fight_id"])
            use_raid[(r["source_name"], r["ability_id"])].append(
                p["start_time"] + (r["timestamp"] or 0))
        use_pers = defaultdict(list)
        ids = ",".join(str(i) for i in PERSONALS)
        for r in db.execute(f"""SELECT fight_id, ts_rel, source_id,
                ability_id FROM deep_cast WHERE report=? AND type='cast'
                AND ability_id IN ({ids})""", (code,)):
            who = comp[r["fight_id"]].get(r["source_id"])
            if who:
                p = next(x for x in pulls if x["fight_id"] == r["fight_id"])
                use_pers[(who[0], r["ability_id"])].append(
                    p["start_time"] + r["ts_rel"])
        for v in use_raid.values():
            v.sort()
        for v in use_pers.values():
            v.sort()

        def available(history, cd_ms, abs_ms):
            prev = [t for t in history if t <= abs_ms]
            return (not prev) or (abs_ms - prev[-1] >= cd_ms)

        prev_pull = {}
        for p in pulls:
            fid, t0 = p["fight_id"], p["start_time"]
            cmp_f = comp[fid]
            deaths = [dict(r) for r in db.execute(
                """SELECT actor_id, player_name, death_time, ability_id,
                ability_name FROM death WHERE report=? AND fight_id=?
                ORDER BY death_time""", (code, fid))]

            ev = []
            for d in deaths:
                who = cmp_f.get(d["actor_id"], (d["player_name"],) * 4)
                ev.append({"t": d["death_time"], "kind": "death",
                           "txt": "%s (%s) — %s" % (
                               d["player_name"], who[3],
                               d["ability_name"] or "?")})
            for r in db.execute("""SELECT timestamp, source_name, ability_id,
                    ability_name FROM raid_event WHERE report=? AND
                    fight_id=? AND kind='cd_cast' ORDER BY timestamp""",
                    (code, fid)):
                ev.append({"t": r["timestamp"],
                           "kind": "lust" if r["ability_id"] in LUST
                           else "cd",
                           "txt": "%s — %s" % (r["source_name"],
                                               r["ability_name"])})
            last_brez = {}
            for r in db.execute(f"""SELECT ts_rel, source_id, ability_id
                    FROM deep_cast WHERE report=? AND fight_id=? AND
                    type='cast' AND ability_id IN (20484,61999)
                    ORDER BY ts_rel""", (code, fid)):
                who = cmp_f.get(r["source_id"], ("?",) * 4)[0]
                if r["ts_rel"] - last_brez.get(who, -99999) < 15000:
                    continue
                last_brez[who] = r["ts_rel"]
                ev.append({"t": r["ts_rel"], "kind": "brez",
                           "txt": "%s — brez (%s)" % (
                               who, BREZ[r["ability_id"]])})
            sig = sig_by_boss.get(p["boss"], {})
            if sig:
                sids = ",".join(str(s) for s in sig)
                for r in db.execute(f"""SELECT ts_rel, ability_id FROM
                        deep_aura WHERE report=? AND fight_id=? AND
                        kind='enemy_cast' AND ability_id IN ({sids})
                        ORDER BY ts_rel""", (code, fid)):
                    ev.append({"t": r["ts_rel"], "kind": "mechanic",
                               "txt": sig[r["ability_id"]]})
            ev.sort(key=lambda x: x["t"] or 0)

            # critical moments
            crits = []
            if deaths:
                crits.append({"t": deaths[0]["death_time"],
                              "why": "first death"})
            i = 0
            while i < len(deaths):
                j = i
                while (j + 1 < len(deaths) and deaths[j + 1]["death_time"]
                       - deaths[i]["death_time"] <= 10000):
                    j += 1
                if j - i + 1 >= 3:
                    crits.append({"t": deaths[i]["death_time"],
                                  "why": "cluster of %d deaths in %ds" % (
                                      j - i + 1,
                                      (deaths[j]["death_time"]
                                       - deaths[i]["death_time"]) // 1000)})
                    i = j + 1
                else:
                    i += 1
            crits.sort(key=lambda c: c["t"])
            merged = []
            for c in crits:
                if merged and c["t"] - merged[-1]["t"] < 8000:
                    merged[-1]["why"] += " + " + c["why"]
                else:
                    merged.append(c)
            crits = merged[:5]

            cd_posted = [(e["t"], e["txt"]) for e in ev
                         if e["kind"] in ("cd", "lust")]
            for c in crits:
                t, abs_t = c["t"], t0 + c["t"]
                c["cds_posted"] = [txt for (tt, txt) in cd_posted
                                   if abs(tt - t) <= 10000]
                avail = []
                for cid in HEALING_CDS_FOR_GAPS:
                    if cid not in RAID_CDS:
                        continue
                    nom, cd_s = RAID_CDS[cid]
                    for nm in sorted(owners.get(cid, [])):
                        dead = any(d["player_name"] == nm
                                   and d["death_time"] is not None
                                   and d["death_time"] < t for d in deaths)
                        if dead:
                            continue
                        if available(use_raid.get((nm, cid), []),
                                     cd_s * 1000, abs_t) and \
                                not any(nom in txt
                                        for txt in c["cds_posted"]):
                            avail.append("%s (%s)" % (nom, nm))
                c["cds_available_not_posted"] = sorted(set(avail))
                vlist = []
                for d in deaths:
                    if d["death_time"] is None or abs(d["death_time"]
                                                      - t) > 10000:
                        continue
                    who = cmp_f.get(d["actor_id"])
                    if not who:
                        continue
                    nm, cls = who[0], who[1]
                    cands = []
                    for sid in CLASS_PERSONALS.get(cls, []):
                        if sid not in PERSONALS:
                            continue
                        used = [u for u in use_pers.get((nm, sid), [])
                                if abs_t - 12000 <= u <= abs_t]
                        if used:
                            cands = []
                            break
                        if available(use_pers.get((nm, sid), []),
                                     PERSONALS[sid][1] * 1000, abs_t):
                            cands.append(PERSONALS[sid][0])
                    if cands:
                        vlist.append({"player": nm,
                                      "defensives_in_reserve": cands[:3]})
                c["victims_without_defensive"] = vlist
                c["t_s"] = round(t / 1000)
                del c["t"]

            delta = None
            key = (p["boss"], p["difficulty"])
            cur_ab = defaultdict(int)
            for d in deaths:
                cur_ab[d["ability_name"] or "?"] += 1
            if key in prev_pull:
                pv = prev_pull[key]
                delta = {"repull_gap_s": round((t0 - pv["end"]) / 1000),
                         "fixed": sorted(a for a in pv["ab"]
                                         if pv["ab"][a] >= 2
                                         and cur_ab.get(a, 0) == 0),
                         "repeated": sorted(a for a in cur_ab
                                            if cur_ab[a] >= 2
                                            and pv["ab"].get(a, 0) >= 2)}
            prev_pull[key] = {"end": p["end_time"], "ab": cur_ab}

            out["pulls"].append({
                "report": code, "fight_id": fid, "boss": p["boss"],
                "difficulty": p["difficulty"],
                "pull_number": p["pull_number"], "kill": bool(p["kill"]),
                "boss_pct": p["boss_pct"], "duration_s": p["duration_s"],
                "chronology": [{"t_s": round((e["t"] or 0) / 1000),
                                "kind": e["kind"], "txt": e["txt"]}
                               for e in ev],
                "critical_moments": crits,
                "delta_vs_prev_pull": delta})

    path = os.path.join(wd, "digests", "analysis", "dossiers.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("dossiers: %d pulls -> %s" % (len(out["pulls"]), path))


if __name__ == "__main__":
    main()
