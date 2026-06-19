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
| I | Unverified classification | a mechanic's avoidable/reducible/raid-wide/soak label OR its "how it works" is taken from the zone ref / memory WITHOUT cross-check — the ref `class` is a HYPOTHESIS, not ground truth | "avoidable" on a mechanic the log shows hitting ~all the raid each wave (= raid-wide, mitigate with CDs); évitable↔réductible swapped (Unstable Corruption = dodgeable bolts; Collapsing Rift = the cost of YOUR close, CD-only); "harmless periodic" on a player-driven cost. Real double-failure 2026-06: Sha rift pair + Bursting Pride mis-labeled in the ref, shipped, verifier missed it. Proof = per-wave distinct-target count in the log + Wowhead tooltip radius/target + an encounter guide |

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
6. **Is every displayed mechanic CLASSIFICATION and description cross-checked?**
   (class I) Before printing any "avoidable / reducible / raid-wide / soak"
   label or any "how it works" sentence: (a) count distinct targets per ~2 s
   wave in the log (`deep_dmg_taken` grouped by a time bucket) — ~all of the
   raid each wave = raid-wide (CDs, NOT avoidable); a handful = positional /
   avoidable; (b) confirm against an authoritative source (Wowhead MoP-Classic
   tooltip radius/target + an encounter guide, e.g. Icy Veins/Wowpedia). The
   zone-ref `class` is a starting hypothesis you VERIFY, never a citation.
   A label the log or source contradicts is corrected before publish — this is
   not optional, it is the rule that the rift/Bursting-Pride double-failure broke.

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
2. **"Active damage during a stop-DPS window" = DIRECT PLAYER-ATTRIBUTED
   SINGLE-TARGET only.** For "who kept feeding rage while he is in Defensive"
   (Nazgrim Defensive Stance, aura 143593, ~60s windows) exclude: (a) DoT ticks
   (`tick=1` — a DoT applied BEFORE the window keeps ticking; the player is
   not actively hitting); (b) cleave/AoE (same `source_id+ability_id+ts_rel`
   also hits another add → incidental splash while correctly on a priority
   target like the banner); (c) **autonomous pet/guardian damage** (pet
   auto-attack, Water Elemental Waterbolt, Dire Beast/Stampede, ghoul,
   Gargoyle, Guardian of Ancient Kings, Dancing Rune Weapon, etc.). Keep only
   hits whose group targets ⊆ {boss} AND are player-attributed.
   **Why (mechanism, player-tested — NOT the tooltip; WCL cannot measure it,
   the rage energize logs source=boss):** Defensive rage is fed ONLY by
   PLAYER-attributed damage. Pet/guardian autonomous damage and DoT ticks do
   NOT feed rage → counting them falsely blames hunters/warlocks/DKs for their
   pet swinging. The exceptions that DO feed rage and must be RE-INCLUDED
   (attributed to the owner): **Kill Command** (pet-sourced ability 83381, but
   it is a player-activated ability) and **DoT applications/reapplications**
   (the cast / `tick=0` initial hit, not the ticks). In SQL terms: drop
   `source_id NOT IN composition` EXCEPT `ability_id IN (Kill Command ids)`
   mapped to `petOwner`.
   Without this, DoT classes (Demonology) and cleavers are falsely top-ranked
   (Thoth 29M → 1.7M once DoT+cleave removed); pet classes would be too
   (a single Defensive set had ~50M of autonomous pet/guardian damage on the
   boss — all rage-irrelevant). The tooltip "+rage when struck" reads as if any
   strike counts — it does not; verify mechanic claims against player-tested
   reports, never tooltip wording.
   **v2 refinements (validated vs icy-veins/mythictrap + the written tooltip):**
   - (d) **Sundering Blow tank exemption — the ONE exclusion written in the
     tooltip** ("tanks with the Sundering Blow debuff are exempt"). Sundering
     Blow = `Coup destructeur` 143494, a stacking debuff on the active tank.
     Exclude a player's boss damage *while they hold it* (build apply→remove
     intervals, merge refresh gaps, ~1.5s pad → handles tank swaps). Without it
     the table just ranks the tanks (one tank 58M→4M generating, 49.7M exempt).
     Show the split (generating vs exempt) per tank.
   - (e) **Autonomous PROCS attributed to the player still don't feed rage** —
     the rule is "player *attacks*", not "anything sourced from the player".
     Exclude a CURATED, audited list of gear/totem/passive procs that fire with
     no deliberate offensive GCD: legendary cloaks (Essence of Yu'lon 148008,
     Flurry of Xuen 147891/149276), Capacitive meta-gem Lightning Strike/Foudre
     (137597/141004/138146), Stormlash 120687, **mastery procs incl. Hand of
     Light 96172 — NOT a DoT, it's the Ret mastery proc (instant, tick=0) — and
     Icicle 148022**, Seal of Truth 42463, Deadly Poison 113780, Lightning
     Shield 26364, and Shadow auto-apparitions 148859 + 73510. KEEP deliberate-
     cast consequences (Starfall stars, Killing Spree, Glaive Toss, Living Bomb,
     Death & Decay, DoT direct hits). Caveat: the proc rule is NOT log-verifiable
     (rage isn't logged per source) → apply it as an execution convention ("did
     you ease off"); keep the exclusion set a NAMED CONSTANT and audit per fight.
   - (f) **Cast-then-autonomous-pecks (A Murder of Crows / Corbeaux dmg 131900,
     logged `tick=0`)** obey the DoT rule: count ONLY if the trigger cast
     (131894) landed DURING the stance window; pecks from a pre-stance cast =
     pre-applied = no rage. Generalize as `{damage_id: cast_id}` gating.
   - (g) **Do NOT classify proc-vs-deliberate by "has a cast event"** — the
     DAMAGE `ability_id` ≠ the CAST `ability_id` (glyphs, spec variants, DoT
     detonations, off-hand), so that heuristic wrongly drops Soul Reaper, Mind
     Flay, Halo, Chaos Bolt. Use the curated id list, not a blanket rule.
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
6. **Prepull / premature-engagement attribution — ABANDONED, DO NOT BUILD.**
   Tempting (who pulled before the raid was ready and wiped it), but WCL offers
   only two signatures — a boss-named TAP segment, and an early `Melee` death
   (`ability_name='Melee'`, `death_time < ~5 s`) — and **both are BLIND to
   PROXIMITY pulls** (zone aggro: the raid walks into the boss's radius without
   tapping or dying, so neither signature fires). The tally is therefore a
   NON-EXHAUSTIVE sample that reads as a firm count = **false numbers**, and
   proximity cannot be recovered from WCL (no player coordinates). **Do not build
   or publish a prepull detector** — removed from the EdR CRs (consolidated 06-11
   + soir-1 06-18) on user decision 2026-06-19. If officers ask "who pulled
   early", say it is NOT log-reconstructible and, at most, hand them the raw
   early-`Melee`-death + boss-tap LEDGER as context — never a ranked recidivist
   blame list, never a count presented as complete. (Even then, the old guards
   apply: a TANK dying early to Melee is AMBIGUOUS; a tap precedes a wipe by
   CORRELATION, not proven cause.)

## Zone-validated instances

Bundled zones ship a `references/zones/<zone>/traps.md` with MEASURED trap
validations (which boss, which mechanic, what the tops actually do, source
parse). Read it before stage 5 investigation; extend it (feedback loop)
whenever a new instance is validated or corrected.
