# WCL API v2 (classic) — gotchas

Every entry below cost real debugging time or produced silently-wrong data.
Read before extraction; re-read when a number looks weird.

## Silent failures (the dangerous ones)

- **`events(dataType:DamageTaken, targetID:X)` returns 0 events, no error**
  (classic endpoint). Fetch FULL DamageTaken and filter code-side.
  Same family: some sourceID+hostilityType combinations. Verified working:
  `Casts` + `sourceID`, `Buffs` + `targetID`, `Debuffs` + `hostilityType`.
  **Validate every extraction by an EXPECTED POSITIVE** (a tank with zero
  damage taken = alarm; a healer with zero casts = alarm), never by the
  absence of an error.
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

## Quota & caching

- ~1 point per simple request, 3600/hour
  (`{ rateLimitData { limitPerHour pointsSpentThisHour pointsResetIn } }`).
- Full 10-player night ≈ 1000-1500 points (pulls ~17 req each, trash 2 each,
  rankings, top details). Pause at >80%.
- Cache every response keyed sha256(query+variables); only cache real
  successes (`data` present). Re-runs and resumes are then free.
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
