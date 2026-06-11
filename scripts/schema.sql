-- wow-raid-debrief: sqlite schema (one db per workdir / raid night).
-- Portable SQL conventions: TEXT/INTEGER/REAL only, idempotent upserts via
-- INSERT ... ON CONFLICT DO UPDATE. ts_rel = ms since pull start.

-- Raw WCL API response cache. Anti rate-limit + reproducible: re-runs are free.
CREATE TABLE IF NOT EXISTS wcl_raw (
    query_hash TEXT PRIMARY KEY,
    query      TEXT NOT NULL,
    variables  TEXT,
    response   TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

-- Resumability markers (all-PK table: INSERT OR IGNORE, never empty upsert).
CREATE TABLE IF NOT EXISTS done_marker (
    report   TEXT NOT NULL,
    fight_id INTEGER NOT NULL,
    what     TEXT NOT NULL,
    PRIMARY KEY (report, fight_id, what)
);

-- One row per raid night report.
CREATE TABLE IF NOT EXISTS raid_session (
    report      TEXT PRIMARY KEY,
    guild       TEXT NOT NULL,
    zone        TEXT,
    zone_id     INTEGER,
    raid_label  TEXT NOT NULL,      -- 'id-YYYY-MM-DD': groups nights of one lockout
    title       TEXT,
    start_ts    INTEGER,            -- epoch ms
    end_ts      INTEGER,
    ingested_at TEXT
);

-- All OUR pulls (kills AND wipes), exhaustive.
CREATE TABLE IF NOT EXISTS pull (
    report       TEXT NOT NULL,
    fight_id     INTEGER NOT NULL,
    encounter_id INTEGER,
    boss         TEXT,
    difficulty   INTEGER,           -- classic: 3=Normal, 4=Heroic
    size         INTEGER,
    kill         INTEGER,           -- 0/1
    boss_pct     REAL,              -- boss HP % at wipe (0 on kill)
    fight_pct    REAL,              -- % of the FIGHT remaining (phase-aware)
    last_phase   INTEGER,
    duration_s   REAL,
    start_time   INTEGER,           -- ms relative to report start
    end_time     INTEGER,
    pull_number  INTEGER,           -- per-boss pull counter in this report
    PRIMARY KEY (report, fight_id)
);

-- Benchmark fights (top logs warehouse; sparser than pull).
CREATE TABLE IF NOT EXISTS fight (
    report       TEXT NOT NULL,
    fight_id     INTEGER NOT NULL,
    encounter_id INTEGER,
    boss         TEXT,
    difficulty   INTEGER,
    size         INTEGER,
    kill         INTEGER,
    duration_s   REAL,
    start_time   INTEGER,
    end_time     INTEGER,
    ingested_at  TEXT,
    PRIMARY KEY (report, fight_id)
);

-- Roster composition per fight. SPEC IS PER PULL (mid-night respecs are
-- common): any join without fight_id lies about specs.
CREATE TABLE IF NOT EXISTS composition (
    report      TEXT NOT NULL,
    fight_id    INTEGER NOT NULL,
    actor_id    INTEGER NOT NULL,
    player_name TEXT,
    class       TEXT,
    spec        TEXT,
    role        TEXT,               -- tank / healer / dps
    item_level  REAL,
    PRIMARY KEY (report, fight_id, actor_id)
);

-- Per-actor per-fight totals (cheap: one table call per dataType).
CREATE TABLE IF NOT EXISTS player_fight (
    report      TEXT NOT NULL,
    fight_id    INTEGER NOT NULL,
    actor_id    INTEGER NOT NULL,
    data_type   TEXT NOT NULL,      -- Healing | DamageDone | DamageTaken
    total       INTEGER,
    active_time INTEGER,
    PRIMARY KEY (report, fight_id, actor_id, data_type)
);

-- Per-ability breakdown. actor_id=0 + data_type='DamageTakenByAbility' is the
-- raid-wide damage-taken-by-ability view (avoidable inference input).
CREATE TABLE IF NOT EXISTS player_ability (
    report       TEXT NOT NULL,
    fight_id     INTEGER NOT NULL,
    actor_id     INTEGER NOT NULL,
    data_type    TEXT NOT NULL,
    ability_id   INTEGER NOT NULL,
    ability_name TEXT,
    total        INTEGER,
    overheal     INTEGER,
    hit_count    INTEGER,
    uses         INTEGER,
    PRIMARY KEY (report, fight_id, actor_id, data_type, ability_id)
);

-- Player deaths, ordered (seq=1 -> first death of the pull).
CREATE TABLE IF NOT EXISTS death (
    report       TEXT NOT NULL,
    fight_id     INTEGER NOT NULL,
    seq          INTEGER NOT NULL,
    actor_id     INTEGER,
    player_name  TEXT,
    death_time   INTEGER,           -- ms since pull start
    ability_id   INTEGER,           -- killing blow
    ability_name TEXT,
    overkill     INTEGER,
    PRIMARY KEY (report, fight_id, seq)
);

-- Selective events: raid CD casts, interrupt/dispel aggregates, avoidable
-- hits, combatantinfo payloads.
CREATE TABLE IF NOT EXISTS raid_event (
    report       TEXT NOT NULL,
    fight_id     INTEGER NOT NULL,
    kind         TEXT NOT NULL,     -- cd_cast | interrupt | dispel | avoidable_hit | combatantinfo | *_ability
    seq          INTEGER NOT NULL,
    timestamp    INTEGER,
    source_id    INTEGER,
    source_name  TEXT,
    target_id    INTEGER,
    target_name  TEXT,
    ability_id   INTEGER,
    ability_name TEXT,
    amount       INTEGER,
    payload      TEXT,
    PRIMARY KEY (report, fight_id, kind, seq)
);

-- Consumables per (pull, player), derived from combatantinfo + potion events.
CREATE TABLE IF NOT EXISTS conso (
    report      TEXT NOT NULL,
    fight_id    INTEGER NOT NULL,
    actor_id    INTEGER NOT NULL,
    prepot      INTEGER,
    combat_pots INTEGER,
    flask       TEXT,
    food        TEXT,
    PRIMARY KEY (report, fight_id, actor_id)
);

-- Avoidable-damage reference per boss. Never invented: source = zone refs
-- (DBM bootstrap), cross-log inference, or manual validation.
CREATE TABLE IF NOT EXISTS avoidable_ref (
    encounter_id INTEGER NOT NULL,
    ability_id   INTEGER NOT NULL,
    ability_name TEXT,
    status       TEXT NOT NULL,     -- candidate | validated | rejected
    source       TEXT,              -- inferred | zone_ref | manual
    ratio        REAL,
    note         TEXT,
    PRIMARY KEY (encounter_id, ability_id)
);

-- ------------------------------------------------------------- deep (raw events)

CREATE TABLE IF NOT EXISTS deep_cast (
    report     TEXT NOT NULL,
    fight_id   INTEGER NOT NULL,
    seq        INTEGER NOT NULL,
    ts_rel     INTEGER,
    type       TEXT,                -- cast | begincast
    source_id  INTEGER,
    target_id  INTEGER,
    ability_id INTEGER,
    PRIMARY KEY (report, fight_id, seq)
);
CREATE INDEX IF NOT EXISTS ix_deep_cast_src ON deep_cast (report, fight_id, source_id);

CREATE TABLE IF NOT EXISTS deep_dmg_taken (
    report      TEXT NOT NULL,
    fight_id    INTEGER NOT NULL,
    seq         INTEGER NOT NULL,
    ts_rel      INTEGER,
    target_id   INTEGER,
    source_id   INTEGER,
    ability_id  INTEGER,
    amount      INTEGER,
    absorbed    INTEGER,
    mitigated   INTEGER,
    unmitigated INTEGER,
    hit_type    INTEGER,
    buffs       TEXT,               -- active buff ids at hit time ("a.b.c.")
    is_aoe      INTEGER,
    PRIMARY KEY (report, fight_id, seq)
);
CREATE INDEX IF NOT EXISTS ix_deep_dt_tgt ON deep_dmg_taken (report, fight_id, target_id);
CREATE INDEX IF NOT EXISTS ix_deep_dt_ab  ON deep_dmg_taken (report, fight_id, ability_id);

CREATE TABLE IF NOT EXISTS deep_dmg_done (
    report     TEXT NOT NULL,
    fight_id   INTEGER NOT NULL,
    seq        INTEGER NOT NULL,
    ts_rel     INTEGER,
    source_id  INTEGER,
    target_id  INTEGER,
    target_instance INTEGER,
    ability_id INTEGER,
    amount     INTEGER,
    absorbed   INTEGER,
    hit_type   INTEGER,
    tick       INTEGER,
    PRIMARY KEY (report, fight_id, seq)
);
CREATE INDEX IF NOT EXISTS ix_deep_dd_src ON deep_dmg_done (report, fight_id, source_id);

-- kind = buff | debuff (on friendlies) | debuff_enemy (DoTs on enemies)
--      | dispel | interrupt | enemy_cast (from `extras`).
CREATE TABLE IF NOT EXISTS deep_aura (
    report     TEXT NOT NULL,
    fight_id   INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    ts_rel     INTEGER,
    type       TEXT,
    source_id  INTEGER,
    target_id  INTEGER,
    ability_id INTEGER,
    stacks     INTEGER,
    PRIMARY KEY (report, fight_id, kind, seq)
);
CREATE INDEX IF NOT EXISTS ix_deep_aura_ab ON deep_aura (report, fight_id, ability_id);

-- Death recap: payload = full WCL Deaths-table entry (deathWindow,
-- damage.abilities/sources, healing, last events, killingBlow).
CREATE TABLE IF NOT EXISTS deep_death_recap (
    report     TEXT NOT NULL,
    fight_id   INTEGER NOT NULL,
    death_seq  INTEGER NOT NULL,
    actor_id   INTEGER,
    ts_rel     INTEGER,
    payload    TEXT,
    PRIMARY KEY (report, fight_id, death_seq)
);

-- WCL graph series (bucketed): kind = dtps | hps | dps | mana:<actor_id>.
CREATE TABLE IF NOT EXISTS deep_graph (
    report   TEXT NOT NULL,
    fight_id INTEGER NOT NULL,
    kind     TEXT NOT NULL,
    payload  TEXT,
    PRIMARY KEY (report, fight_id, kind)
);

CREATE TABLE IF NOT EXISTS deep_phase (
    report     TEXT NOT NULL,
    fight_id   INTEGER NOT NULL,
    idx        INTEGER NOT NULL,
    phase_id   INTEGER,
    phase_name TEXT,
    ts_rel     INTEGER,
    PRIMARY KEY (report, fight_id, idx)
);

-- Per-spell healing totals (WCL Healing table per player).
CREATE TABLE IF NOT EXISTS deep_heal_ability (
    report       TEXT NOT NULL,
    fight_id     INTEGER NOT NULL,
    actor_id     INTEGER NOT NULL,
    ability_id   INTEGER NOT NULL,  -- -1 = TOTAL row
    ability_name TEXT,
    total        INTEGER,
    overheal     INTEGER,
    hit_count    INTEGER,
    PRIMARY KEY (report, fight_id, actor_id, ability_id)
);

CREATE TABLE IF NOT EXISTS deep_heal_event (
    report     TEXT NOT NULL,
    fight_id   INTEGER NOT NULL,
    seq        INTEGER NOT NULL,
    ts_rel     INTEGER,
    source_id  INTEGER,
    target_id  INTEGER,
    ability_id INTEGER,
    amount     INTEGER,
    overheal   INTEGER,
    tick       INTEGER,
    PRIMARY KEY (report, fight_id, seq)
);
CREATE INDEX IF NOT EXISTS ix_deep_he_src ON deep_heal_event (report, fight_id, source_id);

CREATE TABLE IF NOT EXISTS trash_fight (
    report     TEXT NOT NULL,
    fight_id   INTEGER NOT NULL,
    name       TEXT,
    start_time INTEGER,
    end_time   INTEGER,
    duration_s REAL,
    deaths     INTEGER,
    payload    TEXT,                -- JSON: deaths (who/what/when), top damage taken
    PRIMARY KEY (report, fight_id)
);

-- Actor map (players + NPCs + pets): target-name resolution.
CREATE TABLE IF NOT EXISTS actor_name (
    report   TEXT NOT NULL,
    actor_id INTEGER NOT NULL,
    name     TEXT,
    type     TEXT,                  -- Player | NPC | Pet
    sub_type TEXT,
    PRIMARY KEY (report, actor_id)
);

-- Selected top parses (top1/top2 per spec x boss); fight data lives in
-- fight/composition/player_* + deep_* (targeted events).
CREATE TABLE IF NOT EXISTS top_parse (
    encounter_id INTEGER NOT NULL,
    difficulty   INTEGER NOT NULL,
    size         INTEGER NOT NULL,
    spec_key     TEXT NOT NULL,     -- e.g. 'Shaman-Restoration'
    rank         INTEGER NOT NULL,
    report       TEXT,
    fight_id     INTEGER,
    player_name  TEXT,
    actor_id     INTEGER,
    amount       REAL,
    duration_s   REAL,
    PRIMARY KEY (encounter_id, difficulty, size, spec_key, rank)
);
