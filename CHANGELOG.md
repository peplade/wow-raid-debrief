# Changelog

All notable changes to this skill. Format: [Keep a Changelog](https://keepachangelog.com),
newest first. Every lesson backported from real raid-night use gets an entry.

## [1.0.0] — 2026-06-12

Initial public release. Extracted from a battle-tested private pipeline
(real 10-player Siege of Orgrimmar progression nights).

### Added
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
