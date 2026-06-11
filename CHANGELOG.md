# Changelog

All notable changes to this skill. Format: [Keep a Changelog](https://keepachangelog.com),
newest first. Every lesson backported from real raid-night use gets an entry.

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
