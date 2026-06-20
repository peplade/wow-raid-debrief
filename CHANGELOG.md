# Changelog

All notable changes to this skill. Format: [Keep a Changelog](https://keepachangelog.com),
newest first. Every lesson backported from real raid-night use gets an entry.

## [2.0.1] — 2026-06-20

- **Fix: percentiles render as `92`, not `92.0`.** `h_percentile` stored
  WCL percentiles/ilvl in REAL columns, coercing whole numbers (92, 550) to
  floats — so `evolution.py` emitted `92.0` where the legacy raid.db path
  emitted `92`. Switched those columns to NUMERIC (preserves the int-vs-float
  type WCL returned). Functional non-regression recette (full pipeline
  old-vs-new, diff of every generated page + digest) now passes byte-identical;
  the technical unit checks had missed it (`92 == 92.0` in Python). Rebuild
  `history.db` (`history_sync.py --backfill`) to pick up the column type.

## [2.0.0] — 2026-06-19

- **3-tier data architecture: lzma cache + unified `history.db` for cross-lockout
  analysis.** Storage is now tiered by access pattern. **Tier 0** — the `wcl_raw`
  response cache is lzma-compressed (`response` BLOB; `cache_get` decompresses,
  retro-compatible with legacy TEXT rows). Reclaimed ~62% of disk
  (7.3G→2.8G across existing workdirs); migrate old ones with
  `scripts/migrate_lzma.py` (backup + round-trip gate + VACUUM + `user_version`).
  **Tier 1** — the small aggregate layer (pull, composition, player_fight, death,
  conso, deep_heal_ability, top_parse, percentiles) rolls forward into a durable
  `~/raids/_history/history.db` (`scripts/history_sync.py`, Stage 9), with a
  **stable player dimension keyed on name** (WCL exposes no realm/guild per
  player), per-parse percentiles, and 5 materialized rollups (player×boss×**spec**
  encounter throughput/parse/deaths + 4 raw-event: avoidable, interrupt,
  aura-uptime, CD casts), recomputed idempotently per `raid_label`. **Tier 2** —
  the ~9M-row `deep_*` raw events stay partitioned per night (never queried
  cross-night; unifying them would be 300M+ rows for no use case).
  `evolution.py` now reads `history.db` cross-lockout instead of looping over
  per-workdir silos — **output is byte-identical** (verified diff), so
  `pages_ext` rendering is unchanged. Stdlib-only preserved (lzma/sqlite3).
  Validated GO by 3 Conclave specialists (DBA, db-optimizer, code-reviewer).

## [1.2.14] — 2026-06-19

- **Evolution page: rich + dynamic, and it's a ROLLUP you must republish every
  night.** `page_evolution` now renders the week-over-week page in full
  (previously a lean table-only stub): per-player **percentile chart** and
  **ilvl chart** with one series per week, a **strict comparable** section
  (auto-picks the boss+difficulty killed in the most weeks — raw DPS is only
  comparable same boss + same difficulty — and tables/charts per-player DPS Δ
  from `parses`), the raid trajectory table, gear deltas and roster in/out.
  Fully **dynamic over N weeks** — add a week by passing its workdir to
  `evolution.py`/`pages.py --only evolution`, no code edits. Charts use the
  shared `tlChart`/`CHART_JS` path (lazy-imported to avoid the pages↔pages_ext
  cycle). Process lesson: deploying a raid night is NOT done until the
  evolution rollup + guild hub are regenerated and redeployed — they do not
  update themselves (a night shipped with the rollup left a week behind).

## [1.2.13] — 2026-06-19

- **DTPS curves/stats: never derive from the WCL DamageTaken graph — use
  `deep_dmg_taken.amount` (effective).** The graph (`deep_graph kind='dtps'`)
  SUMS the nominal `unmitigated` value of scripted mechanic hits that log
  ~1e9/hit (e.g. Sha of Pride **149031 Banishment**, 1,000,000,000/hit, ~1.4M
  effective). On wipes this inflated the per-pull "raid damage taken / s" curve
  ~100x (up to **480M/s vs ~500k/s** real); the kill happened to be clean, so
  only the WIPE charts looked wrong. `pages.py timeline_pull_chart` now builds
  the raid curve AND a new **Mark of Arrogance (144351)** subset curve from
  per-event effective `amount`, bucketed to 2s; falls back to the graph
  (with corrected per-interval binning) only when no per-event data exists.
  Poison test: `sum(graph.Total.data)` vs `sum(deep_dmg_taken.amount)` —
  ratio ≫1 = poison (Sha wipe: 136x). `interpretation-traps` self-audit updated.

## [1.2.12] — 2026-06-19

- **redaction-guide rule 13: no section YOU invent.** A section = log-backed data,
  period. Forbidden: editorial recaps, "rules observed → to confirm as policy",
  targets you label "priority", strategic classifications, any synthesis-recommendation
  you author — those are officer/domain calls, not log facts (the EdR §12 "Roster &
  rules" was deleted TWICE; a switch-section "priority targets (proposal to validate)"
  was removed). Missing strategic info → ask or omit, never fabricate an advice
  section. Sharpens rule 2 (rule 2 bans bad recommendations; rule 13 bans inventing
  whole sections of them).

## [1.2.11] — 2026-06-19

- **Prepull / premature-engagement detection ABANDONED — do not build.** The two
  WCL signatures (a boss-named TAP segment, an early `Melee` death) are BLIND to
  PROXIMITY pulls (zone aggro: the raid walks into range without tapping or dying),
  so any tally is a non-exhaustive sample masquerading as a firm count = false
  numbers, and proximity is not recoverable from WCL (no player coordinates).
  `interpretation-traps.md` rule 6 flipped to a do-not-build warning (officers get,
  at most, a raw early-death ledger, never a ranked recidivist list). Matching
  caveats live in `wcl-api-gotchas.md` + `extraction_manifest.md` (shipped with
  1.2.10, shared files). Removed from the EdR CRs (consolidated + soir-1), user
  decision 2026-06-19.

## [1.2.10] — 2026-06-19

- **Kicks: canonical pipeline replaces the simple table.** New `scripts/kicks.py`
  (data builder, ingested tables only — never re-parses `wcl_raw`) + new
  `scripts/kicks_render.py` (per-cast TIMELINE: one cast = one lane, x-axis =
  reaction from the begincast; bar to the kick / full if landed / to death / stub;
  dot `left` clamped ≥0; "en avance" if reaction<0 else "en retard"; -0.0
  normalized). Wired into the boss page (`nominative=False` — lanes + names, no
  scoreboard) and exposed for the officers annex (`nominative=True` — + efficiency/
  wasted scoreboard + "never kicked"; a ranking is blame, officers-only). The old
  per-player/per-spell table in `pages_ext.nominative_section` is removed.
- **Damage-as-landing.** Enemy cast COMPLETIONS are under-logged when several adds
  cast the same spell at once (Sha 273 begincast → 89 completions); the spell's
  `deep_dmg_taken` events are now ground truth for "landed", in union with
  completions. A begincast with no damage and no interrupt = "no-hit" (add
  killed/CC'd), not a missed kick. New `references/kicks.md` (canonical doctrine);
  `extraction_manifest.md` kicks block rewritten (drops the abandoned
  `extract_interrupts.py` framing — id 32747 / extraAbilityGameID / target-was-casting
  gate were artifacts, never log truth); `wcl-api-gotchas.md` gotcha added; `SKILL.md`
  completeness assertion aligned. Avenger's Shield (on-CD damage) is excluded from the
  dedicated kick spells and credited via interrupt events → 100% efficiency.

## [1.2.9] — 2026-06-19

- **`scripts/pages.py` — per-pull boss timeline charts made legible.** The rotated
  `☠ <player>` death labels overlapped into an unreadable block on cascade pulls
  (15+ deaths in a few seconds). Death markers are now **dashed lines only** (they
  still show death timing + the cascade cluster visually); the names + killing blows
  live in the deaths table directly below the chart. Added: **Y-axis formatter**
  (`90 000 000` → `90 M`), and a **legend caption** under each chart (dégâts subis/s ·
  phase · mort · CD de raid). The X-axis already showed m:ss.

## [1.2.8] — 2026-06-19

Backport from the EdR Sha 25H review pass (user caught mis-classified mechanics
in the shipped CR — a DOUBLE-FAILURE: generator shipped them from the ref, the
`wow-cr-verifier` waved them through trusting the same ref). Root-cause fixes:

- **DOCTRINE — mechanic classification is a hypothesis, not a citation.** A ref
  `class` (avoidable/reducible/raid-wide/soak/dispel) or "how" must be
  cross-checked BEFORE display against (a) the log's per-wave distinct-target
  count (`deep_dmg_taken` bucketed ~2 s: ~all the raid/wave = raid-wide → CDs,
  NOT avoidable; a few = positional/avoidable) AND (b) an authoritative source
  (Wowhead MoP-Classic tooltip + encounter guide). Gravé : `interpretation-traps.md`
  **trap class I + checklist item 6**, `methodology.md` ("classification = a
  hypothesis to verify"). The `wow-cr-verifier` agent (in consumer `.claude/agents/`)
  gets a matching **active classification audit** in its Mécaniques lens + an
  authoritative-source row.
- **`zones/soo/mechanics_ref.json` — Sha of Pride classification corrected**
  (verified Wowhead + Icy Veins/Wowpedia + log): Unstable Corruption 147198
  `reducible`→**`avoidable`** (dodgeable bolts, 2 y, ~2/25 per wave); Collapsing
  Rift 147388 `avoidable`→**`reducible`** (the *cost of your close* by walking
  over a rift, 8 y, CD-mitigated; applies Weakened Resolve 147207 = 1 close/min);
  Bursting Pride 144911 stays `avoidable` (pool, 3/25 — the raid-wide one is
  Swelling Pride 144400 at 24/25, FR-name collision fracassant↔croissant);
  **Projection 145320 added** (raid-wide pulse, 500 y, unavoidable).
- **`zones/soo/traps.md` — Sha rift entry rewritten**: "soak" framing dropped for
  the real mechanic (open rift → avoidable Unstable Corruption; close by walking
  over → Collapsing Rift + Weakened Resolve lockout; lever = close-COVERAGE
  breadth; count real closes via 147207, NOT 147388-taken which includes 8 y splash).
- **`scripts/pages.py` + `themes/default.css` — collapsible pulls.** Wipes now
  render as `<details>` collapsed by default (kill stays `open`); the timeline
  chart lazy-renders on expand (existing `tlChart` IntersectionObserver). New
  locale `wipes_collapsed_hint` (en/fr). Long progression nights stop being a
  wall of charts.

## [1.2.7] — 2026-06-19

Backport from the EdR Sha of Pride 25H night (per-add participation feature +
full roster spec coverage).

- **`scripts/execution.py` + `scripts/pages_ext.py` + `zones/soo/execution.json`**
  — NEW per-add **damage-participation split** (`npc_dps_split`): the NPC
  participation engine now emits, per priority add, each player's damage AND
  their **% share** of the raid's damage to that add (additive — the lumped
  `npc_dps` stays for back-compat). Rendered as one labelled table per add
  (abs + % share). `npc_dps_targets["Sha of Pride"]` added = Manifestation of
  Pride (big adds) + Corrupted Fragment (rift adds) + Reflection (mirrors). Use
  case: "who actually DPS'd the fragments vs the big adds vs the tank-soaked
  reflets" — the reflet tank-dominance is the intended Vengeance assignment,
  not an anomaly to flag.
- **`zones/spec_kpis_mop.json`** — +13 specs (full MoP roster coverage):
  Paladin Ret/Prot, Warrior Fury/Prot, Warlock Affli/Destro, Mage Arcane/Fire,
  Priest Holy, Monk Mistweaver, Hunter BM, DK Unholy, Shaman Ele. Built from
  the actual logged ability ids (empirical, cross-checked vs deep_cast/deep_aura)
  + Hekili/wowsims/SimC sources. Fixes silent 0%-uptime and empty-bench on
  rosters using these specs (the gap was hit hard by a 28-player GDKP — 13 of
  20 played specs were missing).

## [1.2.6] — 2026-06-19

Backport from extending the Sha of Pride page with per-pull damage-taken and
lockout-time charts (numbers cross-checked exactly against the source DB).

- **`zones/soo/traps.md`** — (1) the Mark of Arrogance row gains a MEASURABLE
  KPI: Mark damage-taken DTPS (id 144351) per pull = the cost of leaving stacks
  up; it collapses on a clean kill (60k DTPS vs 138-285k on wipes) → use it as
  the dispel-discipline signal, not a raw dispel count. (2) New row: Corrupted
  Prison / Banishment **time locked** = a break-SPEED metric (raid frees the
  prisoner), measured by pairing `applydebuff`→`removedebuff` per (target,
  ability) — Corrupted Prison ids 144574/144615/144636/144683/144684 (cast
  144563), Banishment 145215 (HM, avg=0 in Normal = HM-only witness). Lower =
  freed faster, never a fault on the prisoner.

## [1.2.5] — 2026-06-18

Backport from a live session (Sha of Pride rift soaking analysis + a new
prepull-accountability section), validated by an adversarial `wow-cr-verifier`
pass (GO/GO, 0 blocker) before publishing.

- **`interpretation-traps.md`** — new nominative rule 6: **prepull /
  premature-engagement attribution**. Two log signatures (boss-named short TAP
  segment attributed to its death; `Melee` RAN-IN death `<5 s` into a pull) with
  the anti-blame guards (tank-ambiguous never auto-counted, no-death segment not
  attributed, leads-to-wipe = correlation not cause, death = engagement proxy).
  Officers-only by perimeter.
- **`zones/soo/traps.md`** — new Sha of Pride HM row: **Rift of Corruption
  (soak)**. Heroic-only (Normal-kill witness = 0 soak hits). Unstable Corruption
  147198 = 350k Shadow + **5 Pride per hit**; Rift Collapse 147388 = 250k on
  close. Soaking = damage AND Pride injection (fuels Swelling Pride) → a soaker-
  rotation + Pride-economy problem, framed collectively; Pride-from-rifts =
  hits×5 component proxy, never the bar. IDs verified wowhead mop-classic.
- **`wcl-api-gotchas.md`** — (1) boss-named short trash segment = a PREPULL tap
  (opposite of phase-content trash), attribute by the death; deep tables don't
  cover these segments. (2) workdir DB aggregates many guilds → filter EVERY
  query via `raid_session.guild`, never assume a table holds only your reports.
  (3) `death.death_time` is fight-relative ms (≠ absolute `pull.start_time`).
  (4) exception to "resource gains unlogged": a soak with a damage component
  (Sha rift 147198) IS measurable; only its Pride grant is proxied.

## [1.2.4] — 2026-06-18

Backport from a live officers-annex review (Nazgrim Defensive rage table +
kicks). Refines the "who fed rage" doctrine and fixes spell-name resolution.

- `references/interpretation-traps.md` rule 2 — **Nazgrim Defensive rage, v2
  refinements**: (d) **exempt the tank while holding Sundering Blow**
  (`Coup destructeur` 143494 — the ONLY tooltip-written exclusion; per-interval
  so tank swaps work; without it the table just ranks the tanks, one 58M→4M
  generating / 49.7M exempt); (e) **autonomous procs attributed to the player
  still don't feed rage** ("player *attacks*", not "anything player-sourced") —
  curated exclusion of gear/totem/passive procs (legendary cloaks, meta-gem
  Foudre, Stormlash, **mastery incl. Hand of Light 96172 = NOT a DoT, it's the
  Ret mastery proc** + Icicle, seal/poison/lightning-shield, Shadow apparitions)
  while KEEPING deliberate-cast consequences (Starfall, Killing Spree, Living
  Bomb…); (f) **gate cast-then-pecks** (A Murder of Crows dmg 131900 counts only
  if cast 131894 fell during the stance — DoT rule); (g) **don't classify
  proc-vs-deliberate by "has a cast event"** — damage `ability_id` ≠ cast
  `ability_id` (glyphs/variants/detonations/off-hand) wrongly drops Soul Reaper,
  Mind Flay, Halo, Chaos Bolt; use a curated id list. Caveat kept: the proc rule
  is an execution convention, NOT WCL-measurable.
- `references/zones/soo/traps.md` — Nazgrim row updated with the v2 doctrine.
- `scripts/localize.py` — **use the Wowhead mop-classic tooltip endpoint**
  (`/mop-classic/<lang>/tooltip/spell/<id>`); the retail endpoint returns EMPTY
  for old MoP base spell ids (78 Heroic Strike, 3044 Arcane Shot, 421 Chain
  Lightning…), which left "#xxxx" in report tables.

## [1.2.3] — 2026-06-17

Backport from a user review of the consolidated 3-night CR that caught five
real errors/omissions the verdict gate had passed — the lesson is that the
gate verifies CLAIMS, not COVERAGE.

- `references/methodology.md`: two new invariants. **14 — a "phase never
  reached/played" claim needs the phase timeline**: a kill in phase N proves
  N was played; report quantitatively ("P2 reached 5/16, killed in P2"), never
  "P2 never played / killed in P1" (a real published error — the kill was in
  P2 throughout). **15 — coverage is checked separately from claims; the
  verdict gate is blind to omissions**: explicit pre-delivery sweep (every
  night's trash analyzed, every boss/player verdict spans all its nights), and
  hand-written prose must be RE-EXTENDED on every `add-report` (data digests
  recompute across nights automatically, prose does not — it silently freezes
  on night 1).
- `references/wcl-api-gotchas.md`: **some "trash" fights are a boss's own
  later-phase content.** WCL only tags a fight with the boss encounterID once
  the boss frame engages; transition/realm wipes before that log as separate
  encounterID-less fights named after the phase add (Garrosh's Realm of
  Y'Shaarj → `Manifestation`/`Harbinger of Y'Shaarj`, killers Grasp/Reaping/
  Blood of Y'Shaarj). Counting them as trash inflates trash, hides a real boss
  wall (56 deaths), and undercounts attempts (≈7 real vs 4 official pulls).
- `references/redaction-guide.md`: rule 11 — **prose wording must match the
  mechanic's avoidability class** (a REDUCIBLE mechanic like Galakras Drakefire
  is "kill the source / raid CDs", never "dodge it / collective avoidance"
  which is the AVOIDABLE class and reads as individual blame); rule 12 —
  multi-night verdicts span every night.
- `references/zones/soo/traps.md`: Galakras row (Drakefire reducible-not-
  avoidable, 119 deaths = #1 killer but collective; P2 reached 5/16, kill in
  P2) + Garrosh Realm-as-trash and the Kor'kron approach gauntlet rows.
- `SKILL.md` stage 8: a **coverage sweep** added to the publish gate
  (per-night trash, multi-night verdict spans, phase-claim backing, trash-name
  cross-check).

## [1.2.2] — 2026-06-17

Backport from a consolidated multi-night CR (Équipage du Roux, one ID cleared
over 3 nights) with a deep nominative "qui fait quoi" layer.

- `references/interpretation-traps.md`: new section **Nominative-accountability
  traps** (5 rules from real false-blames caught in gate): absence-of-action ≠
  fault (verify an interruptible cast existed before blaming a missing kick —
  IJ/Dark Shaman have none); "active damage during a stop-DPS window" = direct
  single-target only (exclude DoT ticks `tick=1` and cleave that splashes off a
  priority add); "who didn't do X" must use the PRESENT roster of the encounter,
  not the night/week name pool (blamed absent players twice); sum base+empowered
  ids (142913+142928, 144989+145033); tanks-on-boss / melee-on-frontal are
  structural, not faults. Plus a self-audit heuristic: compare kill duration to
  the bracket MEDIAN of tops (n≈46), never the fastest parse (rank-1 = world
  record, exaggerates the gap).
- `themes/default.css`: removed the leading comment that embedded the tool name
  and "workdir" jargon into EVERY generated page's `<style>` — internal-vocab
  leak on published pages. Neutralized to a generic rebrand hint.
- Perimeter: the individual-blame nominative layer belongs in the officers
  annex only, never on guild-facing boss pages.

## [1.2.1] — 2026-06-12

The v1.2 layer now RENDERS: `scripts/pages_ext.py` plugged into pages.py
(5 hooks, everything degrades to nothing on pre-1.2 workdirs — verified:
a legacy workdir regenerates byte-structure-identical pages, 0 probe
errors).

### Added
- `scripts/pages_ext.py` (en/fr): per-pull dossier blocks under each pull
  chart (critical moments with CDs posted / available-NOT-posted / victims
  with a defensive in reserve, collapsible chronology, fixed-vs-repeated
  inter-pull delta); per-boss "who does what" section (kicks + casts
  through, add-switch tables ranged vs melee+tanks, kill add-windows,
  focus conformity, priority-add damage, friendly-NPC healing, trial
  entries, prisons time-to-free, defensives); hub rich pacing + night
  gantt; player-card execution panel; `pages/evolution/index.html`.
- `pages.py --only evolution`; evolution page auto-built when the digest
  exists.

### Changed
- `scripts/pacing.py` output renamed to `pacing_nights.json` (pacing.json
  belongs to analyze.py's legacy hub module; both coexist).

## [1.2.0] — 2026-06-12

The two "candidate" engines from 1.1.1 are now SHIPPED as scripts, plus the
nominative-execution layer — all field-validated on a real 25H progress
night (outputs cross-checked identical to the hand-driven originals).

### Added
- `scripts/dossiers.py` — per-pull dossiers: merged chronology (deaths,
  signature enemy casts, raid CDs, deduped battle-rezzes, lust), critical
  moments (first death + death clusters >=3/10 s), and for each: CDs posted,
  **CDs available-but-not-posted on the ABSOLUTE night timeline** (a CD
  burned late in pull N is still down at the repull), victims with a
  personal defensive in reserve; inter-pull fixed/repeated delta.
- `scripts/execution.py` — nominative execution per boss, driven by
  `references/zones/<zone>/execution.json`: who kicks (event-level) + casts
  that went through (begun/completed), add-switch latency per player
  (spawn-window segmentation; melee rendered separately), AoE-squat runs
  (>=3 consecutive ticks; tanks separately), council focus-conformity on
  the kill, friendly/priority-NPC participation (dps + heal), trial
  entries, prison time-to-free, personal-defensive counts.
- `scripts/pacing.py` — combat vs idle, repull discipline (median), longest
  gaps, full segment list for gantt rendering; `--compare` for prior weeks.
- `scripts/percentiles.py` — per-player WCL kill percentiles
  (report.rankings, dps+hps), THE cross-difficulty comparable.
- `scripts/evolution.py` — week-over-week dataset: median percentile
  trajectory, raid stats, roster moves, gear/ilvl diff via combatantinfo
  (localized item names, `nether.wowhead.com/mop-classic/<lang>/tooltip`).
- `references/zones/soo/execution.json` — SoO config: signature casts,
  adds, ground mechanics, interruptibles, council focus, NPC targets,
  trial/prison auras, and the `not_loggable` honesty list.
- SKILL.md stage 4 + methodology invariants 11-13: the nominative layer is
  part of the standard CR (aggregate-only delivery was rejected twice in
  field use); CD availability on the absolute timeline; name what the log
  cannot see.

## [1.1.1] — 2026-06-12

Lessons backported from a real 25-player Heroic PROGRESS night (3 first
kills + a wall, 23 wipes, 611 deaths) analyzed with the ingest scripts but a
hand-driven analysis layer (wipe forensics, learning curves, week-over-week
evolution).

### Added
- `references/zones/soo/traps.md`: 4 measured rows — Inferno Strike is
  SHARED damage (deaths = soak headcount, collective), Norushen berserk
  deaths = DPS-check verdict, Sha HM Banishment realm lethality (74% on a
  learning night) = execution-drill finding not a heal fault, Sha HM
  first-Swelling wipes = check raid-CD count before blaming heals.
- `references/zones/soo/mechanics_ref.json`: 14 measured HM mechanics added
  (FP Desperate-Measures kit, Norushen berserk/interrupts, Sha HM rifts /
  Ethereal Corruption / Bursting Pride, Immerseus pools) with two new
  avoidability classes: `soak` (shared damage — dying in an undersized soak
  is collective) and `execution` (mechanic drill, e.g. Banishment realm).
- `references/wcl-api-gotchas.md`: composition table contains top-parse
  combatants (always filter own reports), deep_graph pointStart is absolute
  report ms, Norushen-Test false-positive in the DamageTaken integrity
  check, working MoP-Classic wowhead tooltip endpoint (spells AND items).

### Candidate (not yet in scripts/)
- Report-rankings percentile fetch (per-player per-kill, comparable across
  difficulties) + week-over-week evolution page (ilvl/gear via combatantinfo
  diff, median percentile trajectory, roster moves) — proven on a real
  2-week case; to be ported into the public scripts on next iteration.
- Per-wipe dossier engine with "possible reactions at the timing": raid-CD
  and personal-defensive AVAILABILITY computed on the ABSOLUTE evening
  timeline (a CD burned late in pull N is still down at the next repull —
  repulls are ~2 min), critical moments = first death + death clusters
  (>=3 in 10 s), victims listed with the defensives they had in reserve.
  Proven killer finding on a learning night: a 12-death cluster at the
  first Sha HM Swelling with zero raid CD posted and 12 available.

## [1.1.0] — 2026-06-11

Multi-night raid IDs: one lockout cleared over several nights = ONE
consolidated debrief. Built and validated on a real 25-player Siege of
Orgrimmar ID split over two nights (two WCL reports).

### Added
- `ingest.py init` accepts several `--report` codes (repeatable or
  comma-separated): chronological order auto-detected, single-zone enforced.
- `ingest.py add-report`: complete an EXISTING workdir (and an already
  published debrief) when the lockout continues on a later night — only the
  new report costs API points (cache + done markers), then re-run
  `all`/`analyze`/`pages`/`probe` to regenerate.
- Global pull numbering per encounter, chronological ACROSS nights
  (`pulls_all`): pull #7 of a boss can be the kill on night 2. Appending a
  later report never renumbers earlier pulls, so published anchors and
  content fragments stay valid.
- Per-night pacing (`pacing.json` -> `{"nights": [...]}`), night badges on
  pull headers, multi-date hero/footer with one WCL link per night,
  per-player cards aggregated over the whole ID (qualified deaths and pull
  counts keyed by (report, fight) — actor ids are PER report, cross-night
  player identity is the NAME).
- `status` gate runs every per-report check per night.

### Hardened on the first real 25-player two-night run (534 top parses)
- `top-detail --shard k/m`: parallel workers on big rosters (idempotent via
  done markers); `progress` command (live %, machine-parseable, ETA-able).
- Commit discipline: commit after every upsert block BEFORE the next network
  fetch — an open implicit transaction held the WAL write lock through API
  latency and starved sibling workers (crashed twice despite busy_timeout
  30s then 120s). busy_timeout kept at 120s as belt-and-suspenders.
- WCL 504 + Cloudflare 52x now retried as transients (slices were lost
  loudly but un-retried); OAuth gets the same retry (workers died at boot
  during a real WCL outage — the token endpoint 504s too).
- Player cards no longer gated on spec-KPI coverage: every roster player
  renders (verdict, deaths, bench, avoidable); KPI tables simply absent for
  uncovered specs. 18/30 cards were silently dropped before this fix.
- SoO mechanics ref completed to 14/14 bosses (Blackfuse, Paragons, Garrosh
  — engraved 25N findings: MC friendly fire IS active in 25N, Magnetic
  Crush present in 25N, sliding sawblades during Crush windows).

### Compatibility
- Single-report workdirs unchanged: regression-checked vs v1.0.0 on a real
  night — 20/20 digests identical (modulo additive `report`/`night` keys),
  23/23 pages probe-clean.

## [1.0.0] — 2026-06-11

Initial public release. Extracted from a battle-tested private pipeline
(real 10-player Siege of Orgrimmar progression nights), then validated by
a full end-to-end replay (regression diff vs the proven pipeline: 21/21
identical) and two live Opus verdict-gate runs (3/3 correct verdicts each,
with and without the bundled zone traps).

### Added
- Seamless quota management in the WCL client: `rateLimitData` polled every
  ~150 live calls, auto-pause through the hourly reset above 85%
  (`WCL_QUOTA_SOFT_PCT` / `WCL_QUOTA_CHECK_EVERY` env overrides), 429 sleeps
  until `pointsResetIn` instead of giving up after ~15s of backoff (which
  yields silently-partial extractions); uncached failures print a loud
  `[wcl] WARNING` and are retried free on re-run.
- Stdlib-only python pipeline: `ingest.py` (cached WCL extraction: session
  aggregates, raw events per pull, trash, top1/top2 parse details),
  `analyze.py` (10 modules), `localize.py`, `pages.py` (static site, fr/en),
  `probe.py` (mechanical pre-publication gate).
- SKILL.md: 10-stage gated workflow with a mandatory per-verdict 5-point
  anti-false-blame checklist.
- References: methodology (10 engraved invariants), WCL API gotchas,
  interpretation traps (7 classes), redaction guide, zone bootstrap
  procedure.
- Bundled Siege of Orgrimmar refs: mechanics classification (11 bosses,
  source-cited), official French boss names, 11 trap validations measured
  on top parses, MoP spec KPIs (9 specs).

### Baked-in lessons (from the source pipeline's real corrections)
- `events(DamageTaken, targetID:X)` silent-zero on classic → full fetch +
  code-side filter, plus an expected-positive integrity gate in
  `ingest.py status`.
- Spec-per-pull joins (mid-night respecs), uptime ÷ pull duration,
  healer HoTs tracked by source with interval union, carryover-buff orphan
  removebuff handling (pre-pot undercount), pagination dedup.
- Three interpretation corrections engraved as checklist points: tank
  stop-attack windows, dispel-hold under windowed buffs, equal-conditions
  player comparisons.

## 2026-06-11 — backport (player-analysis session, rogue)

- `wcl-api-gotchas.md`: DamageTaken events player filter = `sourceID`
  (perspective entity) refines the silent-zero entry; `graph()` without
  explicit start/end spans the whole report despite `fightIDs`; abilities
  with no cast event (Envenom) counted via buff apply/refresh; transformed
  auto-attack ids (Shadow Blades 121473/121474) in melee-uptime math; new
  "Rankings & cohorts" section (`characterRankings.count` = page size, real
  pool via pagination, percentile-cohort selection, `encounterRankings` as
  full per-character kill inventory).
- `redaction-guide.md`: positives as numbers never adjectives (AI-slop
  flattery = report-rejection class, 2 live occurrences); pre-publish probe
  for internal-methodology vocabulary in rendered text.
- `interpretation-traps.md`: new trap class H — encounter-relative KPI
  thresholds (DoT uptime on swap bosses: top parses drop to 40-66%).
