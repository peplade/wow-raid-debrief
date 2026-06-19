#!/usr/bin/env python3
"""Kicks (interrupts) per boss / add / kickable cast — canonical data builder.

For every boss that has interruptible casts (the KICKABLE map below, derived
from combat events — the SoO "kickables" survey), reconstruct, PER PULL:
  - each INSTANCE of a kickable cast (begincast -> outcome);
  - its outcome: kicked / landed ("leaked") / stopped otherwise (add died,
    cancel / no-hit);
  - every kick laid on that instance (the landing one + attempts) with its
    reaction time (kick_ts - begincast_ts).
Plus, per player: attempts, landed, efficiency, median reaction, overlaps
(a kick wasted on an already-cut cast); and a "never kicked" list (a spec able
to interrupt that laid 0 attempts).

LANDING = "damage-as-landing": WCL under-logs enemy cast COMPLETIONS when many
adds cast the same spell at once, so the spell's DAMAGE events are taken as
ground truth for "passed", in UNION with completions. A begincast with neither
damage nor interrupt = hit nobody (add killed/CC'd) = "no-hit" (NOT a missed
kick). See references/kicks.md + wcl-api-gotchas.md.

Usage:  python3 kicks.py [--workdir WD]
Output: <workdir>/digests/analysis/kicks.json  (consumed by pages.py via
        kicks_render.kicks_section).
"""
import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wcl import workdir_from_args

# --- kickable casts per encounter (danger-spell id -> tier). confirmed = proven
#     kicked in-log; design = kickable by design but never kicked here (leak).
#     SoO-specific (the only bundled zone); move to zone config if more zones ship.
KICKABLE = {
    51598: {143423: "confirmed", 143958: "confirmed"},
    51624: {145064: "confirmed", 144649: "confirmed"},  # 144479 dropped: user-verified not a real kick section (EdR 06-18)
    51604: {144379: "confirmed"},
    51622: {148522: "confirmed", 146757: "confirmed"},
    51603: {143432: "confirmed", 143473: "confirmed", 143431: "confirmed"},
    51594: {145230: "confirmed", 144923: "confirmed", 144922: "confirmed",
            148515: "design", 148518: "design", 148582: "design",
            148513: "design"},
    51593: {143666: "design", 142667: "design", 142576: "design"},
    51623: {144584: "confirmed", 144583: "confirmed", 145275: "confirmed"},
}
BOSS_ORDER_ENC = [51602, 51598, 51624, 51604, 51622, 51600, 51606, 51603,
                  51595, 51594, 51599, 51601, 51593, 51623]

# DEDICATED interrupt spells (Avenger's Shield etc. are damage on-CD, NOT an
# assignable kick — excluded). Used to count ATTEMPTS (landed or not).
KICK_SPELLS = {
    2139: "Counterspell", 6552: "Pummel", 1766: "Kick",
    57994: "Wind Shear", 47528: "Mind Freeze",
    106839: "Skull Bash", 96231: "Rebuke", 78675: "Solar Beam",
    116705: "Spear Hand Strike", 147362: "Counter Shot", 34490: "Silencing Shot",
    19647: "Spell Lock", 115781: "Optical Blast",
    15487: "Silence",
}
# specs WITHOUT a reliable interrupt (for the "never kicked" list: don't accuse
# a Resto druid or a Disc/Holy priest of not kicking).
NO_KICK_SPECS = {("Druid", "Restoration"), ("Priest", "Discipline"),
                 ("Priest", "Holy")}

MAX_CAST_MS = 6000      # max cast window (pairing guard)
ATT_PAD_MS = 500        # attempt <-> cast-window pairing tolerance


def pulls_of(db, enc):
    """[(report, fid, pull_no, kill, diff, dur_s)] sorted by pull."""
    rows = db.execute(
        "SELECT report, fight_id, pull_number, kill, difficulty, duration_s "
        "FROM pull WHERE encounter_id=? ORDER BY start_time", (enc,)).fetchall()
    return [(r["report"], r["fight_id"], r["pull_number"], bool(r["kill"]),
             r["difficulty"], r["duration_s"] or 0) for r in rows]


def comp_map(db, rep, fid):
    out = {}
    for r in db.execute("SELECT actor_id, player_name, role, class, spec FROM "
                        "composition WHERE report=? AND fight_id=?", (rep, fid)):
        out[r["actor_id"]] = (r["player_name"], r["role"], r["class"], r["spec"])
    return out


def actor_names(db, rep):
    return {r["actor_id"]: r["name"] for r in db.execute(
        "SELECT actor_id, name FROM actor_name WHERE report=?", (rep,))}


def extract_boss(db, enc):
    abilities = KICKABLE[enc]
    casts = {ab: {"tier": t, "add": None, "pulls": {},
                  "totals": {"casts": 0, "kicked": 0, "leaked": 0}}
             for ab, t in abilities.items()}
    # per-player aggregates
    P = defaultdict(lambda: {"attempts": 0, "landed": 0, "overlaps": 0,
                             "lat": [], "class": None, "spec": None,
                             "role": None})
    present_kickers = {}     # player -> (class, spec, role)
    pull_index = []

    for rep, fid, pno, kill, diff, dur in pulls_of(db, enc):
        comp = comp_map(db, rep, fid)
        anames = actor_names(db, rep)
        pull_index.append({"pull": pno, "kill": kill, "diff": diff,
                           "dur_s": round(dur, 1)})
        # present kicker roster (for "never kicked")
        for aid, (pl, role, cls, spec) in comp.items():
            present_kickers.setdefault(pl, (cls, spec, role))

        # kick attempts of the pull: (ts, player, target_id, kick_ability)
        attempts = []
        ph = ",".join(map(str, KICK_SPELLS))
        for r in db.execute(
                f"SELECT ts_rel, source_id, target_id, ability_id FROM deep_cast "
                f"WHERE report=? AND fight_id=? AND ability_id IN ({ph}) "
                f"AND type='cast'", (rep, fid)):
            who = comp.get(r["source_id"])
            if not who:
                continue
            attempts.append({"ts": r["ts_rel"], "pl": who[0],
                             "tgt": r["target_id"], "k": r["ability_id"]})
            pp = P[who[0]]
            pp["attempts"] += 1
            pp["class"], pp["spec"], pp["role"] = who[2], who[3], who[1]

        # last hit RECEIVED by each combat actor -> approx. of its death
        # (the death-table holds players only, not adds).
        last_hit = {}
        for r in db.execute("SELECT target_id, MAX(ts_rel) m FROM deep_dmg_done "
                            "WHERE report=? AND fight_id=? GROUP BY target_id",
                            (rep, fid)):
            last_hit[r["target_id"]] = r["m"]

        # an attempt can be claimed by ONE instance only
        for a in attempts:
            a["used"] = False
        for ab in abilities:
            begins = sorted((r["ts_rel"], r["source_id"]) for r in db.execute(
                "SELECT ts_rel, source_id FROM deep_aura WHERE report=? AND "
                "fight_id=? AND kind='enemy_cast' AND type='begincast' AND "
                "ability_id=?", (rep, fid, ab)) if r["ts_rel"] is not None)
            casts_done = sorted((r["ts_rel"], r["source_id"]) for r in db.execute(
                "SELECT ts_rel, source_id FROM deep_aura WHERE report=? AND "
                "fight_id=? AND kind='enemy_cast' AND type='cast' AND ability_id=?",
                (rep, fid, ab)))
            itrs = sorted((r["ts_rel"], r["source_id"], r["target_id"])
                          for r in db.execute(
                "SELECT ts_rel, source_id, target_id FROM deep_aura WHERE "
                "report=? AND fight_id=? AND kind='interrupt' AND "
                "type='interrupt' AND ability_id=?", (rep, fid, ab)))
            # REAL hits of the spell (damage) = proof the cast went through, even
            # when WCL didn't log the 'cast' event (frequent under-log on
            # simultaneous casts). cf wcl-api-gotchas (completions under-logged).
            hits = sorted(r["ts_rel"] for r in db.execute(
                "SELECT ts_rel FROM deep_dmg_taken WHERE report=? AND fight_id=? "
                "AND ability_id=?", (rep, fid, ab)) if r["ts_rel"] is not None)
            if not begins:
                continue
            inst = [{"t0": t0, "csrc": src, "out": "stopped", "end": None,
                     "by": None, "lat": None, "caster": None, "kicks": []}
                    for t0, src in begins]

            def claim(ts, pred):
                """Latest non-terminal instance whose window contains ts."""
                for k in range(len(inst) - 1, -1, -1):
                    I = inst[k]
                    if I["out"] == "stopped" and I["t0"] <= ts \
                            and ts <= I["t0"] + MAX_CAST_MS and pred(I):
                        return I
                return None
            # interrupts = kicked (source = THE kicker, log authority)
            for it, isrc, itgt in itrs:
                I = claim(it, lambda _I: True)
                if I is None:
                    continue
                I["out"], I["end"], I["caster"] = "kicked", it, itgt
                I["lat"] = round((it - I["t0"]) / 1000, 2)
                w = comp.get(isrc)
                I["by"] = w[0] if w else None
                # kick spell = the kicker's attempt closest to the interrupt
                I["bk"] = None
                if I["by"]:
                    cand = [a for a in attempts if a["pl"] == I["by"]
                            and abs(a["ts"] - it) <= 700]
                    if cand:
                        b = min(cand, key=lambda a: abs(a["ts"] - it))
                        I["bk"] = KICK_SPELLS.get(b["k"])
            # completed casts = landed (leak)
            for ct, csrc in casts_done:
                I = claim(ct, lambda _I: True)
                if I is None:
                    continue
                I["out"], I["end"] = "leaked", ct
                if I["caster"] is None:
                    I["caster"] = csrc
            # spell hit without a 'cast' event = unlogged completion -> landed too
            for ht in hits:
                I = claim(ht, lambda _I: True)
                if I is None:
                    continue
                I["out"], I["end"] = "leaked", ht
            for I in inst:
                if I["end"] is None:           # stopped: add DIED vs CANCELLED.
                    # died = the caster's last-EVER hit falls inside the cast
                    # window (after it takes no more damage) -> end = that
                    # instant. Otherwise the add survived = plain cancel.
                    al = last_hit.get(I["csrc"])
                    if al is not None and I["t0"] <= al <= I["t0"] + MAX_CAST_MS:
                        I["end"], I["stopkind"] = al, "died"
                    else:
                        I["end"], I["stopkind"] = I["t0"] + 800, "cancel"
                if I["caster"] is None:        # caster = begincast source
                    I["caster"] = I["csrc"]
                if casts[ab]["add"] is None and I["caster"] is not None:
                    casts[ab]["add"] = anames.get(I["caster"])
            # attempts -> closest instance (claimed once)
            for a in sorted(attempts, key=lambda x: x["ts"]):
                if a["used"]:
                    continue
                best, bestd = None, None
                for I in inst:
                    if a["ts"] < I["t0"] - ATT_PAD_MS \
                            or a["ts"] > I["end"] + ATT_PAD_MS:
                        continue
                    if I["caster"] is not None and a["tgt"] not in (
                            I["caster"], None, 0):
                        continue
                    d = abs(a["ts"] - I["t0"])
                    if bestd is None or d < bestd:
                        best, bestd = I, d
                if best is None:
                    continue
                a["used"] = True
                landed = (best["out"] == "kicked" and a["pl"] == best["by"]
                          and abs(a["ts"] - best["end"]) <= 600)
                best["kicks"].append({
                    "pl": a["pl"], "t": round(a["ts"] / 1000, 2),
                    "lat": round((a["ts"] - best["t0"]) / 1000, 2),
                    "land": landed, "k": KICK_SPELLS.get(a["k"])})
            # finalize
            instances = []
            for I in inst:
                if I["by"]:
                    P[I["by"]]["landed"] += 1
                    if I["lat"] is not None:
                        P[I["by"]]["lat"].append(I["lat"])
                casts[ab]["totals"]["casts"] += 1
                if I["out"] in ("kicked", "leaked"):
                    casts[ab]["totals"][I["out"]] += 1
                # the "landed" marker is authoritative on the interrupt source:
                # drop the lander's own attempt-marker (duplicate) and inject the
                # authoritative kick; the rest = overlaps (land=False).
                kk = I["kicks"]
                if I["by"]:
                    ends = round(I["end"] / 1000, 2)
                    # drop the lander's attempt-marker but RECOVER its kick spell
                    # to attach to the authoritative marker.
                    lander_k, rest = None, []
                    for a in kk:
                        if (lander_k is None and a["pl"] == I["by"]
                                and abs(a["t"] - ends) <= 0.6):
                            lander_k = a["k"]
                        else:
                            a["land"] = False
                            rest.append(a)
                    rest.append({"pl": I["by"], "t": ends, "lat": I["lat"],
                                 "land": True, "k": lander_k or I.get("bk")})
                    kk = rest
                instances.append({
                    "t": round(I["t0"] / 1000, 2),
                    "dur": round(max(I["end"] - I["t0"], 300) / 1000, 2),
                    "out": I["out"], "by": I["by"], "lat": I["lat"],
                    "stopkind": I.get("stopkind"),
                    "kicks": sorted(kk, key=lambda x: x["t"])})
            if instances:
                casts[ab]["pulls"][str(pno)] = {
                    "dur_s": round(dur, 1), "kill": kill, "diff": diff,
                    "inst": instances}

    # scoreboard
    def med(xs):
        xs = sorted(xs)
        return round(xs[len(xs) // 2], 2) if xs else None
    scoreboard = []
    for pl, v in P.items():
        if v["attempts"] == 0 and v["landed"] == 0:
            continue
        att = max(v["attempts"], v["landed"])
        scoreboard.append({
            "pl": pl, "class": v["class"], "spec": v["spec"],
            "attempts": att, "landed": v["landed"],
            "eff": round(100 * v["landed"] / att) if att else 0,
            "react": med(v["lat"]), "wasted": max(0, att - v["landed"])})
    scoreboard.sort(key=lambda x: (-x["landed"], x["react"] or 9))
    # "never kicked": spec able, present, 0 attempt
    kicked_names = {s["pl"] for s in scoreboard}
    never = []
    for pl, (cls, spec, role) in present_kickers.items():
        if pl in kicked_names:
            continue
        if (cls, spec) in NO_KICK_SPECS or role in ("tank", "healer"):
            continue
        never.append({"pl": pl, "class": cls, "spec": spec})
    never.sort(key=lambda x: x["pl"])

    # drop casts never cast at all this lockout (add absent)
    casts = {ab: c for ab, c in casts.items() if c["totals"]["casts"] > 0
             or c["tier"] == "design"}
    return {"casts": casts, "scoreboard": scoreboard, "never": never,
            "pull_index": pull_index}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir")
    args = ap.parse_args()
    wd = workdir_from_args(args)
    db = sqlite3.connect(os.path.join(wd, "raid.db"), timeout=30)
    db.row_factory = sqlite3.Row
    ENC = {51602: "Immerseus", 51598: "Fallen Protectors",
           51624: "Norushen", 51604: "Sha of Pride", 51622: "Galakras",
           51600: "Iron Juggernaut", 51606: "Kor'kron Dark Shaman",
           51603: "General Nazgrim", 51595: "Malkorok",
           51594: "Spoils of Pandaria", 51599: "Thok the Bloodthirsty",
           51601: "Siegecrafter Blackfuse", 51593: "Paragons of the Klaxxi",
           51623: "Garrosh Hellscream"}
    out = {}
    for enc in BOSS_ORDER_ENC:
        if enc not in KICKABLE:
            continue
        res = extract_boss(db, enc)
        res["boss"] = ENC[enc]
        res["encounter_id"] = enc
        out["%s|enc%d" % (ENC[enc], enc)] = res
    path = os.path.join(wd, "digests", "analysis", "kicks.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("ok:", path)
    for k, v in out.items():
        ncast = len(v["casts"])
        tot = sum(c["totals"]["casts"] for c in v["casts"].values())
        kk = sum(c["totals"]["kicked"] for c in v["casts"].values())
        print("  %-34s %d cast-types, %d instances, %d kicked, %d never-kicked"
              % (k, ncast, tot, kk, len(v["never"])))


if __name__ == "__main__":
    main()
