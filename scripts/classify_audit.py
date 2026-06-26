#!/usr/bin/env python3
"""ACTIVE classification audit: does the log contradict the static mechanic
`class` of references/zones/<zone>/mechanics_ref.json (copied to
<workdir>/refs/mechanics_ref.json by the zone bootstrap)?

This is the AUTOMATION of the manual doctrine in CHANGELOG 1.2.8 /
references/interpretation-traps.md trap class I ("Unverified classification"):
the zone-ref `class` is a HYPOTHESIS, never ground truth. The proof the doctrine
demands is "per-wave distinct-target count in the log". This script computes
exactly that and flags every ability whose MEASURED dispersion contradicts its
STATIC label.

ALGORITHM (per the doctrine):
  For each pull of raid.db, for each ability_id present in deep_dmg_taken:
    * keep only hits on PLAYER targets (target_id present in `composition`
      of that fight — pets/NPCs would pollute the distinct-target count and the
      raid-size denominator);
    * bucket events into ~2 s windows (ts_rel // 2000);
    * COUNT(DISTINCT player target_id) per bucket.
  Aggregate every bucket's distinct-count across ALL pulls of the encounter and
  take the MEDIAN. Raid size = median `composition` size over the encounter's
  pulls (stable across a DC'd pull).

  Heuristic (the two doctrine poles):
    * median >= RAIDWIDE_FRAC * raid_size (~all the raid each wave)
        -> measured RAID-WIDE (a CD/heal mechanic, NOT "avoidable");
    * median <= HANDFUL (a handful)
        -> measured POSITIONAL / AVOIDABLE;
    * in between -> indeterminate (no strong verdict; reported, never flagged).

CONTRADICTIONS flagged (ADVISORY — this script NEVER edits mechanics_ref.json):
    * static class is raid-wide-ish (unavoidable / raid-wide / reducible) but the
      median is a handful  -> probable AVOIDABLE / positional;
    * static class is positional (avoidable) but the median is ~the whole raid
      -> probable RAID-WIDE CD;
    * static class is tank but the median is ~the whole raid -> probable RAID-WIDE
      (a tank mechanic must hit 1-2 players, not 20).

  soak / execution / unknown have intrinsically PARTIAL expected dispersion
  (subset soakers / banished players / undocumented) so a mid-range median is
  NOT a contradiction; they are reported with their measurement for context but
  only the clear poles above are flagged.

  CRITICAL GATE — the avoidable<->raid-wide axis only applies to AREA mechanics
  (`target: "all"`). A mechanic the ref marks `target: targeted/random/tank/
  shared/banished` is a debuff/ciblé/soak hit that BY DESIGN lands on 1-few
  players each wave; a low distinct-target count there is EXPECTED and proves
  nothing about avoidability (a placed DoT cannot be dodged, it is a heal/kick
  charge). Flagging those as "should be avoidable" would be the exact false
  reproach the trap-I doctrine forbids. So the contradiction test is GATED on
  target=="all"; non-area targets are measured and reported as context only.

LOUD, not silent (interpretation-traps "no silent cap"): an ability with too few
events/buckets to conclude is reported as "inconclusive" in the JSON, never
dropped in silence.

CLI:
    python3 classify_audit.py [WORKDIR] [--json-only] [--all]
      WORKDIR     raid workdir (default: $RAID_WORKDIR or cwd)
      --all       include the FULL per-(enc,ability) measurement table on stdout
                  (default stdout = flagged contradictions + inconclusive only)
      --json-only suppress the readable table, write JSON only

Writes:  <workdir>/digests/analysis/classify_audit.json
Reads:   <workdir>/raid.db, <workdir>/refs/mechanics_ref.json  (READ-ONLY)
"""
import argparse
import json
import os
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wcl import Backend, load_config, load_env, report_codes, workdir_from_args

# --- tunables (the two doctrine poles) -------------------------------------
BUCKET_MS = 2000        # ~2 s wave window (ts_rel // BUCKET_MS)
RAIDWIDE_FRAC = 0.80    # median >= 80% of raid size  -> raid-wide
HANDFUL = 3             # median <= 3 distinct targets -> positional/avoidable
MIN_BUCKETS = 4         # fewer measured waves than this -> inconclusive

# Static-class semantics. The doctrine's contradiction axis is exactly
# avoidable <-> unavoidable/raid-wide (CHANGELOG 1.2.8 / trap I):
#   * raid-wide-ish = the label asserts ~everyone eats it each wave (CD/heal it),
#     so a handful-median on an AREA mechanic CONTRADICTS it;
#   * positional = the label asserts you avoid it, so a whole-raid median
#     CONTRADICTS it.
# `reducible` is deliberately NOT a raid-wide label: in this ref it covers a
# heterogeneous mix (interrupt-miss / add-switch / kill-speed mechanics whose
# damage is single/few-target by nature, AND spread-managed AoE). It makes no
# clean dispersion claim, so a low median is NOT a contradiction — it is surfaced
# as a softer REVIEW note (with a pointer to verify the prose) but never a hard
# flag, to avoid the false "should be avoidable" reproach trap-I warns against.
# tank/soak/execution/unknown have partial expected dispersion (context only; the
# one hard flag among them is the unambiguous "tank but whole-raid").
RAIDWIDE_LABELS = {"unavoidable", "raid-wide", "raidwide"}
POSITIONAL_LABELS = {"avoidable"}
REVIEW_LABELS = {"reducible"}
PARTIAL_LABELS = {"tank", "soak", "execution", "unknown"}


def load_ref(workdir):
    """mechanics_ref.json -> {encounter_id:int -> {boss, mechs:{ability_id:int ->
    mech_dict}}}. Skips non-numeric top-level keys (e.g. the "trash" entry whose
    `mechanics` is a list, not a per-ability dict) and any non-dict mech."""
    ref_p = os.path.join(workdir, "refs", "mechanics_ref.json")
    if not os.path.exists(ref_p):
        sys.exit(f"missing {ref_p} — run the zone bootstrap first "
                 "(references/zone-bootstrap.md)")
    raw = json.load(open(ref_p, encoding="utf-8"))
    out = {}
    for enc, d in raw.items():
        if not str(enc).isdigit() or not isinstance(d, dict):
            continue
        mechs = d.get("mechanics")
        if not isinstance(mechs, dict):
            continue
        parsed = {}
        for aid, m in mechs.items():
            if str(aid).isdigit() and isinstance(m, dict):
                parsed[int(aid)] = m
        out[int(enc)] = {"boss": d.get("boss"), "mechs": parsed}
    return out, ref_p


def encounter_pulls(be, cfg):
    """All boss pulls of this raid ID grouped by encounter_id, across nights.
    Each pull dict carries report/fight_id/boss/encounter_id."""
    by_enc = defaultdict(list)
    for code in report_codes(cfg):
        for r in be.con.execute(
                "SELECT report, fight_id, encounter_id, boss, kill, difficulty "
                "FROM pull WHERE report=?", (code,)):
            if r["encounter_id"] is not None:
                by_enc[r["encounter_id"]].append(dict(r))
    return by_enc


def pull_players(be, code, fid):
    """Set of PLAYER actor_ids present in this fight's composition."""
    return {r["actor_id"] for r in be.con.execute(
        "SELECT actor_id FROM composition WHERE report=? AND fight_id=?",
        (code, fid))}


def measure_ability(be, pulls):
    """Across all `pulls` of one encounter, for every ability_id seen in
    deep_dmg_taken (restricted to player targets), return:
        ability_id -> {buckets:[distinct-count per ~2s wave], pulls_obs:int}
    Only player targets count (raid dispersion, not pet/NPC noise)."""
    # ability_id -> list of bucket distinct-counts (across pulls)
    buckets = defaultdict(list)
    # ability_id -> set of (report,fid) where it had >=1 player-target bucket
    pulls_seen = defaultdict(set)
    for p in pulls:
        code, fid = p["report"], p["fight_id"]
        players = pull_players(be, code, fid)
        if not players:
            continue
        # ability_id -> {bucket_index -> set(player target_id)}
        per_ab = defaultdict(lambda: defaultdict(set))
        for r in be.con.execute(
                "SELECT ts_rel, target_id, ability_id FROM deep_dmg_taken "
                "WHERE report=? AND fight_id=?", (code, fid)):
            if r["target_id"] in players and r["ts_rel"] is not None:
                per_ab[r["ability_id"]][r["ts_rel"] // BUCKET_MS].add(r["target_id"])
        for aid, bmap in per_ab.items():
            buckets[aid].extend(len(s) for s in bmap.values())
            pulls_seen[aid].add((code, fid))
    return {aid: {"buckets": buckets[aid], "pulls_obs": len(pulls_seen[aid])}
            for aid in buckets}


def raid_size(be, pulls):
    """Median composition size over the encounter's pulls (robust to a pull
    where someone DC'd). 0 if no composition rows anywhere."""
    sizes = [len(pull_players(be, p["report"], p["fight_id"])) for p in pulls]
    sizes = [s for s in sizes if s > 0]
    return int(statistics.median(sizes)) if sizes else 0


def classify(median, rsize):
    """Measured dispersion -> one of raid-wide / positional / mixed."""
    if rsize and median >= RAIDWIDE_FRAC * rsize:
        return "raid-wide"
    if median <= HANDFUL:
        return "positional"
    return "mixed"


def verdict_for(static_class, target, measured, median, rsize):
    """(flagged: bool, verdict: str). The suggested correction when the measured
    dispersion contradicts the static label; otherwise a consistency/context note.

    The avoidable<->raid-wide contradiction is GATED on an AREA mechanic
    (target=="all"): for targeted/random/tank/shared/banished a low distinct-
    target count is expected by design and is never a contradiction."""
    sc = (static_class or "").lower()
    tgt = (target or "").lower()
    is_area = tgt == "all"
    if is_area:
        # raid-wide-ish label but a handful eats it each wave -> avoidable
        if sc in RAIDWIDE_LABELS and measured == "positional":
            return True, (f"labelled '{static_class}' (raid-wide) but median "
                          f"{median}/{rsize} targets = a handful -> probable "
                          f"AVOIDABLE / positional (verify radius/target on Wowhead)")
        # positional label but whole raid eats it -> raid-wide CD
        if sc in POSITIONAL_LABELS and measured == "raid-wide":
            return True, (f"labelled 'avoidable' but median {median}/{rsize} "
                          f"targets = ~whole raid each wave -> probable RAID-WIDE "
                          f"(mitigate with CDs/heal, NOT a positional fault)")
    # tank label but whole raid eats it -> raid-wide (regardless of target field:
    # a tank hit landing on ~everyone is wrong whatever the ref's target says)
    if sc == "tank" and measured == "raid-wide":
        return True, (f"labelled 'tank' but median {median}/{rsize} targets = "
                      f"~whole raid -> probable RAID-WIDE (a tank hit cannot land "
                      f"on the whole raid)")
    # ---- not flagged: review / consistency / context note ----
    # `reducible` on an area mechanic measured at a pole: soft REVIEW pointer (the
    # label is dispersion-neutral, so re-read the prose rather than assert a fix).
    if sc in REVIEW_LABELS and is_area and measured in ("positional", "raid-wide"):
        note = (f"REVIEW: 'reducible' measures {measured} (median {median}/{rsize}). "
                f"reducible is dispersion-neutral (interrupt/switch/kill vs spread-"
                f"AoE) — confirm the 'how' prose matches the measure, not a hard "
                f"contradiction")
    elif not is_area and sc in (RAIDWIDE_LABELS | POSITIONAL_LABELS | REVIEW_LABELS) \
            and measured != "raid-wide":
        note = (f"target='{target}' (not area): {measured} dispersion "
                f"(median {median}/{rsize}) is expected for a {sc} ciblé/debuff "
                f"hit — avoidability axis N/A, context only")
    elif measured == "mixed":
        note = (f"median {median}/{rsize} between the poles — indeterminate, "
                f"no contradiction asserted")
    elif sc in PARTIAL_LABELS:
        note = (f"'{static_class}' has partial expected dispersion; measured "
                f"{measured} (median {median}/{rsize}) — context only")
    else:
        note = f"measured {measured} (median {median}/{rsize}) is consistent"
    return False, note


def run(workdir):
    load_env(workdir)
    cfg = load_config(workdir)
    be = Backend(workdir)
    ref, ref_p = load_ref(workdir)
    by_enc = encounter_pulls(be, cfg)

    rows = []          # full measurement table (one per audited enc,ability)
    for enc, pulls in sorted(by_enc.items()):
        meta = ref.get(enc)
        if not meta:
            continue   # encounter has no ref entry: nothing to audit against
        rsize = raid_size(be, pulls)
        meas = measure_ability(be, pulls)
        for aid, m in sorted(meta["mechs"].items()):
            stat = meas.get(aid)
            bkts = stat["buckets"] if stat else []
            n_buckets = len(bkts)
            pulls_obs = stat["pulls_obs"] if stat else 0
            base = {
                "encounter_id": enc,
                "boss": meta["boss"],
                "ability_id": aid,
                "name": m.get("name"),
                "static_class": m.get("class"),
                "target_field": m.get("target"),
                "raid_size": rsize,
                "pulls_total": len(pulls),
                "pulls_observed": pulls_obs,
                "buckets": n_buckets,
            }
            if n_buckets < MIN_BUCKETS:
                base.update({
                    "median_targets": (round(statistics.median(bkts), 1)
                                       if bkts else None),
                    "max_targets": (max(bkts) if bkts else None),
                    "measured": "inconclusive",
                    "flagged": False,
                    "verdict": (f"only {n_buckets} wave(s) on player targets "
                                f"across {pulls_obs} pull(s) — too few to "
                                f"conclude (need >= {MIN_BUCKETS})"),
                })
                rows.append(base)
                continue
            median = statistics.median(bkts)
            median_disp = round(median, 1)
            measured = classify(median, rsize)
            flagged, verdict = verdict_for(m.get("class"), m.get("target"),
                                           measured, median_disp, rsize)
            base.update({
                "median_targets": median_disp,
                "max_targets": max(bkts),
                "measured": measured,
                "flagged": flagged,
                "verdict": verdict,
            })
            rows.append(base)

    flagged = [r for r in rows if r["flagged"]]
    inconclusive = [r for r in rows if r["measured"] == "inconclusive"]
    review = [r for r in rows
              if not r["flagged"] and str(r["verdict"]).startswith("REVIEW")]
    report = {
        "workdir": workdir,
        "ref_path": ref_p,
        "reports": report_codes(cfg),
        "params": {
            "bucket_ms": BUCKET_MS,
            "raidwide_frac": RAIDWIDE_FRAC,
            "handful": HANDFUL,
            "min_buckets": MIN_BUCKETS,
        },
        "summary": {
            "audited": len(rows),
            "flagged_contradictions": len(flagged),
            "review": len(review),
            "inconclusive": len(inconclusive),
        },
        "flagged": flagged,
        "review": review,
        "inconclusive": inconclusive,
        "all": rows,
    }
    out_dir = os.path.join(workdir, "digests", "analysis")
    os.makedirs(out_dir, exist_ok=True)
    out_p = os.path.join(out_dir, "classify_audit.json")
    with open(out_p, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    return report, out_p


def _print_table(rows, title):
    if not rows:
        return
    print(f"\n{title}")
    for r in rows:
        med = r.get("median_targets")
        meds = f"{med}" if med is not None else "-"
        print(f"  [{r['boss']}] {r['ability_id']} {str(r['name'])[:28]:<28} "
              f"static={str(r['static_class']):<11} "
              f"median={meds}/{r['raid_size']} measured={r['measured']:<12} "
              f"pulls={r['pulls_observed']}/{r['pulls_total']}")
        print(f"      -> {r['verdict']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("workdir", nargs="?", help="raid workdir (default cwd / $RAID_WORKDIR)")
    ap.add_argument("--all", action="store_true",
                    help="print the FULL per-ability measurement table")
    ap.add_argument("--json-only", action="store_true",
                    help="write JSON only, no readable table")
    args = ap.parse_args()
    workdir = workdir_from_args(args)

    report, out_p = run(workdir)
    s = report["summary"]
    print(f"classify_audit: {s['audited']} abilities audited "
          f"({len(report['reports'])} report(s)) — "
          f"{s['flagged_contradictions']} contradiction(s) flagged, "
          f"{s['review']} review, {s['inconclusive']} inconclusive.")

    if not args.json_only:
        if report["flagged"]:
            _print_table(report["flagged"],
                         "CONTRADICTIONS (static class vs measured dispersion):")
        else:
            print("\nNo hard contradiction: every avoidable/raid-wide label is "
                  "consistent with its measured dispersion (or indeterminate). The "
                  "zone ref holds for the audited area mechanics.")
        _print_table(report["review"],
                     "REVIEW (reducible — dispersion-neutral, confirm prose):")
        if args.all:
            consistent = [r for r in report["all"]
                          if not r["flagged"] and r["measured"] != "inconclusive"
                          and not str(r["verdict"]).startswith("REVIEW")]
            _print_table(consistent, "ALL OTHER MEASUREMENTS (consistent / context):")
        _print_table(report["inconclusive"],
                     "INCONCLUSIVE (too few waves on player targets — NOT a verdict):")
    print(f"\n-> {out_p}")


if __name__ == "__main__":
    main()
