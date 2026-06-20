-- history.db — unified longitudinal store (Tier 1 of the 3-tier model).
-- The DURABLE asset (per-night raid.db files are disposable / re-extractible
-- from the lzma cache). history_sync.py rolls each night's aggregates forward
-- here; evolution.py queries it cross-lockout.
--
-- Player identity = canonical(name) ONLY. WCL exposes neither realm nor guild
-- per player; `guild` is a per-report config constant -> observed attribute,
-- never an identity key (keying on it would split a renamed guild and merge a
-- PUG into a homonym). Cross-realm homonyms (rare, detectable by class/spec
-- incoherence) are split manually via player_alias if they ever appear.
--
-- FK integrity is ON (enforced per-connection in history_sync.py): insert the
-- player dimension before any fact. Idempotency: every fact PK carries
-- raid_label so a re-sync UPDATEs in place (never double-counts).

PRAGMA user_version = 1;

-- ----------------------------------------------------------- player dimension
CREATE TABLE IF NOT EXISTS player (
    player_id       INTEGER PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,   -- canonical(player_name)
    last_seen_guild TEXT,                   -- observed, NOT a key
    first_seen      TEXT,                   -- earliest raid_label
    last_seen       TEXT                    -- latest raid_label
);

-- alias -> canonical player_id (renames / server transfers; N alias -> 1 id).
CREATE TABLE IF NOT EXISTS player_alias (
    alias     TEXT PRIMARY KEY,
    player_id INTEGER NOT NULL REFERENCES player(player_id)
);

-- night dimension: one row per (raid_label, report).
CREATE TABLE IF NOT EXISTS h_night (
    raid_label TEXT NOT NULL,
    report     TEXT NOT NULL,
    guild      TEXT,
    zone_id    INTEGER,
    title      TEXT,
    start_ts   INTEGER,
    PRIMARY KEY (raid_label, report)
);

-- ----------------------------------------------------------- Tier-1 facts h_*
-- Mirror the per-night source tables, keyed by raid_label + player_id. Grain
-- stays (report, fight_id): non-destructive, so rollups are 100% reconstructible
-- from h_*, and h_* from the per-night raid.db (raw>aggregates traceability).

CREATE TABLE IF NOT EXISTS h_pull (
    raid_label   TEXT NOT NULL,
    report       TEXT NOT NULL,
    fight_id     INTEGER NOT NULL,
    encounter_id INTEGER,
    boss         TEXT,
    difficulty   INTEGER,
    size         INTEGER,
    kill         INTEGER,
    boss_pct     REAL,
    fight_pct    REAL,
    last_phase   INTEGER,
    duration_s   REAL,
    start_time   INTEGER,
    end_time     INTEGER,
    pull_number  INTEGER,
    PRIMARY KEY (raid_label, report, fight_id)
);

CREATE TABLE IF NOT EXISTS h_composition (
    raid_label  TEXT NOT NULL,
    report      TEXT NOT NULL,
    fight_id    INTEGER NOT NULL,
    player_id   INTEGER NOT NULL REFERENCES player(player_id),
    player_name TEXT,
    class       TEXT,
    spec        TEXT,
    role        TEXT,
    item_level  REAL,
    PRIMARY KEY (raid_label, report, fight_id, player_id)
);

CREATE TABLE IF NOT EXISTS h_player_fight (
    raid_label  TEXT NOT NULL,
    report      TEXT NOT NULL,
    fight_id    INTEGER NOT NULL,
    player_id   INTEGER NOT NULL REFERENCES player(player_id),
    data_type   TEXT NOT NULL,          -- Healing | DamageDone | DamageTaken
    total       INTEGER,
    active_time INTEGER,
    PRIMARY KEY (raid_label, report, fight_id, player_id, data_type)
);

CREATE TABLE IF NOT EXISTS h_player_ability (
    raid_label   TEXT NOT NULL,
    report       TEXT NOT NULL,
    fight_id     INTEGER NOT NULL,
    player_id    INTEGER NOT NULL REFERENCES player(player_id),
    data_type    TEXT NOT NULL,
    ability_id   INTEGER NOT NULL,
    ability_name TEXT,
    total        INTEGER,
    overheal     INTEGER,
    hit_count    INTEGER,
    uses         INTEGER,
    PRIMARY KEY (raid_label, report, fight_id, player_id, data_type, ability_id)
);

CREATE TABLE IF NOT EXISTS h_death (
    raid_label   TEXT NOT NULL,
    report       TEXT NOT NULL,
    fight_id     INTEGER NOT NULL,
    seq          INTEGER NOT NULL,
    player_id    INTEGER REFERENCES player(player_id),
    player_name  TEXT,
    death_time   INTEGER,
    ability_id   INTEGER,
    ability_name TEXT,
    overkill     INTEGER,
    PRIMARY KEY (raid_label, report, fight_id, seq)
);

CREATE TABLE IF NOT EXISTS h_conso (
    raid_label  TEXT NOT NULL,
    report      TEXT NOT NULL,
    fight_id    INTEGER NOT NULL,
    player_id   INTEGER NOT NULL REFERENCES player(player_id),
    prepot      INTEGER,
    combat_pots INTEGER,
    flask       TEXT,
    food        TEXT,
    PRIMARY KEY (raid_label, report, fight_id, player_id)
);

CREATE TABLE IF NOT EXISTS h_deep_heal_ability (
    raid_label   TEXT NOT NULL,
    report       TEXT NOT NULL,
    fight_id     INTEGER NOT NULL,
    player_id    INTEGER NOT NULL REFERENCES player(player_id),
    ability_id   INTEGER NOT NULL,
    ability_name TEXT,
    total        INTEGER,
    overheal     INTEGER,
    hit_count    INTEGER,
    PRIMARY KEY (raid_label, report, fight_id, player_id, ability_id)
);

-- Benchmark top parses (external players, NOT our roster -> no player_id).
CREATE TABLE IF NOT EXISTS h_top_parse (
    raid_label   TEXT NOT NULL,
    encounter_id INTEGER NOT NULL,
    difficulty   INTEGER NOT NULL,
    size         INTEGER NOT NULL,
    spec_key     TEXT NOT NULL,
    rank         INTEGER NOT NULL,
    report       TEXT,
    fight_id     INTEGER,
    player_name  TEXT,
    amount       REAL,
    duration_s   REAL,
    PRIMARY KEY (raid_label, encounter_id, difficulty, size, spec_key, rank)
);

-- Per-parse percentiles (from digests/percentiles.json — a FILE, not raid.db).
-- Stored per-parse (NOT pre-aggregated): median_percentile is recomputed from
-- these so evolution.py reproduces its exact contract.
CREATE TABLE IF NOT EXISTS h_percentile (
    raid_label      TEXT NOT NULL,
    report          TEXT NOT NULL,
    fight_id        INTEGER NOT NULL,
    player_id       INTEGER NOT NULL REFERENCES player(player_id),
    player_name     TEXT,
    encounter_id    INTEGER,
    boss            TEXT,
    difficulty      INTEGER,
    spec            TEXT,
    role            TEXT,
    metric          TEXT,                  -- dps | hps
    -- NUMERIC (not REAL): preserve the int-vs-float type WCL returned, so
    -- evolution.py reproduces percentiles.json byte-for-byte (REAL would coerce
    -- a whole percentile 92 -> 92.0, rendering "92.0" instead of "92").
    amount          NUMERIC,
    rank_percent    NUMERIC,
    best_percent    NUMERIC,
    bracket_percent NUMERIC,
    ilvl            NUMERIC,
    PRIMARY KEY (raid_label, report, fight_id, player_id, metric)
);

-- ------------------------------------------------------ rollups (materialized)
-- Recomputed incrementally per raid_label at sync (DELETE WHERE raid_label=?
-- then re-INSERT). SQLite has no materialized views; the player_fight pivot
-- (multi data_type -> 1 row) is not index-expressible.

-- Main rollup: 1 row / player / boss / night / SPEC (spec-split — a mid-night
-- respec yields one row per spec, never mixing two specs' throughput).
CREATE TABLE IF NOT EXISTS roll_player_encounter (
    player_id         INTEGER NOT NULL REFERENCES player(player_id),
    raid_label        TEXT NOT NULL,
    encounter_id      INTEGER NOT NULL,
    difficulty        INTEGER NOT NULL,    -- N=3 vs H=4 raw throughput NOT comparable
    spec              TEXT NOT NULL,
    n_pulls           INTEGER,
    n_kills           INTEGER,
    dmg_total         INTEGER,
    heal_total        INTEGER,
    dtaken_total      INTEGER,
    active_time_ms    INTEGER,
    duration_s        REAL,
    dps               REAL,
    hps               REAL,
    dtps              REAL,
    active_pct        REAL,
    deaths            INTEGER,
    prepots           INTEGER,
    median_percentile REAL,                -- recomputed from h_percentile parses
    ilvl              REAL,
    PRIMARY KEY (player_id, raid_label, encounter_id, difficulty, spec)
);
-- PK covers the player-trajectory axis (filter player_id, range raid_label).
CREATE INDEX IF NOT EXISTS ix_rpe_boss  ON roll_player_encounter (encounter_id, difficulty, raid_label, player_id);
CREATE INDEX IF NOT EXISTS ix_rpe_label ON roll_player_encounter (raid_label);

-- 4 raw-event rollups (computed at sync from per-night deep_*/raid_event + refs,
-- reusing analyze.py's exact queries/helpers — ~hundreds of rows each, never an
-- unification of the ~9M-row deep_* tables).

-- Avoidable damage taken (deep_dmg_taken x mechanics_ref class in
-- avoidable/reducible/soak), per player x boss x ability x spec.
CREATE TABLE IF NOT EXISTS roll_player_avoidable (
    player_id         INTEGER NOT NULL REFERENCES player(player_id),
    raid_label        TEXT NOT NULL,
    encounter_id      INTEGER NOT NULL,
    ability_id        INTEGER NOT NULL,
    spec              TEXT NOT NULL,
    hit_count         INTEGER,
    total_unmitigated INTEGER,
    n_pulls           INTEGER,
    PRIMARY KEY (player_id, raid_label, encounter_id, ability_id, spec)
);
CREATE INDEX IF NOT EXISTS ix_rpa_ability ON roll_player_avoidable (ability_id, raid_label, player_id);

-- Interrupts landed (raid_event kind='interrupt'), per player x boss x spec.
-- kicks_possible (interruptible enemy casts) is NOT modeled here — deferred to
-- avoid an unsourced denominator; kicks_done is the honest measure.
CREATE TABLE IF NOT EXISTS roll_player_interrupt (
    player_id    INTEGER NOT NULL REFERENCES player(player_id),
    raid_label   TEXT NOT NULL,
    encounter_id INTEGER NOT NULL,
    spec         TEXT NOT NULL,
    kicks_done   INTEGER,
    n_pulls      INTEGER,
    PRIMARY KEY (player_id, raid_label, encounter_id, spec)
);

-- Signature-aura uptime (deep_aura union-of-intervals via analyze._aura_windows,
-- scoped to spec_kpis buff ids), per player x boss x ability x spec. Uptime is
-- the pull-duration-weighted average across the night's pulls.
CREATE TABLE IF NOT EXISTS roll_player_aura_uptime (
    player_id    INTEGER NOT NULL REFERENCES player(player_id),
    raid_label   TEXT NOT NULL,
    encounter_id INTEGER NOT NULL,
    ability_id   INTEGER NOT NULL,
    spec         TEXT NOT NULL,
    uptime_pct   REAL,
    n_pulls      INTEGER,
    PRIMARY KEY (player_id, raid_label, encounter_id, ability_id, spec)
);
CREATE INDEX IF NOT EXISTS ix_rau_ability ON roll_player_aura_uptime (ability_id, raid_label, player_id);

-- Raid-cooldown casts (deep_cast x RAID_CDS ability ids), per player x boss x
-- ability x spec.
CREATE TABLE IF NOT EXISTS roll_player_cd_cast (
    player_id    INTEGER NOT NULL REFERENCES player(player_id),
    raid_label   TEXT NOT NULL,
    encounter_id INTEGER NOT NULL,
    ability_id   INTEGER NOT NULL,
    spec         TEXT NOT NULL,
    casts        INTEGER,
    n_pulls      INTEGER,
    PRIMARY KEY (player_id, raid_label, encounter_id, ability_id, spec)
);
CREATE INDEX IF NOT EXISTS ix_rcc_ability ON roll_player_cd_cast (ability_id, raid_label, player_id);
