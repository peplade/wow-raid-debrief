#!/usr/bin/env python3
"""Extraction: WCL report -> sqlite workdir. Aggregates (session) + raw events
(deep/extras) + trash + top parses for benchmarking. Idempotent + resumable
(wcl_raw cache + done_marker): re-running any command is free and safe.

CLI (run from anywhere; workdir = --workdir / $RAID_WORKDIR / cwd):
    python3 ingest.py init --report CODE [--report CODE2 ...] --guild NAME
                           [--label id-YYYY-MM-DD] [--lang fr] [--size 10]
    python3 ingest.py add-report --report CODE2   # complete an EXISTING workdir
                                         # (lockout finished on a later night)
    python3 ingest.py session            # pulls, compo, totals, deaths, CDs, conso
    python3 ingest.py deep               # raw events per boss pull (the core)
    python3 ingest.py extras             # dispel/interrupt/heal events + enemy casts
    python3 ingest.py trash              # trash fights (deaths/danger/pacing)
    python3 ingest.py tops               # top1/top2 rankings per spec x boss
    python3 ingest.py top-detail         # targeted events for each top parse
    python3 ingest.py all                # session+deep+extras+trash+tops+top-detail
    python3 ingest.py status             # binary gate: what is missing
    python3 ingest.py quota              # API points spent this hour
    python3 ingest.py benchmark [--topn 10]      # zone top logs (avoidable inference)
    python3 ingest.py infer-avoidable    # candidates: tops take ~0, we take >0

Multi-report (one raid ID over several nights): every command loops over
cfg["reports"] in chronological order. Adding a LATER report never disturbs
what was already extracted (cache + done markers) nor the global pull
numbering of earlier nights (chronological), so a published debrief can be
COMPLETED by `add-report` + re-running all/analyze/pages.

API budget: a 10-player night (~20 boss pulls + 30 trash + tops) costs
~1000-1500 points out of 3600/hour (25-player nights cost more: event volume
scales with roster size). Quota is self-managed at the client level.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wcl import (Backend, DIFF_NAME, fetch_events, fetch_graph, fetch_table,
                 fmt_dur, gql, ingest_actors, json_field, load_config, load_env,
                 quota, rankings_reports, report_codes, report_meta,
                 save_config, unwrap, workdir_from_args)

# MoP raid cooldowns tracked (spell_id -> (name, indicative cooldown_s)).
# Offensive raid CDs included (banners, lust). Extend per expansion if needed.
RAID_CDS = {
    62618:  ("Power Word: Barrier", 180),
    33206:  ("Pain Suppression", 180),
    47788:  ("Guardian Spirit", 180),
    64843:  ("Divine Hymn", 480),
    64901:  ("Hymn of Hope", 360),
    15286:  ("Vampiric Embrace", 180),
    31821:  ("Devotion Aura", 180),
    633:    ("Lay on Hands", 600),
    6940:   ("Hand of Sacrifice", 120),
    740:    ("Tranquility", 480),
    102342: ("Ironbark", 90),
    98008:  ("Spirit Link Totem", 180),
    108280: ("Healing Tide Totem", 180),
    16190:  ("Mana Tide Totem", 180),
    108281: ("Ancestral Guidance", 120),
    115310: ("Revival", 180),
    116849: ("Life Cocoon", 120),
    97462:  ("Rallying Cry", 180),
    114203: ("Demoralizing Banner", 180),
    114207: ("Skull Banner", 180),
    76577:  ("Smoke Bomb", 180),
    51052:  ("Anti-Magic Zone", 120),
    120668: ("Stormlash Totem", 300),
    2825:   ("Bloodlust", 600),
    32182:  ("Heroism", 600),
    80353:  ("Time Warp", 600),
    90355:  ("Ancient Hysteria", 600),
}

# MoP potion/flask buffs (applybuff = potion used; in combatantinfo = pre-pot).
POTION_BUFFS = {105697: "Virmen's Bite", 105706: "Potion of Mogu Power",
                105702: "Jade Serpent Potion", 105698: "Potion of the Mountains",
                105701: "Potion of Focus"}
FLASK_BUFFS = {105689: "Flask of Spring Blossoms", 105691: "Flask of the Warm Sun",
               105693: "Flask of Falling Leaves", 105694: "Flask of the Earth",
               105696: "Flask of Winter's Bite", 105617: "Alchemist's Flask",
               127230: "Crystal of Insanity"}
WELL_FED_NAMES = ("well fed", "bien nourri")

ROLE_METRIC = {"healer": "hps", "tank": "dps", "dps": "dps"}
HEALER_SPECS = {"Restoration", "Discipline", "Holy", "Mistweaver"}


def fights_query(be, code, encounters_only=True):
    kt = "(killType:Encounters)" if encounters_only else ""
    q = ('{ reportData { report(code:"%s"){ fights%s '
         '{ id name encounterID difficulty size kill bossPercentage '
         'fightPercentage lastPhase startTime endTime '
         'phaseTransitions { id startTime } } } } }' % (code, kt))
    return unwrap(gql(be, q), "reportData", "report", "fights") or []


# ------------------------------------------------------------------------ init

def _parse_report_args(report_args):
    """--report repeatable AND comma-splittable: -r A -r B == -r A,B."""
    return [c.strip() for r in (report_args or []) for c in r.split(",")
            if c.strip()]


def _validated_codes(be, codes, zone_id=None):
    """Fetch metas, enforce single zone, return (chronological codes, metas)."""
    metas = {c: report_meta(be, c) for c in dict.fromkeys(codes)}
    zones = {c: (metas[c].get("zone") or {}) for c in metas}
    ids = {z.get("id") for z in zones.values()} | ({zone_id} if zone_id else set())
    if len(ids) > 1:
        sys.exit("reports span DIFFERENT zones — one workdir = one raid ID:\n  "
                 + "\n  ".join(f"{c}: {zones[c].get('name')} (id {zones[c].get('id')})"
                               for c in metas))
    ordered = sorted(metas, key=lambda c: metas[c]["startTime"])
    return ordered, metas


def _print_nights(codes, metas):
    for night, c in enumerate(codes, 1):
        m = metas[c]
        d = datetime.fromtimestamp(m["startTime"] / 1000,
                                   tz=timezone.utc).strftime("%Y-%m-%d")
        print(f"  night {night}: {c} | {d} | {m.get('title')}")


def cmd_init(be, cfg, args):
    if not args.report or not args.guild:
        sys.exit("init requires --report and --guild")
    load_env(be.workdir)
    codes, metas = _validated_codes(be, _parse_report_args(args.report))
    first = metas[codes[0]]
    zone = first.get("zone") or {}
    label = args.label or "id-" + datetime.fromtimestamp(
        first["startTime"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    cfg = {
        "reports": codes,
        "report": codes[0],            # kept for backward compatibility
        "guild": args.guild,
        "label": label,
        "lang": args.lang,
        "size": args.size,
        "zone_id": zone.get("id"),
        "zone_name": zone.get("name"),
        "title": first.get("title"),
    }
    save_config(be.workdir, cfg)
    for d in ("digests", "digests/analysis", "refs", "content", "pages"):
        os.makedirs(os.path.join(be.workdir, d), exist_ok=True)
    print(f"workdir ready: {be.workdir}")
    print(f"  zone {zone.get('name')} (id {zone.get('id')}) | label {label} | "
          f"{len(codes)} report(s)")
    _print_nights(codes, metas)
    print("next: python3 ingest.py all   (or step by step: session, deep, ...)")


def cmd_add_report(be, cfg, args):
    """Complete an EXISTING workdir with later report(s) — the lockout
    continued on another night after the debrief was started (or published).
    Safe by construction:
      * extraction is incremental (cache + done markers): re-running `all`
        only fetches the NEW report(s);
      * global pull numbers are chronological, so pulls of earlier nights
        keep their numbers — existing verdicts.md anchors and content
        fragments (pull_<n>.html) stay valid;
      * analyze + pages + probe are full regens (free, local).
    """
    if not args.report:
        sys.exit("add-report requires --report")
    load_env(be.workdir)
    new = _parse_report_args(args.report)
    merged, metas = _validated_codes(be, report_codes(cfg) + new,
                                     zone_id=cfg.get("zone_id"))
    added = [c for c in merged if c not in report_codes(cfg)]
    cfg["reports"] = merged
    cfg["report"] = merged[0]
    save_config(be.workdir, cfg)
    print(f"{len(added)} report(s) added; raid ID now spans {len(merged)} night(s):")
    _print_nights(merged, metas)
    if added and merged[-1] not in added:
        print("NOTE: an added report is EARLIER than an existing one — global "
              "pull numbers of later nights shift; re-check content fragment "
              "anchors (pull_<n>.html) after regen.")
    print("next: python3 ingest.py all      # only the new report(s) cost points")
    print("then: analyze.py all && pages.py && probe.py   # full regen, free")


# --------------------------------------------------------------------- session

def cmd_session(be, cfg, args):
    """v1 aggregates: pulls, compo (SPEC PER PULL), totals, deaths, raid CD
    casts, interrupt/dispel aggregates, consumables, damage-taken-by-ability."""
    avoid = {r["ability_id"] for r in be.con.execute(
        "SELECT ability_id FROM avoidable_ref WHERE status IN ('candidate','validated')")}
    codes = report_codes(cfg)
    total = 0
    for night, code in enumerate(codes, 1):
        if len(codes) > 1:
            print(f"-- night {night}/{len(codes)}: {code} --")
        rep = report_meta(be, code)
        be.upsert("raid_session", {
            "report": code, "guild": cfg["guild"],
            "zone": (rep.get("zone") or {}).get("name"),
            "zone_id": (rep.get("zone") or {}).get("id"),
            "raid_label": cfg["label"], "title": rep.get("title"),
            "start_ts": rep.get("startTime"), "end_ts": rep.get("endTime"),
            "ingested_at": datetime.now(timezone.utc).isoformat(),
        }, ["report"])

        fights = fights_query(be, code)
        if not fights:
            sys.exit(f"no boss pulls in report {code}")
        seen_per_boss = {}
        for f in sorted(fights, key=lambda x: x["startTime"]):
            fid, fs, fe = f["id"], f["startTime"], f["endTime"]
            n = seen_per_boss[f["encounterID"]] = seen_per_boss.get(f["encounterID"], 0) + 1
            be.upsert("pull", {
                "report": code, "fight_id": fid, "encounter_id": f["encounterID"],
                "boss": f.get("name"), "difficulty": f.get("difficulty"),
                "size": f.get("size"), "kill": 1 if f.get("kill") else 0,
                "boss_pct": f.get("bossPercentage"), "fight_pct": f.get("fightPercentage"),
                "last_phase": f.get("lastPhase"), "duration_s": (fe - fs) / 1000.0,
                "start_time": fs, "end_time": fe, "pull_number": n,
            }, ["report", "fight_id"])
            if not be.done(code, fid, "session"):
                ingest_pull_session(be, code, fid, fs, fe, avoid)
                be.mark(code, fid, "session")
            res = "kill" if f.get("kill") else f"wipe {f.get('bossPercentage')}%"
            print(f"  pull #{n:<3} {f.get('name', '?')[:24]:24} "
                  f"{(fe - fs) / 1000:6.0f}s  {res}", flush=True)
        be.commit()
        total += len(fights)
    print(f"ok: {total} pulls -> label {cfg['label']}")


def ingest_pull_session(be, code, fid, fs, fe, avoid_ids):
    qm = ('{ reportData { report(code:"%s"){ playerDetails(fightIDs:[%d]) } } }'
          % (code, fid))
    pd = json_field(unwrap(gql(be, qm), "reportData", "report", "playerDetails")) or {}
    if isinstance(pd, dict):
        pd = pd.get("data", pd).get("playerDetails", pd)
    if not isinstance(pd, dict):
        pd = {}
    names = {}
    for role in ("tanks", "healers", "dps"):
        for a in pd.get(role, []) or []:
            specs = a.get("specs") or []
            spec = ""
            if specs:
                spec = specs[0] if isinstance(specs[0], str) else (specs[0].get("spec") or "")
            names[a.get("id")] = a.get("name")
            be.upsert("composition", {
                "report": code, "fight_id": fid, "actor_id": a.get("id"),
                "player_name": a.get("name"), "class": a.get("type"), "spec": spec,
                "role": {"tanks": "tank", "healers": "healer", "dps": "dps"}[role],
                "item_level": a.get("minItemLevel") or a.get("itemLevel"),
            }, ["report", "fight_id", "actor_id"])

    for dt in ("Healing", "DamageDone", "DamageTaken"):
        t = fetch_table(be, code, fid, fs, fe, dt, ",hostilityType:Friendlies")
        for e in t.get("data", {}).get("entries", []):
            if e.get("id") is None:
                continue
            be.upsert("player_fight", {
                "report": code, "fight_id": fid, "actor_id": e["id"], "data_type": dt,
                "total": e.get("total"), "active_time": e.get("activeTime"),
            }, ["report", "fight_id", "actor_id", "data_type"])

    ingest_damage_taken_by_ability(be, code, fid, fs, fe)

    t = fetch_table(be, code, fid, fs, fe, "Deaths")
    for i, d in enumerate(sorted(t.get("data", {}).get("entries", []),
                                 key=lambda x: x.get("timestamp") or 0), 1):
        dt_ms = d.get("timestamp") or 0
        if dt_ms > (fe - fs):           # report-absolute -> pull-relative
            dt_ms -= fs
        kb = d.get("killingBlow") or {}
        be.upsert("death", {
            "report": code, "fight_id": fid, "seq": i, "actor_id": d.get("id"),
            "player_name": d.get("name"), "death_time": dt_ms,
            "ability_id": kb.get("guid"), "ability_name": kb.get("name"),
            "overkill": d.get("overkill"),
        }, ["report", "fight_id", "seq"])

    flt = ',filterExpression:"ability.id IN (%s)"' % ",".join(str(i) for i in RAID_CDS)
    for i, ev in enumerate(fetch_events(be, code, fid, fs, fe, "Casts", flt), 1):
        sid = ev.get("sourceID")
        be.upsert("raid_event", {
            "report": code, "fight_id": fid, "kind": "cd_cast", "seq": i,
            "timestamp": (ev.get("timestamp") or fs) - fs,
            "source_id": sid, "source_name": names.get(sid),
            "target_id": ev.get("targetID"), "target_name": None,
            "ability_id": ev.get("abilityGameID"),
            "ability_name": RAID_CDS.get(ev.get("abilityGameID"), ("?",))[0],
            "amount": None, "payload": None,
        }, ["report", "fight_id", "kind", "seq"])

    # Interrupts / dispels (per-player + per-ability views).
    def _flat(entries):
        out = []
        for e in entries or []:
            if e.get("details") is not None or e.get("guid") is not None:
                out.append(e)
            out.extend(_flat(e.get("entries")))
        return out

    for kind, dt in (("interrupt", "Interrupts"), ("dispel", "Dispels")):
        t = fetch_table(be, code, fid, fs, fe, dt)
        per_player = {}
        for n in _flat(t.get("data", {}).get("entries", [])):
            tot_ab = 0
            for d in n.get("details", []) or []:
                pid = d.get("id")
                if pid is None:
                    continue
                pp = per_player.setdefault(pid, [d.get("name"), 0])
                pp[1] += d.get("total") or 0
                tot_ab += d.get("total") or 0
            if n.get("guid"):
                be.upsert("raid_event", {
                    "report": code, "fight_id": fid, "kind": kind + "_ability",
                    "seq": n["guid"], "timestamp": None, "source_id": None,
                    "source_name": None, "target_id": None, "target_name": None,
                    "ability_id": n["guid"], "ability_name": n.get("name"),
                    "amount": n.get("spellsInterrupted") or tot_ab,
                    "payload": json.dumps({"begun": n.get("spellsBegun"),
                                           "completed": n.get("spellsCompleted")}),
                }, ["report", "fight_id", "kind", "seq"])
        for pid, (pname, tot) in per_player.items():
            be.upsert("raid_event", {
                "report": code, "fight_id": fid, "kind": kind, "seq": pid,
                "timestamp": None, "source_id": pid, "source_name": pname,
                "target_id": None, "target_name": None, "ability_id": None,
                "ability_name": None, "amount": tot, "payload": None,
            }, ["report", "fight_id", "kind", "seq"])

    # combatantinfo: auras at pull (flask/food) + gear.
    cinfo = fetch_events(be, code, fid, fs, fe, "CombatantInfo")
    pot_flt = ',filterExpression:"ability.id IN (%s)"' % ",".join(
        str(i) for i in POTION_BUFFS)
    pot_events = fetch_events(be, code, fid, fs, fe, "Buffs", pot_flt)
    # PRE-POT (recurring lesson): a potion drunk DURING the countdown emits NO
    # applybuff inside the fight (classic WCL does not synthesize t=0 applies
    # for carryover buffs) -> its ONLY trace is an ORPHAN removebuff (~20-26s).
    # Counting applybuff only undercounts pre-pots massively.
    prepot, combat_pots, applied = {}, {}, set()
    for ev in sorted(pot_events, key=lambda e: e.get("timestamp") or 0):
        tid = ev.get("targetID")
        rel = (ev.get("timestamp") or fs) - fs
        ty = ev.get("type")
        if ty in ("applybuff", "refreshbuff"):
            if rel < 10000:
                prepot[tid] = 1
            else:
                combat_pots[tid] = combat_pots.get(tid, 0) + 1
            applied.add(tid)
        elif ty == "removebuff" and tid not in applied:
            prepot[tid] = 1            # orphan removebuff = carryover pre-pot
    for i, ev in enumerate(cinfo, 1):
        sid = ev.get("sourceID")
        auras = ev.get("auras") or []
        aura_ids = {a.get("ability") for a in auras}
        aura_names = [(a.get("name") or "") for a in auras]
        flask = next((FLASK_BUFFS[i_] for i_ in aura_ids if i_ in FLASK_BUFFS), None)
        if not flask:
            flask = next((n for n in aura_names
                          if n.lower().startswith(("flask", "flacon"))), None)
        food = next((n for n in aura_names
                     if any(w in n.lower() for w in WELL_FED_NAMES)), None)
        is_prepot = 1 if (any(i_ in POTION_BUFFS for i_ in aura_ids)
                          or prepot.get(sid)) else 0
        be.upsert("conso", {
            "report": code, "fight_id": fid, "actor_id": sid,
            "prepot": is_prepot, "combat_pots": combat_pots.get(sid, 0),
            "flask": flask, "food": food,
        }, ["report", "fight_id", "actor_id"])
        be.upsert("raid_event", {
            "report": code, "fight_id": fid, "kind": "combatantinfo", "seq": i,
            "timestamp": 0, "source_id": sid, "source_name": names.get(sid),
            "target_id": None, "target_name": None, "ability_id": None,
            "ability_name": None, "amount": None,
            "payload": json.dumps({"auras": auras, "gear": ev.get("gear") or []}),
        }, ["report", "fight_id", "kind", "seq"])

    if avoid_ids:
        flt = (',filterExpression:"ability.id IN (%s)"'
               % ",".join(str(i) for i in sorted(avoid_ids)))
        for i, ev in enumerate(fetch_events(be, code, fid, fs, fe,
                                            "DamageTaken", flt), 1):
            tid = ev.get("targetID")
            be.upsert("raid_event", {
                "report": code, "fight_id": fid, "kind": "avoidable_hit", "seq": i,
                "timestamp": (ev.get("timestamp") or fs) - fs,
                "source_id": None, "source_name": None,
                "target_id": tid, "target_name": names.get(tid),
                "ability_id": ev.get("abilityGameID"), "ability_name": None,
                "amount": (ev.get("amount") or 0) + (ev.get("absorbed") or 0),
                "payload": None,
            }, ["report", "fight_id", "kind", "seq"])
    be.commit()


def ingest_damage_taken_by_ability(be, code, fid, fs, fe):
    """Raid-wide damage-taken-by-ability breakdown (actor_id=0 convention).
    viewBy:Ability is mandatory: the default view is by ACTOR."""
    t = fetch_table(be, code, fid, fs, fe, "DamageTaken", ",viewBy:Ability")
    for e in t.get("data", {}).get("entries", []):
        ab = e.get("guid") or e.get("abilityGameID")
        # Guard: an actor entry (character guid > 10M) must never pass as a spell.
        if ab is None or ab > 10_000_000 or isinstance(e.get("type"), str):
            continue
        be.upsert("player_ability", {
            "report": code, "fight_id": fid, "actor_id": 0,
            "data_type": "DamageTakenByAbility", "ability_id": ab,
            "ability_name": e.get("name"), "total": e.get("total"),
            "overheal": None, "hit_count": e.get("hitCount"), "uses": None,
        }, ["report", "fight_id", "actor_id", "data_type", "ability_id"])


# ------------------------------------------------------------------------ deep

def cmd_deep(be, cfg, args):
    """Raw events per boss pull: phases, death recaps, graphs (dtps/hps/dps +
    healer mana), casts, damage taken/done, buffs/debuffs, healing table."""
    for code in report_codes(cfg):
        ingest_actors(be, code)
        fights = fights_query(be, code)
        healer_ids = [r["actor_id"] for r in be.con.execute(
            "SELECT DISTINCT actor_id FROM composition WHERE report=? AND role='healer'",
            (code,))]
        if not healer_ids:
            print("WARNING: no healers in composition — run `session` first "
                  "(mana series will be skipped)")
        print(f"{code}: {len(fights)} boss pulls; healers (mana tracked): {healer_ids}")
        for f in sorted(fights, key=lambda x: x["startTime"]):
            st = ingest_pull_deep(be, code, f, healer_ids)
            n = {k: be.con.execute(
                f"SELECT COUNT(*) c FROM {t} WHERE report=? AND fight_id=?",
                (code, f["id"])).fetchone()["c"]
                for k, t in (("casts", "deep_cast"), ("dt", "deep_dmg_taken"),
                             ("dd", "deep_dmg_done"), ("aura", "deep_aura"))}
            print(f"  f{f['id']:>3} {f['name'][:22]:22} {st:4} "
                  f"casts={n['casts']:<5} dmgTaken={n['dt']:<5} dmgDone={n['dd']:<6} "
                  f"auras={n['aura']:<5}", flush=True)
    print("quota:", json.dumps(quota()))


def ingest_pull_deep(be, code, f, healer_ids):
    fid, fs, fe = f["id"], f["startTime"], f["endTime"]
    if be.done(code, fid, "pull_deep"):
        return "skip"

    for i, p in enumerate(f.get("phaseTransitions") or []):
        be.upsert("deep_phase", {
            "report": code, "fight_id": fid, "idx": i, "phase_id": p["id"],
            "phase_name": None, "ts_rel": p["startTime"] - fs,
        }, ["report", "fight_id", "idx"])

    t = fetch_table(be, code, fid, fs, fe, "Deaths")
    for i, d in enumerate(sorted(t.get("data", {}).get("entries", []),
                                 key=lambda x: x.get("timestamp") or 0), 1):
        ts = d.get("timestamp") or 0
        be.upsert("deep_death_recap", {
            "report": code, "fight_id": fid, "death_seq": i,
            "actor_id": d.get("id"), "ts_rel": (ts - fs) if ts > (fe - fs) else ts,
            "payload": json.dumps(d),
        }, ["report", "fight_id", "death_seq"])

    for kind, dt in (("dtps", "DamageTaken"), ("hps", "Healing"), ("dps", "DamageDone")):
        g = fetch_graph(be, code, fid, fs, fe, dt, ",hostilityType:Friendlies")
        be.upsert("deep_graph", {"report": code, "fight_id": fid, "kind": kind,
                                 "payload": json.dumps(g.get("series") or [])},
                  ["report", "fight_id", "kind"])
    for hid in healer_ids:
        g = fetch_graph(be, code, fid, fs, fe, "Resources",
                        ",sourceID:%d,abilityID:100" % hid)
        be.upsert("deep_graph", {"report": code, "fight_id": fid,
                                 "kind": "mana:%d" % hid,
                                 "payload": json.dumps(g.get("series") or [])},
                  ["report", "fight_id", "kind"])

    for i, e in enumerate(fetch_events(be, code, fid, fs, fe, "Casts"), 1):
        be.upsert("deep_cast", {
            "report": code, "fight_id": fid, "seq": i,
            "ts_rel": (e.get("timestamp") or fs) - fs, "type": e.get("type"),
            "source_id": e.get("sourceID"), "target_id": e.get("targetID"),
            "ability_id": e.get("abilityGameID"),
        }, ["report", "fight_id", "seq"])

    for i, e in enumerate(fetch_events(be, code, fid, fs, fe, "DamageTaken"), 1):
        be.upsert("deep_dmg_taken", {
            "report": code, "fight_id": fid, "seq": i,
            "ts_rel": (e.get("timestamp") or fs) - fs,
            "target_id": e.get("targetID"), "source_id": e.get("sourceID"),
            "ability_id": e.get("abilityGameID"),
            "amount": e.get("amount"), "absorbed": e.get("absorbed"),
            "mitigated": e.get("mitigated"), "unmitigated": e.get("unmitigatedAmount"),
            "hit_type": e.get("hitType"), "buffs": e.get("buffs"),
            "is_aoe": 1 if e.get("isAoE") else 0,
        }, ["report", "fight_id", "seq"])

    for i, e in enumerate(fetch_events(be, code, fid, fs, fe, "DamageDone"), 1):
        be.upsert("deep_dmg_done", {
            "report": code, "fight_id": fid, "seq": i,
            "ts_rel": (e.get("timestamp") or fs) - fs,
            "source_id": e.get("sourceID"), "target_id": e.get("targetID"),
            "target_instance": e.get("targetInstance"),
            "ability_id": e.get("abilityGameID"),
            "amount": e.get("amount"), "absorbed": e.get("absorbed"),
            "hit_type": e.get("hitType"), "tick": 1 if e.get("tick") else 0,
        }, ["report", "fight_id", "seq"])

    for kind, dt, extra in (("buff", "Buffs", ""), ("debuff", "Debuffs", ""),
                            ("debuff_enemy", "Debuffs", ",hostilityType:Enemies")):
        for i, e in enumerate(fetch_events(be, code, fid, fs, fe, dt, extra), 1):
            be.upsert("deep_aura", {
                "report": code, "fight_id": fid, "kind": kind, "seq": i,
                "ts_rel": (e.get("timestamp") or fs) - fs, "type": e.get("type"),
                "source_id": e.get("sourceID"), "target_id": e.get("targetID"),
                "ability_id": e.get("abilityGameID"), "stacks": e.get("stack"),
            }, ["report", "fight_id", "kind", "seq"])

    t = fetch_table(be, code, fid, fs, fe, "Healing")
    for e in t.get("data", {}).get("entries", []):
        aid = e.get("id")
        if aid is None:
            continue
        for ab in e.get("abilities") or []:
            be.upsert("deep_heal_ability", {
                "report": code, "fight_id": fid, "actor_id": aid,
                "ability_id": ab.get("guid") or 0, "ability_name": ab.get("name"),
                "total": ab.get("total"), "overheal": None,
                "hit_count": ab.get("totalUses") or ab.get("hitCount"),
            }, ["report", "fight_id", "actor_id", "ability_id"])
        be.upsert("deep_heal_ability", {
            "report": code, "fight_id": fid, "actor_id": aid,
            "ability_id": -1, "ability_name": "TOTAL",
            "total": e.get("total"), "overheal": e.get("overheal"),
            "hit_count": e.get("hitCount"),
        }, ["report", "fight_id", "actor_id", "ability_id"])

    be.mark(code, fid, "pull_deep")
    return "ok"


def cmd_extras(be, cfg, args):
    """Timestamped dispel/interrupt events + full healing events + enemy casts
    (interrupt opportunities) — per boss pull."""
    for code in report_codes(cfg):
        _extras_one(be, code)
    print("quota:", json.dumps(quota()))


def _extras_one(be, code):
    fights = fights_query(be, code)
    for f in sorted(fights, key=lambda x: x["startTime"]):
        fid, fs, fe = f["id"], f["startTime"], f["endTime"]
        if be.done(code, fid, "extras"):
            continue
        n = 0
        for kind, dt in (("dispel", "Dispels"), ("interrupt", "Interrupts")):
            for i, e in enumerate(fetch_events(be, code, fid, fs, fe, dt), 1):
                ab = e.get("extraAbilityGameID") or e.get("abilityGameID")
                be.upsert("deep_aura", {
                    "report": code, "fight_id": fid, "kind": kind, "seq": i,
                    "ts_rel": (e.get("timestamp") or fs) - fs, "type": e.get("type"),
                    "source_id": e.get("sourceID"), "target_id": e.get("targetID"),
                    "ability_id": ab, "stacks": None,
                }, ["report", "fight_id", "kind", "seq"])
                n += 1
        nh = 0
        for i, e in enumerate(fetch_events(be, code, fid, fs, fe, "Healing"), 1):
            be.upsert("deep_heal_event", {
                "report": code, "fight_id": fid, "seq": i,
                "ts_rel": (e.get("timestamp") or fs) - fs,
                "source_id": e.get("sourceID"), "target_id": e.get("targetID"),
                "ability_id": e.get("abilityGameID"),
                "amount": e.get("amount"), "overheal": e.get("overheal"),
                "tick": 1 if e.get("tick") else 0,
            }, ["report", "fight_id", "seq"])
            nh += 1
        ne = 0
        for i, e in enumerate(fetch_events(be, code, fid, fs, fe, "Casts",
                                           ",hostilityType:Enemies"), 1):
            be.upsert("deep_aura", {
                "report": code, "fight_id": fid, "kind": "enemy_cast", "seq": i,
                "ts_rel": (e.get("timestamp") or fs) - fs, "type": e.get("type"),
                "source_id": e.get("sourceID"), "target_id": e.get("targetID"),
                "ability_id": e.get("abilityGameID"), "stacks": None,
            }, ["report", "fight_id", "kind", "seq"])
            ne += 1
        be.mark(code, fid, "extras")
        print(f"  f{fid:>3} extras: {n} dispel/interrupt, {nh} heal, "
              f"{ne} enemy casts", flush=True)


# ----------------------------------------------------------------------- trash

def cmd_trash(be, cfg, args):
    """Trash fights = fights WITHOUT encounterID. Deaths + top damage sources
    + timing (pacing input). One Deaths table + one DamageTaken table per fight."""
    for code in report_codes(cfg):
        _trash_one(be, code)
    print("quota:", json.dumps(quota()))


def _trash_one(be, code):
    ingest_actors(be, code)
    q = ('{ reportData { report(code:"%s"){ fights '
         '{ id name encounterID startTime endTime } } } }' % code)
    fights = unwrap(gql(be, q), "reportData", "report", "fights") or []
    trash = [f for f in fights if not f.get("encounterID")]
    print(f"{code}: {len(trash)} trash fights")
    for f in sorted(trash, key=lambda x: x["startTime"]):
        fid, fs, fe = f["id"], f["startTime"], f["endTime"]
        t = fetch_table(be, code, fid, fs, fe, "Deaths")
        deaths = []
        for d in t.get("data", {}).get("entries", []):
            ts = d.get("timestamp") or 0
            kb = d.get("killingBlow") or {}
            deaths.append({"name": d.get("name"),
                           "ts_rel": (ts - fs) if ts > (fe - fs) else ts,
                           "ability": kb.get("name")})
        td = fetch_table(be, code, fid, fs, fe, "DamageTaken", ",viewBy:Ability")
        top_dmg = [{"name": e.get("name"), "total": e.get("total"),
                    "hits": e.get("hitCount")}
                   for e in (td.get("data", {}).get("entries") or [])[:8]]
        be.upsert("trash_fight", {
            "report": code, "fight_id": fid, "name": f.get("name"),
            "start_time": fs, "end_time": fe, "duration_s": (fe - fs) / 1000.0,
            "deaths": len(deaths),
            "payload": json.dumps({"deaths": deaths, "top_dmg": top_dmg}),
        }, ["report", "fight_id"])
        flag = f" deaths={len(deaths)}" if deaths else ""
        print(f"  f{fid:>3} {f['name'][:36]:36} {(fe-fs)/1000:5.0f}s{flag}", flush=True)
    be.commit()


# ------------------------------------------------------------------------ tops

def cmd_tops(be, cfg, args):
    """top1/top2 rankings per (spec x boss x difficulty) actually played by
    the roster. Same-size only (cfg size): benchmark apples-to-apples."""
    codes = report_codes(cfg)
    ph = ",".join("?" for _ in codes)
    size = cfg.get("size") or 10
    specs = [dict(r) for r in be.con.execute(
        f"SELECT DISTINCT class, spec, role FROM composition WHERE report IN ({ph})",
        codes)]
    encs = [dict(r) for r in be.con.execute(
        f"SELECT DISTINCT encounter_id, boss, difficulty FROM pull "
        f"WHERE report IN ({ph}) ORDER BY encounter_id, difficulty", codes)]
    print(f"{len(specs)} specs x {len(encs)} (boss,diff)")
    for e in encs:
        for s in specs:
            if not s["spec"]:
                continue
            spec_key = f"{s['class']}-{s['spec']}"
            metric = ROLE_METRIC.get(s["role"], "dps")
            q = ('{ worldData { encounter(id:%d){ characterRankings(metric:%s,'
                 'difficulty:%d, size:%d, className:"%s", specName:"%s") } } }'
                 % (e["encounter_id"], metric, e["difficulty"], size,
                    s["class"], s["spec"]))
            cr = json_field(unwrap(gql(be, q), "worldData", "encounter",
                                   "characterRankings")) or {}
            picked, seen_names = [], set()
            for r in cr.get("rankings", []):
                nm = r.get("name")
                rep = r.get("report") or {}
                if not nm or nm in seen_names or not rep.get("code"):
                    continue
                seen_names.add(nm)
                picked.append((nm, rep["code"], rep.get("fightID"),
                               r.get("amount"), r.get("duration")))
                if len(picked) >= 2:
                    break
            for rank, (nm, rcode, rfid, amount, dur_ms) in enumerate(picked, 1):
                be.upsert("top_parse", {
                    "encounter_id": e["encounter_id"], "difficulty": e["difficulty"],
                    "size": size, "spec_key": spec_key, "rank": rank,
                    "report": rcode, "fight_id": rfid, "player_name": nm,
                    "actor_id": None, "amount": amount,
                    "duration_s": (dur_ms or 0) / 1000.0,
                }, ["encounter_id", "difficulty", "size", "spec_key", "rank"])
            be.commit()
            print(f"  {e['boss'][:20]:20} {DIFF_NAME.get(e['difficulty'], '?')}{size} "
                  f"{spec_key:24} -> {len(picked)} parses", flush=True)
    print("rankings ok; details: top-detail")
    print("quota:", json.dumps(quota()))


def ingest_top_fight(be, code, fid, enc_id):
    """Benchmark fight skeleton: fight row + compo + per-actor totals."""
    if be.con.execute("SELECT 1 FROM composition WHERE report=? AND fight_id=? "
                      "LIMIT 1", (code, fid)).fetchone():
        return
    qm = ('{ reportData { report(code:"%s"){ fights(fightIDs:[%d])'
          '{ kill difficulty size startTime endTime }'
          ' playerDetails(fightIDs:[%d]) } } }' % (code, fid, fid))
    rep = unwrap(gql(be, qm), "reportData", "report")
    if not rep or not rep.get("fights"):
        return
    f = rep["fights"][0]
    be.upsert("fight", {
        "report": code, "fight_id": fid, "encounter_id": enc_id, "boss": "",
        "difficulty": f.get("difficulty"), "size": f.get("size"),
        "kill": 1 if f.get("kill") else 0,
        "duration_s": (f["endTime"] - f["startTime"]) / 1000.0,
        "start_time": f["startTime"], "end_time": f["endTime"],
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }, ["report", "fight_id"])
    pd = json_field(rep.get("playerDetails")) or {}
    if isinstance(pd, dict):
        pd = pd.get("data", pd).get("playerDetails", pd)
    if not isinstance(pd, dict):
        pd = {}
    for role in ("tanks", "healers", "dps"):
        for a in pd.get(role, []) or []:
            specs = a.get("specs") or []
            spec = ""
            if specs:
                spec = specs[0] if isinstance(specs[0], str) else (specs[0].get("spec") or "")
            be.upsert("composition", {
                "report": code, "fight_id": fid, "actor_id": a.get("id"),
                "player_name": a.get("name"), "class": a.get("type"), "spec": spec,
                "role": {"tanks": "tank", "healers": "healer", "dps": "dps"}[role],
                "item_level": a.get("minItemLevel") or a.get("itemLevel"),
            }, ["report", "fight_id", "actor_id"])
    fs, fe = f["startTime"], f["endTime"]
    for dt in ("Healing", "DamageDone", "DamageTaken"):
        t = fetch_table(be, code, fid, fs, fe, dt, ",hostilityType:Friendlies")
        for e in t.get("data", {}).get("entries", []):
            if e.get("id") is None:
                continue
            be.upsert("player_fight", {
                "report": code, "fight_id": fid, "actor_id": e["id"], "data_type": dt,
                "total": e.get("total"), "active_time": e.get("activeTime"),
            }, ["report", "fight_id", "actor_id", "data_type"])
    be.commit()


def cmd_top_detail(be, cfg, args):
    """Targeted events for each top parse. SAME data as our players so the
    SAME formulas apply on both sides (benchmark symmetry rule).

    GOTCHA (engraved): events(dataType:DamageTaken, targetID:X) returns ZERO
    silently on classic -> fetch FULL DamageTaken and filter code-side.
    Casts+sourceID and Buffs+targetID are verified OK."""
    codes = report_codes(cfg)
    ph = ",".join("?" for _ in codes)
    rows = [dict(r) for r in be.con.execute(f"""
        SELECT tp.* FROM top_parse tp WHERE EXISTS (
          SELECT 1 FROM pull p JOIN composition c
            ON c.report=p.report AND c.fight_id=p.fight_id
          WHERE p.encounter_id=tp.encounter_id AND p.difficulty=tp.difficulty
            AND (c.class || '-' || c.spec) = tp.spec_key AND p.report IN ({ph}))
        ORDER BY tp.encounter_id, tp.spec_key, tp.rank""", codes)]
    print(f"{len(rows)} top parses to detail (played specs only)")
    for tp in rows:
        rcode, rfid = tp["report"], tp["fight_id"]
        what = "top:%s" % tp["player_name"]
        if be.done(rcode, rfid, what):
            continue
        ingest_top_fight(be, rcode, rfid, tp["encounter_id"])
        f = be.con.execute("SELECT start_time, end_time FROM fight "
                           "WHERE report=? AND fight_id=?", (rcode, rfid)).fetchone()
        if not f:
            print(f"  !! fight not found {rcode} f{rfid} ({tp['player_name']})")
            continue
        fs, fe = f["start_time"], f["end_time"]
        names = ingest_actors(be, rcode)
        aid = next((k for k, v in names.items() if v == tp["player_name"]), None)
        if aid is None:
            print(f"  !! actor not found {tp['player_name']} in {rcode}")
            continue
        be.upsert("top_parse", {**tp, "actor_id": aid},
                  ["encounter_id", "difficulty", "size", "spec_key", "rank"])

        for i, e in enumerate(fetch_events(be, rcode, rfid, fs, fe, "Casts",
                                           ",sourceID:%d" % aid), 1):
            be.upsert("deep_cast", {
                "report": rcode, "fight_id": rfid, "seq": i,
                "ts_rel": (e.get("timestamp") or fs) - fs, "type": e.get("type"),
                "source_id": e.get("sourceID"), "target_id": e.get("targetID"),
                "ability_id": e.get("abilityGameID"),
            }, ["report", "fight_id", "seq"])
        # FULL fetch + code-side filter (see gotcha above).
        dmg = [e for e in fetch_events(be, rcode, rfid, fs, fe, "DamageTaken")
               if e.get("targetID") == aid]
        for i, e in enumerate(dmg, 1):
            be.upsert("deep_dmg_taken", {
                "report": rcode, "fight_id": rfid, "seq": i,
                "ts_rel": (e.get("timestamp") or fs) - fs,
                "target_id": e.get("targetID"), "source_id": e.get("sourceID"),
                "ability_id": e.get("abilityGameID"),
                "amount": e.get("amount"), "absorbed": e.get("absorbed"),
                "mitigated": e.get("mitigated"), "unmitigated": e.get("unmitigatedAmount"),
                "hit_type": e.get("hitType"), "buffs": e.get("buffs"),
                "is_aoe": 1 if e.get("isAoE") else 0,
            }, ["report", "fight_id", "seq"])
        if not dmg:
            print(f"  WARNING: zero DamageTaken for top {tp['player_name']} "
                  f"({rcode} f{rfid}) — expected-positive check FAILED, "
                  f"do not use damage-taken numbers for this parse")
        for kind, dt, extra in (("buff", "Buffs", ",targetID:%d" % aid),
                                ("debuff_enemy", "Debuffs", ",hostilityType:Enemies")):
            for i, e in enumerate(fetch_events(be, rcode, rfid, fs, fe, dt, extra), 1):
                be.upsert("deep_aura", {
                    "report": rcode, "fight_id": rfid, "kind": kind, "seq": i,
                    "ts_rel": (e.get("timestamp") or fs) - fs, "type": e.get("type"),
                    "source_id": e.get("sourceID"), "target_id": e.get("targetID"),
                    "ability_id": e.get("abilityGameID"), "stacks": e.get("stack"),
                }, ["report", "fight_id", "kind", "seq"])
        t = fetch_table(be, rcode, rfid, fs, fe, "Healing")
        for en in t.get("data", {}).get("entries", []):
            if en.get("id") != aid:
                continue
            be.upsert("deep_heal_ability", {
                "report": rcode, "fight_id": rfid, "actor_id": aid,
                "ability_id": -1, "ability_name": "TOTAL",
                "total": en.get("total"), "overheal": en.get("overheal"),
                "hit_count": en.get("hitCount"),
            }, ["report", "fight_id", "actor_id", "ability_id"])
        spec_name = tp["spec_key"].split("-", 1)[1] if "-" in tp["spec_key"] else ""
        if spec_name in HEALER_SPECS:
            g = fetch_graph(be, rcode, rfid, fs, fe, "Resources",
                            ",sourceID:%d,abilityID:100" % aid)
            be.upsert("deep_graph", {"report": rcode, "fight_id": rfid,
                                     "kind": "mana:%d" % aid,
                                     "payload": json.dumps(g.get("series") or [])},
                      ["report", "fight_id", "kind"])
        be.mark(rcode, rfid, what)
        print(f"  ok {tp['spec_key']:24} enc {tp['encounter_id']} top{tp['rank']} "
              f"{tp['player_name']}", flush=True)
    print("quota:", json.dumps(quota()))


# ----------------------------------------------------- benchmark zone (optional)

def cmd_benchmark(be, cfg, args):
    """Zone top logs raid-wide (avoidable inference input). Optional: only
    needed when no zone mechanics ref exists yet (data-driven bootstrap)."""
    codes = report_codes(cfg)
    ph = ",".join("?" for _ in codes)
    size = cfg.get("size") or 10
    encs = [dict(r) for r in be.con.execute(
        f"SELECT DISTINCT encounter_id, boss, difficulty FROM pull "
        f"WHERE report IN ({ph})", codes)]
    for e in encs:
        reports = {}
        for metric in ("dps", "hps"):
            for ck, fid in rankings_reports(be, e["encounter_id"], metric,
                                            e["difficulty"], size, args.topn):
                reports[(ck, fid)] = True
        done = 0
        for (ck, fid) in reports:
            ingest_top_fight(be, ck, fid, e["encounter_id"])
            f = be.con.execute(
                "SELECT start_time, end_time FROM fight WHERE report=? AND fight_id=?",
                (ck, fid)).fetchone()
            if f:
                ingest_damage_taken_by_ability(be, ck, fid,
                                               f["start_time"], f["end_time"])
                done += 1
        be.commit()
        print(f"  {e['boss'][:22]:22} {DIFF_NAME.get(e['difficulty'], '?')}{size}: "
              f"{done}/{len(reports)} fights", flush=True)
    print("benchmark ok")


def cmd_infer_avoidable(be, cfg, args):
    """Avoidable candidates: abilities where tops take ~0 dmg/min and we take
    >0. Writes avoidable_ref status='candidate'; validate manually or against
    the zone mechanics ref afterwards."""
    codes = report_codes(cfg)
    ph = ",".join("?" for _ in codes)
    enc_ids = {r["encounter_id"]: r["boss"] for r in be.con.execute(
        f"SELECT DISTINCT encounter_id, boss FROM pull WHERE report IN ({ph})",
        codes)}
    n = 0
    for eid, ename in enc_ids.items():
        ours = {r["ability_id"]: dict(r) for r in be.con.execute("""
            SELECT pa.ability_id, pa.ability_name,
                   SUM(pa.total) * 60.0 / NULLIF(SUM(p.duration_s), 0) AS dpm
            FROM player_ability pa
            JOIN pull p ON p.report = pa.report AND p.fight_id = pa.fight_id
            WHERE pa.actor_id = 0 AND pa.data_type = 'DamageTakenByAbility'
              AND p.encounter_id = ?
            GROUP BY pa.ability_id""", (eid,))}
        tops = {r["ability_id"]: dict(r) for r in be.con.execute("""
            SELECT pa.ability_id,
                   SUM(pa.total) * 60.0 / NULLIF(SUM(f.duration_s), 0) AS dpm
            FROM player_ability pa
            JOIN fight f ON f.report = pa.report AND f.fight_id = pa.fight_id
            WHERE pa.actor_id = 0 AND pa.data_type = 'DamageTakenByAbility'
              AND f.encounter_id = ?
            GROUP BY pa.ability_id""", (eid,))}
        if not ours:
            continue
        for ab, r in ours.items():
            ours_dpm = r["dpm"] or 0
            top_dpm = (tops.get(ab) or {"dpm": None})["dpm"]
            if ours_dpm < 50000:        # noise floor
                continue
            if top_dpm is None or top_dpm < ours_dpm * 0.20:
                ratio = (ours_dpm / top_dpm) if top_dpm else None
                be.upsert("avoidable_ref", {
                    "encounter_id": eid, "ability_id": ab,
                    "ability_name": r["ability_name"], "status": "candidate",
                    "source": "inferred", "ratio": ratio,
                    "note": f"{ename}: us {ours_dpm:,.0f}/min vs tops "
                            f"{(top_dpm or 0):,.0f}/min",
                }, ["encounter_id", "ability_id"])
                n += 1
    be.commit()
    print(f"{n} avoidable candidates written (status=candidate). Re-run "
          f"`session` to trace per-player hits, then validate against the zone "
          f"mechanics ref or manually (UPDATE avoidable_ref SET status=...).")


# ---------------------------------------------------------------------- status

def cmd_status(be, cfg, args):
    """Binary gate: print PASS/FAIL per stage. Exit code 1 if anything missing.
    Use this between pipeline stages — never assume a stage is complete."""
    codes = report_codes(cfg)
    fails = []

    def check(name, ok, detail=""):
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
        if not ok:
            fails.append(name)

    for night, code in enumerate(codes, 1):
        tag = f"night {night} ({code}) " if len(codes) > 1 else ""
        n_pulls = be.con.execute("SELECT COUNT(*) c FROM pull WHERE report=?",
                                 (code,)).fetchone()["c"]
        check(tag + "session: pulls ingested", n_pulls > 0, f"{n_pulls} pulls")
        n_sess = be.con.execute(
            "SELECT COUNT(*) c FROM done_marker WHERE report=? AND what='session'",
            (code,)).fetchone()["c"]
        check(tag + "session: per-pull detail", n_pulls > 0 and n_sess >= n_pulls,
              f"{n_sess}/{n_pulls}")
        n_deep = be.con.execute(
            "SELECT COUNT(*) c FROM done_marker WHERE report=? AND what='pull_deep'",
            (code,)).fetchone()["c"]
        check(tag + "deep: raw events", n_pulls > 0 and n_deep >= n_pulls,
              f"{n_deep}/{n_pulls}")
        n_ext = be.con.execute(
            "SELECT COUNT(*) c FROM done_marker WHERE report=? AND what='extras'",
            (code,)).fetchone()["c"]
        check(tag + "extras: dispel/interrupt/heal events",
              n_pulls > 0 and n_ext >= n_pulls, f"{n_ext}/{n_pulls}")
        n_trash = be.con.execute("SELECT COUNT(*) c FROM trash_fight WHERE report=?",
                                 (code,)).fetchone()["c"]
        check(tag + "trash: ingested", n_trash > 0, f"{n_trash} fights "
              "(0 can be legit on a full-clear-no-trash log — verify on WCL)")

        # Expected-positive integrity checks (extraction sanity).
        bad = [dict(r) for r in be.con.execute("""
            SELECT p.fight_id, c.player_name FROM pull p
            JOIN composition c ON c.report=p.report AND c.fight_id=p.fight_id
            WHERE p.report=? AND p.duration_s > 60 AND NOT EXISTS (
              SELECT 1 FROM deep_dmg_taken dt WHERE dt.report=p.report
                AND dt.fight_id=p.fight_id AND dt.target_id=c.actor_id)""",
            (code,))] if n_deep else []
        check(tag + "integrity: every player has DamageTaken events (pulls >60s)",
              n_deep > 0 and not bad,
              "" if not bad else f"{len(bad)} player-pulls with ZERO events, e.g. "
              + ", ".join(f"f{b['fight_id']}:{b['player_name']}" for b in bad[:4]))

    n_tops = be.con.execute("SELECT COUNT(*) c FROM top_parse").fetchone()["c"]
    check("tops: rankings", n_tops > 0, f"{n_tops} parses")
    n_topd = be.con.execute(
        "SELECT COUNT(*) c FROM done_marker WHERE what LIKE 'top:%'").fetchone()["c"]
    check("tops: details", n_tops > 0 and n_topd > 0, f"{n_topd}/{n_tops}")

    refs = os.path.join(be.workdir, "refs")
    check("refs: mechanics_ref.json", os.path.exists(os.path.join(refs, "mechanics_ref.json")))
    check("refs: spec_kpis.json", os.path.exists(os.path.join(refs, "spec_kpis.json")))
    print("STATUS:", "OK" if not fails else f"INCOMPLETE ({', '.join(fails)})")
    if fails:
        sys.exit(1)


def cmd_quota(be, cfg, args):
    print(json.dumps(quota(), indent=1))


def cmd_all(be, cfg, args):
    """Full extraction. Quota is self-managed at the client level (wcl.py):
    rateLimitData polled every ~150 live calls, auto-pause through the hourly
    reset at >85%, 429 sleeps to reset — no babysitting needed."""
    for step, fn in (("session", cmd_session), ("deep", cmd_deep),
                     ("extras", cmd_extras), ("trash", cmd_trash),
                     ("tops", cmd_tops), ("top-detail", cmd_top_detail)):
        print(f"== {step} ==")
        fn(be, cfg, args)
    print("== status ==")
    cmd_status(be, cfg, args)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("init")
    pi.add_argument("--report", action="append", required=True,
                    help="repeatable and/or comma-separated (multi-night ID)")
    pi.add_argument("--guild", required=True)
    pi.add_argument("--label", default=None)
    pi.add_argument("--lang", default="fr")
    pi.add_argument("--size", type=int, default=10)
    pa = sub.add_parser("add-report")
    pa.add_argument("--report", action="append", required=True,
                    help="report code(s) to append to this workdir's raid ID")
    for name in ("session", "deep", "extras", "trash", "tops", "top-detail",
                 "status", "quota", "all", "infer-avoidable"):
        sub.add_parser(name)
    pb = sub.add_parser("benchmark")
    pb.add_argument("--topn", type=int, default=10)
    args = ap.parse_args()
    if not hasattr(args, "topn"):
        args.topn = 10
    wd = workdir_from_args(args)
    load_env(wd)
    be = Backend(wd)
    if args.cmd == "init":
        cmd_init(be, None, args)
        return
    cfg = load_config(wd)
    {"session": cmd_session, "deep": cmd_deep, "extras": cmd_extras,
     "trash": cmd_trash, "tops": cmd_tops, "top-detail": cmd_top_detail,
     "status": cmd_status, "quota": cmd_quota, "all": cmd_all,
     "add-report": cmd_add_report, "benchmark": cmd_benchmark,
     "infer-avoidable": cmd_infer_avoidable}[args.cmd](be, cfg, args)


if __name__ == "__main__":
    main()
