#!/usr/bin/env python3
"""Analysis: deep_* tables -> sourced facts (pull, timestamp) as JSON digests.
Read-only on the db; outputs to <workdir>/digests/analysis/.

CLI:
    python3 analyze.py all                 # run every module
    python3 analyze.py pacing              # night segments boss/trash/idle
    python3 analyze.py deaths  [--fight N] # readable recap per death
    python3 analyze.py cdmap   [--fight N] # raid CDs vs DTPS curve (naked peaks)
    python3 analyze.py phases              # measured phases per pull
    python3 analyze.py heals   [--fight N] # overheal/CPM/mana/targets/gaps
    python3 analyze.py dispels             # reactivity per dispel event
    python3 analyze.py avoidable           # player x mechanic heatmap data
    python3 analyze.py execution [--fight N]  # CPM/downtime/DoT uptime/CD usage
    python3 analyze.py bench               # us vs top1/top2, SAME formulas
    python3 analyze.py bossdigest          # per-boss timeline-ready JSON

Core rules baked in (do not "simplify" them away):
  * spec is PER PULL (mid-night respecs) — players(be, code, fid).
  * DoT/buff uptime divides by PULL duration (WCL standard), never player life.
  * healer HoT uptime = tracked by SOURCE, union of intervals (>=1 target up).
  * exec_row() is the ONE formula set used for our players AND top parses.
  * top parses with cpm==0 are excluded (actor mis-resolution guard).
  * multi-report (one ID over several nights): pull numbers are GLOBAL per
    (encounter, difficulty), chronological across nights (pulls_all). Adding
    a LATER report never renumbers earlier pulls. actor_id is PER REPORT —
    cross-night player identity is the player NAME; digests carry
    report/night on every row.
"""
import argparse
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wcl import (Backend, fmt_dur, gql, load_config, load_env, report_codes,
                 unwrap, workdir_from_args)
from ingest import RAID_CDS


def out_dir(be):
    p = os.path.join(be.workdir, "digests", "analysis")
    os.makedirs(p, exist_ok=True)
    return p


def players(be, code, fid=None):
    """actor_id -> {player_name, class, spec, role}. WITH fid = THIS fight's
    compo (mandatory whenever spec matters: respecs happen mid-night).
    Without fid: first compo seen (stable name/class only)."""
    out = {}
    if fid is not None:
        for r in be.con.execute(
                "SELECT actor_id, player_name, class, spec, role "
                "FROM composition WHERE report=? AND fight_id=?", (code, fid)):
            out[r["actor_id"]] = dict(r)
        return out
    for r in be.con.execute(
            "SELECT DISTINCT actor_id, player_name, class, spec, role "
            "FROM composition WHERE report=?", (code,)):
        out.setdefault(r["actor_id"], dict(r))
    return out


def actor_names(be, code):
    return {r["actor_id"]: r["name"] for r in be.con.execute(
        "SELECT actor_id, name FROM actor_name WHERE report=?", (code,))}


def pulls(be, code):
    return [dict(r) for r in be.con.execute(
        "SELECT * FROM pull WHERE report=? ORDER BY start_time", (code,))]


def pulls_all(be, cfg):
    """Every boss pull of the raid ID, chronological ACROSS nights.
    pull.start_time is relative to its report start -> absolute order needs
    raid_session.start_ts. Adds per pull: night (1-based), abs_ms, and
    gpull = global pull number per (encounter, difficulty) — THE displayed
    number. Chronological numbering => appending a later report never
    renumbers earlier pulls (published anchors stay valid)."""
    out = []
    for night, code in enumerate(report_codes(cfg), 1):
        s = be.con.execute("SELECT start_ts FROM raid_session WHERE report=?",
                           (code,)).fetchone()
        t0 = s["start_ts"] if s and s["start_ts"] else 0
        for p in pulls(be, code):
            p["night"] = night
            p["abs_ms"] = t0 + (p["start_time"] or 0)
            out.append(p)
    out.sort(key=lambda p: p["abs_ms"])
    cnt = {}
    for p in out:
        # Per ENCOUNTER (all difficulties), same semantics as the per-report
        # pull_number from ingest — identical numbers on single-report runs.
        k = p["encounter_id"]
        cnt[k] = cnt.get(k, 0) + 1
        p["gpull"] = cnt[k]
    return out


def phase_names_all(be, cfg):
    out = {}
    for code in report_codes(cfg):
        for enc, m in phase_names_map(be, code).items():
            out.setdefault(enc, {}).update(m)
    return out


def diff_letter(d):
    return "H" if d == 4 else "N"


def phase_names_map(be, code):
    """encounterID -> {phase_id: name} — same query as ingest => cache hit."""
    q = ('{ reportData { report(code:"%s"){ '
         'phases { encounterID phases { id name } } } } }' % code)
    rep = unwrap(gql(be, q), "reportData", "report") or {}
    return {p["encounterID"]: {x["id"]: x["name"] for x in p["phases"]}
            for p in rep.get("phases") or []}


def pull_phases(be, code, fid, enc_id, pnames):
    rows = [dict(r) for r in be.con.execute(
        "SELECT idx, phase_id, ts_rel FROM deep_phase WHERE report=? AND fight_id=? "
        "ORDER BY idx", (code, fid))]
    names = pnames.get(enc_id, {})
    return [{"phase": names.get(r["phase_id"], str(r["phase_id"])),
             "t": r["ts_rel"] / 1000.0} for r in rows]


def J_out(be, name, obj):
    p = os.path.join(out_dir(be), name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    return p


# ---------------------------------------------------------------------- pacing

def cmd_pacing(be, cfg, args):
    """Pacing PER NIGHT (idle between nights is meaningless). pacing.json =
    {"nights": [{night, report, t0_ms, segments, totals_s}], "totals_s": sum}."""
    nights, merged = [], {"boss": 0.0, "trash": 0.0, "idle": 0.0}
    for night, code in enumerate(report_codes(cfg), 1):
        sess = be.con.execute("SELECT * FROM raid_session WHERE report=?",
                              (code,)).fetchone()
        segs = []
        for p in pulls(be, code):
            segs.append({"kind": "boss", "name": p["boss"],
                         "diff": diff_letter(p["difficulty"]),
                         "kill": p["kill"], "fight_id": p["fight_id"],
                         "s": p["start_time"] / 1000.0, "e": p["end_time"] / 1000.0})
        for r in be.con.execute("SELECT * FROM trash_fight WHERE report=?", (code,)):
            segs.append({"kind": "trash", "name": r["name"], "deaths": r["deaths"],
                         "fight_id": r["fight_id"],
                         "s": r["start_time"] / 1000.0, "e": r["end_time"] / 1000.0})
        segs.sort(key=lambda x: x["s"])
        full, prev_e = [], 0.0
        for s in segs:
            if s["s"] - prev_e > 1.0 and prev_e > 0:
                full.append({"kind": "idle", "s": prev_e, "e": s["s"]})
            full.append(s)
            prev_e = max(prev_e, s["e"])
        tot = {"boss": 0.0, "trash": 0.0, "idle": 0.0}
        for s in full:
            tot[s["kind"]] += s["e"] - s["s"]
        for k in merged:
            merged[k] += tot[k]
        nights.append({"night": night, "report": code,
                       "t0_ms": sess["start_ts"] if sess else None,
                       "segments": full, "totals_s": tot})
        dur = ((sess["end_ts"] - sess["start_ts"]) / 1000.0) if sess else 0
        print(f"night {night} ({code}) {fmt_dur(dur)} — boss {fmt_dur(tot['boss'])} | "
              f"trash {fmt_dur(tot['trash'])} | out-of-combat {fmt_dur(tot['idle'])}")
        idles = sorted((s for s in full if s["kind"] == "idle"),
                       key=lambda x: x["e"] - x["s"], reverse=True)[:10]
        for i in idles:
            pi = [s for s in full if s["e"] <= i["s"] and s["kind"] != "idle"]
            ni = [s for s in full if s["s"] >= i["e"] and s["kind"] != "idle"]
            a = pi[-1]["name"] if pi else "start"
            b = ni[0]["name"] if ni else "end"
            print(f"  idle {fmt_dur(i['e']-i['s'])}  after \"{a}\" before \"{b}\"")
    out = {"nights": nights, "totals_s": merged}
    p = J_out(be, "pacing.json", out)
    print(f"-> {p}")


# ---------------------------------------------------------------------- deaths

def _parse_buffs(s):
    return [int(x) for x in (s or "").split(".") if x]


def cmd_deaths(be, cfg, args):
    Pf, An = {}, {}

    def P_at(code, fid):
        if (code, fid) not in Pf:
            Pf[(code, fid)] = players(be, code, fid)
        return Pf[(code, fid)]

    def A_of(code):
        if code not in An:
            An[code] = actor_names(be, code)
        return An[code]
    pnames = phase_names_all(be, cfg)
    out = []
    for p in pulls_all(be, cfg):
        if args.fight and p["fight_id"] != args.fight:
            continue
        code, fid = p["report"], p["fight_id"]
        for r in be.con.execute(
                "SELECT * FROM deep_death_recap WHERE report=? AND fight_id=? "
                "ORDER BY death_seq", (code, fid)):
            d = json.loads(r["payload"])
            t_rel = r["ts_rel"] / 1000.0
            phs = pull_phases(be, code, fid, p.get("encounter_id"), pnames)
            cur_phase = None
            for ph in phs:
                if ph["t"] <= t_rel:
                    cur_phase = ph["phase"]
            kb = d.get("killingBlow") or {}
            dmg = d.get("damage") or {}
            win_abs = [{"name": ab.get("name"), "total": ab.get("total"),
                        "hits": ab.get("totalUses") or ab.get("hitCount")}
                       for ab in (dmg.get("abilities") or [])[:6]]
            heal = d.get("healing") or {}
            last_hit = be.con.execute(
                "SELECT buffs FROM deep_dmg_taken "
                "WHERE report=? AND fight_id=? AND target_id=? AND ts_rel<=? "
                "ORDER BY ts_rel DESC LIMIT 1",
                (code, fid, r["actor_id"], r["ts_rel"] + 50)).fetchone()
            out.append({
                "report": code, "night": p["night"],
                "fight_id": fid, "boss": p.get("boss"), "pull": p.get("gpull"),
                "diff": diff_letter(p.get("difficulty")),
                "seq": r["death_seq"], "t": t_rel, "phase": cur_phase,
                "player": (P_at(code, fid).get(r["actor_id"]) or {}).get("player_name")
                          or A_of(code).get(r["actor_id"]),
                "role": (P_at(code, fid).get(r["actor_id"]) or {}).get("role"),
                "killing_blow": kb.get("name"), "kb_id": kb.get("guid"),
                "overkill": d.get("overkill"),
                "window_damage": win_abs,
                "window_heal_total": heal.get("total"),
                "buffs_at_death": _parse_buffs(last_hit["buffs"]) if last_hit else [],
            })
    p = J_out(be, "deaths.json", out)
    for d in out:
        wd = ", ".join(f"{w['name']} {w['total']:,}" for w in d["window_damage"][:3]
                       if w.get("total"))
        ph = f" [{d['phase']}]" if d["phase"] else ""
        print(f"{d['boss']} #{d['pull']}{d['diff']} death{d['seq']:<2} "
              f"{d['t']:6.1f}s{ph} {str(d['player']):<12} "
              f"KB={str(d['killing_blow']):<20} window: {wd}")
    print(f"-> {len(out)} deaths, {p}")


# ----------------------------------------------------------------------- cdmap

def cmd_cdmap(be, cfg, args):
    """Raid CDs vs Total DTPS curve: covered peaks / NAKED peaks / off-peak CDs."""
    Pn = {code: players(be, code) for code in report_codes(cfg)}
    out = []
    for p in pulls_all(be, cfg):
        if args.fight and p["fight_id"] != args.fight:
            continue
        code, fid = p["report"], p["fight_id"]
        P = Pn[code]
        row = be.con.execute(
            "SELECT payload FROM deep_graph WHERE report=? AND fight_id=? AND kind='dtps'",
            (code, fid)).fetchone()
        if not row:
            continue
        series = json.loads(row["payload"])
        tot = next((s for s in series if s.get("name") == "Total"), None)
        if not tot:
            continue
        data = tot.get("data") or []
        n = len(data)
        if not n:
            continue
        dur = (p.get("duration_s") or 1)
        step = dur / n
        b5 = defaultdict(float)
        for i, v in enumerate(data):
            b5[int(i * step // 5) * 5] += (v or 0)
        buckets = sorted(b5.items())
        vals = sorted(v for _, v in buckets)
        p75 = vals[int(0.75 * (len(vals) - 1))] if vals else 0
        peaks = [{"t": t, "dmg_5s": v} for t, v in buckets if v > p75 * 1.4 and v > 0]
        cds = [dict(r) for r in be.con.execute(
            "SELECT ts_rel, source_id, ability_id FROM deep_cast "
            "WHERE report=? AND fight_id=? AND ability_id IN (%s) AND type='cast' "
            "ORDER BY ts_rel" % ",".join(str(i) for i in RAID_CDS), (code, fid))]
        cd_list = [{"t": c["ts_rel"] / 1000.0,
                    "name": RAID_CDS[c["ability_id"]][0],
                    "by": (P.get(c["source_id"]) or {}).get("player_name")}
                   for c in cds]
        for pk in peaks:
            pk["covered_by"] = [c["name"] + " (" + (c["by"] or "?") + ")"
                                for c in cd_list if pk["t"] - 8 <= c["t"] <= pk["t"] + 5]
        out.append({"report": code, "night": p["night"],
                    "fight_id": fid, "boss": p.get("boss"), "pull": p.get("gpull"),
                    "diff": diff_letter(p.get("difficulty")),
                    "duration_s": dur, "p75_5s": p75,
                    "peaks": peaks, "cds": cd_list})
    p = J_out(be, "cdmap.json", out)
    for o in out:
        nu = [x for x in o["peaks"] if not x["covered_by"]]
        print(f"{o['boss']} #{o['pull']}{o['diff']} ({fmt_dur(o['duration_s'])}): "
              f"{len(o['peaks'])} peaks, {len(nu)} NAKED; {len(o['cds'])} CDs cast")
        for x in nu[:4]:
            print(f"    naked peak at {fmt_dur(x['t'])} ({x['dmg_5s']:,.0f}/5s)")
    print(f"-> {p}")


# ---------------------------------------------------------------------- phases

def cmd_phases(be, cfg, args):
    pnames = phase_names_all(be, cfg)
    for p in pulls_all(be, cfg):
        phs = pull_phases(be, p["report"], p["fight_id"], p["encounter_id"], pnames)
        if phs:
            s = " -> ".join(f"{x['phase']}@{fmt_dur(x['t'])}" for x in phs)
            print(f"{p['boss']} #{p['gpull']}{diff_letter(p['difficulty'])}: {s}")


# ----------------------------------------------------------------------- heals

def _mana_series(be, code, fid, aid):
    r = be.con.execute("SELECT payload FROM deep_graph WHERE report=? AND fight_id=? "
                       "AND kind=?", (code, fid, "mana:%d" % aid)).fetchone()
    if not r:
        return []
    series = json.loads(r["payload"])
    return (series[0].get("data") or []) if series else []


def cmd_heals(be, cfg, args):
    pls = pulls_all(be, cfg)
    if args.fight:
        pls = [p for p in pls if p["fight_id"] == args.fight]
    out = []
    for p in pls:
        code, fid, dur = p["report"], p["fight_id"], p["duration_s"]
        P = players(be, code, fid)
        healers = {a: q for a, q in P.items() if q["role"] == "healer"}
        for aid, hp in healers.items():
            row = {"report": code, "night": p["night"],
                   "fight_id": fid, "boss": p["boss"], "pull": p["gpull"],
                   "diff": diff_letter(p["difficulty"]),
                   "player": hp["player_name"], "spec": hp["spec"]}
            t = be.con.execute(
                "SELECT total, overheal FROM deep_heal_ability "
                "WHERE report=? AND fight_id=? AND actor_id=? AND ability_id=-1",
                (code, fid, aid)).fetchone()
            if t and t["total"]:
                oh = t["overheal"] or 0
                row["heal_total"] = t["total"]
                row["overheal_pct"] = round(100.0 * oh / (t["total"] + oh), 1)
            casts = be.con.execute(
                "SELECT COUNT(*) c FROM deep_cast WHERE report=? AND fight_id=? "
                "AND source_id=? AND type='cast'", (code, fid, aid)).fetchone()["c"]
            row["cpm"] = round(casts / (dur / 60.0), 1) if dur else None
            ts = [r2["ts_rel"] / 1000.0 for r2 in be.con.execute(
                "SELECT ts_rel FROM deep_cast WHERE report=? AND fight_id=? "
                "AND source_id=? AND type='cast' ORDER BY ts_rel", (code, fid, aid))]
            gaps = [{"t": round(a2, 1), "len": round(b2 - a2, 1)}
                    for a2, b2 in zip(ts, ts[1:]) if b2 - a2 > 6.0]
            row["cast_gaps_6s"] = sorted(gaps, key=lambda g: -g["len"])[:4]
            mana = _mana_series(be, code, fid, aid)
            if mana:
                vals = [v for _, v in mana]
                row["mana_end_pct"] = vals[-1]
                row["mana_min_pct"] = min(vals)
            tgt = {"tank": 0, "healer": 0, "dps": 0, "other": 0}
            for r2 in be.con.execute(
                    "SELECT target_id, SUM(amount) s FROM deep_heal_event "
                    "WHERE report=? AND fight_id=? AND source_id=? GROUP BY target_id",
                    (code, fid, aid)):
                role = (P.get(r2["target_id"]) or {}).get("role") or "other"
                tgt[role] = tgt.get(role, 0) + (r2["s"] or 0)
            if sum(tgt.values()):
                tot = sum(tgt.values())
                row["heal_to"] = {k: round(100.0 * v / tot) for k, v in tgt.items() if v}
            out.append(row)
    p = J_out(be, "heals.json", out)
    for r in out:
        if "heal_total" not in r:
            continue
        mn = (f" mana end {r.get('mana_end_pct')}% (min {r.get('mana_min_pct')}%)"
              if r.get("mana_end_pct") is not None else "")
        ht = (" -> " + "/".join(f"{k} {v}%" for k, v in (r.get("heal_to") or {}).items())
              if r.get("heal_to") else "")
        print(f"{r['boss']} #{r['pull']}{r['diff']} {r['player']:<10} "
              f"{r['heal_total']/1e6:6.1f}M oh {r.get('overheal_pct', 0):4.1f}% "
              f"cpm {r.get('cpm', 0):4.1f}{mn}{ht}")
    print(f"-> {p}")


def cmd_dispels(be, cfg, args):
    """Reactivity: applydebuff -> dispel delay per event, vs the dispellables
    list from refs/mechanics_ref.json (zone bootstrap).
    CAUTION (engraved trap): a slow dispel can BE the strategy (windowed buff
    making it free, hidden cost). Correlate before blaming — see
    references/interpretation-traps.md checklist."""
    ref_p = os.path.join(be.workdir, "refs", "mechanics_ref.json")
    dispellable = {}
    if os.path.exists(ref_p):
        ref = json.load(open(ref_p, encoding="utf-8"))
        for enc, d in ref.items():
            for x in d.get("dispellables") or []:
                if x.get("id"):
                    dispellable[int(x["id"])] = x.get("name")
    Pn = {c: players(be, c) for c in report_codes(cfg)}
    An = {c: actor_names(be, c) for c in report_codes(cfg)}
    out = []
    for p in pulls_all(be, cfg):
        code, fid = p["report"], p["fight_id"]
        P, A = Pn[code], An[code]
        disp = [dict(r) for r in be.con.execute(
            "SELECT * FROM deep_aura WHERE report=? AND fight_id=? AND kind='dispel' "
            "ORDER BY ts_rel", (code, fid))]
        applies = [dict(r) for r in be.con.execute(
            "SELECT * FROM deep_aura WHERE report=? AND fight_id=? AND kind='debuff' "
            "AND type IN ('applydebuff','applydebuffstack') ORDER BY ts_rel",
            (code, fid))]
        for d in disp:
            cand = [a for a in applies
                    if a["ability_id"] == d["ability_id"]
                    and a["target_id"] == d["target_id"]
                    and a["ts_rel"] <= d["ts_rel"]]
            delay = (d["ts_rel"] - cand[-1]["ts_rel"]) / 1000.0 if cand else None
            out.append({"report": code, "night": p["night"],
                        "fight_id": fid, "boss": p["boss"], "pull": p["gpull"],
                        "t": d["ts_rel"] / 1000.0,
                        "by": (P.get(d["source_id"]) or {}).get("player_name"),
                        "on": (P.get(d["target_id"]) or {}).get("player_name")
                              or A.get(d["target_id"]),
                        "debuff_id": d["ability_id"],
                        "debuff": dispellable.get(d["ability_id"]),
                        "delay_s": round(delay, 1) if delay is not None else None})
    p = J_out(be, "dispels.json", out)
    slow = [o for o in out if (o["delay_s"] or 0) > 5]
    print(f"{len(out)} dispels; {len(slow)} slow (>5s) — check traps checklist "
          "before calling any of them a fault")
    for o in sorted(out, key=lambda x: -(x["delay_s"] or 0))[:12]:
        print(f"  {o['boss']} #{o['pull']} {o['t']:6.1f}s {str(o['by']):<10} dispel "
              f"{o['debuff'] or o['debuff_id']} on {o['on']} after {o['delay_s']}s")
    print(f"-> {p}")


# ------------------------------------------------------------------- avoidable

def cmd_avoidable(be, cfg, args):
    """Player x mechanic heatmap data from deep_dmg_taken x zone mechanics ref."""
    ref_p = os.path.join(be.workdir, "refs", "mechanics_ref.json")
    if not os.path.exists(ref_p):
        sys.exit(f"missing {ref_p} — run the zone bootstrap first "
                 "(references/zone-bootstrap.md)")
    ref = json.load(open(ref_p, encoding="utf-8"))
    out = []
    for p in pulls_all(be, cfg):
        code, fid = p["report"], p["fight_id"]
        P = players(be, code, fid)
        enc = ref.get(str(p["encounter_id"])) or {}
        mechs = enc.get("mechanics") or {}
        for r in be.con.execute(
                "SELECT target_id, ability_id, COUNT(*) hits, "
                "SUM(amount+COALESCE(absorbed,0)) tot FROM deep_dmg_taken "
                "WHERE report=? AND fight_id=? GROUP BY target_id, ability_id",
                (code, fid)):
            m = mechs.get(str(r["ability_id"]))
            if not m or m.get("class") not in ("avoidable", "reducible", "soak"):
                continue
            pl = P.get(r["target_id"])
            if not pl:
                continue
            out.append({"report": code, "night": p["night"],
                        "fight_id": fid, "boss": p["boss"], "pull": p["gpull"],
                        "diff": diff_letter(p["difficulty"]),
                        "player": pl["player_name"], "role": pl["role"],
                        "ability_id": r["ability_id"], "ability": m.get("name"),
                        "class": m["class"], "hits": r["hits"], "total": r["tot"]})
    p = J_out(be, "avoidable.json", out)
    agg = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for o in out:
        if o["class"] == "avoidable":
            a = agg[(o["boss"], o["diff"])][o["player"]]
            a[0] += o["hits"]
            a[1] += o["total"]
    for (boss, diff), m in agg.items():
        rank = sorted(m.items(), key=lambda kv: -kv[1][1])
        s = " ; ".join(f"{k} {v[0]}x/{v[1]/1e6:.1f}M" for k, v in rank[:5])
        print(f"{boss} {diff} strictly avoidable: {s}")
    print(f"-> {len(out)} rows, {p}")


# ------------------------------------------------------------------- execution

def _spec_kpis(be):
    p = os.path.join(be.workdir, "refs", "spec_kpis.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}


def _dead_windows(be, code, fid, aid, dur_ms):
    """Dead windows: [death, next own cast or fight end]."""
    deaths = [r["ts_rel"] for r in be.con.execute(
        "SELECT ts_rel FROM deep_death_recap WHERE report=? AND fight_id=? "
        "AND actor_id=? ORDER BY ts_rel", (code, fid, aid))]
    wins = []
    for d in deaths:
        nxt = be.con.execute(
            "SELECT MIN(ts_rel) t FROM deep_cast WHERE report=? AND fight_id=? "
            "AND source_id=? AND ts_rel>?", (code, fid, aid, d + 1000)).fetchone()["t"]
        wins.append((d, nxt if nxt is not None else dur_ms))
    return wins


def _active_ms(dur_ms, dead_wins):
    return max(1, dur_ms - sum(e - s for s, e in dead_wins))


def _aura_windows(rows, t_end_ms):
    """[(s,e)] windows per (target, ability). Carryover handled: orphan remove
    = active since 0; apply without remove = until fight end."""
    open_t, wins = {}, defaultdict(list)
    for r in rows:
        k = (r["target_id"], r["ability_id"])
        ty = r["type"] or ""
        if ty.startswith("apply") or ty.startswith("refresh"):
            open_t.setdefault(k, r["ts_rel"])
        elif ty.startswith("remove"):
            s = open_t.pop(k, 0)
            if r["ts_rel"] > s:
                wins[k].append((s, r["ts_rel"]))
    for k, s in open_t.items():
        if t_end_ms > s:
            wins[k].append((s, t_end_ms))
    return wins


def _aura_uptime_ms(rows, t_end_ms):
    return {k: sum(e - s for s, e in v)
            for k, v in _aura_windows(rows, t_end_ms).items()}


def _union_ms(intervals):
    """Union length of [(s,e)] intervals (the '>=1 target up' uptime)."""
    tot, cur_s, cur_e = 0, None, None
    for s, e in sorted(intervals):
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                tot += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        tot += cur_e - cur_s
    return tot


def exec_row(be, code, fid, dur_ms, aid, sk):
    """Execution KPIs for ONE actor on ONE fight — the SAME formulas for our
    players and for top parses (absolute benchmark symmetry)."""
    dead = _dead_windows(be, code, fid, aid, dur_ms)
    act_ms = _active_ms(dur_ms, dead)
    casts = [dict(r) for r in be.con.execute(
        "SELECT ts_rel, ability_id FROM deep_cast WHERE report=? AND fight_id=? "
        "AND source_id=? AND type='cast' ORDER BY ts_rel", (code, fid, aid))]
    row = {"active_s": round(act_ms / 1000.0, 1), "deaths": len(dead),
           "cpm": round(len(casts) / (act_ms / 60000.0), 1) if act_ms else 0}
    gcd = (sk.get("gcd_base_ms") or 1500) / 1000.0
    gaps_ms, last = 0, 0
    for c in casts:
        g = c["ts_rel"] - last
        in_dead = any(s <= last and c["ts_rel"] <= e + 1500 for s, e in dead)
        if g > gcd * 1000 + 100 and not in_dead:
            gaps_ms += g - gcd * 1000
        last = c["ts_rel"]
    if dur_ms - last > 2500 and not any(s <= last for s, e in dead):
        gaps_ms += dur_ms - last - 1500
    row["downtime_pct"] = round(100.0 * min(gaps_ms, act_ms) / act_ms, 1)
    # DoT uptime on main target (the one THIS actor hit the most).
    # Reference = PULL duration (WCL standard), never player lifetime.
    dots = [d for d in (sk.get("dots_uptime") or []) if d.get("id")]
    if dots:
        main_tgt = be.con.execute(
            "SELECT target_id FROM deep_dmg_done WHERE report=? AND fight_id=? "
            "AND source_id=? GROUP BY target_id ORDER BY SUM(amount) DESC LIMIT 1",
            (code, fid, aid)).fetchone()
        if main_tgt:
            mt = main_tgt["target_id"]
            ids = ",".join(str(d["id"]) for d in dots)
            rows = [dict(r) for r in be.con.execute(
                "SELECT ts_rel, type, target_id, ability_id FROM deep_aura "
                "WHERE report=? AND fight_id=? AND kind='debuff_enemy' "
                "AND source_id=? AND ability_id IN (%s) ORDER BY ts_rel" % ids,
                (code, fid, aid))]
            up = _aura_uptime_ms(rows, dur_ms)
            row["dots"] = {d["name"]: round(min(100.0, 100.0 * up.get((mt, d["id"]), 0)
                                            / dur_ms), 1) for d in dots}
    cds = {}
    for c in (sk.get("cds_major") or []):
        if not c.get("id"):
            continue
        used = sum(1 for x in casts if x["ability_id"] == c["id"])
        poss = 1 + int((act_ms / 1000.0) / c.get("cd_s", 180)) if act_ms else 1
        cds[c["name"]] = {"used": used, "possible": poss}
    if cds:
        row["cds"] = cds
    # Buff/proc uptime. DPS/tank: auras ON self. Healer: auras CAST BY them on
    # anyone (HoTs/absorbs) — '>=1 target' union-of-intervals uptime.
    bt = [b for b in (sk.get("buffs_track") or []) if b.get("id")]
    if bt:
        ids = ",".join(str(b["id"]) for b in bt)
        if sk.get("role") == "healer":
            rows = [dict(r) for r in be.con.execute(
                "SELECT ts_rel, type, target_id, ability_id FROM deep_aura "
                "WHERE report=? AND fight_id=? AND kind='buff' AND source_id=? "
                "AND ability_id IN (%s) ORDER BY ts_rel" % ids, (code, fid, aid))]
            wins = _aura_windows(rows, dur_ms)
            by_ab = defaultdict(list)
            for (tgt, ab), v in wins.items():
                by_ab[ab].extend(v)
            row["buffs"] = {b["name"]: round(min(100.0, 100.0 * _union_ms(
                by_ab.get(b["id"], [])) / dur_ms), 1) for b in bt}
        else:
            rows = [dict(r) for r in be.con.execute(
                "SELECT ts_rel, type, target_id, ability_id FROM deep_aura "
                "WHERE report=? AND fight_id=? AND kind='buff' AND target_id=? "
                "AND ability_id IN (%s) ORDER BY ts_rel" % ids, (code, fid, aid))]
            up = _aura_uptime_ms(rows, dur_ms)
            row["buffs"] = {b["name"]: round(min(100.0, 100.0 * up.get((aid, b["id"]), 0)
                                             / dur_ms), 1) for b in bt}
    return row


def cmd_execution(be, cfg, args):
    K = _spec_kpis(be)
    if not K:
        sys.exit("missing refs/spec_kpis.json — run the zone/spec bootstrap first")
    out = []
    pls = pulls_all(be, cfg)
    if args.fight:
        pls = [p for p in pls if p["fight_id"] == args.fight]
    for p in pls:
        code, fid, dur_ms = p["report"], p["fight_id"], int(p["duration_s"] * 1000)
        for aid, pl in players(be, code, fid).items():
            sk = K.get(f"{pl['class']}-{pl['spec']}")
            if not sk:
                continue
            row = {"report": code, "night": p["night"],
                   "fight_id": fid, "boss": p["boss"], "pull": p["gpull"],
                   "diff": diff_letter(p["difficulty"]),
                   "player": pl["player_name"], "spec": pl["spec"], "role": pl["role"]}
            row.update(exec_row(be, code, fid, dur_ms, aid, sk))
            out.append(row)
    suf = f"_f{args.fight}" if args.fight else ""
    p = J_out(be, f"execution{suf}.json", out)
    for r in out:
        dots = " ".join(f"{k} {v}%" for k, v in (r.get("dots") or {}).items())
        print(f"{r['boss']} #{r['pull']}{r['diff']} {r['player']:<12} {r['spec']:<12} "
              f"cpm {r['cpm']:5.1f} down {r['downtime_pct']:4.1f}% {dots}")
    print(f"-> {len(out)} rows, {p}")


def cmd_bench(be, cfg, args):
    """Us vs top1/top2 same spec — SAME exec_row() formulas on both sides +
    amount/s + kill duration (structural factor shown separately, never mixed).
    CAUTION (engraved): only compare players under EQUAL conditions — kills,
    both alive, no asymmetric assignment/eviction phase. See traps checklist."""
    K = _spec_kpis(be)
    if not K:
        sys.exit("missing refs/spec_kpis.json")
    out = []
    for p in [x for x in pulls_all(be, cfg) if x["kill"]]:
        code, fid, dur_ms = p["report"], p["fight_id"], int(p["duration_s"] * 1000)
        for aid, pl in players(be, code, fid).items():
            sk = K.get(f"{pl['class']}-{pl['spec']}")
            if not sk:
                continue
            mine = exec_row(be, code, fid, dur_ms, aid, sk)
            t = be.con.execute(
                "SELECT total FROM player_fight WHERE report=? AND "
                "fight_id=? AND actor_id=? AND data_type=?",
                (code, fid, aid, "Healing" if pl["role"] == "healer"
                 else "DamageDone")).fetchone()
            if t and t["total"]:
                mine["amount_ps"] = round(t["total"] / (dur_ms / 1000.0))
            tops = []
            for tp in be.con.execute(
                    "SELECT tp.* FROM top_parse tp WHERE tp.encounter_id=? AND "
                    "tp.difficulty=? AND tp.spec_key=? AND EXISTS (SELECT 1 FROM "
                    "done_marker dd WHERE dd.report=tp.report AND dd.fight_id=tp.fight_id "
                    "AND dd.what='top:'||tp.player_name) ORDER BY tp.rank",
                    (p["encounter_id"], p["difficulty"], f"{pl['class']}-{pl['spec']}")):
                if tp["actor_id"] is None:
                    continue
                tdur = int((tp["duration_s"] or 0) * 1000)
                if not tdur:
                    continue
                trow = exec_row(be, tp["report"], tp["fight_id"], tdur,
                                tp["actor_id"], sk)
                if not trow.get("cpm"):
                    continue    # incomplete top extraction (actor mis-resolved)
                trow.update({"rank": tp["rank"], "player": tp["player_name"],
                             "duration_s": tp["duration_s"],
                             "amount_ps": round(tp["amount"] or 0)})
                tops.append(trow)
            out.append({"report": code, "night": p["night"],
                        "boss": p["boss"], "diff": diff_letter(p["difficulty"]),
                        "fight_id": fid, "duration_s": p["duration_s"],
                        "player": pl["player_name"], "spec": pl["spec"],
                        "role": pl["role"], "mine": mine, "tops": tops})
    p = J_out(be, "bench.json", out)
    for o in out:
        if not o["tops"]:
            continue
        t1 = o["tops"][0]
        print(f"{o['boss']} {o['player']:<12} {o['spec']:<11} "
              f"us {o['mine'].get('amount_ps', 0):>7,}/s in {o['duration_s']:.0f}s "
              f"| top1 {t1.get('amount_ps', 0):>7,}/s in {t1.get('duration_s', 0):.0f}s "
              f"| down {o['mine']['downtime_pct']:.0f}% vs {t1['downtime_pct']:.0f}%")
    print(f"-> {len(out)} rows, {p}")


# -------------------------------------------------------- boss digest (timeline)

def boss_digest(be, cfg, enc_id, diff):
    """Timeline-ready JSON per boss (all pulls, ALL nights): phases, deaths,
    CDs, per-mechanic 5s damage buckets, for the page generator charts."""
    pnames = phase_names_all(be, cfg)
    An = {c: actor_names(be, c) for c in report_codes(cfg)}
    out = {"encounter_id": enc_id, "pulls": []}
    for p in [x for x in pulls_all(be, cfg)
              if x["encounter_id"] == enc_id and x["difficulty"] == diff]:
        code, fid, dur = p["report"], p["fight_id"], p["duration_s"]
        A = An[code]
        P = players(be, code, fid)
        o = {"report": code, "night": p["night"],
             "fight_id": fid, "pull": p["gpull"], "kill": p["kill"],
             "duration_s": dur, "fight_pct": p["fight_pct"],
             "phases": pull_phases(be, code, fid, enc_id, pnames),
             "compo": {v["player_name"]: f"{v['spec']} {v['role']}"
                       for v in P.values()}}
        o["deaths"] = []
        for r in be.con.execute(
                "SELECT actor_id, ts_rel, payload FROM deep_death_recap "
                "WHERE report=? AND fight_id=? ORDER BY death_seq", (code, fid)):
            d = json.loads(r["payload"])
            kb = d.get("killingBlow") or {}
            o["deaths"].append({
                "t": r["ts_rel"] / 1000.0,
                "player": (P.get(r["actor_id"]) or {}).get("player_name")
                          or A.get(r["actor_id"]),
                "kb": kb.get("name"), "kb_id": kb.get("guid"),
                "window": [{"name": a.get("name"), "total": a.get("total")}
                           for a in (d.get("damage", {}).get("abilities") or [])[:4]]})
        o["cds"] = [{"t": r["ts_rel"] / 1000.0,
                     "name": RAID_CDS[r["ability_id"]][0],
                     "by": (P.get(r["source_id"]) or {}).get("player_name")}
                    for r in be.con.execute(
                        "SELECT ts_rel, source_id, ability_id FROM deep_cast "
                        "WHERE report=? AND fight_id=? AND type='cast' "
                        "AND ability_id IN (%s) ORDER BY ts_rel"
                        % ",".join(str(i) for i in RAID_CDS), (code, fid))]
        mechs = [r["ability_id"] for r in be.con.execute(
            "SELECT ability_id, SUM(amount+COALESCE(absorbed,0)) t FROM deep_dmg_taken "
            "WHERE report=? AND fight_id=? AND ability_id>1 GROUP BY ability_id "
            "ORDER BY t DESC LIMIT 8", (code, fid))]
        names = {r["ability_id"]: r["n"] for r in be.con.execute(
            "SELECT DISTINCT dt.ability_id, COALESCE(pa.ability_name,'') n "
            "FROM deep_dmg_taken dt LEFT JOIN player_ability pa "
            "ON pa.report=dt.report AND pa.fight_id=dt.fight_id "
            "AND pa.ability_id=dt.ability_id AND pa.actor_id=0 "
            "WHERE dt.report=? AND dt.fight_id=?", (code, fid))}
        mech_series = {}
        for ab in mechs:
            buckets = defaultdict(float)
            for r in be.con.execute(
                    "SELECT ts_rel, amount, absorbed FROM deep_dmg_taken "
                    "WHERE report=? AND fight_id=? AND ability_id=?", (code, fid, ab)):
                buckets[int(r["ts_rel"] / 5000)] += (r["amount"] or 0) + (r["absorbed"] or 0)
            nb = int(dur / 5) + 1
            key = names.get(ab) or str(ab)
            mech_series[key] = {"id": ab,
                                "buckets": [round(buckets.get(i, 0)) for i in range(nb)]}
        o["mech_5s"] = mech_series
        out["pulls"].append(o)
    return out


def cmd_bossdigest(be, cfg, args):
    codes = report_codes(cfg)
    ph = ",".join("?" for _ in codes)
    encs, seen = [], set()
    for r in be.con.execute(
            f"SELECT DISTINCT encounter_id, boss, difficulty FROM pull "
            f"WHERE report IN ({ph})", codes):
        if (r["encounter_id"], r["difficulty"]) in seen:
            continue
        seen.add((r["encounter_id"], r["difficulty"]))
        encs.append(dict(r))
    if args.fight:    # --fight doubles as encounter_id filter here
        encs = [e for e in encs if e["encounter_id"] == args.fight]
    for e in encs:
        d = boss_digest(be, cfg, e["encounter_id"], e["difficulty"])
        d["boss"] = e["boss"]
        d["difficulty"] = e["difficulty"]
        fn = "boss_%d_%s.json" % (e["encounter_id"], diff_letter(e["difficulty"]))
        with open(os.path.join(out_dir(be), fn), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        print(f"{e['boss']:24} {diff_letter(e['difficulty'])}: "
              f"{len(d['pulls'])} pulls -> {fn}")


MODULES = {"pacing": cmd_pacing, "deaths": cmd_deaths, "cdmap": cmd_cdmap,
           "phases": cmd_phases, "heals": cmd_heals, "dispels": cmd_dispels,
           "avoidable": cmd_avoidable, "execution": cmd_execution,
           "bench": cmd_bench, "bossdigest": cmd_bossdigest}


def cmd_all(be, cfg, args):
    for name, fn in MODULES.items():
        print(f"== {name} ==")
        try:
            fn(be, cfg, args)
        except SystemExit as e:
            print(f"  SKIPPED: {e}")
    print("analyze all done")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in list(MODULES) + ["all"]:
        sp = sub.add_parser(name)
        sp.add_argument("--fight", type=int, default=None)
    args = ap.parse_args()
    wd = workdir_from_args(args)
    load_env(wd)
    be = Backend(wd)
    cfg = load_config(wd)
    ({"all": cmd_all} | MODULES)[args.cmd](be, cfg, args)


if __name__ == "__main__":
    main()
