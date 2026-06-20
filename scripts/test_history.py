#!/usr/bin/env python3
"""Technical test harness for the 3-tier data layer (Tier 0 lzma cache + Tier 1
history.db). Stdlib `unittest`, fully synthetic (no WCL, no network, no real
workdir) — builds a tiny raid.db + percentiles.json in a temp dir and asserts
the plumbing invariants.

Run:  python3 scripts/test_history.py
This covers the PLUMBING; functional non-regression of the rendered report is
covered separately by recette_nonreg.sh (old-vs-new deliverable diff).
"""
import json
import lzma
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wcl
import history_sync


def build_workdir(wd, label, report, *, alice_dmg=1_000_000, dur_s=100.0,
                  alice_pctl=92):
    """Minimal but representative raid.db + raid.json + percentiles.json.
    Roster Alice (dps) + Bob (healer); plus a Pet actor and an actor_id=0
    sentinel row that MUST be excluded from the player dimension. Bob also has a
    conso row on a fight WITHOUT a composition entry (resolved via actor_name)."""
    os.makedirs(wd, exist_ok=True)
    with open(os.path.join(wd, "raid.json"), "w") as f:
        json.dump({"reports": [report], "report": report, "guild": "Testguild",
                   "label": label, "size": 25, "zone_id": 1054}, f)
    be = wcl.Backend(wd)
    be.upsert("raid_session", {
        "report": report, "guild": "Testguild", "zone": "Z", "zone_id": 1054,
        "raid_label": label, "title": "T", "start_ts": 1000, "end_ts": 9000,
        "ingested_at": "now"}, ["report"])
    be.upsert("actor_name", {"report": report, "actor_id": 1, "name": "Alice",
                             "type": "Player", "sub_type": None}, ["report", "actor_id"])
    be.upsert("actor_name", {"report": report, "actor_id": 2, "name": "Bob",
                             "type": "Player", "sub_type": None}, ["report", "actor_id"])
    be.upsert("actor_name", {"report": report, "actor_id": 99, "name": "Wolf",
                             "type": "Pet", "sub_type": None}, ["report", "actor_id"])
    be.upsert("pull", {
        "report": report, "fight_id": 10, "encounter_id": 1000, "boss": "Boss",
        "difficulty": 4, "size": 25, "kill": 1, "boss_pct": 0, "fight_pct": 0,
        "last_phase": 1, "duration_s": dur_s, "start_time": 0, "end_time": int(dur_s * 1000),
        "pull_number": 1}, ["report", "fight_id"])
    for aid, nm, cls, spec, role, il in ((1, "Alice", "Warrior", "Arms", "dps", 550),
                                         (2, "Bob", "Priest", "Holy", "healer", 549)):
        be.upsert("composition", {
            "report": report, "fight_id": 10, "actor_id": aid, "player_name": nm,
            "class": cls, "spec": spec, "role": role, "item_level": il},
            ["report", "fight_id", "actor_id"])
    be.upsert("player_fight", {"report": report, "fight_id": 10, "actor_id": 1,
              "data_type": "DamageDone", "total": alice_dmg, "active_time": 90000},
              ["report", "fight_id", "actor_id", "data_type"])
    be.upsert("player_fight", {"report": report, "fight_id": 10, "actor_id": 2,
              "data_type": "Healing", "total": 500000, "active_time": 95000},
              ["report", "fight_id", "actor_id", "data_type"])
    # Rows that MUST be excluded: a pet, and the actor_id=0 raid-wide sentinel.
    be.upsert("player_fight", {"report": report, "fight_id": 10, "actor_id": 99,
              "data_type": "DamageDone", "total": 123, "active_time": 1},
              ["report", "fight_id", "actor_id", "data_type"])
    be.upsert("player_fight", {"report": report, "fight_id": 10, "actor_id": 0,
              "data_type": "DamageTaken", "total": 9, "active_time": 1},
              ["report", "fight_id", "actor_id", "data_type"])
    be.upsert("death", {"report": report, "fight_id": 10, "seq": 1, "actor_id": 1,
              "player_name": "Alice", "death_time": 50000, "ability_id": 5,
              "ability_name": "X", "overkill": 0}, ["report", "fight_id", "seq"])
    be.upsert("conso", {"report": report, "fight_id": 10, "actor_id": 1, "prepot": 1,
              "combat_pots": 1, "flask": "F", "food": "Fd"},
              ["report", "fight_id", "actor_id"])
    # Bob's conso on a fight with NO composition row -> must resolve via actor_name.
    be.upsert("conso", {"report": report, "fight_id": 11, "actor_id": 2, "prepot": 1,
              "combat_pots": 0, "flask": "F", "food": "Fd"},
              ["report", "fight_id", "actor_id"])
    be.commit()
    dig = os.path.join(wd, "digests")
    os.makedirs(dig, exist_ok=True)
    with open(os.path.join(dig, "percentiles.json"), "w") as f:
        json.dump({f"{report}:10": {
            "encounter_id": 1000, "boss": "Boss", "difficulty": 4, "duration_ms": int(dur_s * 1000),
            "players": [{"name": "Alice", "class": "Warrior", "spec": "Arms",
                         "role": "dps", "metric": "dps", "amount": 10000,
                         "rank_percent": alice_pctl, "best_percent": alice_pctl,
                         "bracket_percent": alice_pctl, "ilvl": 550}]}}, f)
    return be


class CacheLzmaTest(unittest.TestCase):
    def test_roundtrip_blob_and_legacy_text(self):
        with tempfile.TemporaryDirectory() as d:
            be = wcl.Backend(d)
            payload = {"data": {"x": 1, "accent": "éà"}}
            be.con.execute("INSERT INTO wcl_raw VALUES (?,?,?,?,?)",
                           ("blob", "q", "{}", lzma.compress(json.dumps(payload).encode()), "now"))
            be.con.execute("INSERT INTO wcl_raw VALUES (?,?,?,?,?)",
                           ("legacy", "q", "{}", json.dumps(payload), "now"))
            be.commit()
            self.assertEqual(json.loads(be.cache_get("blob")), payload)
            self.assertEqual(json.loads(be.cache_get("legacy")), payload)
            self.assertIsNone(be.cache_get("missing"))


class HistorySyncTest(unittest.TestCase):
    def _sync(self, hist, wd):
        history_sync.sync_workdir(hist, wd)

    def test_dimension_idempotence_rollup_and_types(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["RAID_HISTORY_DB"] = os.path.join(d, "history.db")
            wd = os.path.join(d, "wd1")
            build_workdir(wd, "id-test-1", "R1")
            hist = history_sync.HistoryDB(history_sync.history_db_path())

            self._sync(hist, wd)
            c = hist.con

            # 1. dimension: only real Players (no pet 'Wolf', no actor_id=0).
            names = {r[0] for r in c.execute("SELECT name FROM player")}
            self.assertEqual(names, {"Alice", "Bob"})

            # 2. no h_player_fight row resolves to a non-Player.
            self.assertEqual(c.execute(
                "SELECT COUNT(*) FROM h_player_fight WHERE player_id NOT IN "
                "(SELECT player_id FROM player)").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM h_player_fight").fetchone()[0], 2)

            # 3. conso resolved via actor_name even without composition (fight 11).
            self.assertEqual(c.execute(
                "SELECT COUNT(*) FROM h_conso").fetchone()[0], 2)

            # 4. rollup throughput: Alice 1e6 dmg / 100s = 10000 dps.
            dps = c.execute("SELECT dps FROM roll_player_encounter e JOIN player p "
                            "USING(player_id) WHERE p.name='Alice'").fetchone()[0]
            self.assertEqual(dps, 10000.0)

            # 5. NUMERIC guard: whole percentile stays integer (renders '92', not '92.0').
            t = c.execute("SELECT typeof(rank_percent) FROM h_percentile "
                          "WHERE player_name='Alice'").fetchone()[0]
            self.assertEqual(t, "integer")

            # 6. rollup reconstructible from facts (non-destructive grain).
            recomputed = c.execute(
                "SELECT SUM(total) FROM h_player_fight WHERE data_type='DamageDone'").fetchone()[0]
            rolled = c.execute("SELECT SUM(dmg_total) FROM roll_player_encounter").fetchone()[0]
            self.assertEqual(recomputed, rolled)

            # 7. idempotence: re-sync -> identical counts.
            before = [c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                      for t in ("player", "h_player_fight", "roll_player_encounter", "h_percentile")]
            self._sync(hist, wd)
            after = [c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                     for t in ("player", "h_player_fight", "roll_player_encounter", "h_percentile")]
            self.assertEqual(before, after)

            del os.environ["RAID_HISTORY_DB"]

    def test_cross_lockout_identity_stable(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["RAID_HISTORY_DB"] = os.path.join(d, "history.db")
            wd1, wd2 = os.path.join(d, "wd1"), os.path.join(d, "wd2")
            build_workdir(wd1, "id-test-1", "R1")
            build_workdir(wd2, "id-test-2", "R2", alice_dmg=2_000_000, alice_pctl=80)
            hist = history_sync.HistoryDB(history_sync.history_db_path())
            history_sync.sync_workdir(hist, wd1)
            history_sync.sync_workdir(hist, wd2)
            # Alice is ONE player across both lockouts.
            rows = hist.con.execute(
                "SELECT COUNT(DISTINCT player_id) FROM roll_player_encounter e "
                "JOIN player p USING(player_id) WHERE p.name='Alice'").fetchone()[0]
            self.assertEqual(rows, 1)
            labels = {r[0] for r in hist.con.execute(
                "SELECT DISTINCT raid_label FROM roll_player_encounter")}
            self.assertEqual(labels, {"id-test-1", "id-test-2"})
            del os.environ["RAID_HISTORY_DB"]


if __name__ == "__main__":
    unittest.main(verbosity=2)
