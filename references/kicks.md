# Kicks — interrupt scoreboard & timeline (canonical doctrine)

The kick data builder (`scripts/kicks.py`) and the timeline renderer
(`scripts/kicks_render.py`) are the **canonical path**. Do NOT reinvent extraction
per CR, and do NOT re-parse `wcl_raw`: the abandoned `extract_interrupts.py` re-parse
caused cross-report contamination, and its "cast-less id 32747 / target-was-casting
gate / separate `extraAbilityGameID`" were **artifacts of that approach, never log
truth**. Everything below comes from the clean INGESTED tables.

## Data sources (all ingested, scoped report+fight)

- `deep_aura kind='enemy_cast'`, `type` ∈ {`begincast`, `cast`} — enemy cast starts / completions.
- `deep_aura kind='interrupt'` — interrupts: `source_id`=kicker, `target_id`=mob,
  **`ability_id` = the STOPPED spell**. (The kick-spell id is re-derived from the
  player's `deep_cast`, not needed on the interrupt event.)
- `deep_cast` where `ability_id` ∈ the DEDICATED interrupt spells — player attempts.
- `deep_dmg_taken` by `ability_id`=<danger spell> — **the landing** (damage-as-landing).

## Measurement model

- **kicked** = an `interrupt` event (WCL authority).
- **attempt** = a cast of a DEDICATED interrupt spell (Counterspell 2139, Pummel 6552,
  Wind Shear 57994, Mind Freeze 47528, Skull Bash 106839, Rebuke 96231, Spear Hand
  Strike 116705, Counter Shot 147362, Silence 15487, …). **Avenger's Shield (31935) is
  NOT in the dedicated list** — it is an on-CD *damage* spell: its interrupts are
  credited via the `interrupt` events → 100% efficiency, 0 wasted. **There is NO
  "target-was-casting" gate**: the on-CD exclusion is done by the SPELL LIST, never by
  a time window (the old ≤4 s gate is abandoned).
- **landed / "passé"** = the spell HIT: a DAMAGE event (`deep_dmg_taken ability_id`)
  **OR** a `type='cast'` completion, taken in **UNION**. Completions alone under-count
  (see damage-as-landing) → damage is the truth.
- **no-hit / "sans frappe"** = neither kicked nor any damage logged: add killed/CC'd, or
  a simultaneous cast unresolved in the log. Verified no-damage = it hit NOBODY — **not a
  missed kick to blame**.
- **coverage** (raid KPI) = kicked / (kicked + landed).
- **efficiency** = kicked / attempts (dedicated spells only).

## Damage-as-landing (why completions are not enough)

WCL under-logs enemy cast COMPLETIONS when several adds cast the same spell at once
(measured on Sha de l'Orgueil: **273 begincast → 89 completions** on Mocking Blast). So
"did it land?" cannot be read from completions alone — UNION the spell's
`deep_dmg_taken` events. **Limit:** an enemy HEAL (e.g. Galakras Chain Heal 146757) has
no player-damage signal, so its unlogged completions stay invisible — say so, never
fabricate. See `wcl-api-gotchas.md` (silent failures).

## Timeline render contract (`kicks_render.py`)

- **One cast = one horizontal LANE.** X-axis = reaction time measured from the enemy
  `begincast`.
- **Bar length:** kicked → to the kick (`lat`, **no 0.3 s floor**); landed → FULL width;
  add died mid-cast → to the death; no-hit / cancel → stub (CSS `min-width:3px`).
- **Dot (the kick):** `left = max(0, min(lat/scale, 1)) * 100` — **clamp ≥ 0** (a kick can
  never render before its cast). Filled dot = the kicker; ring = wasted (a kick that
  didn't land).
- **Reaction label:** "en avance" if reaction < 0, else "en retard". Normalize `-0.0`→`0.0`
  (the `if lat == 0: lat = 0.0` guard) so a kick exactly on the begincast doesn't read
  as negative.
- **Per-pull selector**, a collapsible cast-by-cast list (Δ vs the previous kickable
  cast), and a per-boss **scoreboard** [Launched / Landed / Efficiency / Median reaction /
  Wasted] + a "never kicked" list.

## Perimeter (`nominative` param) — read this before publishing

- **Per-cast timeline lanes carry the kicker NAME on BOTH the public boss page and the
  officers annex** — explicit user decision (consistent with the nominative execution
  section already public). Showing who kicked which cast, and the reaction, is allowed
  public for kicks.
- **The `nominative=True` (officers) path adds the SCOREBOARD** (efficiency / wasted /
  "never kicked" ranking). A ranking is blame → **officers annex ONLY, never public.**
- NEVER drop the `nominative` param (a prior port lost it → the section silently became
  officers-only). Public boss page calls with `nominative=False` (lanes + names, no
  scoreboard); officers annex calls with `nominative=True` (+ scoreboard).

See also `extraction_manifest.md` (kicks measurement block) and
`interpretation-traps.md` (0 kicks ≠ fault unless the opportunity existed).
