---
name: wow-raid-debrief
description: Generate a deep, evidence-based raid night report (per-boss pages + per-player cards + officers annex) from a WarcraftLogs report code, for WoW MoP Classic. Use when the user asks for a raid debrief/report/CR/analysis of a raid night log. Raw-events analysis, charts, top-parse benchmarks, written verdicts. Requires WCL API credentials and python3.
---

# wow-raid-debrief

You produce a raid-night debrief that a raid lead can act on: per-boss pages
(pull-by-pull timelines, deaths, mechanics), per-player cards (benchmarks vs
top parses, qualified deaths, avoidable damage), an officers annex, all
static HTML. Scripts do ALL extraction and computation; your job is framing,
prioritization, **verified interpretation**, and writing.

## The two iron rules

1. **Nothing is written that cannot be sourced to a precise event** (pull,
   timestamp) or an aggregate computed by the scripts. Aggregates only tell
   you WHERE to dig; claims come from raw events.
2. **No blame before the traps checklist.** A behavior that looks wrong
   (delay, inaction, refusal to act, low number) must survive the 5-point
   checklist in `references/interpretation-traps.md` BEFORE being published
   as a fault. If any check fails or is uncertain, the finding is published
   as an **open question to the raid lead**, never as a fault. This rule
   exists because three real-world corrections followed the exact same
   pattern: the number was right, the interpretation ignored a mechanic that
   rewarded the behavior.

## Pipeline overview

Every stage ends with a **binary gate** (a command whose output you check).
Do not proceed past a failed gate; fix or escalate.

```
0 setup -> 1 framing -> 2 extraction -> 3 zone refs -> 4 analysis
-> 5 investigation -> 6 verdicts gate -> 7 writing -> 8 pages+probe gate
-> 9 delivery -> 10 feedback loop
```

Reference docs (read when the stage starts, not before):
- `references/methodology.md` — the method, invariants, formulas
- `references/wcl-api-gotchas.md` — API failure modes (read at stage 2)
- `references/interpretation-traps.md` — trap classes + THE checklist (stage 5-6)
- `references/redaction-guide.md` — writing rules, formats, anti-patterns (stage 7)
- `references/zone-bootstrap.md` — building refs for a new zone (stage 3)
- `references/zones/<zone>/traps.md` — zone-validated traps if bundled (stage 5-6)

## Stage 0 — setup check

```bash
SKILL=~/.claude/skills/wow-raid-debrief        # adjust if cloned elsewhere
python3 --version                              # need 3.9+
ls "$SKILL/.env" || echo "MISSING .env"
```
GATE: `.env` exists with WCL_CLIENT_ID/WCL_CLIENT_SECRET (or env vars set).
If missing: stop and ask the user to follow README "Getting WCL credentials".

## Stage 1 — framing (ask, then respect the answers)

If the user has not already specified them, ask (multiple-choice, with
defaults marked) — group in ONE message:

1. **Deliverable axes**: boss pages + player cards cross-linked (default) /
   boss only / players only.
2. **Depth**: max grain everywhere incl. one-shot kills (default) / focus
   progression bosses only.
3. **Trash**: deaths + dangers + night pacing (default) / skip.
4. **Strategy reference** for progression bosses: standard guide strat with
   deviations marked "to confirm with raid lead" (default) / user provides
   their actual assignments now.
5. **Healer analysis**: all four axes — CDs vs real damage windows, dispel
   reactivity+misses, efficiency (overheal/GCD/mana), target split (default).
6. **Benchmark**: each player vs top1/top2 same spec, same size, SAME
   formulas (default) / none.
7. **Language** of the report if not in config (default: user's language).
8. **Deadline / publication target** (directory to host, or just local).

Running fully autonomously (user said "go, deliver"): take every default,
write the choices into the final summary.

## Stage 2 — extraction

Read `references/wcl-api-gotchas.md` NOW (failure modes are silent).

```bash
cd <workdir-parent> && mkdir -p <label> && cd <label>
python3 "$SKILL/scripts/ingest.py" init --report <CODE> [--report <CODE2>] \
        --guild <NAME> --lang <fr|en|...> --size <10|25> [--label id-YYYY-MM-DD]
python3 "$SKILL/scripts/ingest.py" all          # quota-guarded, resumable
```

Multi-night raid ID (one lockout over several nights): pass every report code
to `init` (repeatable `--report`, chronological order is detected, same zone
enforced) — ONE consolidated debrief. If the lockout CONTINUES after a
debrief was produced, do NOT re-init: `ingest.py add-report --report <CODE2>`
in the existing workdir, then re-run `all` (only the new report costs
points), `analyze.py all`, pages and probe. Global pull numbers are
chronological per encounter, so earlier nights keep their numbers — existing
verdicts.md anchors and content fragments stay valid; only ADD content for
the new pulls and update syntheses that the new night changes.

GATE: `python3 "$SKILL/scripts/ingest.py" status` prints `STATUS: OK`
(exit 0). On FAIL lines: re-run the failed stage (free thanks to the cache),
or investigate with the gotchas doc. The integrity check "every player has
DamageTaken events" failing = extraction bug, NEVER "they took no damage".

Quota is SELF-MANAGED at the client level (WCL `rateLimitData`): polled every
~150 live calls, auto-pause through the hourly reset above 85%, and a 429
sleeps until reset instead of failing. A full night costs ~1000-1500 of the
3600 points/hour, so big extractions can legitimately pause up to ~1h —
run `ingest.py all` in the background and watch for `[quota]` lines; never
kill a paused run (it resumes alone, and re-runs are cache-free anyway).
Any `[wcl] WARNING: request failed` line = that slice is partial: re-run the
same command after the run ends (free) until the warning disappears.
`ingest.py quota` shows the meter anytime.

## Stage 3 — zone refs

```bash
ls "$SKILL/references/zones/"        # is the zone bundled? (zone_id in raid.json)
```
- Bundled (e.g. `soo/`): copy as shown in `references/zone-bootstrap.md`
  ("COPY and go"), then do its steps 4-5 only (id reconciliation vs YOUR log,
  spec gaps for YOUR roster).
- Not bundled: follow `references/zone-bootstrap.md` fully. Use subagents for
  the DBM-lua inventory and the spec-KPI compilation; YOU validate ambiguous
  classifications on top parses before accepting them.

```bash
python3 "$SKILL/scripts/localize.py" spells     # localized names cache
```

GATE: `ingest.py status` now also shows refs PASS; and for each roster spec,
`spec_kpis.json` has an entry whose dot/buff ids appear in `deep_aura`
(reconciliation done — unmatched ids WILL silently produce 0% uptimes).

## Stage 4 — analysis

```bash
python3 "$SKILL/scripts/analyze.py" all         # writes digests/analysis/*.json
python3 "$SKILL/scripts/dossiers.py"            # per-pull dossiers + CD availability
python3 "$SKILL/scripts/execution.py"           # WHO kicks/switches/camps (zone config)
python3 "$SKILL/scripts/pacing.py"              # combat vs idle, repull discipline
python3 "$SKILL/scripts/percentiles.py"         # per-player kill percentiles (~2 calls/report)
# week-over-week (only when earlier debrief workdirs exist for this guild):
python3 "$SKILL/scripts/evolution.py" <wd_week1> ... <this_workdir>
```

GATE: every module printed output and `digests/analysis/` contains pacing,
deaths, cdmap, heals, dispels, avoidable, execution, bench, boss_*.json,
PLUS dossiers.json, execution_nominative.json, pacing.json (and
evolution.json + gear_evolution.json on multi-week runs).
A SKIPPED module = its precondition failed; go back, do not shrug it off.

The nominative layer is NOT optional. A debrief that stops at aggregates
(deaths per mechanic, raid-wide CD counts) without the per-pull dossiers
(chronology + which CDs were AVAILABLE and unused at each critical moment)
and the per-player execution tables (who kicks, who switches on adds and how
fast, who camps ground AoE, who carries soaks) will be rejected by any raid
lead who knows the fights — field-tested twice on the same delivery.
`execution.py` needs `references/zones/<zone>/execution.json` (bundled for
SoO; for a new zone, build it during stage 3 — see zone-bootstrap.md).
Honesty rule: some things are NOT in the combat log (resource-bar gains such
as orb soaks, who clicks a pressure plate). Say "not measurable from logs"
instead of proxying — the zone execution.json lists known cases under
`not_loggable`. Rendering fairness: melee switch latency includes travel
time (separate melee from ranged); tank ground-AoE ticks read as boss
placement, not personal fails; soak-duty deaths are collective.

Sanity pass (5 min, catches extraction/ref bugs before they poison verdicts):
- uptimes ≤ 100 and not absurdly low for the spec's signature DoT,
- healer HoT uptimes nonzero,
- bench rows have tops (cpm>0) for most specs,
- death counts match WCL's own death counter for 2 spot-checked pulls.

## Stage 5 — investigation (your real work)

Work boss by boss, then player by player. For each, READ the digests and the
db (sqlite queries on deep_* tables) and build a list of CANDIDATE findings:
wipe causes, recurring death patterns, naked damage peaks, execution
outliers, dispel anomalies, pacing losses.

For each candidate that matters, dig to EVENT level and try to prove the
mechanism, not just the correlation:
- **timestamp correlation is your strongest proof** (e.g. "71/73 debuff
  applications within 300ms of the player's own hit on the boss" proves a
  reactive mechanic);
- compare the failed pull to the successful pull (identical totals with a
  different first-death tells you the cause is the trigger, not throughput);
- qualified deaths only: deaths on kills, and first 1-2 deaths of wipes.
  Dying in the collective wipe is not an individual fail.

Write every candidate into `<workdir>/verdicts.md` as it emerges (next stage
formalizes them). Use the zone traps doc as a pre-filter: if a candidate
matches a known trap, kill or reframe it immediately.

## Stage 6 — verdicts gate (MANDATORY, one entry per finding)

For EVERY finding that blames or grades a player/role/strategy, append to
`<workdir>/verdicts.md`:

```markdown
## V<n>: <one-line finding>
- Evidence: <pull, timestamps, numbers, query/digest used>
- Check 1 (hidden cost — what mechanic REWARDS this behavior?): <answer>
- Check 2 (windowed buff making it free? timestamps correlated?): <answer>
- Check 3 (DBM/zone-ref: role-targeted warning? absent warning?): <answer>
- Check 4 (what do TOPS OF THE ROLE do here? measured, not guessed): <answer>
- Check 5 (equal conditions if comparing players? alive, same assignment,
  no eviction): <answer>
- VERDICT: PUBLISH AS FAULT | PUBLISH AS POSITIVE | OPEN QUESTION | DROP
```

The last line of each entry MUST be literally `- VERDICT: <one of the four>`
(machine-greppable; bold prose variants break the stage-7 cross-check).

Rules:
- Check 4 requires a MEASUREMENT on a top parse when the finding targets
  role gameplay (tank/healer behavior especially). "Obvious" is not a measure.
- Any check unanswered or uncertain -> OPEN QUESTION (phrased to the raid
  lead, e.g. "was there a stack rule? position call?"), never a fault.
- This file ships in the workdir (not published) — it is your audit trail.

GATE: every player-facing reproach in stage 7 maps to a V<n> with verdict
PUBLISH AS FAULT. No orphan reproaches.

## Stage 7 — writing

Read `references/redaction-guide.md` NOW. Write the content fragments into
`<workdir>/content/` (layout documented at the top of `scripts/pages.py`):
hub hero/body, per-boss synthesis/intro/pull notes/sections, per-player
verdicts, officers hero/body.

Non-negotiables (full list in the guide):
- every claim carries its anchor: (pull #N, m:ss) or (xN across the night);
- verdict structure: measured fact -> verified mechanism -> actionable axis;
- no generic advice that ignores role/spec/context; no padding, no trivia
  dressed as insight; deviations from assumed strat marked "to confirm";
- positives that the data proves (e.g. a correct dispel-hold pattern) are
  findings too — report them.

## Stage 8 — pages + probe gate

```bash
python3 "$SKILL/scripts/pages.py"
python3 "$SKILL/scripts/probe.py" [--forbid <internal-codenames,...>]
```

`pages.py` auto-renders the v1.2 layer when its digests exist (everything
degrades silently when absent): per-pull dossier blocks (critical moments,
CDs available-not-posted, defensives in reserve, chronology), the
"who does what" nominative section per boss, the rich pacing block + night
gantt on the hub (needs pacing_nights.json), execution lines on player
cards, and `pages/evolution/index.html` when evolution.json exists
(also forced via `--only evolution`).

GATE: probe exits 0 (no forbidden tokens, no undrawn canvas, no dead links,
no empty main). Fix and re-run until clean. Then open 2-3 pages yourself
(read the HTML) and check: titles coherent, numbers formatted, language
uniform (no EN leakage in a FR report).

## Stage 9 — delivery

Deliver to the user:
1. where the pages are (path/URL) and what was generated (counts);
2. the 3-5 most actionable findings (one line each, with their anchor);
3. ALL open questions from verdicts.md, grouped, phrased for the raid lead;
4. the framing choices that were defaulted (if autonomous).

The pages directory is fully static: any web server or file host works.

## Stage 10 — feedback loop (after user review)

When the user corrects an interpretation or a new gotcha/trap is discovered:
1. fix the affected pages (content fragment + regenerate + probe);
2. append the lesson to the right file — new trap instance:
   `references/zones/<zone>/traps.md`; new trap CLASS or checklist change:
   `references/interpretation-traps.md`; API gotcha:
   `references/wcl-api-gotchas.md`; writing rule:
   `references/redaction-guide.md`; pipeline bug: fix `scripts/`;
3. one dated CHANGELOG.md entry + one commit per lesson, push.
A correction that is not backported will be repeated.
