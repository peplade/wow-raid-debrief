# Methodology — deep raid debrief (max grain)

Born from a real failure: a first report generated from AGGREGATES (CD
counters, totals, a handful of "avoidable" metrics) was rejected as AI slop —
trivia without value, zero improvement axes, one recommendation that made no
sense for the role it targeted. The rebuild rule that fixed it:

> **Write NOTHING that cannot be sourced to a precise event (pull,
> timestamp). Aggregates only rank where to dig.**

## Why raw events beat aggregates

| Question | Aggregate answer (weak) | Event answer (strong) |
|---|---|---|
| Why did we wipe? | "damage too high" | "first death 12.1s, frontal cone on a non-tank, repeated p2/p7/p9 -> base position inside the frontal arc" |
| Is this DoT uptime bad? | "uptime 75%" | "uptime 75% BUT 30s of it is a scripted eviction phase; windowed uptime is 96%" |
| Was the healing weak? | "HPS below tops" | "32M healed on the best pull vs 113M on an early wipe: when avoidance fails, healing cannot compensate — the lever is avoidance" |
| Mechanic X reactive? | (cannot tell) | "71/73 debuff applications ≤300ms after the victim's own hit on the boss = reactive on-hit, proven" |

## Invariants (engraved — violating any of these produced real errors)

1. **Spec per pull.** Mid-night respecs are common (healer flipping
   resto<->dps between progression and farm, dps swapping specs). Any join of
   composition without fight_id lies. `players(be, code, fid)`.
2. **Uptime divides by PULL duration** (WCL standard), never by player
   lifetime — DoTs keep ticking after the caster dies, yielding >100%.
3. **Healer HoTs are tracked BY SOURCE with interval union** ("≥1 target
   active"). HoTs are buffs ON ALLIES: searching them on the healer
   themselves yields zero; filing them as enemy debuffs yields zero.
4. **One formula set, both sides.** Player vs top benchmarks use the SAME
   exec_row() on identical event extractions. If a number cannot be computed
   identically for the top, it does not go in the comparison.
5. **Kill duration is a structural factor, displayed separately.** A 2x
   slower kill inflates some uptimes and CD counts mechanically; compare
   executions, not raw values.
6. **Qualified deaths only.** Deaths on kills, and the first 1-2 deaths of a
   wipe (probable trigger). Dying in the collective wipe is not an individual
   fail; the raw death counter lies.
7. **Spell IDs are reconciled against the actual log.** Cast id != debuff id
   (a sting cast can log its DoT under another id); glyphs change ids. An
   unreconciled id silently produces 0% and a false verdict.
8. **Wipe != damage.** Compare the failed pull to the kill pull first: if
   totals match and only the first death differs, the cause is the trigger
   event (an unmitigated burst window, a missing external), not throughput.
9. **Healing burst != survival.** When avoidance fails, more HPS does not
   save the pull. Check avoidance levers before healing levers.
10. **Evictions/assignments first.** On any boss with an eviction phase or
    asymmetric assignment (belt teams, realm phases, soak rosters), identify
    who was assigned BEFORE comparing anyone's numbers.
11. **The deliverable is nominative execution, not aggregates.** A raid lead
    asks "who kicks their assignment, who switches to the right target on
    time, who camps the ground AoE, who had a defensive available and sat on
    it" — per pull, with timings. Aggregate tables (deaths per mechanic, CD
    totals) are inputs to that answer, never the answer. Field-tested: an
    aggregate-only delivery was rejected twice on the same night's CR.
12. **CD availability runs on the ABSOLUTE night timeline.** Repulls are
    ~2 min; a raid CD burned late in pull N is still down at the next pull
    start. "They had Barrier available" must survive that check (durations
    indicative; talents are not in the log — say so when rendering).
13. **Name what the log cannot see.** Resource-bar gains (orb soaks,
    corruption levels) and world interactions (pressure plates) leave no
    event. State "not measurable from logs" explicitly; a plausible proxy
    presented as measurement is a false verdict waiting to happen.
14. **A "phase never reached/played" claim needs the phase timeline.** Before
    writing that a boss phase was never seen, check measured phases (`phases`
    module / `deep_phase`): a kill in phase N PROVES phase N was played, by
    definition. Report it quantitatively — "P2 reached on 5/16 pulls, real
    damage on 2, kill done in P2" — never "P2 never played / killed in P1"
    (a real published error: the kill was in P2 the whole time). Phase counts
    also re-bracket attempts: see the WCL trash-bucketing gotcha.
15. **Coverage is checked separately from claims — the verdict gate is blind
    to omissions.** Stages 5-6 verify what you DID assert; they never catch
    what you forgot. Before delivery, run an explicit coverage sweep: every
    NIGHT's trash analyzed (not just night 1), every boss verdict spans ALL
    the nights it was pulled, every present-roster player's prose covers the
    nights they played. On a multi-night ID the data digests recompute across
    all nights automatically, but **hand-written prose does NOT** — re-extend
    boss/trash/player/officers prose every time `add-report` appends a night,
    or it silently freezes on night 1 (a real omission caught only on user
    review, not by the gate).
16. **Longitudinal facts are scored per (player, night, spec), never an
    averaged blob.** Cross-lockout trajectories live in `history.db`; player
    identity is the NAME (actor_id is per-report, not stable), and throughput is
    spec-split (a mid-night respec is two rows, never one mixed number). N vs H
    raw DPS is not comparable — only the percentile is (invariant carries to
    trends). A rollup must stay reconstructible from the per-night facts (which
    stay sourced to events): an aggregate that cannot be traced back is a
    verdict waiting to drift.

## Extraction design (API economy)

Principle: **graph/table first, events only where grain demands it.**
- `graph` (DamageTaken/Healing/DamageDone, per-player + Total bucketed
  series; Resources+abilityID:100 = mana%) ≈ 1 point per request vs ~10k
  events.
- `table Deaths` embeds the full death recap (deathWindow, damage by ability,
  healing received, last events, killingBlow): 1 request per pull replaces 2
  per death.
- Full event extraction reserved for: Casts (rotation/downtime/CPM),
  DamageTaken (per-player avoidable heatmap, all abilities), Buffs/Debuffs
  (uptimes, CDs, procs), Healing events (target split), Dispels/Interrupts
  (reactivity), enemy Casts (kick opportunities).
- `fights { phaseTransitions }`: measured per-pull phases (not all bosses).
- Everything cached (`wcl_raw`, sha256 key): re-runs are free, interruptions
  resume at no cost. ~1000-1500 points per 10-player night out of 3600/h.

## Analysis modules (what each one answers)

| Module | Question |
|---|---|
| pacing | where did the night's time go (boss/trash/idle), longest gaps |
| deaths | who died of what, when, in which phase, with what in the last 10s, with which defensives available |
| cdmap | which damage peaks were covered by raid CDs and which were NAKED |
| phases | measured phase timings per pull (progression across pulls) |
| heals | overheal, cast tempo + gaps, mana trajectory, target split per healer |
| dispels | reactivity per dispel event + never-dispelled debuffs (vs zone ref) |
| avoidable | who eats which avoidable/reducible mechanic, how much |
| execution | CPM, GCD downtime (dead-window aware), DoT uptime on main target, CD usage vs possible, proc/buff uptimes — per spec KPIs |
| bench | all of the above vs top1/top2 same spec, same size, same formulas |
| bossdigest | per-pull timeline JSON (phases/deaths/CDs/mechanic buckets) for charts |

## Proof techniques (ranked by strength)

1. **Event-to-event timestamp correlation** ("X always within N ms after Y").
2. **Top-parse differential measurement** (same extraction, same formula, on
   a top kill: what do they actually do at the same point?).
3. **Failed-vs-successful pull comparison** (isolate the trigger).
4. **Cross-source mechanic confirmation** (DBM lua + sim/encounter spec
   agree on id/cadence/target).
5. Aggregate patterns (only to rank candidates — never publishable alone).

## Mechanic classification is a HYPOTHESIS to verify, never a citation

The zone-ref `class` (avoidable / reducible / raid-wide / soak / dispel) and its
"how" are a STARTING POINT. Before any of them reaches a page, cross-check:
1. **Per-wave distinct-target count in the log** — `deep_dmg_taken` bucketed by
   ~2 s: median ≈ the whole raid each wave ⇒ **raid-wide** (mitigate with CDs,
   NOT "avoidable"); a handful ⇒ positional / avoidable.
2. **Authoritative tooltip** — Wowhead MoP-Classic `spell=<id>` (radius / target /
   school) + an encounter guide (Icy Veins / Wowpedia) for the strategic role.

A ref label the log or source contradicts is corrected, not shipped. This rule
exists because the Sha rift pair (Unstable Corruption / Collapsing Rift) and
Bursting Pride shipped MIS-classified from the ref and the verifier missed them
(double-failure, 2026-06). See interpretation-traps class I + checklist item 6.

**A DEEP-DIVE multiplies the stakes — verify EVERY cited mechanic FIRST.** When a
whole boss-page thesis rests on a classification ("the wall was avoidable-ground
attrition"), an unverified ref label is a house of cards: if it falls, the
conclusion falls. Recurrence 2026-06 (Kor'kron Dark Shaman): a 3-table deep-dive
was built straight on `mech_class` WITHOUT per-boss Wowhead cross-check — the
`how` strings carried DBM/sim notes, which made the ref *look* vetted ("annotated"
≠ "verified for THIS boss"). The user (domain authority) flagged it. Corrections
once cross-checked: Foul Geyser = SUBI proximity aura (was "avoidable"); Froststorm
Bolt = TANK nuke uninterruptible (was framed "focus-heal not held / 29 hits on one
player" — it's the tank); Iron Prison (HM) = REDUCIBLE, 100% HP → mandatory
personal CD (a DEFENSIVE lever, not placement); Iron Tomb (HM) = avoidable, was
missing; Falling Ash = mi-avoidable (oneshot zone dodgeable + mandatory raid-wide
chip). Rule: list every mechanic id the deep-dive will cite, cross-check each one
(tooltip + guide) BEFORE writing the narrative — and prefer splitting attrition by
LEVER (placement / defensive-CD / heal), which a single avoidable/unavoidable
binary hides.

## Officers annex

A separate unlisted page (noindex, token URL) for franker per-player notes
and open strategy questions. Public pages stay factual and constructive;
the annex carries the blunt rankings and the raid-lead questions.
