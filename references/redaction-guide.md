# Redaction guide — writing the report

The difference between "AI slop" and a report a raid lead acts on is not the
data — it is what you choose to say and how every sentence is anchored.

## The verdict structure (every finding follows it)

```
[MEASURED FACT with anchor] -> [VERIFIED MECHANISM] -> [ACTIONABLE AXIS]
```

Example (good):
> Seven deaths at 12.1s on pull #2 to a frontal cone (first: the shadow
> priest), again 6 on #7 and 7 on #9 — the pattern does not improve across
> the night, so it is a BASE POSITION problem (raid inside the frontal arc),
> not individual dodging. Axis: reference spot outside the arc + tank
> orientation call. Expected gain: ~2 of the 10 pulls were lost to this
> alone. [To confirm: was a position pack defined?]

Counter-example (slop — never write this):
> The raid should work on positioning and awareness to avoid unnecessary
> damage. Remember to use defensive cooldowns!

## Hard rules

1. **Anchor every claim**: (pull #N, m:ss), (xN across the night), or (vs
   top1: X). A sentence with a judgment and no anchor gets deleted.
2. **No generic advice.** Every recommendation passes: "does it make sense
   for THIS role/spec in THIS context?" (the "healers should pre-pot" class
   of error). If it could be pasted into any report, delete it.
3. **Reproaches only from gated verdicts** (verdicts.md, PUBLISH AS FAULT).
   Everything else is a fact, a positive, or an open question.
4. **Open questions are first-class content.** "Was there a stack rule?"
   placed in a visible "to confirm" block beats a wrong affirmation. Mark
   every deviation from ASSUMED strategy with "to confirm" — you compared to
   a standard guide, not to their actual plan.
5. **Positives are findings — stated as numbers, never as adjectives.**
   A measured correct behavior (dispel-hold pattern under the right windows,
   a clean kill streak, a top-matching uptime) gets reported with the same
   rigor. But "flawless prep", "excellent reflexes", "he clearly knows his
   spec" = AI-slop flattery and gets the whole report rejected (two live
   rejections of this exact class). Write "flask+food on 210/212 kills",
   let the reader conclude. Never compliment before criticizing as a
   rhetorical device. A report that only blames
   reads as hostile and gets rejected; a report that praises without
   measurement reads as slop.
6. **Collective vs individual, explicitly.** Name the player when the
   evidence is individual (qualified deaths, repeated identical fail);
   name the raid/call when the pattern is collective (same second, same
   behavior, everyone). Misattributing a call problem to a player is the
   fastest way to lose the room.
7. **Tanks/healers get role-aware framing**: part of tank intake is
   structural (post obligation); healer HPS vs tops is indicative only
   (2-3 heal context, absorbs, sniping) — label it, never reproach on it.
8. **Numbers formatted for humans**: 1.3 M not 1,302,847; m:ss for times;
   percentages with at most 1 decimal. Keep raw precision in digests, not
   in prose.
9. **Trivia is allowed ONLY as clearly-labeled fun** (a "records" corner:
   biggest overkill, first death of the night) — never dressed as analysis.
10. **The officers annex carries the blunt content** (rankings, individual
    discipline items, strategy questions). Public pages stay factual and
    constructive — they are readable by the players named on them.

### Pre-publish probe additions

- Scan the RENDERED text (body innerText, not the source) for
  internal-methodology vocabulary leaking into player-facing content:
  "our analyses", "validated across our reports", "gotcha", "pipeline",
  "we publish". Internal rules justify the content; they never appear in it.

## Per-page content expectations

**Boss page**: synthesis (what blocked/won, in 5-8 lines, anchored) ->
pull-by-pull (chart + 1-3 line note per significant pull: what changed,
what triggered the wipe) -> mechanics sections (one per killer mechanic:
proof, who, axis) -> auto sections (heatmap/execution/heals) need no prose
unless an anomaly survived the gate.

**Player card**: verdict (3-6 lines: role played, what the night proves,
one axis) -> benchmark table (already explained by the generator legend) ->
qualified deaths -> avoidable intake. The verdict NEVER repeats the tables;
it interprets them.

**Hub**: the night in one glance — boss table, pacing bar, roster links,
3-5 night-level findings, the "to confirm" block for the raid lead.

**Officers annex**: per-player frank notes (one block each), strategy
questions grouped by boss, re-pull discipline numbers if relevant.

## Language

Write the report in the configured language (raid.json `lang`), uniformly:
spell names via the localization cache (never hand-translated), boss names
via zone.json, UI strings come from the generator. A FR report with EN
leakage fails the probe stage review.

## Length discipline

A boss page: synthesis ≤ 8 lines; pull note ≤ 3 lines; mechanic section
≤ 12 lines. A player verdict: ≤ 6 lines. If you need more, you are
narrating data the tables already show — cut. Density of ANCHORED facts is
the quality metric, not word count.
