# Siege of Orgrimmar — measured interpretation traps (validated)

Zone-specific instances of the generic trap classes (see
`references/interpretation-traps.md` for the classes and the mandatory
checklist). Every entry below was VALIDATED by measurement on real top-ranked
WCL classic kill parses (10H unless noted) — not from memory, not from guides.

Use this table BEFORE writing any verdict on these bosses. If your night's
data seems to contradict a row, re-measure on a fresh top parse before
publishing.

| Boss | Mechanic | Trap avoided / finding | Measurement (source) |
|---|---|---|---|
| Immerseus HM | Swelling/Sha Corruption | **C**: collective stop-attack INCLUDING TANKS; top raids show whole 30s windows at zero DoT ticks; the DoT expires in ~6s. Burn windows align with the Swelling cycle (~75s). A tank stacking 8-9 is NOT "unhealable damage the healers failed": tanks purge by stop-attack + taunt-swap like everyone | top1 Blood DK: 49 ticks/405s vs a wiping tank at 195/160s (~10x less per second); top raid 353 ticks spread thin |
| Sha of Pride | Mark of Arrogance x Gift of the Titans | **B**: HOLDING the dispel until Gift of the Titans (144359, ~25.5s cadence, healer-targeted) is CORRECT play — dispelling Mark costs Pride; under Gift it is free. End-of-fight burn = free dispels too. A "slow dispel table" here is a POSITIVE finding | timestamp correlation dispels x 144359 windows on top parses |
| Norushen | Test (144849/850/851) | **D**: raw DPS not comparable (eviction 27-44s inside the Test realm + annex damage on Essences/Manifestations varies 2x). Purifying only ~5/10 players is the TOP-KILL NORM, not a coverage fault — half the roster never goes | top 10H kill: 5 purified/10, half never |
| Fallen Protectors | Shadow Word: Bane (143434) | NO trap: dispel on sight, NO bounce penalty; tops dispel 35/36 with median 1.3s | Debuffs+Dispels events, top 10H parse |
| Malkorok | Displaced Energy (debuff 142913, damage 142928) | NO trap: immediate dispel is safe (top: 3/3 dispelled in 1.2-4.4s). Letting it expire IS a real fail. Note debuff id != damage id | same, two parses |
| Iron Juggernaut | Siege Mode | **C**: boss-DPS collapse during the WHOLE Siege phase is the norm in top kills (20M -> 1-8M per 10s); never blame the dip | DamageDone timeline, top 10H parse |
| Siegecrafter Blackfuse | Belt team | **D MAJOR**: 3 DPS at -40/-69% total damage are the belt team, not slackers. **Signature = share of targets named Deactivated/Disassembled weapons.** Identify the belt assignment BEFORE any DPS comparison; the team rotates (Pattern Recognition ~60s) so track by windows | per-target damage split of 6 DPS, top 10H parse |
| Garrosh (10N) | Realm of Y'Shaarj | NO eviction in 10N: the whole raid keeps hitting Garrosh during the Realm (61s measured); MC not observed in 10N top (0 friendly fire). Sum base+empowered spell-id pairs (Touch/Whirling/Desecrate) before any per-mechanic total | 84-145s window, top 10N parse |
| Thok | Deafening Screech x hard casts | **C-heal**: few hard casts + batching is correct adaptation, not laziness. The KPI is "casts CLIPPED by Screech" (top healer: 0/55 casts clipped across 65 Screeches), not cast count. Screech cadence RAMPS 13.5s -> 3.5s: any caster uptime KPI must be windowed by phase | begincast without cast <3s, top 10H parse |
| General Nazgrim | Defensive Stance | Residual damage during Defensive reads NOMINATIVELY: DoTs applied BEFORE the stance switch tick harmlessly-looking, vs ACTIVE shots during the stance (the real fail). Two different habits, same aggregate number — split them per player | per-player cast/DoT timeline vs stance windows |
| Garrosh (25N) | Touch of Y'Shaarj (MC) + Desecrated | MC **IS active in 25N** (unlike 10N): intra-raid damage logged under PLAYER CLASS spell ids (Serpent Sting, Living Bomb, dots...) = the scripted rescue, never a reproach to the MC nor the breakers — the KILL pull shows the most friendly fire. Desecrated hit totals = survivor-biased: rank on the kill pull only (live case: 69/54 total hits but 11/10 on the kill, BELOW tops' 12.98/parse) | 25N two-night run, deep_dmg_taken cross-checked vs 2 top 25N parses |
| Paragons of the Klaxxi | several | Kill order means "off-target" damage is scripted; **Amber Parasite is the single most-damaged target of the fight in top kills (244M)** — heavy "parasite damage" IS the job, not padding. Encase in Amber: broken in N (4.6M), NOT broken in HM (inverted polarity) — in HM a paragon healing under amber is the strat, not a focus fail. Buffs picked from dead Paragons = off-class casts in the log, not a player glitching. Expected dips: scorpion mutation (Rik'kal) = broken rotation ~20s per ~31.5s cycle; Bloodletting (Skeer) = whole raid swaps to Bloods (recurring collective dip); Catalyst BLUE = the only color where clumping is correct. Amber-carrier (Kunchong feed) is a volunteer/assigned role — their detours and Mesmerize target are not fails | top 10N kill 503s, per-target totals + DBM options |

## Trash (zone-wide)

- Pre-boss trash deaths cluster on a handful of identifiable abilities; treat
  trash as deaths + dangers + pacing, not as a DPS race.
- A false pull right before a boss (multiple deaths, then immediate boss pull)
  is a pacing finding, not an individual blame.

## Extraction gotchas re-verified on this zone

- `events(dataType:DamageTaken, targetID:X)` = silent 0 on classic -> fetch
  full + filter code-side.
- Always validate an extraction by an EXPECTED POSITIVE (a tank with zero
  damage taken = alarm).
- Debuff spell id != damage spell id (Displaced Energy 142913/142928; same
  pattern for many DoT-applying casts).
