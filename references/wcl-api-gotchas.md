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
