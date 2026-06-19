#!/usr/bin/env python3
"""One-shot migration: compress the `wcl_raw.response` cache to lzma BLOBs.

Tier 0 of the 3-tier model. The WCL response cache is write-once JSON text that
dominates disk (~62%). lzma shrinks it ~x21 losslessly; cache_get (wcl.py)
decompresses on read and stays retro-compatible with legacy TEXT rows, so this
migration is OPTIONAL for correctness — it only reclaims disk.

Lazy migration is impossible (wcl_raw is write-once: old rows are never
rewritten), hence this explicit one-shot pass.

Safety (stdlib only, no pip):
  * Backs up raid.db (+ -wal/-shm) before touching anything.
  * Compresses every TEXT row inside ONE transaction; per row it verifies an
    in-memory round-trip AND re-reads the stored BLOB (catches sqlite typing
    surprises). Any mismatch -> rollback + abort, nothing committed.
  * VACUUM reclaims freed pages (needs ~2x the file size as temp space).
  * Bumps `PRAGMA user_version` to 1 = "lzma-migrated" (anti-double-migration
    guard; re-running skips already-migrated dbs unless --force).

Usage:
    python3 migrate_lzma.py --dry-run <workdir|raid.db> [...]   # verify only
    python3 migrate_lzma.py <workdir|raid.db> [...]             # migrate
    python3 migrate_lzma.py --all-under ~/raids                 # every raid.db
    python3 migrate_lzma.py --force <...>                       # ignore guard
"""
import argparse
import glob
import lzma
import os
import shutil
import sqlite3
import sys

SCHEMA_VERSION_LZMA = 1   # user_version once a db's wcl_raw is lzma-migrated


def _db_path(arg):
    """Resolve a CLI arg (workdir or direct raid.db path) to a raid.db path."""
    if arg.endswith(".db"):
        return os.path.abspath(arg)
    return os.path.abspath(os.path.join(arg, "raid.db"))


def _human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}"
        n /= 1024


def _backup(db):
    """Copy raid.db and its WAL sidecars to *.pre-lzma.bak (consistent set)."""
    made = []
    for suffix in ("", "-wal", "-shm"):
        src = db + suffix
        if os.path.exists(src):
            dst = db + ".pre-lzma.bak" + suffix
            if os.path.exists(dst):
                sys.exit(f"[abort] backup already exists: {dst} "
                         f"(remove it or pass --force to overwrite)")
            shutil.copy2(src, dst)
            made.append(dst)
    return made


def dry_run(db):
    if not os.path.exists(db):
        print(f"[skip] {db}: not found")
        return
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT query_hash, response FROM wcl_raw WHERE typeof(response)='text'"
    ).fetchall()
    n_text = len(rows)
    n_blob = con.execute(
        "SELECT COUNT(*) FROM wcl_raw WHERE typeof(response)='blob'").fetchone()[0]
    ver = con.execute("PRAGMA user_version").fetchone()[0]
    con.close()
    orig = comp = 0
    for _, resp in rows:
        b = resp.encode("utf-8")
        c = lzma.compress(b)
        if lzma.decompress(c).decode("utf-8") != resp:        # round-trip gate
            sys.exit(f"[FAIL] round-trip mismatch in {db} (dry-run)")
        orig += len(b)
        comp += len(c)
    ratio = (orig / comp) if comp else 0
    print(f"[dry-run] {db}")
    print(f"    user_version={ver}  text_rows={n_text}  blob_rows={n_blob}")
    if n_text:
        print(f"    response: {_human(orig)} -> {_human(comp)}  (/{ratio:.1f}, "
              f"round-trip OK on all {n_text} rows)")
    else:
        print("    nothing to compress (already all-BLOB)")


def migrate(db, force=False):
    if not os.path.exists(db):
        print(f"[skip] {db}: not found")
        return
    con = sqlite3.connect(db)
    ver = con.execute("PRAGMA user_version").fetchone()[0]
    if ver >= SCHEMA_VERSION_LZMA and not force:
        print(f"[skip] {db}: already migrated (user_version={ver})")
        con.close()
        return
    # Flush WAL into the main file so the backup is a consistent snapshot.
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()

    size_before = os.path.getsize(db)
    backups = _backup(db)
    print(f"[migrate] {db}  (backup: {', '.join(os.path.basename(b) for b in backups)})")

    con = sqlite3.connect(db)
    keys = [r[0] for r in con.execute(
        "SELECT query_hash FROM wcl_raw WHERE typeof(response)='text'")]
    try:
        con.execute("BEGIN")
        for k in keys:
            orig = con.execute(
                "SELECT response FROM wcl_raw WHERE query_hash=?", (k,)).fetchone()[0]
            comp = lzma.compress(orig.encode("utf-8"))
            if lzma.decompress(comp).decode("utf-8") != orig:
                raise ValueError(f"round-trip mismatch (in-memory) for key {k}")
            con.execute("UPDATE wcl_raw SET response=? WHERE query_hash=?", (comp, k))
            back = con.execute(
                "SELECT response FROM wcl_raw WHERE query_hash=?", (k,)).fetchone()[0]
            if (not isinstance(back, (bytes, bytearray)) or bytes(back) != comp
                    or lzma.decompress(back).decode("utf-8") != orig):
                raise ValueError(f"stored-blob verification failed for key {k}")
        con.commit()
    except Exception as e:
        con.rollback()
        con.close()
        sys.exit(f"[ABORT] {db}: {e} — rolled back, nothing changed. "
                 f"Backups left in place: {backups}")
    # VACUUM (reclaims freed pages) must run outside a transaction.
    con.execute("VACUUM")
    con.execute(f"PRAGMA user_version={SCHEMA_VERSION_LZMA}")
    con.commit()
    con.close()
    size_after = os.path.getsize(db)
    print(f"    {len(keys)} rows compressed  |  {_human(size_before)} -> "
          f"{_human(size_after)}  (freed {_human(size_before - size_after)})")
    print(f"    user_version set to {SCHEMA_VERSION_LZMA}. Verify, then delete "
          f"backups: rm {db}.pre-lzma.bak*")


def main():
    ap = argparse.ArgumentParser(description="lzma-compress wcl_raw cache (Tier 0)")
    ap.add_argument("targets", nargs="*", help="workdir(s) or raid.db path(s)")
    ap.add_argument("--all-under", metavar="DIR",
                    help="migrate every <DIR>/*/raid.db")
    ap.add_argument("--dry-run", action="store_true",
                    help="verify round-trip + report sizes, write nothing")
    ap.add_argument("--force", action="store_true",
                    help="re-migrate even if user_version already set / overwrite backup")
    args = ap.parse_args()

    dbs = [_db_path(t) for t in args.targets]
    if args.all_under:
        dbs += sorted(glob.glob(os.path.join(os.path.expanduser(args.all_under),
                                             "*", "raid.db")))
    if not dbs:
        ap.error("nothing to do: pass workdir(s)/raid.db path(s) or --all-under DIR")

    for db in dbs:
        if args.dry_run:
            dry_run(db)
        else:
            migrate(db, force=args.force)


if __name__ == "__main__":
    main()
