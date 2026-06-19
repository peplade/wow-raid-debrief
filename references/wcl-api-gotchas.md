# WCL API v2 (classic) — gotchas

Every entry below cost real debugging time or produced silently-wrong data.
Read before extraction; re-read when a number looks weird.

## Silent failures (the dangerous ones)

- **`events(dataType:DamageTaken, targetID:X)` returns 0 events, no error**
  (classic endpoint). Fetch FULL DamageTaken and filter code-side — or use
  `sourceID:X`: for DamageTaken the "perspective entity" filter is the one
  TAKING the damage (verified: `sourceID:<player>` returns events whose
  `targetID` is that player). Same family: some sourceID+hostilityType
  combinations. Verified working:
  `Casts` + `sourceID`, `Buffs` + `targetID`, `Debuffs` + `hostilityType`.
  **Validate every extraction by an EXPECTED POSITIVE** (a tank with zero
  damage taken = alarm; a healer with zero casts = alarm), never by the
  absence of an error.
- **Enemy-cast COMPLETIONS are under-logged when several adds cast the same
  spell in unison.** WCL logs every `begincast` but drops completions
  (`type='cast'`): measured on Sha de l'Orgueil, **273 begincast → 89
  completions** on Mocking Blast. NEVER infer "the cast landed / passed" from
  the completion count. Landing truth = the spell's DAMAGE events
  (`deep_dmg_taken ability_id`), taken in UNION with completions. A begincast
  with NO damage and NO interrupt = hit nobody (add killed/CC'd), not an
  ambiguous "cancelled cast". **Limit:** an enemy HEAL (e.g. Galakras Chain Heal
  146757) has no player-damage signal → its unlogged completions stay invisible
  (say so, don't invent). See `kicks.md` (damage-as-landing).
- **`graph()` without explicit `startTime`/`endTime` spans the WHOLE REPORT**
  even with `fightIDs` set (same family: windowless `events()` on some
  dataTypes truncates or widens silently). Symptom: cumulative
  reconstructions (boss HP %, execute windows) land OUTSIDE the pull
  duration. Always pass the fight bounds to `graph()` and `events()`.
- **Some abilities emit NO cast event** (MoP Classic: Envenom). Count them
  via buff `applybuff`+`refreshbuff` or their damage events instead of
  `Casts`. Related: auto-attacks replaced by transformation buffs get their
  own ability ids (e.g. Shadow Blades melee 121473/121474) — include them in
  melee-uptime / swing-gap math or movement gets overestimated.
- **Pagination duplicates events at page boundaries** (`nextPageTimestamp`
  overlaps; x1.1-2 volume). Dedup on a wide key (timestamp, type, sourceID,
  targetID, abilityGameID, amount, stack, targetInstance).
- **Carryover buffs emit NO applybuff** inside the fight. A potion drunk
  during the countdown shows ONLY as an orphan `removebuff` ~20-26s in.
  Counting applybuff undercounts pre-pots massively. Rule: pre-pot =
  applybuff <10s OR orphan removebuff; in-combat pot = applybuff ≥10s.
  Generalizes to ALL aura windows: orphan remove = active since t=0; apply
  without remove = active until fight end.
- **`Deaths` table timestamps are sometimes report-absolute, sometimes
  pull-relative.** If ts > pull duration, subtract fight start.
- **Character-actor entries can leak into ability tables.** In
  `viewBy:Ability` results, guard `guid > 10_000_000 or type is a string`
  -> skip (those are actors, not spells).

## Schema surprises

- `phases { ... }` on classic has NO `separatesWipes` field (schema differs
  from retail docs).
- `characterRankings` / `playerDetails` / `table` / `graph` return JSON
  **strings** in some fields — always `json.loads` when the value is a str.
- `playerDetails` nests as `{data: {playerDetails: {...}}}` or directly —
  unwrap both.
- `table(dataType:DamageTaken)` default view is BY ACTOR; `viewBy:Ability`
  is required for the by-spell breakdown.
- Interrupt/Dispel tables nest entries recursively (`entries` inside
  entries); flatten before reading `details`.
- `phaseTransitions` exists on ~75-80% of boss pulls only (encounter-
  dependent); never assume presence.

## Rankings & cohorts

- **`characterRankings.count` is the PAGE size (always ≤100), not the pool
  total** — there is no total field. Real pool size = paginate until
  `hasMorePages` is false. Percentile cohorts (p95/p75/p50 reference
  parses) = index into the flattened, paginated list:
  `pos = ceil(total * (100-p) / 100)`. Cross-check: the character's
  `zoneRankings ... allStars.total` ≈ same pool.
- **`character.encounterRankings(encounterID, difficulty, size)`** returns
  the FULL kill inventory of one character for that boss (percentile,
  report code, fightID, duration, spec per kill) — shortest path to "every
  kill of player X", no report scanning. `className`+`specName` are both
  required on `characterRankings` or the query errors.

## Cheap data you might not know about

- **`table Deaths` = integrated death recap**: `deathWindow`, `damage
  {abilities, sources, total}`, `healing{...}`, last `events`, `killingBlow`,
  `overkill`. 1 request per pull covers every death's last-10s story.
- **`graph`** = per-player + `Total` bucketed series for any dataType,
  ~1 point. `dataType:Resources, sourceID:X, abilityID:100` = mana%
  timeline `[[ts, pct]...]` per actor.
- **DamageTaken events carry `mitigated`, `unmitigatedAmount`, `hitType`,
  and `buffs`** (ids of buffs ACTIVE at hit time, dot-separated string):
  "did they have a defensive up when they died?" costs zero extra requests.
- **masterData.actors** resolves every NPC/pet target name once per report.
- **Trash = fights without encounterID** (request `fights` WITHOUT
  `killType:Encounters`; the filtered query silently hides trash).
- **BUT some "trash" fights are a boss's own later-phase content.** WCL only
  labels a fight with the boss `encounterID` once the boss frame/health is
  engaged; a raid that wipes in a transition/realm phase BEFORE that (fighting
  phase-spawned adds) gets logged as a SEPARATE encounterID-less fight named
  after the add. Measured: Garrosh's Realm of Y'Shaarj wipes surfaced as
  fights named `Manifestation of Y'Shaarj` / `Harbinger of Y'Shaarj` (killers
  Grasp/Reaping/Blood of Y'Shaarj — all P2/P3 mechanics), sitting just before
  the first official "Garrosh Hellscream" pull. Two consequences: (1) counting
  them as trash INFLATES trash and HIDES a real boss wall (56 deaths here);
  (2) the official-pull count UNDERCOUNTS attempts (≈7 real, not 4). Rule:
  before tallying trash, cross-check each trash-fight NAME against the boss's
  known phase adds/abilities; reattribute boss-phase fights to the boss.
- **A boss-named SHORT segment (a few seconds, with one death) is the opposite
  case: a PREPULL/accidental tap, not phase content.** Someone tagged the boss
  and combat dropped (e.g. "Iron Juggernaut" 1.6 s, a melee death at 24 ms).
  Useful ONLY to avoid mistaking it for phase trash (distinguish by duration —
  seconds, not minutes — + a low-`ts_rel` Melee death). **Do NOT turn it into a
  prepull scoreboard:** tap + early-Melee signatures are blind to PROXIMITY pulls,
  so any "who prepulls" tally is a non-exhaustive sample masquerading as a count
  (ABANDONED — see `interpretation-traps.md` rule 6). The deep damage/aura/cast
  tables do NOT cover these segments (no first-damage event); you only have the
  death.

## Quota & caching

- ~1 point per simple request, 3600/hour
  (`{ rateLimitData { limitPerHour pointsSpentThisHour pointsResetIn } }`).
- Full 10-player night ≈ 1000-1500 points (pulls ~17 req each, trash 2 each,
  rankings, top details).
- **Quota is hourly: backoff cannot fix a 429.** Exponential backoff gives up
  in ~15s while the reset is up to 3600s away — the abandoned request then
  produces a SILENTLY PARTIAL extraction (the empty result looks like "no
  events"). Correct handling (built into `wcl.py`): poll rateLimitData every
  ~150 live calls, auto-sleep through `pointsResetIn` above 85%, and on a
  429 read `pointsResetIn` and sleep exactly that. rateLimitData itself must
  NEVER be served from the response cache (stale meter).
- Cache every response keyed sha256(query+variables); only cache real
  successes (`data` present). Re-runs and resumes are then free. Failed
  requests are NOT cached and print a `[wcl] WARNING` — re-running the same
  command retries only those slices, for free.
- The report-root query must be cache-busted daily (a `_asof` variable works)
  because a live report GROWS (multi-night lockouts).

## Endpoints

- OAuth (client credentials): `https://www.warcraftlogs.com/oauth/token` —
  the www token works on the classic API.
- API: `https://classic.warcraftlogs.com/api/v2/client`.
- MoP Classic difficulties: 3 = Normal, 4 = Heroic. Sizes 10/25.
- Throttle ~0.2s between live calls; backoff on 429/502/503.

## Localization

- Combat-log names are English. Official localized names per spell id:
  `https://nether.wowhead.com/tooltip/spell/<id>?locale=N` (fr=2, de=3,
  es=6, ru=8...). Cache them; extend the EN->local map by crossing logged
  (id, EN name) pairs with the id->local cache.

## Workdir-DB pitfalls (measured 2026-06-12, 25H progress night)

- The `composition` table also stores combatants of TOP-PARSE reports
  ingested by `top-detail` (other guilds). Any roster/ilvl query MUST filter
  `report IN (<own report codes>)` — an unfiltered roster query returned
  thousands of foreign players. **This generalizes: the whole workdir DB
  aggregates many guilds (`top_parse` percentiles). Resolve your own reports
  once via `raid_session WHERE guild LIKE '<own>'` and filter EVERY analysis
  query by it. Some tables (e.g. `pull`) may happen to hold only your reports,
  but never rely on that — filter explicitly.**
- `death.death_time` is **fight-relative milliseconds** (≠ the report-absolute
  `pull.start_time`). Don't subtract the two — a `death_time` of 1510 is 1.5 s
  into the pull, not 1.5 s after epoch. (`<5 s` Melee deaths are ran-in
  candidates — but a prepull TALLY is ABANDONED: proximity-blind, see
  interpretation-traps rule 6.)
- `deep_graph` series (`pointStart`) are in ABSOLUTE report milliseconds,
  not fight-relative: subtract the pull's `start_time` before computing
  fight-relative timestamps (a 111 s pull otherwise shows events at ~3200 s).
- The "every player has DamageTaken on pulls >60 s" integrity check has one
  legit false-positive: a player evicted into Norushen's Test realm
  (buff 144849/144850/144851 applied early) for the whole of a short pull
  takes zero raid damage. Verify the Test aura before treating it as an
  extraction hole.
- `nether.wowhead.com/tooltip/{spell,item}/<id>?locale=2` returns "Entity
  not found" for MoP-Classic-only entities; the working classic endpoint is
  `https://nether.wowhead.com/mop-classic/fr/tooltip/{spell,item}/<id>`
  (item names included — useful for gear-evolution displays).
- Resource gains are NOT in the combat log: orb-soaking mechanics that grant
  a power bar (e.g. Norushen corruption) and the power bar itself leave no
  aura/cast trace — "who soaked the orb" is unmeasurable from events. Say so
  explicitly instead of proxying. **Exception — a soak that comes with a DAMAGE
  component IS measurable:** Sha of Pride HM Rift of Corruption deals Unstable
  Corruption 147198 (a damage event) to soakers, so soak load per player IS
  countable; only the +5 Pride it grants is unlogged → proxy Pride-from-rifts
  as hits×5 and label it a component, never the bar (see zones/soo/traps.md). Trial/realm auras may log applydebuff
  reliably but removedebuff only sporadically: count entries, don't trust
  durations.
- Friendly-NPC healing (e.g. healable add phases) IS in healing events with
  the NPC as target — per-healer contribution on such adds is measurable.
