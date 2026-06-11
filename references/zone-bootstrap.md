# Zone bootstrap — building the reference files for a new raid zone

The pipeline needs two reference files in `<workdir>/refs/` before `analyze.py
avoidable|execution|bench` can run:

| File | Content | Consumed by |
|---|---|---|
| `refs/mechanics_ref.json` | per-encounter mechanic classification (avoidable / reducible / soak / unavoidable / tank) + dispellables + interruptibles | analyze avoidable, dispels; the analyst's verdicts |
| `refs/spec_kpis.json` | per-spec rotation casts, DoTs to track, major CDs, buffs/procs, gcd_base_ms | analyze execution, bench |
| `refs/zone.json` *(optional)* | localized boss display names + spec names | pages.py |
| `refs/spell_names.json` | localized spell names (auto: `localize.py spells`) | pages.py |

If the zone is already bundled (see `references/zones/`), COPY and go:

```bash
cp <skill>/references/zones/soo/mechanics_ref.json  <workdir>/refs/mechanics_ref.json
cp <skill>/references/zones/spec_kpis_mop.json      <workdir>/refs/spec_kpis.json
cp <skill>/references/zones/soo/zone.fr.json        <workdir>/refs/zone.json   # if lang=fr
```

Then ONLY do step 4 (id reconciliation) and step 5 (spec gap check) below —
they depend on YOUR roster and YOUR log, not on the zone.

---

## Building mechanics_ref.json from scratch (new zone)

**Never classify a mechanic from memory.** Two independent sources minimum,
classification justified by an explicit signal, every entry cites its source.

### 1. Source A — DBM (Deadly Boss Mods) lua, the encoded strategy

Clone the DBM module covering the zone (for MoP Classic:
`https://github.com/DeadlyBossMods/DBM-MoP` -> `DBM-Raids-MoP/<Zone>/`).
One lua file per boss. The WARNING TYPE encodes the strategy:

| DBM signal | Classification |
|---|---|
| `specWarn...Dodge`, `GTFO`, "move away", "run out" | **avoidable** (strict individual dodge) |
| `specWarn...Stack`, "soak", "share" | **soak** (assigned intake — NOT a fail) |
| warning gated on tank role / tank icon | **tank** (structural intake) |
| announce-only, no special warning | **unavoidable** (raid-wide pressure) |
| volume depends on kill speed / add control / own cadence | **reducible** |
| `NewSpecialWarningDispel(...)` | -> dispellables list |
| `NewSpecialWarningInterrupt(...)` / kick-targeted | -> interruptibles list |
| absence of a dispel warning on a dispellable-looking debuff | CLUE: dispelling may be wrong/conditional — investigate (trap class A/B) |

Gotchas:
- In MoP backports, `IsMythic()` in DBM lua means **Heroic** (id 4), not a
  mythic mode. Difficulty-gated warnings tell you which mechanics change in HM.
- Options OFF by default with a comment (e.g. "person who grabs X wants this")
  reveal VOLUNTEER ROLES — their weird behavior in logs is the strat.
- Cite `file.lua:line` for every classification.

### 2. Source B — a simulator or encounter spec, the timings

Any independent encounter source: SimulationCraft encounter scripts, an
open-source sim's encounter AI, or a maintained encounter spec doc. Use it to
cross-check spell IDs, cadences (CD seconds), and phase structure. When A and
B disagree, measure on a top parse (step 3) before writing anything.

### 3. Validation — top WCL parses (MANDATORY for ambiguous entries)

For every mechanic where the classification is not obvious, and for EVERY
entry that will support a player-facing verdict:
- pull 1-2 top kill parses of the zone/difficulty (`ingest.py benchmark`
  or rankings + targeted events),
- measure what top players actually take/do on that mechanic,
- tops take ~0 -> avoidable confirmed; tops take it deliberately -> soak or
  unavoidable; tops show windowed behavior -> suspect trap class B/C
  (document it in the zone traps.md).

The data-driven fallback also works without DBM: `ingest.py benchmark` then
`ingest.py infer-avoidable` writes candidates (tops ~0 dmg/min vs you >0) to
`avoidable_ref`. Treat them as candidates until validated.

### 4. ID reconciliation vs YOUR log (every zone, every roster)

KPI/mechanic spell ids must match what is ACTUALLY LOGGED:
- cast id != debuff id is common (e.g. a sting cast 1978 logs debuff 118253),
- glyphed/talented variants change ids (e.g. a glyphed CD logs a different id).

After `ingest.py deep`, for each id in spec_kpis dots/buffs and mechanics_ref,
check it appears in `deep_aura`/`deep_dmg_taken`; reconcile by name when the
id is absent but the name matches another id. Update the refs.

### 5. spec_kpis.json — per-spec KPIs for YOUR roster

For each `Class-Spec` present in the roster (check
`SELECT DISTINCT class, spec FROM composition`):

```json
"Hunter-Survival": {
  "role": "dps", "gcd_base_ms": 1500,
  "casts_rotation": [{"id": ..., "name": "..."}],
  "dots_uptime":    [{"id": 118253, "name": "Serpent Sting"}],
  "cds_major":      [{"id": ..., "name": "...", "cd_s": 180}],
  "buffs_track":    [{"id": ..., "name": "..."}]
}
```

Sources: an in-game rotation addon's priority lists (e.g. Hekili `.simc`
files), a simulator's APL for the spec, class guides for the era. Healers:
HoTs/absorbs go in `buffs_track` (tracked BY SOURCE, union across targets),
NOT in `dots_uptime`.

### 6. Output format

`mechanics_ref.json`:
```json
{
  "<encounter_id>": {
    "boss": "Name",
    "mechanics": {
      "<spell_id>": {"name": "...", "class": "avoidable|reducible|soak|unavoidable|tank",
                      "how": "1-2 lines: why this class, what behavior drives it",
                      "source": "DBM <file>:<line> + <second source>"}
    },
    "dispellables":   [{"id": ..., "name": "...", "note": "dispel on sight / HOLD until <window> / costs <resource>"}],
    "interruptibles": [{"id": ..., "name": "...", "note": "real kick vs filler"}],
    "notes_hm": "what flips in heroic (inverted polarities, added mechanics)"
  },
  "trash": { "...": "danger abilities worth naming on trash" }
}
```

### 7. Contribute it back

A bootstrapped zone is valuable to everyone: PR your
`references/zones/<zone>/` (mechanics_ref.json + traps.md + zone.<lang>.json)
to the skill repository.
