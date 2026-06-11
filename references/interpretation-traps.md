# Interpretation traps — the checklist that prevents false blame

## Why this file exists

Three published interpretation errors in one real report cycle, all the SAME
pattern: **the raw number was right, the interpretation ignored a mechanic
that REWARDED the observed behavior.**

1. "A tank cannot stop attacking; externals must absorb his stacks" —
   WRONG. The stacking DoT expired in ~6s; top tanks of the bracket take
   ~10x fewer ticks per second by stop-attacking in windows, with
   taunt-swaps; whole top raids show 30s windows at zero ticks.
2. "Dispels were too slow on this boss" — WRONG. Dispelling that debuff fed
   the boss's resource; the strategy is to HOLD dispels until a windowed
   buff makes them free. 13/18 of the "slow" dispels were placed exactly
   inside those windows: the delay WAS the strategy.
3. "Player A does -30/-60% vs player B, same spec" — MISLEADING. The boss
   evicts players into a solo phase (27s vs 44s), B carried 2x more annex
   target damage, and other fights were death-biased. At equal conditions
   the real gap was -4 to -19%.

A fourth from the previous cycle: "healers should pre-pot" — a generic
checklist item applied to a role/context where it is pointless.

## Trap classes

| Class | Name | Signature | Example |
|---|---|---|---|
| A | Hidden cost | acting is penalized by a resource/debuff | dispel feeds boss energy; hitting the boss applies a stacking self-DoT |
| B | Windowed free action | a periodic buff makes the action free; correct play = wait | hold dispels until the immunity-window buff |
| C | Strategic stop | stopping DPS/casts IS the play | stop-attack windows incl. tanks; boss-phase DPS collapse; cast batching under a recurring interrupt |
| D | Asymmetric assignment | numbers measure the task split, not the player | belt/side teams, eviction phases, volunteer roles (amber carrier), soak rosters |
| E | Voluntary damage | taking the hit is assigned | soaks; "tank" mechanics on a designated non-tank |
| F | Misleading IDs | cast id != debuff id; glyph variants; base+empowered pairs | a DoT logging under a different id than its cast; empowered spell pairs that must be summed |
| G | Absorb/HPS illusions | absorbs, smart heals and sniping distort HPS comparisons | discipline absorbs vs throughput healers; healer HPS vs tops is INDICATIVE only |

## THE checklist (mandatory before publishing any reproach)

1. **What mechanic REWARDS this behavior?** (hidden cost on the action —
   class A). Check the boss kit for resources fed / reactive debuffs.
2. **Does a windowed buff make the action free?** Correlate the action's
   timestamps with the buff's windows (class B). If ≥70% of "anomalous"
   actions fall inside the windows, it is the strategy.
3. **What does DBM/the zone ref encode?** Role-targeted warnings = strategy;
   an ABSENT dispel/interrupt warning on a dispellable-looking debuff is a
   clue something is conditional. Volunteer-role options too (class D).
4. **What do TOPS OF THE ROLE do at the same point?** Measured on a top
   parse — same extraction, same formula. Mandatory for any tank/healer
   gameplay claim. "Obvious" is not a measurement.
5. **Equal conditions if comparing players?** Both alive, same assignments,
   no eviction/asymmetric phase, kills only (classes D/E/G).

If every check passes -> publishable fault.
If any check fails -> the finding flips POSITIVE (they played it right) or
gets reframed.
If any check is UNCERTAIN -> publish as an open question to the raid lead
("was there a stack rule? a position call? an assignment?"), never a fault.

## Audit trail

Each finding gets a numbered entry in `<workdir>/verdicts.md` with the 5
answers written out (format in SKILL.md stage 6). The file is the audit
trail proving the gate ran; it stays in the workdir, unpublished.

## Measurement techniques for the checks (validated in live gate runs)

- **Reactive-mechanic proof (checks 1-2):** the debuff's `source_id` is
  usually the BOSS even when the application is triggered by the victim's
  own action — source attribution proves NOTHING about reactivity. The
  proof is timestamp correlation: count applications landing within ~300ms
  of the victim's own hit/cast on the boss (deep_aura applydebuff x
  deep_dmg_done/deep_cast of the same player). A live gate run concluded
  "boss-applied, therefore not reactive" from source_id alone — wrong
  mechanism, luckily right verdict. Do the correlation.
- **Check 4 when a top event type is missing:** top parses are extracted
  with casts, FULL damage-taken, buffs-on-self, enemy debuffs and healing
  totals — but NOT their dispels/friendly-debuff/heal events (API cost).
  For received-DoT behavior, the top's `deep_dmg_taken` ticks of that
  ability are always available and measure the same thing (ticks received
  ≈ uptime suffered). If genuinely unmeasurable, SAY SO in the check and
  weigh the verdict accordingly — never guess the top behavior.

## Self-audit heuristics (cheap pre-filters)

- A "worst offender" table where one player concentrates the metric:
  check aggregation FIRST (subset bias: computing shares on the filtered-
  anomalous subset instead of the full set produced a real published error).
- A metric at a suspiciously round 0% or 100%: suspect id mismatch (class F)
  or extraction silent-zero before suspecting the player.
- A behavior shared by EVERYONE in the raid (e.g. all melee stop at the same
  second): collective pattern = mechanic or call, not 10 individual fails.
- A "fail" by the most experienced player on a mechanic they know: raise the
  prior that it is a trap, double-check classes A-D.

## Zone-validated instances

Bundled zones ship a `references/zones/<zone>/traps.md` with MEASURED trap
validations (which boss, which mechanic, what the tops actually do, source
parse). Read it before stage 5 investigation; extend it (feedback loop)
whenever a new instance is validated or corrected.
