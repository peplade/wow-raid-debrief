# Changelog

All notable changes to this skill. Format: [Keep a Changelog](https://keepachangelog.com),
newest first. Every lesson backported from real raid-night use gets an entry.

## [1.0.0] — 2026-06-12

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
