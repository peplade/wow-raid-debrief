#!/usr/bin/env python3
"""Shared module: WCL API v2 client (OAuth + GraphQL + sqlite response cache),
sqlite backend, workdir config. Stdlib only (no pip install).

Every WCL response is cached in `wcl_raw` keyed by sha256(query+variables):
re-runs are free (no API points), interrupted runs resume at no cost.

Credentials: env WCL_CLIENT_ID / WCL_CLIENT_SECRET, or a `.env` file (one
KEY=value per line) in the workdir or next to the scripts. NEVER commit them.

Workdir layout (one per raid ID — one or several nights/reports — created by
`ingest.py init`):
    <workdir>/raid.json     config: report code(s), guild, lang, host, ...
    <workdir>/raid.db       sqlite (cache + all extracted tables)
    <workdir>/digests/      analysis outputs (analyze.py)
    <workdir>/refs/         zone mechanics ref + spec KPIs (bootstrap)
    <workdir>/content/      written verdicts (HTML fragments, by the skill)
    <workdir>/pages/        generated static report (pages.py)
"""
import base64
import hashlib
import json
import lzma
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEMA = os.path.join(SCRIPTS_DIR, "schema.sql")

# Classic difficulty ids (MoP: 3=Normal, 4=Heroic).
DIFF_NAME = {1: "LFR", 3: "N", 4: "H", 5: "M"}


# ----------------------------------------------------------------- env/config

def load_env(workdir=None):
    """Load .env files (workdir first, then scripts dir). Existing env wins."""
    paths = []
    if workdir:
        paths.append(os.path.join(workdir, ".env"))
    paths.append(os.path.join(os.path.dirname(SCRIPTS_DIR), ".env"))
    paths.append(os.path.join(SCRIPTS_DIR, ".env"))
    for p in paths:
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())


def workdir_from_args(args):
    wd = getattr(args, "workdir", None) or os.environ.get("RAID_WORKDIR") or os.getcwd()
    return os.path.abspath(wd)


def load_config(workdir):
    p = os.path.join(workdir, "raid.json")
    if not os.path.exists(p):
        sys.exit(f"missing {p} — run `python3 ingest.py init` first")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_config(workdir, cfg):
    with open(os.path.join(workdir, "raid.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)


def report_codes(cfg):
    """Report codes of this raid ID, chronological. Multi-report = one lockout
    split over several nights, consolidated into ONE debrief (cfg "reports").
    Single-report configs (cfg "report") keep working unchanged."""
    codes = cfg.get("reports") or ([cfg["report"]] if cfg.get("report") else [])
    return list(codes)


def killing_blow(d):
    """WCL `killingBlow` may be a dict ({name, guid, ...}), a bare ability-name
    str, or None depending on endpoint/version. Always return a dict so callers
    can `.get("name")`/`.get("guid")` safely. A truthy str slips past the old
    `… or {}` idiom and crashed `.get` with AttributeError — this guards it."""
    kb = d.get("killingBlow")
    if isinstance(kb, dict):
        return kb
    if isinstance(kb, str):
        return {"name": kb}
    return {}


# -------------------------------------------------------------------- backend

class Backend:
    """Sqlite store: WCL response cache + extracted tables (schema.sql)."""

    def __init__(self, workdir):
        os.makedirs(workdir, exist_ok=True)
        self.workdir = workdir
        self.con = sqlite3.connect(os.path.join(workdir, "raid.db"))
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        # WAL = single writer: concurrent workers (sharded top-detail,
        # progress samplers) must WAIT, not crash with 'database is locked'.
        # 120s: a worker's per-parse implicit transaction can hold the write
        # lock for tens of seconds while upserting 20-30k event rows
        # (25-player enemy-debuff streams) — 30s was measured insufficient.
        self.con.execute("PRAGMA busy_timeout=120000")
        with open(SCHEMA, encoding="utf-8") as f:
            self.con.executescript(f.read())

    def upsert(self, table, row, pk):
        cols = list(row.keys())
        ph = ",".join("?" for _ in cols)
        setc = ",".join(f"{c}=excluded.{c}" for c in cols if c not in pk)
        if setc:
            sql = (f"INSERT INTO {table} ({','.join(cols)}) VALUES ({ph}) "
                   f"ON CONFLICT ({','.join(pk)}) DO UPDATE SET {setc}")
        else:
            # All-PK table: an empty DO UPDATE SET is invalid SQL.
            sql = f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) VALUES ({ph})"
        self.con.execute(sql, [row[c] for c in cols])

    def commit(self):
        self.con.commit()

    def count(self, table):
        return self.con.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]

    def cache_get(self, key):
        r = self.con.execute("SELECT response FROM wcl_raw WHERE query_hash=?",
                             (key,)).fetchone()
        if not r:
            return None
        raw = r["response"]
        # Retro-compat: legacy rows are TEXT (sqlite -> str), new rows are
        # lzma-compressed JSON BLOB (sqlite -> bytes). A decompress failure on a
        # corrupt blob MUST crash loudly here — never swallow it into a silent
        # cache-miss (that would mask a paid re-fetch), per the loud-failure rule.
        return (lzma.decompress(raw).decode("utf-8")
                if isinstance(raw, (bytes, bytearray)) else raw)

    def done(self, code, fid, what):
        return self.con.execute(
            "SELECT 1 FROM done_marker WHERE report=? AND fight_id=? AND what=?",
            (code, fid, what)).fetchone() is not None

    def mark(self, code, fid, what):
        self.con.execute("INSERT OR IGNORE INTO done_marker VALUES (?,?,?)",
                         (code, fid, what))
        self.commit()


# ----------------------------------------------------------------- WCL client

_TOKEN = None
_LAST = [0.0]
THROTTLE_S = 0.20   # gentle spacing between live calls

# Seamless quota management (WCL rateLimitData: points per rolling hour).
# Every QUOTA_CHECK_EVERY live calls the client polls rateLimitData (~1 pt)
# and AUTO-PAUSES until the hourly reset when spent > QUOTA_SOFT_PCT of the
# limit. A 429 sleeps until reset instead of giving up — abandoned requests
# would otherwise produce silently-partial extractions.
QUOTA_CHECK_EVERY = int(os.environ.get("WCL_QUOTA_CHECK_EVERY", "150"))
QUOTA_SOFT_PCT = float(os.environ.get("WCL_QUOTA_SOFT_PCT", "0.85"))
_LIVE_N = [0]          # live calls since process start
_QUOTA_GUARD = [False]  # re-entrancy guard (quota checks use the same client)

OAUTH_URL = "https://www.warcraftlogs.com/oauth/token"
# MoP Classic logs live on the classic endpoint; the www OAuth token works here.
API_URL = "https://classic.warcraftlogs.com/api/v2/client"


def _token():
    global _TOKEN
    if _TOKEN:
        return _TOKEN
    cid = os.environ.get("WCL_CLIENT_ID")
    secret = os.environ.get("WCL_CLIENT_SECRET")
    if not cid or not secret:
        sys.exit("Set WCL_CLIENT_ID / WCL_CLIENT_SECRET (env or .env file — "
                 "see README, never commit them).")
    data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    req = urllib.request.Request(OAUTH_URL, data=data)
    req.add_header("Authorization", "Basic " +
                   base64.b64encode(f"{cid}:{secret}".encode()).decode())
    # OAuth goes through the same gateway as the API: it 504s during WCL
    # incidents too. Retry transients — a worker must not die at boot.
    last_err = None
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                _TOKEN = json.load(r)["access_token"]
            return _TOKEN
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                sys.exit("WCL OAuth refused (401/403): check WCL_CLIENT_ID / "
                         "WCL_CLIENT_SECRET")
            last_err = e
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            last_err = e
        wait = min(60, 5 * 2 ** attempt)
        print(f"[wcl] OAuth transient ({last_err}) — retry in {wait}s "
              f"({attempt + 1}/6)", flush=True)
        time.sleep(wait)
    sys.exit(f"WCL OAuth failed after 6 attempts: {last_err}")


def _post_gql(query, variables):
    """One throttled POST, no quota logic (used by the quota check itself)."""
    body = json.dumps({"query": query, "variables": variables}).encode()
    dt = time.monotonic() - _LAST[0]
    if dt < THROTTLE_S:
        time.sleep(THROTTLE_S - dt)
    req = urllib.request.Request(API_URL, data=body)
    req.add_header("Authorization", "Bearer " + _token())
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            _LAST[0] = time.monotonic()
            return json.load(r)
    except urllib.error.HTTPError as e:
        _LAST[0] = time.monotonic()
        # Transients seen in production: 5xx app/gateway + Cloudflare 52x
        # (WCL sits behind CF). A 504 NOT retried = a silently lost slice.
        if e.code in (429, 500, 502, 503, 504, 520, 522, 524):
            return {"_http": e.code}
        try:
            return json.load(e)
        except Exception:
            return {"errors": [{"http": e.code}]}


def _quota_live():
    """Uncached rateLimitData (never goes through gql/cache: must be fresh)."""
    r = _post_gql("{ rateLimitData { limitPerHour pointsSpentThisHour "
                  "pointsResetIn } }", {})
    d = r.get("data") if isinstance(r, dict) else None
    return (d or {}).get("rateLimitData") or {}


def _quota_pause_if_needed(force=False):
    """Poll quota every QUOTA_CHECK_EVERY live calls (and on the very first);
    sleep through the hourly reset when above the soft threshold."""
    if _QUOTA_GUARD[0]:
        return
    _LIVE_N[0] += 1
    if not force and _LIVE_N[0] % QUOTA_CHECK_EVERY != 1:
        return
    _QUOTA_GUARD[0] = True
    try:
        q = _quota_live()
    finally:
        _QUOTA_GUARD[0] = False
    spent, limit = q.get("pointsSpentThisHour", 0), q.get("limitPerHour", 0)
    if limit and spent >= QUOTA_SOFT_PCT * limit:
        wait = int(q.get("pointsResetIn", 600)) + 5
        print(f"[quota] {spent:.0f}/{limit} points (>{QUOTA_SOFT_PCT:.0%}) — "
              f"auto-pausing {wait}s until the hourly reset, then resuming",
              flush=True)
        time.sleep(wait)


def _live_gql(query, variables):
    _quota_pause_if_needed()
    for attempt in range(6):
        r = _post_gql(query, variables)
        code = r.get("_http") if isinstance(r, dict) else None
        if code is None:
            return r
        if code == 429:
            # Hourly quota exhausted: backoff cannot fix it — sleep to reset.
            if _QUOTA_GUARD[0]:
                return {"errors": [{"http": 429}]}
            _QUOTA_GUARD[0] = True
            try:
                q = _quota_live()
            finally:
                _QUOTA_GUARD[0] = False
            wait = int(q.get("pointsResetIn", 0) or 0) + 5
            if wait <= 5 or wait > 3700:
                wait = 60 * (attempt + 1)      # 429 without quota info: step up
            print(f"[quota] HTTP 429 — sleeping {wait}s until reset "
                  f"(attempt {attempt + 1}/6)", flush=True)
            time.sleep(wait)
            continue
        time.sleep(2 ** attempt)               # 5xx/52x transient
    return {"errors": [{"throttled": True}]}


def gql(be, query, **variables):
    """Cached GraphQL: parsed JSON; live-fetches + stores on cache miss."""
    key = hashlib.sha256((query + json.dumps(variables, sort_keys=True)).encode()).hexdigest()
    hit = be.cache_get(key)
    if hit:
        return json.loads(hit)
    resp = _live_gql(query, variables)
    if "data" in resp and resp.get("data"):    # only cache real successes
        be.upsert("wcl_raw", {
            "query_hash": key, "query": query,
            "variables": json.dumps(variables, sort_keys=True),
            # lzma BLOB (~x21 smaller than raw JSON). sqlite binds bytes->BLOB
            # natively; cache_get decompresses on read. wcl_raw is write-once so
            # this never round-trips a read-modify cycle.
            "response": lzma.compress(json.dumps(resp).encode("utf-8")),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }, ["query_hash"])
        be.commit()
    elif resp.get("errors"):
        # Loud, not silent: an uncached error means THIS extraction is partial.
        print(f"[wcl] WARNING: request failed (not cached): "
              f"{json.dumps(resp['errors'])[:200]} — re-run the command to "
              f"retry this slice for free", flush=True)
    return resp


def unwrap(resp, *path):
    cur = resp.get("data") if isinstance(resp, dict) else None
    for p in path:
        if cur is None:
            return None
        cur = cur.get(p) if isinstance(cur, dict) else None
    return cur


def json_field(v):
    return json.loads(v) if isinstance(v, str) else v


def quota():
    return _quota_live()


# ----------------------------------------------------------------- fetchers

def fetch_events(be, code, fid, fs, fe, data_type, extra=""):
    """All events of a type for one fight, paginated, WITH dedup.

    Pagination boundaries (nextPageTimestamp) duplicate events (x1.1-2 volume):
    dedup on a wide key is mandatory.

    `extra` = raw clause, e.g. ',hostilityType:Enemies' or ',sourceID:12' or
    ',filterExpression:"ability.id IN (1,2)"'.

    KNOWN SILENT FAILURE (classic API): `dataType:DamageTaken` combined with
    `targetID:X` returns ZERO events with no error. Fetch FULL and filter
    code-side instead. Always validate an extraction by an EXPECTED POSITIVE
    (a tank with zero damage taken = alarm), never by the absence of error.
    """
    out, seen, start = [], set(), fs
    while start is not None:
        q = ('{ reportData { report(code:"%s"){ events(startTime:%d,endTime:%d,'
             'fightIDs:[%d],dataType:%s%s,limit:10000){ data nextPageTimestamp } } } }'
             % (code, start, fe, fid, data_type, extra))
        ev = unwrap(gql(be, q), "reportData", "report", "events")
        if not ev:
            break
        for e in json_field(ev.get("data")) or []:
            k = (e.get("timestamp"), e.get("type"), e.get("sourceID"),
                 e.get("targetID"), e.get("abilityGameID"), e.get("amount"),
                 e.get("stack"), e.get("targetInstance"))
            if k in seen:
                continue
            seen.add(k)
            out.append(e)
        start = ev.get("nextPageTimestamp")
    return out


def fetch_table(be, code, fid, fs, fe, data_type, extra=""):
    q = ('{ reportData { report(code:"%s"){ table(startTime:%d,endTime:%d,'
         'fightIDs:[%d],dataType:%s%s) } } }' % (code, fs, fe, fid, data_type, extra))
    return json_field(unwrap(gql(be, q), "reportData", "report", "table")) or {}


def fetch_graph(be, code, fid, fs, fe, data_type, extra=""):
    """Bucketed per-player series (+ 'Total'). dataType Resources with
    sourceID:X,abilityID:100 = mana % timeline for one actor. ~1 pt/request."""
    q = ('{ reportData { report(code:"%s"){ graph(startTime:%d,endTime:%d,'
         'fightIDs:[%d],dataType:%s%s) } } }' % (code, fs, fe, fid, data_type, extra))
    g = json_field(unwrap(gql(be, q), "reportData", "report", "graph")) or {}
    return g.get("data", g)


def report_meta(be, code):
    """Report header: title, zone (id+name), bounds. Zone is AUTO-DETECTED here —
    never ask the user for it."""
    q = ('{ reportData { report(code:"%s"){ title startTime endTime '
         'zone { id name } } } }' % code)
    rep = unwrap(gql(be, q), "reportData", "report")
    if not rep:
        sys.exit(f"report not found: {code}")
    return rep


def zone_encounters(be, zone_id):
    """(id, name) list for a zone id."""
    encs = unwrap(gql(be, '{ worldData { zone(id:%d){ encounters { id name } } } }'
                      % zone_id), "worldData", "zone", "encounters")
    return encs or []


def ingest_actors(be, code):
    """masterData.actors -> actor_name table (resolves NPC + pet target names)."""
    q = ('{ reportData { report(code:"%s"){ masterData { actors '
         '{ id name type subType } } } } }' % code)
    actors = unwrap(gql(be, q), "reportData", "report", "masterData", "actors") or []
    for a in actors:
        be.upsert("actor_name", {
            "report": code, "actor_id": a.get("id"), "name": a.get("name"),
            "type": a.get("type"), "sub_type": a.get("subType"),
        }, ["report", "actor_id"])
    be.commit()
    return {a.get("id"): a.get("name") for a in actors}


def rankings_reports(be, enc_id, metric, diff, size, topn,
                     class_name=None, spec_name=None):
    """Top (code, fightID) pairs from characterRankings, deduped."""
    cs = ""
    if class_name and spec_name:
        cs = ', className:"%s", specName:"%s"' % (class_name, spec_name)
    q = ('{ worldData { encounter(id:%d){ characterRankings(metric:%s, '
         'difficulty:%d, size:%d%s) } } }' % (enc_id, metric, diff, size, cs))
    cr = json_field(unwrap(gql(be, q), "worldData", "encounter",
                           "characterRankings")) or {}
    out, seen = [], set()
    for e in cr.get("rankings", []):
        rep = e.get("report", {})
        ck, fid = rep.get("code"), rep.get("fightID")
        if ck and (ck, fid) not in seen:
            seen.add((ck, fid))
            out.append((ck, fid))
        if len(out) >= topn:
            break
    return out


def fmt_dur(s):
    return f"{int(s // 60)}:{int(s % 60):02d}"
