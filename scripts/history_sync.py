#!/usr/bin/env python3
"""history_sync.py — roll a raid night's Tier-1 aggregates forward into the
unified longitudinal store ~/raids/_history/history.db (the durable asset).

Per-night raid.db files stay the source of raw detail; this lifts the small
aggregate layer (player_fight, composition, pull, death, conso, top_parse,
percentiles) + 5 materialized rollups into one cross-lockout db that
evolution.py queries. Stdlib only.

Idempotency = per-raid_label REPLACE: each sync DELETEs this label's facts and
rollups then re-inserts, all in one transaction (a re-run never double-counts,
and a removed pull/player disappears cleanly). The player dimension accumulates
(never deleted). Identity = canonical(name); rows whose actor_id is absent from
`composition` (the Players-only roster: actor_id=0 sentinel, pets, NPCs) are
skipped — that IS the type='Player' filter.

Usage:
    python3 history_sync.py <workdir>              # sync ONE night (Stage 9)
    python3 history_sync.py --backfill <wd> [...]  # (re)sync several nights
    python3 history_sync.py --rebuild-rollups <wd> [...]  # rollups only
    # history db path: $RAID_HISTORY_DB or ~/raids/_history/history.db
"""
import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from statistics import median

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wcl import Backend, load_config, load_env, report_codes
import analyze
from ingest import RAID_CDS

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_SCHEMA = os.path.join(SCRIPTS_DIR, "schema_history.sql")
ROLL_TABLES = ("roll_player_encounter", "roll_player_avoidable",
               "roll_player_interrupt", "roll_player_aura_uptime",
               "roll_player_cd_cast")
FACT_TABLES = ("h_night", "h_pull", "h_composition", "h_player_fight",
               "h_player_ability", "h_death", "h_conso", "h_deep_heal_ability",
               "h_top_parse", "h_percentile")
ROLE_METRIC_DT = {"healer": "Healing", "tank": "DamageDone", "dps": "DamageDone"}


def history_db_path():
    p = os.environ.get("RAID_HISTORY_DB")
    return os.path.abspath(p) if p else os.path.expanduser(
        "~/raids/_history/history.db")


# ----------------------------------------------------------------- history db

class HistoryDB:
    """history.db connection: dedicated schema, FK enforced, same WAL tuning."""

    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.con = sqlite3.connect(path)
        # Autocommit mode: we drive transactions explicitly (BEGIN ... commit)
        # for the per-raid_label atomic replace; avoids sqlite3's implicit
        # transaction clashing with our explicit BEGIN.
        self.con.isolation_level = None
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA busy_timeout=120000")
        self.con.execute("PRAGMA foreign_keys=ON")
        with open(HISTORY_SCHEMA, encoding="utf-8") as f:
            self.con.executescript(f.read())

    def upsert(self, table, row, pk):
        cols = list(row.keys())
        ph = ",".join("?" for _ in cols)
        setc = ",".join(f"{c}=excluded.{c}" for c in cols if c not in pk)
        if setc:
            sql = (f"INSERT INTO {table} ({','.join(cols)}) VALUES ({ph}) "
                   f"ON CONFLICT ({','.join(pk)}) DO UPDATE SET {setc}")
        else:
            sql = f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) VALUES ({ph})"
        self.con.execute(sql, [row[c] for c in cols])

    def commit(self):
        self.con.commit()


def canonical(name):
    return (name or "").strip()


def player_id_for(hist, name, raid_label, guild, cache):
    """Resolve canonical(name) -> stable player_id (create on first sight).
    Updates observed first/last_seen + guild. Honors player_alias."""
    cn = canonical(name)
    if not cn:
        return None
    if cn in cache:
        return cache[cn]
    a = hist.con.execute("SELECT player_id FROM player_alias WHERE alias=?",
                         (cn,)).fetchone()
    if a:
        pid = a["player_id"]
    else:
        row = hist.con.execute("SELECT player_id FROM player WHERE name=?",
                               (cn,)).fetchone()
        if row:
            pid = row["player_id"]
        else:
            pid = hist.con.execute(
                "INSERT INTO player(name, first_seen, last_seen, last_seen_guild) "
                "VALUES (?,?,?,?)", (cn, raid_label, raid_label, guild)).lastrowid
    hist.con.execute(
        "UPDATE player SET first_seen=MIN(first_seen,?), last_seen=MAX(last_seen,?), "
        "last_seen_guild=? WHERE player_id=?", (raid_label, raid_label, guild, pid))
    cache[cn] = pid
    return pid


# --------------------------------------------------------------- fact ingest

def comp_map(be, code):
    """(fight_id, actor_id) -> composition row. Carries spec (per-pull) for the
    spec-split rollups; present only for fights with a recorded composition."""
    out = {}
    for r in be.con.execute("SELECT * FROM composition WHERE report=?", (code,)):
        out[(r["fight_id"], r["actor_id"])] = dict(r)
    return out


def player_map(be, code):
    """actor_id -> player name, report-scoped, Players ONLY (actor_name.type).
    More complete than composition (which is per-fight and can be absent for
    some fights, e.g. conso recorded without a composition row); this is the
    canonical resolution for actor_id-only fact tables, and the type filter
    excludes pets/NPCs and the actor_id=0 raid-wide sentinel."""
    return {r["actor_id"]: r["name"] for r in be.con.execute(
        "SELECT actor_id, name FROM actor_name WHERE report=? AND type='Player'",
        (code,))}


def ingest_facts(hist, be, cfg, raid_label, guild, pid_cache):
    """Copy this night's Tier-1 tables into history.db, resolving player_id.
    Returns the set of report codes synced."""
    codes = report_codes(cfg)
    for code in codes:
        rs = be.con.execute("SELECT * FROM raid_session WHERE report=?",
                            (code,)).fetchone()
        if rs:
            hist.upsert("h_night", {
                "raid_label": raid_label, "report": code, "guild": rs["guild"],
                "zone_id": rs["zone_id"], "title": rs["title"],
                "start_ts": rs["start_ts"]}, ["raid_label", "report"])

        for p in be.con.execute("SELECT * FROM pull WHERE report=?", (code,)):
            hist.upsert("h_pull", {
                "raid_label": raid_label, "report": code, "fight_id": p["fight_id"],
                "encounter_id": p["encounter_id"], "boss": p["boss"],
                "difficulty": p["difficulty"], "size": p["size"], "kill": p["kill"],
                "boss_pct": p["boss_pct"], "fight_pct": p["fight_pct"],
                "last_phase": p["last_phase"], "duration_s": p["duration_s"],
                "start_time": p["start_time"], "end_time": p["end_time"],
                "pull_number": p["pull_number"]},
                ["raid_label", "report", "fight_id"])

        cmap = comp_map(be, code)
        pmap = player_map(be, code)   # actor_id -> name, Players only (complete)
        for (fid, aid), c in cmap.items():
            pid = player_id_for(hist, c["player_name"], raid_label, guild, pid_cache)
            if pid is None:
                continue
            hist.upsert("h_composition", {
                "raid_label": raid_label, "report": code, "fight_id": fid,
                "player_id": pid, "player_name": canonical(c["player_name"]),
                "class": c["class"], "spec": c["spec"], "role": c["role"],
                "item_level": c["item_level"]},
                ["raid_label", "report", "fight_id", "player_id"])

        # player_fight / player_ability / conso / deep_heal_ability: actor_id
        # only -> resolve via the Players-only actor_name map (excludes the
        # actor_id=0 sentinel + pets/NPCs; complete even on fights lacking a
        # composition row, matching the legacy per-report counts).
        for r in be.con.execute("SELECT * FROM player_fight WHERE report=?", (code,)):
            nm = pmap.get(r["actor_id"])
            if not nm:
                continue
            pid = player_id_for(hist, nm, raid_label, guild, pid_cache)
            hist.upsert("h_player_fight", {
                "raid_label": raid_label, "report": code, "fight_id": r["fight_id"],
                "player_id": pid, "data_type": r["data_type"], "total": r["total"],
                "active_time": r["active_time"]},
                ["raid_label", "report", "fight_id", "player_id", "data_type"])

        for r in be.con.execute("SELECT * FROM player_ability WHERE report=?", (code,)):
            nm = pmap.get(r["actor_id"])
            if not nm:
                continue
            pid = player_id_for(hist, nm, raid_label, guild, pid_cache)
            hist.upsert("h_player_ability", {
                "raid_label": raid_label, "report": code, "fight_id": r["fight_id"],
                "player_id": pid, "data_type": r["data_type"],
                "ability_id": r["ability_id"], "ability_name": r["ability_name"],
                "total": r["total"], "overheal": r["overheal"],
                "hit_count": r["hit_count"], "uses": r["uses"]},
                ["raid_label", "report", "fight_id", "player_id", "data_type", "ability_id"])

        for r in be.con.execute("SELECT * FROM death WHERE report=?", (code,)):
            pid = player_id_for(hist, r["player_name"], raid_label, guild, pid_cache)
            hist.upsert("h_death", {
                "raid_label": raid_label, "report": code, "fight_id": r["fight_id"],
                "seq": r["seq"], "player_id": pid,
                "player_name": canonical(r["player_name"]),
                "death_time": r["death_time"], "ability_id": r["ability_id"],
                "ability_name": r["ability_name"], "overkill": r["overkill"]},
                ["raid_label", "report", "fight_id", "seq"])

        for r in be.con.execute("SELECT * FROM conso WHERE report=?", (code,)):
            nm = pmap.get(r["actor_id"])
            if not nm:
                continue
            pid = player_id_for(hist, nm, raid_label, guild, pid_cache)
            hist.upsert("h_conso", {
                "raid_label": raid_label, "report": code, "fight_id": r["fight_id"],
                "player_id": pid, "prepot": r["prepot"],
                "combat_pots": r["combat_pots"], "flask": r["flask"],
                "food": r["food"]},
                ["raid_label", "report", "fight_id", "player_id"])

        for r in be.con.execute("SELECT * FROM deep_heal_ability WHERE report=?", (code,)):
            nm = pmap.get(r["actor_id"])
            if not nm:
                continue
            pid = player_id_for(hist, nm, raid_label, guild, pid_cache)
            hist.upsert("h_deep_heal_ability", {
                "raid_label": raid_label, "report": code, "fight_id": r["fight_id"],
                "player_id": pid, "ability_id": r["ability_id"],
                "ability_name": r["ability_name"], "total": r["total"],
                "overheal": r["overheal"], "hit_count": r["hit_count"]},
                ["raid_label", "report", "fight_id", "player_id", "ability_id"])

    # top_parse: external benchmark players (no player_id), per report-agnostic key.
    for r in be.con.execute("SELECT * FROM top_parse"):
        hist.upsert("h_top_parse", {
            "raid_label": raid_label, "encounter_id": r["encounter_id"],
            "difficulty": r["difficulty"], "size": r["size"],
            "spec_key": r["spec_key"], "rank": r["rank"], "report": r["report"],
            "fight_id": r["fight_id"], "player_name": r["player_name"],
            "amount": r["amount"], "duration_s": r["duration_s"]},
            ["raid_label", "encounter_id", "difficulty", "size", "spec_key", "rank"])

    ingest_percentiles(hist, be, cfg, raid_label, guild, pid_cache)
    return codes


def ingest_percentiles(hist, be, cfg, raid_label, guild, pid_cache):
    """percentiles.json (a digest FILE) -> h_percentile, per-parse (no median)."""
    path = os.path.join(be.workdir, "digests", "percentiles.json")
    if not os.path.exists(path):
        print(f"[history] no percentiles.json in {be.workdir} (skipping h_percentile)")
        return
    data = json.load(open(path, encoding="utf-8"))
    for key, fight in data.items():
        report, _, fid = key.rpartition(":")
        fid = int(fid)
        for pl in fight.get("players", []):
            pid = player_id_for(hist, pl.get("name"), raid_label, guild, pid_cache)
            if pid is None:
                continue
            hist.upsert("h_percentile", {
                "raid_label": raid_label, "report": report, "fight_id": fid,
                "player_id": pid, "player_name": canonical(pl.get("name")),
                "encounter_id": fight.get("encounter_id"), "boss": fight.get("boss"),
                "difficulty": fight.get("difficulty"), "spec": pl.get("spec"),
                "role": pl.get("role"), "metric": pl.get("metric"),
                "amount": pl.get("amount"), "rank_percent": pl.get("rank_percent"),
                "best_percent": pl.get("best_percent"),
                "bracket_percent": pl.get("bracket_percent"),
                "ilvl": pl.get("ilvl") if isinstance(pl.get("ilvl"), (int, float, str)) else None},
                ["raid_label", "report", "fight_id", "player_id", "metric"])


# ----------------------------------------------------------------- rollups

def _pid_on(hist, be, raid_label, guild, code, fid, aid, cmap, pid_cache):
    c = cmap.get((fid, aid))
    if not c:
        return None, None
    return player_id_for(hist, c["player_name"], raid_label, guild, pid_cache), c["spec"]


def rebuild_rollups(hist, be, cfg, raid_label, guild, pid_cache):
    """Recompute all 5 rollups for THIS raid_label from per-night raw tables.
    Caller wraps in the same transaction after DELETE ... WHERE raid_label=?."""
    pulls = analyze.pulls_all(be, cfg)
    cmaps = {code: comp_map(be, code) for code in report_codes(cfg)}

    _roll_encounter(hist, be, cfg, raid_label, guild, pulls, cmaps, pid_cache)
    _roll_avoidable(hist, be, raid_label, guild, pulls, cmaps, pid_cache)
    _roll_interrupt(hist, be, raid_label, guild, pulls, cmaps, pid_cache)
    _roll_cd_cast(hist, be, raid_label, guild, pulls, cmaps, pid_cache)
    _roll_aura_uptime(hist, be, raid_label, guild, pulls, cmaps, pid_cache)


def _roll_encounter(hist, be, cfg, raid_label, guild, pulls, cmaps, pid_cache):
    """Throughput / deaths / prepots / median percentile per
    (player, encounter, difficulty, spec). spec-split: a pull contributes to the
    player's spec ON THAT PULL only."""
    agg = defaultdict(lambda: {
        "n_pulls": 0, "n_kills": 0, "dmg": 0, "heal": 0, "dtaken": 0,
        "active": 0, "dur": 0.0, "deaths": 0, "prepots": 0, "ilvl": None})
    for p in pulls:
        code, fid = p["report"], p["fight_id"]
        cmap = cmaps[code]
        # per-fight player_fight totals by (actor_id, data_type)
        pf = defaultdict(dict)
        for r in be.con.execute(
                "SELECT actor_id, data_type, total, active_time FROM player_fight "
                "WHERE report=? AND fight_id=?", (code, fid)):
            pf[r["actor_id"]][r["data_type"]] = (r["total"], r["active_time"])
        deaths = defaultdict(int)
        for r in be.con.execute(
                "SELECT player_name FROM death WHERE report=? AND fight_id=?",
                (code, fid)):
            deaths[canonical(r["player_name"])] += 1
        prepot = {r["actor_id"]: r["prepot"] for r in be.con.execute(
            "SELECT actor_id, prepot FROM conso WHERE report=? AND fight_id=?",
            (code, fid))}
        for (cfid, aid), c in cmap.items():
            if cfid != fid:
                continue
            pid = player_id_for(hist, c["player_name"], raid_label, guild, pid_cache)
            if pid is None:
                continue
            k = (pid, p["encounter_id"], p["difficulty"], c["spec"])
            a = agg[k]
            a["n_pulls"] += 1
            a["n_kills"] += p["kill"] or 0
            a["dur"] += p["duration_s"] or 0.0
            tdt = pf.get(aid, {})
            a["dmg"] += (tdt.get("DamageDone") or (0, 0))[0] or 0
            a["heal"] += (tdt.get("Healing") or (0, 0))[0] or 0
            a["dtaken"] += (tdt.get("DamageTaken") or (0, 0))[0] or 0
            metric_dt = ROLE_METRIC_DT.get(c["role"], "DamageDone")
            a["active"] += (tdt.get(metric_dt) or (0, 0))[1] or 0
            a["deaths"] += deaths.get(canonical(c["player_name"]), 0)
            a["prepots"] += prepot.get(aid) or 0
            if c["item_level"]:
                a["ilvl"] = max(a["ilvl"] or 0, c["item_level"])

    # median percentile per (player, encounter, difficulty) from h_percentile,
    # already ingested for this raid_label.
    pct = defaultdict(list)
    for r in hist.con.execute(
            "SELECT player_id, encounter_id, difficulty, rank_percent "
            "FROM h_percentile WHERE raid_label=? AND rank_percent IS NOT NULL",
            (raid_label,)):
        pct[(r["player_id"], r["encounter_id"], r["difficulty"])].append(r["rank_percent"])

    for (pid, enc, diff, spec), a in agg.items():
        dur = a["dur"] or 0.0
        pl = pct.get((pid, enc, diff))
        hist.upsert("roll_player_encounter", {
            "player_id": pid, "raid_label": raid_label, "encounter_id": enc,
            "difficulty": diff, "spec": spec, "n_pulls": a["n_pulls"],
            "n_kills": a["n_kills"], "dmg_total": a["dmg"], "heal_total": a["heal"],
            "dtaken_total": a["dtaken"], "active_time_ms": a["active"],
            "duration_s": round(dur, 1),
            "dps": round(a["dmg"] / dur, 1) if dur else None,
            "hps": round(a["heal"] / dur, 1) if dur else None,
            "dtps": round(a["dtaken"] / dur, 1) if dur else None,
            "active_pct": round(100.0 * a["active"] / (dur * 1000), 1) if dur else None,
            "deaths": a["deaths"], "prepots": a["prepots"],
            "median_percentile": round(median(pl), 1) if pl else None,
            "ilvl": a["ilvl"]},
            ["player_id", "raid_label", "encounter_id", "difficulty", "spec"])


def _roll_avoidable(hist, be, raid_label, guild, pulls, cmaps, pid_cache):
    """deep_dmg_taken x mechanics_ref (class avoidable/reducible/soak), mirroring
    analyze.cmd_avoidable. Needs refs/mechanics_ref.json (per-workdir)."""
    ref_p = os.path.join(be.workdir, "refs", "mechanics_ref.json")
    if not os.path.exists(ref_p):
        print(f"[history] no mechanics_ref.json (skipping roll_player_avoidable)")
        return
    ref = json.load(open(ref_p, encoding="utf-8"))
    agg = defaultdict(lambda: {"hits": 0, "tot": 0, "pulls": set()})
    for p in pulls:
        code, fid = p["report"], p["fight_id"]
        mechs = (ref.get(str(p["encounter_id"])) or {}).get("mechanics") or {}
        cmap = cmaps[code]
        for r in be.con.execute(
                "SELECT target_id, ability_id, COUNT(*) hits, "
                "SUM(amount+COALESCE(absorbed,0)) tot FROM deep_dmg_taken "
                "WHERE report=? AND fight_id=? GROUP BY target_id, ability_id",
                (code, fid)):
            m = mechs.get(str(r["ability_id"]))
            if not m or m.get("class") not in ("avoidable", "reducible", "soak"):
                continue
            pid, spec = _pid_on(hist, be, raid_label, guild, code, fid,
                                r["target_id"], cmap, pid_cache)
            if pid is None:
                continue
            a = agg[(pid, p["encounter_id"], r["ability_id"], spec)]
            a["hits"] += r["hits"]
            a["tot"] += r["tot"] or 0
            a["pulls"].add((code, fid))
    for (pid, enc, ab, spec), a in agg.items():
        hist.upsert("roll_player_avoidable", {
            "player_id": pid, "raid_label": raid_label, "encounter_id": enc,
            "ability_id": ab, "spec": spec, "hit_count": a["hits"],
            "total_unmitigated": a["tot"], "n_pulls": len(a["pulls"])},
            ["player_id", "raid_label", "encounter_id", "ability_id", "spec"])


def _roll_interrupt(hist, be, raid_label, guild, pulls, cmaps, pid_cache):
    """raid_event kind='interrupt' counted by interrupter (source_id)."""
    agg = defaultdict(lambda: {"kicks": 0, "pulls": set()})
    for p in pulls:
        code, fid = p["report"], p["fight_id"]
        cmap = cmaps[code]
        for r in be.con.execute(
                "SELECT source_id, COUNT(*) n FROM raid_event WHERE report=? "
                "AND fight_id=? AND kind='interrupt' GROUP BY source_id", (code, fid)):
            pid, spec = _pid_on(hist, be, raid_label, guild, code, fid,
                                r["source_id"], cmap, pid_cache)
            if pid is None:
                continue
            a = agg[(pid, p["encounter_id"], spec)]
            a["kicks"] += r["n"]
            a["pulls"].add((code, fid))
    for (pid, enc, spec), a in agg.items():
        hist.upsert("roll_player_interrupt", {
            "player_id": pid, "raid_label": raid_label, "encounter_id": enc,
            "spec": spec, "kicks_done": a["kicks"], "n_pulls": len(a["pulls"])},
            ["player_id", "raid_label", "encounter_id", "spec"])


def _roll_cd_cast(hist, be, raid_label, guild, pulls, cmaps, pid_cache):
    """deep_cast of RAID_CDS abilities, counted by caster."""
    ids = ",".join(str(i) for i in RAID_CDS)
    agg = defaultdict(lambda: {"casts": 0, "pulls": set()})
    for p in pulls:
        code, fid = p["report"], p["fight_id"]
        cmap = cmaps[code]
        for r in be.con.execute(
                "SELECT source_id, ability_id, COUNT(*) n FROM deep_cast "
                "WHERE report=? AND fight_id=? AND type='cast' AND ability_id IN (%s) "
                "GROUP BY source_id, ability_id" % ids, (code, fid)):
            pid, spec = _pid_on(hist, be, raid_label, guild, code, fid,
                                r["source_id"], cmap, pid_cache)
            if pid is None:
                continue
            a = agg[(pid, p["encounter_id"], r["ability_id"], spec)]
            a["casts"] += r["n"]
            a["pulls"].add((code, fid))
    for (pid, enc, ab, spec), a in agg.items():
        hist.upsert("roll_player_cd_cast", {
            "player_id": pid, "raid_label": raid_label, "encounter_id": enc,
            "ability_id": ab, "spec": spec, "casts": a["casts"],
            "n_pulls": len(a["pulls"])},
            ["player_id", "raid_label", "encounter_id", "ability_id", "spec"])


def _roll_aura_uptime(hist, be, raid_label, guild, pulls, cmaps, pid_cache):
    """Signature-buff uptime via analyze._aura_windows/_union_ms, scoped to
    spec_kpis buffs_track ids. Pull-duration-weighted across the night. Needs
    refs/spec_kpis.json (per-workdir)."""
    kpis = analyze._spec_kpis(be)
    if not kpis:
        print("[history] no spec_kpis.json (skipping roll_player_aura_uptime)")
        return
    agg = defaultdict(lambda: {"up": 0, "dur": 0, "pulls": set()})
    for p in pulls:
        code, fid = p["report"], p["fight_id"]
        dur_ms = int((p["duration_s"] or 0) * 1000)
        if dur_ms <= 0:
            continue
        cmap = cmaps[code]
        for (cfid, aid), c in cmap.items():
            if cfid != fid:
                continue
            sk = kpis.get(f"{c['class']}-{c['spec']}")
            if not sk:
                continue
            bt = [b for b in (sk.get("buffs_track") or []) if b.get("id")]
            if not bt:
                continue
            pid = player_id_for(hist, c["player_name"], raid_label, guild, pid_cache)
            if pid is None:
                continue
            ids = ",".join(str(b["id"]) for b in bt)
            if sk.get("role") == "healer":
                rows = [dict(r) for r in be.con.execute(
                    "SELECT ts_rel, type, target_id, ability_id FROM deep_aura "
                    "WHERE report=? AND fight_id=? AND kind='buff' AND source_id=? "
                    "AND ability_id IN (%s) ORDER BY ts_rel" % ids, (code, fid, aid))]
                wins = analyze._aura_windows(rows, dur_ms)
                by_ab = defaultdict(list)
                for (tgt, ab), v in wins.items():
                    by_ab[ab].extend(v)
                up = {ab: analyze._union_ms(v) for ab, v in by_ab.items()}
            else:
                rows = [dict(r) for r in be.con.execute(
                    "SELECT ts_rel, type, target_id, ability_id FROM deep_aura "
                    "WHERE report=? AND fight_id=? AND kind='buff' AND target_id=? "
                    "AND ability_id IN (%s) ORDER BY ts_rel" % ids, (code, fid, aid))]
                uptime = analyze._aura_uptime_ms(rows, dur_ms)
                up = {ab_id: uptime.get((aid, ab_id), 0)
                      for ab_id in (b["id"] for b in bt)}
            for b in bt:
                a = agg[(pid, p["encounter_id"], b["id"], c["spec"])]
                a["up"] += min(up.get(b["id"], 0), dur_ms)
                a["dur"] += dur_ms
                a["pulls"].add((code, fid))
    for (pid, enc, ab, spec), a in agg.items():
        hist.upsert("roll_player_aura_uptime", {
            "player_id": pid, "raid_label": raid_label, "encounter_id": enc,
            "ability_id": ab, "spec": spec,
            "uptime_pct": round(100.0 * a["up"] / a["dur"], 1) if a["dur"] else None,
            "n_pulls": len(a["pulls"])},
            ["player_id", "raid_label", "encounter_id", "ability_id", "spec"])


# ----------------------------------------------------------------- driver

def sync_workdir(hist, workdir, rollups_only=False):
    workdir = os.path.abspath(workdir)
    load_env(workdir)
    cfg = load_config(workdir)
    raid_label = cfg.get("label")
    if not raid_label:
        sys.exit(f"[history] {workdir}: raid.json has no 'label' (raid_label)")
    guild = cfg.get("guild")
    be = Backend(workdir)
    pid_cache = {}
    hist.con.execute("BEGIN")
    try:
        if not rollups_only:
            for t in FACT_TABLES:
                hist.con.execute(f"DELETE FROM {t} WHERE raid_label=?", (raid_label,))
            ingest_facts(hist, be, cfg, raid_label, guild, pid_cache)
        for t in ROLL_TABLES:
            hist.con.execute(f"DELETE FROM {t} WHERE raid_label=?", (raid_label,))
        rebuild_rollups(hist, be, cfg, raid_label, guild, pid_cache)
        hist.commit()
    except Exception:
        hist.con.rollback()
        raise
    n_players = hist.con.execute("SELECT COUNT(*) FROM player").fetchone()[0]
    n_roll = hist.con.execute(
        "SELECT COUNT(*) FROM roll_player_encounter WHERE raid_label=?",
        (raid_label,)).fetchone()[0]
    print(f"[history] synced {raid_label} ({workdir}): players={n_players}, "
          f"roll_player_encounter rows={n_roll}")


def main():
    ap = argparse.ArgumentParser(description="roll Tier-1 aggregates -> history.db")
    ap.add_argument("workdirs", nargs="+", help="raid workdir(s)")
    ap.add_argument("--backfill", action="store_true",
                    help="(re)sync several existing nights (same as listing them)")
    ap.add_argument("--rebuild-rollups", action="store_true",
                    help="recompute rollups only (facts untouched)")
    args = ap.parse_args()

    hist = HistoryDB(history_db_path())
    print(f"[history] db: {history_db_path()}")
    for wd in args.workdirs:
        sync_workdir(hist, wd, rollups_only=args.rebuild_rollups)
    hist.con.execute("ANALYZE")
    hist.commit()


if __name__ == "__main__":
    main()
