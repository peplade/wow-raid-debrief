#!/usr/bin/env python3
"""verify.py — GATE de re-dérivation + canary claims du CR de SOIRÉE (raid, multi-joueurs).

PORT du squelette de wow-player-debrief/engine/verify.py (classe `Findings`, comparaison
relative `cmp_rel`, mécanisme `apply_canaries`, verdict GO/NO-GO, exit codes) — mais la
re-dérivation est RÉÉCRITE pour les tables RAID (`pull`, `death`, `deep_dmg_taken`,
`composition`), pas les tables joueur `jp_*`.

PHILOSOPHIE : on ne fait JAMAIS confiance au digest. Chaque chiffre affiché est RECALCULÉ
depuis `raid.db` (events/faits bruts), indépendamment, AVEC LA MÊME FORMULE des deux côtés ;
NO-GO sur écart. Échec BRUYANT (un chiffre non re-dérivable est marqué SKIP avec la raison,
jamais avalé silencieusement). Read-only sur tous les originaux ; le seul write est
`verification.md` dans le workdir.

CE QUE LE GATE RE-DÉRIVE (au minimum, cf brief) :
  1. PAR BOSS — nb de pulls + durée du kill, depuis `pull` (group (encounter_id, difficulty)).
     Comparé à wipe_forensics.json bosses["<boss>|diff<N>"] {n_pulls, courbe[kill].duration_s}.
  2. MORTS — (a) le n_deaths RAW par pull du digest == COUNT(death) (faute = digest pourri),
     (b) INVARIANT 6 (morts qualifiées = toutes sur kills + 1-2 premières d'un wipe) recalculé
     indépendamment depuis `death`, avec ses garde-fous (qualifié ≤ raw ; kills comptés entiers).
  3. DÉGÂTS ÉVITABLES par joueur/boss — depuis `deep_dmg_taken.amount` (EFFECTIF) joint au
     mechanics_ref de la zone, JAMAIS depuis le graph WCL DamageTaken (invariant CHANGELOG
     1.2.13 « DTPS poison » : le graph somme la valeur NOMINALE ~1e9/hit). Comparé à
     player_progress.json[bkey][player].avoidable. Match par NOM de mécanique (le suffixe de
     classe du digest peut avoir dérivé après une re-classification du ref — cf finding Sha rift) ;
     une mécanique absente du ref courant => SKIP explicite, jamais un faux PASS/FAIL.
  4. BENCHMARK top-parse — chaque fight de kill a une réf de percentile same-encounter dans
     percentiles.json (clé "<report>:<fid>", champ rank_percent peuplé pour ≥1 joueur).

CANARY CLAIMS — détecteur de vérificateur lazy
    --canaries fichier.json : liste de claims FAUX injectés. Le gate DOIT TOUS les flagger
    (les re-dériver en MISMATCH). Si UN SEUL canary passe (n'est pas détecté) → verdict VOID.

SORTIE : {workdir}/verification.md (rapport opposable) + verdict GO/NO-GO/VOID imprimé ;
exit≠0 si NO-GO ou VOID.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict

# tolérances de re-dérivation
DMG_TOL = 0.01     # 1 % sur une somme de dégâts recalculée exactement depuis deep_dmg_taken
DUR_TOL = 0.01     # 1 % sur une durée de kill (pull.duration_s recopié verbatim — doit coller)


# ════════════════════════════════ collecteur de findings ════════════════════════════════
class Findings:
    """accumulateur de records atomiques. Chaque record porte un schéma de preuve FORCÉ
    {claim, recalc, match}. Une preuve vide est interdite côté re-dérivation."""

    def __init__(self):
        self.recs = []  # (gate, level, name, message, proof)

    def add(self, gate, level, name, message, proof=None):
        self.recs.append((gate, level, name, message, proof or {}))

    def ok(self, gate, name, claim, got, detail=""):
        self.add(gate, "OK", name, f"{name}: CR={claim} recalc={got}{(' ' + detail) if detail else ''}",
                 {"claim": claim, "recalc": got, "match": True})

    def block(self, gate, name, claim, got, detail=""):
        self.add(gate, "BLOCKER", name, f"{name}: CR={claim} recalc={got}{(' ' + detail) if detail else ''}",
                 {"claim": claim, "recalc": got, "match": False})

    def skip(self, gate, name, why):
        self.add(gate, "SKIP", name, f"{name}: {why}", None)

    def cmp_rel(self, gate, name, claim, got, tol, detail=""):
        """diff relatif → OK/BLOCKER. got=None → SKIP (non re-dérivable, PAS un échec muet)."""
        if got is None:
            self.skip(gate, name, f"non re-dérivable (CR={claim})")
            return None
        if claim in (0, None):
            ok = got in (0, None)
            (self.ok if ok else self.block)(gate, name, claim, got, detail)
            return ok
        d = abs(got - claim) / abs(claim)
        ok = d <= tol
        (self.ok if ok else self.block)(gate, name, claim, got, f"(écart {d*100:.2f}%) {detail}".strip())
        return ok

    def cmp_eq(self, gate, name, claim, got, detail=""):
        ok = (claim == got)
        (self.ok if ok else self.block)(gate, name, claim, got, detail)
        return ok

    def by_level(self, level):
        return [r for r in self.recs if r[1] == level]

    def count(self, level):
        return len(self.by_level(level))


# ════════════════════════════════ contexte (config + db + ref) ════════════════════════════════
def _loadj(path):
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else None


class Ctx:
    """Charge raid.json, ouvre raid.db (read-only), résout le mechanics_ref et les digests.
    Stdlib only — pas de dépendance au backend WCL (le gate ne touche jamais le réseau)."""

    def __init__(self, workdir):
        self.workdir = os.path.abspath(os.path.expanduser(workdir))
        cfg_p = os.path.join(self.workdir, "raid.json")
        if not os.path.exists(cfg_p):
            sys.exit(f"missing {cfg_p} — ce n'est pas un workdir de soirée raid")
        self.cfg = json.load(open(cfg_p, encoding="utf-8"))
        self.reports = self.cfg.get("reports") or (
            [self.cfg["report"]] if self.cfg.get("report") else [])
        db_p = os.path.join(self.workdir, "raid.db")
        if not os.path.exists(db_p):
            sys.exit(f"missing {db_p}")
        # read-only : le gate ne doit JAMAIS muter la db originale
        self.con = sqlite3.connect(f"file:{db_p}?mode=ro", uri=True)
        self.con.row_factory = sqlite3.Row
        self.mech = self._load_mech()
        # bkey "<boss>|diff<N>" -> encounter_id (clé d'affichage des digests bossesés)
        self.bkey_enc = {}
        self.bkey_split = {}  # bkey -> (boss, difficulty)
        for p in self.con.execute("SELECT DISTINCT boss, difficulty, encounter_id FROM pull"):
            bk = "%s|diff%d" % (p["boss"], p["difficulty"])
            self.bkey_enc[bk] = p["encounter_id"]
            self.bkey_split[bk] = (p["boss"], p["difficulty"])

    def _load_mech(self):
        """mechanics_ref : workdir/refs d'abord (la copie effectivement utilisée pour générer
        les digests), sinon le ref canonique du skill. Forme : {enc:int -> {ability_id:int -> m}}.
        On garde aussi name->{ability_id} (TOUTES classes) pour re-dériver par nom (drift-safe)."""
        cands = [
            os.path.join(self.workdir, "refs", "mechanics_ref.json"),
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "references", "zones",
                         self.cfg.get("zone_slug", "soo"), "mechanics_ref.json"),
        ]
        raw = None
        self.mech_ref_path = None
        for p in cands:
            if os.path.exists(p):
                raw = json.load(open(p, encoding="utf-8"))
                self.mech_ref_path = p
                break
        if raw is None:
            return {}
        out = {}
        self.name2ids = defaultdict(lambda: defaultdict(set))   # enc -> name -> {ids}
        for e, v in raw.items():
            if not e.isdigit():
                continue
            enc = int(e)
            out[enc] = {}
            for k, m in (v.get("mechanics") or {}).items():
                if not k.isdigit():
                    continue
                out[enc][int(k)] = m
                if m.get("name"):
                    self.name2ids[enc][m["name"]].add(int(k))
        return out

    def dig(self, name):
        """chemin d'un digest sous digests/analysis/ (ou digests/ pour percentiles)."""
        if name == "percentiles.json":
            return os.path.join(self.workdir, "digests", name)
        return os.path.join(self.workdir, "digests", "analysis", name)

    # ---- helpers de re-dérivation ----
    def player_actor(self, report, fid, player):
        r = self.con.execute(
            "SELECT actor_id FROM composition WHERE report=? AND fight_id=? AND player_name=?",
            (report, fid, player)).fetchone()
        return r["actor_id"] if r else None

    def boss_pulls(self, boss, diff):
        return [dict(r) for r in self.con.execute(
            "SELECT report, fight_id, kill, duration_s, pull_number, encounter_id "
            "FROM pull WHERE boss=? AND difficulty=? ORDER BY start_time", (boss, diff))]


# ════════════════════════════════ re-dérivations atomiques ════════════════════════════════
def rederive_avoidable_player_mech(ctx, bkey, player, mech_name):
    """(hits, dmg) re-dérivés pour (joueur, mécanique) sur un boss = somme de
    deep_dmg_taken.amount (EFFECTIF — JAMAIS le graph WCL DamageTaken empoisonné) sur les
    ability_id qui portent ce NOM de mécanique dans le ref courant, sur tous les pulls du boss.
    Retourne None si le nom n'existe pas dans le ref courant (=> SKIP : non re-dérivable).
    Reproduit EXACTEMENT player_progress.py: SUM(amount) (PAS +absorbed), group target×ability."""
    enc = ctx.bkey_enc.get(bkey)
    if enc is None:
        return None
    ids = ctx.name2ids.get(enc, {}).get(mech_name)
    if not ids:
        return None
    idlist = ",".join(str(i) for i in ids)
    boss, diff = ctx.bkey_split[bkey]
    hits, dmg = 0, 0.0
    for p in ctx.boss_pulls(boss, diff):
        aid = ctx.player_actor(p["report"], p["fight_id"], player)
        if aid is None:
            continue
        r = ctx.con.execute(
            f"SELECT COUNT(*) n, COALESCE(SUM(amount),0) s FROM deep_dmg_taken "
            f"WHERE report=? AND fight_id=? AND target_id=? AND ability_id IN ({idlist})",
            (p["report"], p["fight_id"], aid)).fetchone()
        hits += r["n"] or 0
        dmg += r["s"] or 0
    return [hits, dmg]


def qualified_deaths_pull(ctx, report, fid, kill):
    """INVARIANT 6 : morts qualifiées d'un pull = TOUTES si kill, sinon les 1-2 premières
    (par death_time) d'un wipe (déclencheur probable). Le compteur brut ment sur un wipe."""
    if kill:
        return ctx.con.execute(
            "SELECT COUNT(*) n FROM death WHERE report=? AND fight_id=?", (report, fid)).fetchone()["n"]
    return ctx.con.execute(
        "SELECT COUNT(*) n FROM (SELECT 1 FROM death WHERE report=? AND fight_id=? "
        "ORDER BY death_time LIMIT 2)", (report, fid)).fetchone()["n"]


def raw_deaths_pull(ctx, report, fid):
    return ctx.con.execute(
        "SELECT COUNT(*) n FROM death WHERE report=? AND fight_id=?", (report, fid)).fetchone()["n"]


# ════════════════════════════════ GATE — par boss : pulls + durée kill ════════════════════
def gate_boss(ctx, F):
    gate = "boss(pulls+kill_dur)"
    wf = _loadj(ctx.dig("wipe_forensics.json"))
    if not wf or not wf.get("bosses"):
        F.skip(gate, "wipe_forensics.json", "absent (analyze pas lancé) — pas de table boss à vérifier")
        return
    for bkey, bv in wf["bosses"].items():
        boss, diff = ctx.bkey_split.get(bkey, (bv.get("boss"), bv.get("difficulty")))
        pulls = ctx.boss_pulls(boss, diff)
        # (1) nb de pulls = toutes lignes pull (kills + wipes) de ce (boss,diff)
        F.cmp_eq(gate, f"[{bkey}] n_pulls", bv.get("n_pulls"), len(pulls))
        # (2) durée du kill = pull.duration_s du (premier) kill ; courbe[kill].duration_s
        kills = [p for p in pulls if p["kill"]]
        courbe = {c.get("pull"): c for c in (bv.get("courbe") or [])}
        if kills:
            kp = kills[0]
            cc = courbe.get(kp["pull_number"])
            claim = cc.get("duration_s") if cc else None
            if claim is None:
                F.skip(gate, f"[{bkey}] kill duration", "kill absent de la courbe du digest")
            else:
                F.cmp_rel(gate, f"[{bkey}] kill duration_s", claim, kp["duration_s"], DUR_TOL)
        # (3) cohérence par-pull de la courbe : durée de CHAQUE pull (pas que le kill)
        bad = []
        for c in (bv.get("courbe") or []):
            match = next((p for p in pulls if p["pull_number"] == c.get("pull")), None)
            if match is None:
                bad.append(f"pull#{c.get('pull')} absent de la db")
            elif c.get("duration_s") is not None and match["duration_s"]:
                if abs(match["duration_s"] - c["duration_s"]) / match["duration_s"] > DUR_TOL:
                    bad.append(f"pull#{c.get('pull')} dur {c['duration_s']}≠{match['duration_s']}")
        if bad:
            F.block(gate, f"[{bkey}] courbe durées par-pull", "cohérent", "; ".join(bad[:4]))
        else:
            F.ok(gate, f"[{bkey}] courbe durées par-pull", len(bv.get("courbe") or []),
                 len(bv.get("courbe") or []), "toutes les durées par-pull collent")


# ════════════════════════════════ GATE — morts (raw + invariant 6) ════════════════════════
def gate_deaths(ctx, F):
    gate = "deaths"
    wf = _loadj(ctx.dig("wipe_forensics.json"))
    # (a) n_deaths RAW par pull du digest == COUNT(death) (faute => digest pourri / mal extrait)
    if wf and wf.get("bosses"):
        bad = []
        npulls = 0
        for bkey, bv in wf["bosses"].items():
            boss, diff = ctx.bkey_split.get(bkey, (bv.get("boss"), bv.get("difficulty")))
            for c in (bv.get("courbe") or []):
                p = next((x for x in ctx.boss_pulls(boss, diff)
                          if x["pull_number"] == c.get("pull")), None)
                if p is None or c.get("n_deaths") is None:
                    continue
                npulls += 1
                got = raw_deaths_pull(ctx, p["report"], p["fight_id"])
                if got != c["n_deaths"]:
                    bad.append(f"{bkey} pull#{c['pull']}: digest n_deaths={c['n_deaths']} ≠ COUNT(death)={got}")
        if bad:
            F.block(gate, "n_deaths RAW par pull == COUNT(death)", "tous égaux",
                    f"{len(bad)} écart(s): " + " ; ".join(bad[:4]))
        else:
            F.ok(gate, "n_deaths RAW par pull == COUNT(death)", npulls, npulls,
                 "le compteur brut de chaque pull colle au log")
    else:
        F.skip(gate, "n_deaths RAW", "wipe_forensics.json absent")

    # (b) INVARIANT 6 — morts qualifiées re-dérivées indépendamment, avec garde-fous bruyants.
    #     Pas un champ unique de digest à matcher ; on PROUVE la cohérence interne de la règle
    #     (qualifié ≤ raw ; kills => qualifié == raw ; wipe => qualifié == min(raw,2)).
    viol = []
    tot_qual = tot_raw = tot_kill = 0
    for p in ctx.con.execute("SELECT report, fight_id, kill FROM pull"):
        raw = raw_deaths_pull(ctx, p["report"], p["fight_id"])
        qual = qualified_deaths_pull(ctx, p["report"], p["fight_id"], p["kill"])
        tot_raw += raw
        tot_qual += qual
        if p["kill"]:
            tot_kill += raw
            if qual != raw:
                viol.append(f"kill {p['report']}#{p['fight_id']}: qual={qual}≠raw={raw}")
        else:
            if qual != min(raw, 2):
                viol.append(f"wipe {p['report']}#{p['fight_id']}: qual={qual}≠min(raw,2)={min(raw,2)}")
        if qual > raw:
            viol.append(f"{p['report']}#{p['fight_id']}: qual>{raw} (impossible)")
    if viol:
        F.block(gate, "invariant 6 (morts qualifiées)", "règle respectée", "; ".join(viol[:5]))
    else:
        F.ok(gate, "invariant 6 (morts qualifiées)", f"raw={tot_raw}", f"qualifiées={tot_qual}",
             f"(kills={tot_kill} comptés entiers ; wipes plafonnés à 2)")


# ════════════════════════════════ GATE — dégâts évitables par joueur/boss ════════════════
def gate_avoidable(ctx, F):
    gate = "avoidable(deep_dmg_taken)"
    pp = _loadj(ctx.dig("player_progress.json"))
    if not pp:
        F.skip(gate, "player_progress.json", "absent (analyze pas lancé) — pas d'évitable à vérifier")
        return
    if not ctx.mech:
        F.skip(gate, "mechanics_ref", "ref de mécaniques introuvable (refs/ ni references/zones)")
        return
    matched = mism = skipped = 0
    mism_detail = []
    skip_names = set()
    for bkey, players in pp.items():
        if not isinstance(players, dict):
            continue
        for player, v in players.items():
            if not isinstance(v, dict):
                continue
            for mkey, av in (v.get("avoidable") or {}).items():
                # le digest suffixe le nom par " (classe)" — la classe a pu dériver après
                # re-classification du ref ; on matche sur le NOM seul (drift-safe).
                name = re.sub(r"\s*\([^)]*\)$", "", mkey)
                claim_hits = av.get("hits")
                claim_dmg = av.get("dmg")
                rd = rederive_avoidable_player_mech(ctx, bkey, player, name)
                if rd is None:
                    skipped += 1
                    skip_names.add(f"{bkey}:{name}")
                    continue
                got_hits, got_dmg = rd
                ok_hits = (got_hits == claim_hits)
                ok_dmg = (claim_dmg in (0, None) and got_dmg in (0, None)) or (
                    claim_dmg and abs(got_dmg - claim_dmg) / abs(claim_dmg) <= DMG_TOL)
                if ok_hits and ok_dmg:
                    matched += 1
                else:
                    mism += 1
                    if len(mism_detail) < 8:
                        mism_detail.append(
                            f"{bkey} {player} «{name}»: digest=(h{claim_hits},{claim_dmg}) "
                            f"recalc=(h{got_hits},{round(got_dmg)})")
    if mism:
        F.block(gate, "évitable par joueur/boss (SUM(amount) effectif)", f"{matched} OK",
                f"{mism} MISMATCH: " + " ; ".join(mism_detail))
    elif matched:
        F.ok(gate, "évitable par joueur/boss (SUM(amount) effectif)", matched, matched,
             "re-dérivés depuis deep_dmg_taken.amount (jamais le graph DamageTaken)")
    else:
        F.skip(gate, "évitable par joueur/boss", "aucune entrée re-dérivable")
    if skipped:
        # SKIP BRUYANT : on NOMME ce qu'on n'a pas pu re-dériver (mécanique absente du ref courant
        # = ref re-classé/renommé après génération du digest), jamais avalé en silence.
        ex = ", ".join(sorted(skip_names)[:6])
        F.skip(gate, "évitable — mécaniques non re-dérivables",
               f"{skipped} entrée(s) dont le NOM n'existe pas dans le ref courant "
               f"({os.path.basename(ctx.mech_ref_path or '?')}) — ex: {ex}")


# ════════════════════════════════ GATE — présence benchmark top-parse ════════════════════
def gate_benchmark(ctx, F):
    gate = "benchmark(top-parse)"
    perc = _loadj(ctx.dig("percentiles.json"))
    if perc is None:
        F.skip(gate, "percentiles.json", "absent (percentiles pas fetchés) — pas de réf de champ")
        return
    # chaque fight de KILL doit avoir une entrée "<report>:<fid>" avec rank_percent peuplé.
    missing, empty, ok = [], [], 0
    kills = [dict(r) for r in ctx.con.execute(
        "SELECT report, fight_id, boss, difficulty FROM pull WHERE kill=1")]
    for k in kills:
        key = "%s:%s" % (k["report"], k["fight_id"])
        ent = perc.get(key)
        if ent is None:
            missing.append(f"{k['boss']}|diff{k['difficulty']} ({key})")
            continue
        has = any(isinstance(p, dict) and p.get("rank_percent") is not None
                  for p in (ent.get("players") or []))
        if has:
            ok += 1
        else:
            empty.append(f"{k['boss']} ({key})")
    if missing or empty:
        det = ""
        if missing:
            det += f"{len(missing)} kill(s) SANS entrée: " + " ; ".join(missing[:4])
        if empty:
            det += (" | " if det else "") + f"{len(empty)} sans rank_percent: " + " ; ".join(empty[:4])
        F.block(gate, "réf de percentile par kill", f"{len(kills)} kills", det)
    elif ok:
        F.ok(gate, "réf de percentile par kill", len(kills), ok,
             "chaque kill a une réf same-encounter (rank_percent WCL) dans percentiles.json")
    else:
        F.skip(gate, "réf de percentile par kill", "aucun kill")

    # top_parse (réf same-encounter/spec) présent en db pour servir les comparaisons KPI
    tp = ctx.con.execute("SELECT COUNT(*) n, COUNT(DISTINCT encounter_id) e FROM top_parse").fetchone()
    if tp["n"]:
        F.ok(gate, "table top_parse peuplée", "présente",
             f"{tp['n']} parses / {tp['e']} boss")
    else:
        F.skip(gate, "table top_parse", "vide (benchmark same-spec non fetché)")


# ════════════════════════════════ CANARY ════════════════════════════════
def apply_canaries(ctx, F, canary_path):
    """charge un fichier de canaries (claims FAUX) et VÉRIFIE qu'ils sont TOUS flaggés.
    Format (liste de dicts), chaque canary = un claim faux à détecter :
      [{ "id":"c1", "kind":"boss_pulls",   "bkey":"Sha of Pride|diff4", "claim": 99 },
       { "id":"c2", "kind":"kill_duration","bkey":"Galakras|diff4",     "claim": 12.3 },
       { "id":"c3", "kind":"deaths",       "report":"X","fid":5,"kill":1,"claim": 0 },
       { "id":"c4", "kind":"avoidable",    "bkey":"Sha of Pride|diff4","player":"Cossion",
                    "mech":"Collapsing Rift", "claim_dmg": 1 },
       { "id":"c5", "kind":"raw", "name":"...", "claim":X, "recalc":Y, "tol":0.01 }]
    DÉTECTÉ = re-dérive en MISMATCH (le gate l'aurait tué). UN SEUL raté → verdict VOID."""
    gate = "CANARY"
    cans = _loadj(canary_path)
    if cans is None:
        F.add(gate, "BLOCKER", "canaries", f"fichier canary introuvable: {canary_path}", {})
        return False, 0, 0
    if isinstance(cans, dict):
        cans = cans.get("canaries", [])
    detected = 0
    missed = []
    for can in cans:
        cid = can.get("id", "?")
        kind = can.get("kind")
        recalc = None
        is_mismatch = None
        try:
            if kind == "boss_pulls":
                boss, diff = ctx.bkey_split.get(can["bkey"], (None, None))
                if boss is None:
                    raise ValueError(f"bkey inconnu {can['bkey']}")
                recalc = len(ctx.boss_pulls(boss, diff))
                is_mismatch = recalc != can["claim"]
            elif kind == "kill_duration":
                boss, diff = ctx.bkey_split.get(can["bkey"], (None, None))
                if boss is None:
                    raise ValueError(f"bkey inconnu {can['bkey']}")
                kp = next((p for p in ctx.boss_pulls(boss, diff) if p["kill"]), None)
                recalc = kp["duration_s"] if kp else None
                claim = can["claim"]
                is_mismatch = recalc is None or not claim or \
                    abs(recalc - claim) / abs(claim) > can.get("tol", DUR_TOL)
            elif kind == "deaths":
                recalc = qualified_deaths_pull(ctx, can["report"], can["fid"], can.get("kill", 1))
                is_mismatch = recalc != can["claim"]
            elif kind == "raw_deaths":
                recalc = raw_deaths_pull(ctx, can["report"], can["fid"])
                is_mismatch = recalc != can["claim"]
            elif kind == "avoidable":
                rd = rederive_avoidable_player_mech(ctx, can["bkey"], can["player"], can["mech"])
                if rd is None:
                    # mécanique non re-dérivable : un canary dessus est INVÉRIFIABLE → traiter
                    # comme raté (le gate ne peut pas le tuer honnêtement).
                    F.add(gate, "BLOCKER", f"canary {cid} INVÉRIFIABLE",
                          f"[avoidable] mécanique «{can['mech']}» absente du ref → canary mal posé", {})
                    missed.append(cid)
                    continue
                got_hits, got_dmg = rd
                claim_dmg = can.get("claim_dmg")
                claim_hits = can.get("claim_hits")
                recalc = {"hits": got_hits, "dmg": round(got_dmg)}
                mm = False
                if claim_dmg is not None:
                    mm = mm or (not claim_dmg) or abs(got_dmg - claim_dmg) / abs(claim_dmg) > DMG_TOL
                if claim_hits is not None:
                    mm = mm or (got_hits != claim_hits)
                is_mismatch = mm
            elif kind == "raw":
                recalc = can.get("recalc")
                claim = can.get("claim")
                tol = can.get("tol", DMG_TOL)
                is_mismatch = abs(recalc - claim) / abs(claim) > tol if claim else recalc != claim
            else:
                F.add(gate, "BLOCKER", f"canary {cid}", f"kind inconnu '{kind}' — invérifiable", {})
                missed.append(cid)
                continue
        except Exception as e:
            F.add(gate, "BLOCKER", f"canary {cid}", f"exception pendant la vérif: {e}", {})
            missed.append(cid)
            continue
        claim_disp = can.get("claim", can.get("claim_dmg", can.get("claim_hits")))
        if is_mismatch:
            detected += 1
            F.add(gate, "OK", f"canary {cid} DÉTECTÉ",
                  f"[{kind}] CR-injecté={claim_disp} recalc={recalc} → flaggé",
                  {"claim": claim_disp, "recalc": recalc, "detected": True})
        else:
            missed.append(cid)
            F.add(gate, "BLOCKER", f"canary {cid} RATÉ",
                  f"[{kind}] CR-injecté={claim_disp} recalc={recalc} → NON flaggé",
                  {"claim": claim_disp, "recalc": recalc, "detected": False})
    all_caught = (len(missed) == 0 and len(cans) > 0)
    return all_caught, detected, len(cans)


# ════════════════════════════════ rapport + verdict ════════════════════════════════
def write_report(ctx, F, canary_summary, verdict):
    out = os.path.join(ctx.workdir, "verification.md")
    L = []
    L.append(f"# Vérification CR de soirée — {ctx.cfg.get('guild', '?')} ({ctx.cfg.get('label', '?')})")
    L.append("")
    L.append(f"- workdir : `{ctx.workdir}`")
    L.append(f"- zone : {ctx.cfg.get('zone_name', '?')} ({ctx.cfg.get('size', '?')})")
    L.append(f"- reports : {', '.join(ctx.reports)}")
    L.append(f"- mechanics_ref : `{ctx.mech_ref_path}`")
    L.append(f"- **VERDICT : {verdict}**")
    L.append("")
    nb, nok, nsk = F.count("BLOCKER"), F.count("OK"), F.count("SKIP")
    L.append(f"Re-dérivation : **{nb} BLOCKER** · {nok} OK · {nsk} SKIP")
    if canary_summary is not None:
        caught, det, tot = canary_summary
        L.append(f"Canaries : {det}/{tot} détectés · "
                 f"{'TOUS flaggés ✓' if caught else 'AU MOINS UN RATÉ ✗ → VOID'}")
    L.append("")

    def section(title, recs):
        L.append(f"## {title}")
        L.append("")
        for lvl in ("BLOCKER", "SKIP", "OK"):
            items = [m for g, v, n, m, p in recs if v == lvl]
            if not items:
                continue
            tag = {"BLOCKER": "BLOCKER ✗", "SKIP": "skip ·", "OK": "OK ✓"}[lvl]
            for m in items:
                L.append(f"- **{tag}** {m}")
        L.append("")

    for gate_label, prefix in (
        ("Par boss — pulls + durée du kill", "boss"),
        ("Morts — RAW par pull + invariant 6", "deaths"),
        ("Dégâts évitables par joueur/boss", "avoidable"),
        ("Benchmark top-parse (présence)", "benchmark"),
    ):
        section(gate_label, [r for r in F.recs if r[0].startswith(prefix)])
    gc = [r for r in F.recs if r[0] == "CANARY"]
    if gc:
        section("Canaries", gc)
    L.append("---")
    L.append("Re-dérivé depuis raid.db (faits/events bruts) — dégâts évitables lus de "
             "`deep_dmg_taken.amount` (EFFECTIF), JAMAIS du graph WCL DamageTaken (empoisonné "
             "~1e9/hit) ; morts qualifiées = invariant 6 ; même formule des deux côtés ; "
             "tout claim non re-dérivable explicitement SKIP (jamais avalé).")
    open(out, "w", encoding="utf-8").write("\n".join(L) + "\n")
    return out


def compute_verdict(F, canary_summary):
    if canary_summary is not None:
        caught, _, tot = canary_summary
        if tot > 0 and not caught:
            return "VOID"   # un canary raté → le vérificateur est lazy/cassé, verdict nul
    return "NO-GO" if F.count("BLOCKER") > 0 else "GO"


def main():
    ap = argparse.ArgumentParser(description="Gate de re-dérivation du CR de soirée raid.")
    ap.add_argument("--workdir", default=None, help="dossier de la soirée (raid.json + raid.db)")
    ap.add_argument("--canaries", default="", help="fichier JSON de canaries (claims faux à détecter)")
    a = ap.parse_args()
    wd = a.workdir or os.environ.get("RAID_WORKDIR") or os.getcwd()
    ctx = Ctx(wd)
    F = Findings()

    gate_boss(ctx, F)
    gate_deaths(ctx, F)
    gate_avoidable(ctx, F)
    gate_benchmark(ctx, F)

    canary_summary = None
    if a.canaries:
        canary_summary = apply_canaries(ctx, F, os.path.expanduser(a.canaries))

    verdict = compute_verdict(F, canary_summary)
    report = write_report(ctx, F, canary_summary, verdict)

    # console
    print("=" * 72)
    for g, v, n, m, p in F.recs:
        if v == "BLOCKER":
            print("  ✗ BLOCKER", m)
    if canary_summary is not None:
        caught, det, tot = canary_summary
        print(f"  canaries: {det}/{tot} détectés ({'OK' if caught else 'VOID — un raté'})")
    print("=" * 72)
    print(f"Re-dérivation : {F.count('BLOCKER')} BLOCKER · {F.count('OK')} OK · {F.count('SKIP')} SKIP")
    print(f"Rapport : {report}")
    print(f"VERDICT : {verdict}")
    if verdict in ("NO-GO", "VOID"):
        sys.exit(1)


if __name__ == "__main__":
    main()
