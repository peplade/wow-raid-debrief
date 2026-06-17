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
| H | Encounter-relative thresholds | a KPI's "good" value depends on the fight, not the spec | DoT uptime on target-swap bosses: top-1 parses themselves drop to 40-66% (vs 90%+ on stationary bosses) — never grade a maintenance KPI red without a same-encounter top reference next to it |

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
- A total that grows with time alive (avoidable hits, debuff ticks summed
  over wipes): SURVIVOR BIAS — the players who live longest eat the most.
  Normalize per kill-pull or per minute alive before ranking; a live run's
  "worst Desecrated offenders" (69 and 54 hits) were BELOW the top-parse
  average once restricted to the kill (11 and 10 vs tops' 12.98).
- A kill-duration "vs tops" gap: compare to the bracket MEDIAN (n≈46), never
  the fastest parse — rank-1 is the world record and exaggerates the gap
  (Galakras kill 615s ≈ median 575s, but "vs 422s" reads as a chasm).

## Nominative-accountability traps (who-did-what / who-failed tables)

The deep "qui fait quoi" layer assigns individual blame — the highest
false-blame risk. Five rules, each from a real error caught in gate. (This
content is officers-only by perimeter — see redaction-guide — but the
measurement rules apply wherever it is rendered.)

1. **Absence of an action ≠ fault.** "0 kicks", "0 switches", "didn't soak"
   is a reproach ONLY if the opportunity existed. Before blaming a missing
   interrupt, confirm an interruptible cast was present (`raid_event
   kind='interrupt_ability'` begun>0, or the spell appears interrupted
   elsewhere in the log). Iron Juggernaut / Kor'kron Dark Shaman have ZERO
   interruptible casts → "0 kicks" is correct, not a fail. Froststorm Bolt:
   197 casts, never interrupted anywhere = not interruptible.
2. **"Active damage during a stop-DPS window" = DIRECT SINGLE-TARGET only.**
   For "who kept hitting the boss while he reflects/DRs/rages" (Nazgrim
   Defensive Stance, aura 143593, ~60s windows) exclude: (a) DoT ticks
   (`tick=1` — a DoT applied BEFORE the window keeps ticking; the player is
   not actively hitting); (b) cleave/AoE (same `source_id+ability_id+ts_rel`
   also hits another add → incidental splash while correctly on a priority
   target like the banner). Keep only hits whose group targets ⊆ {boss}.
   Without this, DoT classes (Demonology) and cleavers are falsely top-ranked
   (Thoth 29M → 1.7M once DoT+cleave removed).
3. **"Who didn't do X" must use the PRESENT roster** of that encounter
   (`composition WHERE report,fight_id of the boss`), never the night/week
   name pool — else players who were on OTHER nights surface as "didn't touch
   the priority add" (a fully-absent false list, caught in gate twice).
4. **Sum base+empowered ids for intake** (class F): Énergie déplacée
   142913+142928, Whirling Corruption 144989+145033 — one id alone flips the
   top-3 offenders.
5. **Structural exposure is not a fault:** tanks MUST hit the boss (threat);
   melee MUST stand in front on run-in bosses. Tag them and keep them out of
   the DPS-accountability signal — never red a tank for boss damage or a
   melee for eating a frontal.

## Zone-validated instances

Bundled zones ship a `references/zones/<zone>/traps.md` with MEASURED trap
validations (which boss, which mechanic, what the tops actually do, source
parse). Read it before stage 5 investigation; extend it (feedback loop)
whenever a new instance is validated or corrected.
