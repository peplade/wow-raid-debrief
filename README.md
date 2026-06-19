# wow-raid-debrief

A [Claude Code](https://claude.com/claude-code) skill that turns a
WarcraftLogs report code into a **deep, evidence-based raid night debrief**
for World of Warcraft — MoP Classic: per-boss pages with pull-by-pull
annotated timelines, per-player cards benchmarked against top parses, an
unlisted officers annex — as a fully static site you can host anywhere.

Built and battle-tested on a real 10-player Siege of Orgrimmar progression
roster; maintained through weekly real-world use.

## Philosophy: raw events, not aggregates

Most log summaries are aggregate trivia ("DPS was X, deaths were Y, use your
cooldowns"). This skill was born from such a report being thrown back with
*"this is AI slop"* — and rebuilt on one rule:

> **Nothing is written that cannot be sourced to a precise event
> (pull, timestamp).** Aggregates only rank where to dig.

What that buys you, concretely:

- *"First death 12.1s, frontal cone on a non-tank, repeated on pulls 2/7/9
  with no improvement → base-position problem, not individual dodging"*
  instead of *"too much avoidable damage"*.
- Mechanics **proven** by event correlation (e.g. *"71/73 debuff
  applications within 300 ms of the victim's own hit on the boss"* = it's a
  reactive on-hit mechanic — stop attacking).
- Player benchmarks vs **top1/top2 of the same spec, same boss, same raid
  size, computed with the *same formulas on the same raw events* on both
  sides** — not a percentile number nobody can act on.
- A built-in **anti-false-blame gate**: every reproach must survive a
  5-point checklist (hidden costs, windowed buffs, encoded strategy, what
  the tops actually do, equal conditions) before publication. Findings that
  fail the gate become *open questions to the raid lead* instead of wrong
  accusations. The checklist exists because each of its points corresponds
  to a real published mistake.

## What you get

- **Hub page** — boss table, night pacing bar (boss/trash/idle + longest
  gaps), roster links, night-level findings.
- **Per-boss pages** — pull-by-pull DTPS timelines annotated with phases,
  deaths and raid CDs (Chart.js), death tables with last-10-seconds
  breakdowns, avoidable-damage heatmap (player x mechanic), execution and
  healing tables, written synthesis and per-pull notes.
- **Per-player cards** — verdict, benchmark vs tops, qualified deaths
  (deaths on kills and wipe-triggers only — dying in the collective wipe is
  not a fail), avoidable intake.
- **Officers annex** — unlisted token URL, noindex, for the franker notes.
- Localized output (French and English UI bundled; spell names localized
  via official client strings for any supported language).

## Requirements

- [Claude Code](https://claude.com/claude-code) (the skill orchestrates;
  scripts also run standalone)
- Python 3.9+ (**stdlib only — zero pip installs**)
- A free WarcraftLogs API client (2 minutes, below)

## Getting WCL credentials

1. Log in at <https://www.warcraftlogs.com> and open
   <https://www.warcraftlogs.com/api/clients/>.
2. **Create Client** — name it anything, redirect URL
   `https://localhost`, leave "Public" unchecked.
3. Copy the **client ID** and **client secret**.
4. `cp .env.example .env` and fill both values. `.env` is gitignored —
   never commit it.

The skill uses the client-credentials flow: it reads public logs only, no
user authorization involved.

## Install

```bash
git clone https://github.com/peplade/wow-raid-debrief ~/.claude/skills/wow-raid-debrief
cp ~/.claude/skills/wow-raid-debrief/.env.example ~/.claude/skills/wow-raid-debrief/.env
# fill in .env, then in Claude Code:
#   /wow-raid-debrief  (or just ask: "fais le CR du raid d'hier, report ABC123")
```

Updating: `git -C ~/.claude/skills/wow-raid-debrief pull`.

## Usage

In Claude Code, give it a report code and answer the framing questions:

> Generate the raid debrief for report `AbCdEf123`, guild MyGuild, in French.

**Multi-night raid IDs** are first-class: pass several report codes (one
lockout cleared over 2+ nights) and you get ONE consolidated debrief — global
pull numbering per boss across nights, per-night pacing, per-player cards
aggregated over the whole ID. If the lockout continues after you already
produced the debrief, `ingest.py add-report` completes the existing workdir:
only the new report costs API points, earlier pull numbers never shift, and
the written content stays valid.

**Cross-lockout history** is kept in a separate durable store
`~/raids/_history/history.db` (one row per night's aggregates, stable
player identity by name). `scripts/history_sync.py <workdir>` rolls a night
into it (run at delivery; idempotent); `scripts/evolution.py` reads it to build
the week-over-week page. The per-night `<workdir>/raid.db` files stay the source
of raw detail and are re-extractible from the lzma cache, so the history store
is the asset worth backing up. Disk note: the `wcl_raw` API cache is
lzma-compressed; pre-2.0 workdirs reclaim ~60% via `scripts/migrate_lzma.py`.

The skill will extract (~5 min, quota-aware), analyze, investigate, write,
generate and probe the pages into a local workdir:

```
<workdir>/
  raid.json            night config
  raid.db              sqlite (cached API responses + extracted events)
  digests/analysis/    computed facts (JSON)
  verdicts.md          the audit trail of every finding vs the checklist
  content/             written fragments (HTML)
  pages/<label>/       THE DELIVERABLE — static site, host anywhere
  pages/officers-...   unlisted annex
```

Scripts are plain CLIs and work without Claude too — see headers of
`scripts/ingest.py`, `analyze.py`, `pages.py`, `probe.py`.

### API budget

A 10-player night (~20 boss pulls + trash + top-parse benchmarking) costs
**~1000–1500 points of the 3600/hour quota**. Quota is self-managed: the
client polls WCL's `rateLimitData` as it goes, auto-pauses through the
hourly reset when above 85%, and turns 429s into sleep-until-reset instead
of failures — start the extraction and walk away. Every response is cached
in sqlite: re-runs and resumes are free.

## Zone coverage

Bundled, validated on real top parses:

| Zone | Mechanics ref | Localized names | Validated traps |
|---|---|---|---|
| Siege of Orgrimmar | 14/14 bosses classified, sourced from DBM lua + sim cross-checks + real 10/25-player logs | fr | 11 measured trap validations |

Any other MoP Classic zone: the skill bootstraps the references itself
(documented, source-cited procedure: DBM warning-type classification + sim
cross-check + top-parse validation — `references/zone-bootstrap.md`), or
falls back to data-driven inference (tops take ~0 on ability X, you don't).
**PRs of bootstrapped zones are very welcome.**

## A living skill

This repository is maintained through weekly use on a real progression
roster. Every interpretation error caught in review, every API gotcha,
every newly-validated trap is backported here (see `CHANGELOG.md`):

| Discovery | Lands in |
|---|---|
| new interpretation-trap instance | `references/zones/<zone>/traps.md` |
| new trap class / checklist change | `references/interpretation-traps.md` |
| WCL API gotcha | `references/wcl-api-gotchas.md` |
| writing rule | `references/redaction-guide.md` |
| pipeline fix/feature | `scripts/` |

## FAQ

**Does it work for retail / other Classic eras?**
The API client targets `classic.warcraftlogs.com` and the bundled
refs/CD lists are MoP. The architecture is era-agnostic (endpoints and
spell tables are the only era-coupled parts) — port away.

**Why python stdlib only?**
So that "install" is literally `git clone` + credentials. No venv, no pip,
no node.

**Can I theme the pages?**
Drop a `theme.css` in the workdir (replaces `themes/default.css`), or edit
the `:root` variables. Guild name, language and labels come from
`raid.json`.

**Is my data sent anywhere?**
Only to the WarcraftLogs API (your own log). Pages are local files until
*you* host them.

## License

[MIT](LICENSE).
