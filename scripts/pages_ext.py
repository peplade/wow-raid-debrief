#!/usr/bin/env python3
"""Rendering extensions for pages.py — the nominative/dossier layer (v1.2).

Renders the digests produced by dossiers.py / execution.py / pacing.py /
evolution.py. Every function degrades to "" when its digest is absent, so
pages.py keeps working on pre-1.2 workdirs. Chart functions follow the
(html, js) convention of pages.py (tlChart lazy-init).
"""
import html
import json
import os
from collections import defaultdict

MELEE = {("Warrior", "Fury"), ("Warrior", "Arms"), ("Warrior", "Protection"),
         ("Paladin", "Retribution"), ("Paladin", "Protection"),
         ("Rogue", "Combat"), ("Rogue", "Assassination"),
         ("Rogue", "Subtlety"), ("DeathKnight", "Unholy"),
         ("DeathKnight", "Blood"), ("DeathKnight", "Frost"),
         ("Monk", "Windwalker"), ("Monk", "Brewmaster"),
         ("Druid", "Feral"), ("Druid", "Guardian"),
         ("Shaman", "Enhancement")}

EXT_L = {
    "en": {
        "critical": "Critical moments — possible reactions",
        "posted": "posted", "available": "available, NOT posted",
        "reserve": "victims with a defensive in reserve",
        "chrono": "full chronology", "events": "events",
        "repull": "repull in", "fixed": "fixed", "repeated": "repeated",
        "who_title": "Execution — who does what",
        "who_sub": ("kicks, add switches, ground AoE — nominative, measured "
                    "over the whole pull series; CD availability uses the "
                    "absolute night timeline (indicative durations, talents "
                    "not in the log)"),
        "kicks": "Interrupts — who kicks",
        "th_spell": "enemy cast", "th_begun": "begun", "th_through": "through",
        "th_kicked": "% kicked", "th_kill": "on the kill",
        "switch": "Add switches",
        "switch_sub": ("latency = delay between the add spawning and this "
                       "player's first hit — read melee separately (travel "
                       "time)"),
        "ranged": "RANGED", "melee": "MELEE + TANKS",
        "th_lat": "median latency", "th_dmg": "add damage", "th_win": "windows",
        "windows_kill": "Add windows on the kill",
        "th_top3": "top 3", "th_late": "switch > 8 s",
        "squat": "Who stays in the ground AoE",
        "squat_sub": ("“squat” = ≥3 consecutive ticks of the same zone "
                      "(≤3 s apart) — tanks listed apart (boss placement is "
                      "not a personal fail)"),
        "th_ticks": "ticks", "th_squats": "squats", "th_detail": "detail",
        "tanks": "TANKS",
        "focus": "Focus conformity on the kill",
        "focus_sub": ("share of each DPS's damage on the raid's majority "
                      "target per 10 s window (council bosses only; cleave "
                      "specs read lower mechanically — the relative gap is "
                      "the signal; tanks excluded, off-target is their job)"),
        "th_focus": "on the raid's target",
        "npc_dps": "Priority-add damage", "npc_heal": "Friendly-NPC healing",
        "npc_dps_part": "Damage participation on adds", "th_share": "share",
        "add_manif": "Manifestation of Pride (big adds)",
        "add_frag": "Corrupted Fragment (rift adds)",
        "add_reflet": "Reflection (Self-Reflection mirror)",
        "trials": "Trial entries",
        "trials_sub": ("orb soaks and the resource bar are NOT in the combat "
                       "log; entries are; durations only sporadically"),
        "prisons": "Prisons — time to free",
        "prisons_sub": ("the freer (pressure plates) is not logged; the "
                        "collective reaction time is"),
        "th_prisoner": "prisoner", "th_when": "when", "th_freed": "freed in",
        "defensives": "Personal defensives",
        "def_sub": "casts over the series (average per pull)",
        "pacing_rich": "Night pacing",
        "combat": "combat", "idle": "idle", "repull_med": "median repull",
        "gaps2": "gaps ≥ 2 min", "top_gaps": "longest gaps",
        "th_after": "after", "th_before": "before", "th_dur": "duration",
        "minutes_since": "minutes since start",
        "deaths_cum": "cumulative deaths over the night",
        "evo_title": "Guild evolution — week over week",
        "evo_sub": ("median WCL kill percentile (comparable across "
                    "difficulties) + equipped ilvl; healer percentiles "
                    "collapse mechanically on progress nights — read the "
                    "trend, not the absolute"),
        "th_player": "player", "th_role": "role", "th_pctl": "pctl",
        "th_ilvl": "ilvl", "th_deaths": "deaths",
        "evo_raid": "Raid trajectory", "th_week": "week",
        "th_pulls": "pulls (boss)", "th_kills": "kills", "th_wipes": "wipes",
        "th_prepot": "pre-pot", "new_items": "notable gear",
        "departed": "left roster", "arrived": "new to roster",
        "evo_pctl_h": "Median percentile per week (latest in green)",
        "evo_ilvl_h": "Average equipped ilvl, per player",
        "evo_cmp_h": "Strict comparable",
        "evo_cmp_intro": ("Same boss, same difficulty — the only raw-DPS "
                          "comparable. %s %s, weeks: %s. DPS per player (k):"),
        "th_dps_k": "DPS %s (k)", "th_delta": "Δ",
        "evo_roster_h": "Roster", "th_roster": "present",
        "player_exec": "Mechanic execution",
        "pe_kicks": "kicks", "pe_switch_lat": "median add-switch latency",
        "pe_squats": "ground-AoE squats",
    },
    "fr": {
        "critical": "Moments critiques — réactions possibles",
        "posted": "posés", "available": "disponibles, NON posés",
        "reserve": "victimes avec un défensif en réserve",
        "chrono": "chronologie complète", "events": "événements",
        "repull": "repull en", "fixed": "corrigé", "repeated": "répété",
        "who_title": "Exécution — qui fait quoi",
        "who_sub": ("kicks, switch sur les adds, zones au sol — nominatif, "
                    "mesuré sur toute la série de pulls ; disponibilité des "
                    "CDs calculée en temps absolu de soirée (durées "
                    "indicatives, talents non journalisés)"),
        "kicks": "Interruptions — qui kick",
        "th_spell": "cast ennemi", "th_begun": "commencés",
        "th_through": "passés", "th_kicked": "% kické", "th_kill": "sur le kill",
        "switch": "Switch sur les adds",
        "switch_sub": ("latence = délai entre l'apparition de l'add et le "
                       "premier hit du joueur — à lire séparément pour les "
                       "melee (temps de déplacement)"),
        "ranged": "RANGED", "melee": "MELEE + TANKS",
        "th_lat": "latence méd.", "th_dmg": "dégâts adds", "th_win": "fenêtres",
        "windows_kill": "Les fenêtres d'adds du kill",
        "th_top3": "top 3", "th_late": "switch > 8 s",
        "squat": "Qui reste dans les zones au sol",
        "squat_sub": ("« squat » = ≥3 ticks consécutifs de la même zone "
                      "(≤3 s d'écart) — tanks à part (placement du boss, pas "
                      "une faute individuelle)"),
        "th_ticks": "ticks", "th_squats": "squats", "th_detail": "détail",
        "tanks": "TANKS",
        "focus": "Conformité de focus sur le kill",
        "focus_sub": ("part des dégâts de chaque DPS sur la cible "
                      "MAJORITAIRE du raid par fenêtre de 10 s (boss "
                      "conseil ; les specs à cleave descendent "
                      "mécaniquement — l'écart relatif est le signal ; "
                      "tanks exclus, l'off-target est leur rôle)"),
        "th_focus": "sur la cible du raid",
        "npc_dps": "Dégâts sur les adds prioritaires",
        "npc_heal": "Soins sur les PNJ alliés",
        "npc_dps_part": "Participation aux dégâts sur les adds",
        "th_share": "% part",
        "add_manif": "Manifestation d'Orgueil (gros adds)",
        "add_frag": "Fragment corrompu (failles)",
        "add_reflet": "Reflet (miroir Self-Reflection)",
        "trials": "Entrées en épreuve",
        "trials_sub": ("le soak d'orbes et la barre de ressource ne sont PAS "
                       "dans le log ; les entrées si ; les durées seulement "
                       "sporadiquement"),
        "prisons": "Prisons — vitesse de libération",
        "prisons_sub": ("le libérateur (plaques) n'est pas journalisé ; le "
                        "temps de réaction collectif l'est"),
        "th_prisoner": "prisonnier", "th_when": "quand",
        "th_freed": "libéré en",
        "defensives": "Défensifs personnels",
        "def_sub": "casts sur la série (moyenne par pull)",
        "pacing_rich": "Cadence de la soirée",
        "combat": "combat", "idle": "temps mort",
        "repull_med": "repull médian", "gaps2": "pauses ≥ 2 min",
        "top_gaps": "plus longues pauses",
        "th_after": "après", "th_before": "avant", "th_dur": "durée",
        "minutes_since": "minutes depuis le début",
        "deaths_cum": "morts cumulées au fil de la soirée",
        "evo_title": "Évolution de la guilde — semaine à semaine",
        "evo_sub": ("percentile WCL médian sur les kills (comparable entre "
                    "difficultés) + ilvl équipé ; les percentiles heal "
                    "s'effondrent mécaniquement en soirée de progress — "
                    "lire la tendance, pas l'absolu"),
        "th_player": "joueur", "th_role": "rôle", "th_pctl": "pctl",
        "th_ilvl": "ilvl", "th_deaths": "morts",
        "evo_raid": "Trajectoire du raid", "th_week": "semaine",
        "th_pulls": "pulls (boss)", "th_kills": "kills", "th_wipes": "wipes",
        "th_prepot": "pré-pot", "new_items": "loot notable",
        "departed": "sortis du roster", "arrived": "entrants",
        "evo_pctl_h": "Percentile médian par semaine (dernière en vert)",
        "evo_ilvl_h": "ilvl moyen équipé, par joueur",
        "evo_cmp_h": "Comparable strict",
        "evo_cmp_intro": ("Même boss, même difficulté — le seul comparable en "
                          "DPS brut. %s %s, semaines : %s. DPS par joueur (k) :"),
        "th_dps_k": "DPS %s (k)", "th_delta": "Δ",
        "evo_roster_h": "Roster", "th_roster": "présents",
        "player_exec": "Exécution des mécaniques",
        "pe_kicks": "kicks", "pe_switch_lat": "latence de switch médiane",
        "pe_squats": "squats de zones au sol",
    },
}


def esc(s):
    return html.escape(str(s), quote=True)


def XL(gen):
    return EXT_L.get(gen.lang, EXT_L["en"])


def table(headers, rows):
    th = "".join(f"<th>{h}</th>" for h in headers)
    trs = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"
                  for r in rows)
    return (f'<table class="tbl"><thead><tr>{th}</tr></thead>'
            f'<tbody>{trs}</tbody></table>')


def class_spec_role(gen):
    """player -> (class, spec, role) across own reports."""
    if getattr(gen, "_csr", None) is None:
        ph = ",".join("?" for _ in gen.codes)
        gen._csr = {}
        for r in gen.be.con.execute(
                f"SELECT player_name, MAX(class) c, MAX(spec) s, MAX(role) ro"
                f" FROM composition WHERE report IN ({ph})"
                f" GROUP BY player_name", gen.codes):
            gen._csr[r["player_name"]] = (r["c"], r["s"], r["ro"])
    return gen._csr


def is_melee(gen, nm):
    c, s, ro = class_spec_role(gen).get(nm, (None, None, None))
    return ro == "tank" or (c, s) in MELEE


# ---------------------------------------------------------- per-pull dossier
def _dossier_index(gen):
    if getattr(gen, "_dossiers", None) is None:
        d = gen.J("dossiers.json") or {"pulls": []}
        gen._dossiers = {(p["report"], p["fight_id"]): p for p in d["pulls"]}
    return gen._dossiers


def pull_dossier_block(gen, pull, pcode):
    """Critical moments + reactions + chronology for one pull."""
    d = _dossier_index(gen).get((pcode, pull["fight_id"]))
    if not d:
        return ""
    X = XL(gen)
    parts = []
    delta = d.get("delta_vs_prev_pull")
    meta = []
    if delta:
        meta.append("%s %d:%02d" % (X["repull"], delta["repull_gap_s"] // 60,
                                    delta["repull_gap_s"] % 60))
        if delta.get("fixed"):
            meta.append("%s : <span class='kpi-g'>%s</span>" % (
                X["fixed"], esc(", ".join(
                    gen.tr_name(a) for a in delta["fixed"][:4]))))
        if delta.get("repeated"):
            meta.append("%s : <span class='kpi-r'>%s</span>" % (
                X["repeated"], esc(", ".join(
                    gen.tr_name(a) for a in delta["repeated"][:4]))))
    if meta:
        parts.append("<p class='mut'>%s</p>" % " · ".join(meta))
    for c in d.get("critical_moments") or []:
        rows = []
        if c.get("cds_posted"):
            rows.append("<p><span class='tag kill'>%s</span> %s</p>" % (
                X["posted"], esc(" · ".join(c["cds_posted"]))))
        if c.get("cds_available_not_posted"):
            rows.append("<p><span class='tag wipe'>%s</span> %s</p>" % (
                X["available"],
                esc(" · ".join(c["cds_available_not_posted"][:8]))))
        if c.get("victims_without_defensive"):
            v = " · ".join("%s (%s)" % (x["player"],
                                        ", ".join(x["defensives_in_reserve"]))
                           for x in c["victims_without_defensive"][:5])
            rows.append("<p><span class='tag'>%s</span> %s</p>" % (
                X["reserve"], esc(v)))
        parts.append("<div style='border-left:3px solid var(--acc);"
                     "padding:4px 10px;margin:8px 0'><strong>t=%ds — %s"
                     "</strong>%s</div>" % (c["t_s"], esc(c["why"]),
                                            "".join(rows)))
    chrono = d.get("chronology") or []
    if chrono:
        rows = "".join("<tr><td>%ds</td><td>%s</td><td>%s</td></tr>" % (
            e["t_s"], esc(e["kind"]), esc(e["txt"])) for e in chrono)
        parts.append("<details><summary class='mut'>%s (%d %s)</summary>"
                     "<table class='tbl'><tbody>%s</tbody></table>"
                     "</details>" % (X["chrono"], len(chrono), X["events"],
                                     rows))
    return "".join(parts)


# ----------------------------------------------------- nominative execution
def nominative_section(gen, boss_log_name):
    ex = (gen.J("execution_nominative.json") or {}).get(boss_log_name)
    if not ex:
        return ""
    X = XL(gen)
    parts = ['<h2 id="who">%s <small>%s</small></h2>'
             % (X["who_title"], X["who_sub"])]

    # Kicks: the simple per-player/per-spell table is RETIRED in favor of the
    # canonical kicks TIMELINE (scripts/kicks_render.kicks_section), wired on the
    # boss page (nominative=False) and the officers annex (nominative=True, with
    # the scoreboard). See references/kicks.md.

    sw = ex.get("switch") or {}
    if sw:
        parts.append("<h3>%s <small>%s</small></h3>" % (X["switch"],
                                                        X["switch_sub"]))
        mel, rng = [], []
        for nm, v in sw.items():
            row = (esc(nm), "%.1f s" % v["median_latency_s"],
                   "%.0f M" % v["add_dmg_M"], v["windows"])
            (mel if is_melee(gen, nm) else rng).append(row)
        cols = [X["th_player"], X["th_lat"], X["th_dmg"], X["th_win"]]
        parts.append('<div class="grid2"><div><h4 class="mut">%s</h4>%s</div>'
                     '<div><h4 class="mut">%s</h4>%s</div></div>' % (
                         X["ranged"], table(cols, rng),
                         X["melee"], table(cols, mel)))
        kw = [w for w in (ex.get("add_windows") or [])
              if w["kill"] and w["dmg_M"] >= 20]
        if kw:
            parts.append("<h3>%s</h3>" % X["windows_kill"])
            rows = [("t=%ds" % w["t_s"], esc(gen.tr_name(w["add"])),
                     "%ds" % w["dur_s"], "%.0f M" % w["dmg_M"],
                     esc(", ".join(w["top3"])),
                     esc(", ".join(w["late_8s"])) or "—") for w in kw]
            parts.append(table(["t", "add", X["th_dur"], "dmg",
                                X["th_top3"], X["th_late"]], rows))

    fk = ex.get("focus_kill") or {}
    if fk:
        parts.append("<h3>%s <small>%s</small></h3>" % (X["focus"],
                                                        X["focus_sub"]))
        rows = [(esc(nm), "%d %%" % v) for nm, v in fk.items()
                if class_spec_role(gen).get(nm, (0, 0, 0))[2] == "dps"]
        parts.append(table([X["th_player"], X["th_focus"]], rows))

    for key, title in (("npc_dps", X["npc_dps"]), ("npc_heal", X["npc_heal"])):
        if ex.get(key + "_split"):
            continue  # rendered per-add (with % share) below
        agg = ex.get(key) or {}
        if agg:
            parts.append("<h3>%s</h3>" % title)
            parts.append(table([X["th_player"], "M"],
                               [(esc(nm), v) for nm, v in
                                list(agg.items())[:15]]))

    split = ex.get("npc_dps_split") or {}
    if split:
        labels = {"Manifestation of Pride": X["add_manif"],
                  "Corrupted Fragment": X["add_frag"],
                  "Reflection": X["add_reflet"]}
        for add_name, rows0 in split.items():
            lbl = labels.get(add_name, gen.tr_name(add_name))
            parts.append("<h3>%s — <small>%s</small></h3>" %
                         (X["npc_dps_part"], lbl))
            parts.append(table([X["th_player"], "M", X["th_share"]],
                               [(esc(nm), "%.1f" % m, "%.1f %%" % p)
                                for nm, m, p in rows0[:20]]))

    te = ex.get("trial_entries") or {}
    if te:
        parts.append("<h3>%s <small>%s</small></h3>" % (X["trials"],
                                                        X["trials_sub"]))
        parts.append(table([X["th_player"], "n"],
                           [(esc(nm), n) for nm, n in te.items()]))

    pris = ex.get("prisons") or []
    if pris:
        lib = sorted(p["freed_in_s"] for p in pris)
        parts.append("<h3>%s <small>%s — n=%d, med %.0f s</small></h3>" % (
            X["prisons"], X["prisons_sub"], len(pris), lib[len(lib) // 2]))
        rows = [("p%d" % p["pull"], esc(p["player"]), "%ds" % p["t_s"],
                 '<span class="%s">%.1f s</span>' % (
                     "kpi-g" if p["freed_in_s"] <= 8 else "kpi-r",
                     p["freed_in_s"])) for p in pris]
        parts.append(table(["pull", X["th_prisoner"], X["th_when"],
                            X["th_freed"]], rows))

    dfs = ex.get("defensives") or {}
    if dfs:
        parts.append("<h3>%s <small>%s</small></h3>" % (X["defensives"],
                                                        X["def_sub"]))
        parts.append(table([X["th_player"], ""],
                           [(esc(nm), "%d (%.1f)" % (v["casts"],
                                                     v["per_pull"]))
                            for nm, v in list(dfs.items())[:16]]))
    return "".join(parts)


# ------------------------------------------------------------- rich pacing
def rich_pacing(gen):
    pac = gen.J("pacing_nights.json")
    if not pac or "self" not in pac:
        return "", ""
    X = XL(gen)
    parts, js = [], []
    for blk in [pac["self"]] + (pac.get("compare") or []):
        for n in blk["nights"]:
            mine = blk is pac["self"]
            tagc = "kill" if mine else ""
            parts.append(
                "<p><span class='tag %s'>%s %s</span> %dmin %s "
                "(%.0f %%) · %dmin %s · %s %s · %s : %d</p>" % (
                    tagc, esc(blk.get("label") or ""), n["report"][:8],
                    n["combat_s"] // 60, X["combat"],
                    100 * (n["combat_share"] or 0), n["idle_s"] // 60,
                    X["idle"], X["repull_med"],
                    ("%d:%02d" % (n["repull_median_s"] // 60,
                                  n["repull_median_s"] % 60))
                    if n["repull_median_s"] else "—",
                    X["gaps2"], n["n_gaps_2min"]))
    n0 = pac["self"]["nights"][0] if pac["self"]["nights"] else None
    if n0:
        # gantt of the first own night
        t0 = n0["segments"][0]["start"]
        labels, data, colors = [], [], []
        for s in n0["segments"]:
            if s["kind"] == "trash" and (s["end"] - s["start"]) < 30000:
                continue
            labels.append(s["label"])
            data.append([round((s["start"] - t0) / 60000, 1),
                         round((s["end"] - t0) / 60000, 1)])
            colors.append("#3fb950" if s.get("kill") else
                          ("#3a3f4a" if s["kind"] == "trash" else "#e0744f"))
        cfg = {"type": "bar",
               "data": {"labels": labels,
                        "datasets": [{"data": data,
                                      "backgroundColor": colors,
                                      "borderSkipped": False,
                                      "barPercentage": .9,
                                      "categoryPercentage": 1.0}]},
               "options": {"indexAxis": "y", "animation": False,
                           "responsive": True,
                           "maintainAspectRatio": False,
                           "scales": {"x": {"min": 0,
                                            "title": {"display": True,
                                                      "text":
                                                      X["minutes_since"]},
                                            "ticks": {"color": "#8a93a2"},
                                            "grid": {"color": "#2a2f3a"}},
                                      "y": {"ticks": {"font": {"size": 9},
                                                      "autoSkip": False,
                                                      "color": "#8a93a2"},
                                            "grid": {"display": False}}},
                           "plugins": {"legend": {"display": False}}}}
        hpx = max(380, 11 * len(labels))
        parts.append('<div class="chartbox" style="height:%dpx">'
                     '<canvas id="extgantt"></canvas></div>' % hpx)
        js.append("tlChart('extgantt',%s);\n"
                  % json.dumps(cfg, ensure_ascii=False))
        if n0.get("top_gaps"):
            parts.append("<h4 class='mut'>%s</h4>" % X["top_gaps"])
            parts.append(table([X["th_dur"], X["th_after"], X["th_before"]],
                               [("%d:%02d" % (g["gap_s"] // 60,
                                              g["gap_s"] % 60),
                                 esc(g["after"]), esc(g["before"]))
                                for g in n0["top_gaps"]]))
    return "".join(parts), "".join(js)


# ------------------------------------------------------------ player lines
def player_execution_panel(gen, name):
    mex = gen.J("execution_nominative.json") or {}
    X = XL(gen)
    items = []
    kicks = defaultdict(int)
    for bo in mex.values():
        for s, n in (bo.get("kicks") or {}).get(name, {}).items():
            kicks[s] += n
    if kicks:
        items.append("<li>%s : <strong>%d</strong> (%s)</li>" % (
            X["pe_kicks"], sum(kicks.values()),
            esc(", ".join("%s ×%d" % (gen.tr_name(s), n)
                          for s, n in sorted(kicks.items(),
                                             key=lambda x: -x[1])))))
    for boss, bo in mex.items():
        v = (bo.get("switch") or {}).get(name)
        if v and class_spec_role(gen).get(name, (0, 0, 0))[2] != "healer":
            items.append("<li>%s — %s : <strong>%.1f s</strong> · %.0f M · "
                         "%d</li>" % (esc(gen.boss_name_by_log(boss)),
                                      X["pe_switch_lat"],
                                      v["median_latency_s"], v["add_dmg_M"],
                                      v["windows"]))
    sq = defaultdict(lambda: [0, 0])
    for bo in mex.values():
        for mech, v in (bo.get("squat") or {}).get(name, {}).items():
            sq[mech][0] += v["ticks"]
            sq[mech][1] += v["squats"]
    sql = [(m, t, s) for m, (t, s) in sq.items() if s > 0]
    if sql:
        items.append("<li>%s : %s</li>" % (X["pe_squats"], esc(
            ", ".join("%s ×%d (%d ticks)" % (gen.tr_name(m), s, t)
                      for m, t, s in sorted(sql, key=lambda x: -x[2])))))
    if not items:
        return ""
    return "<h2>%s</h2><div class='panel'><ul>%s</ul></div>" % (
        X["player_exec"], "".join(items))


# ------------------------------------------------------------ evolution page
def page_evolution(gen):
    ev = gen.J("evolution.json")
    if not ev:
        print("evolution.json absent — run scripts/evolution.py first")
        return
    X = XL(gen)
    gear = gen.J("gear_evolution.json") or {}
    weeks = ev["weeks"]
    players = ev["players"]
    n = len(weeks)
    js = []
    _PAL = ["#3a3f4a", "#6db3f2", "#e8b923", "#a371f7", "#f778ba", "#56d4dd"]

    def wcol(i):
        return "#3fb950" if i == n - 1 else _PAL[i % len(_PAL)]

    def wshort(w):
        return w["label"][-5:]

    h = [gen.head("%s — %s" % (X["evo_title"], gen.guild))]
    h.append("<header class='hero'><h1>%s — <em>%s</em></h1>"
             "<div class='sub'>%s</div></header><main>" % (
                 X["evo_title"], esc(gen.guild), X["evo_sub"]))

    # ---- raid trajectory (dynamic over all weeks)
    h.append("<h2>%s</h2>" % X["evo_raid"])
    rows = []
    for w in weeks:
        r = w["raid"]
        kn = sum(1 for k in r["kills"] if k["difficulty"] == 3)
        kh = sum(1 for k in r["kills"] if k["difficulty"] == 4)
        rows.append((esc(w["label"]), r["n_pulls"],
                     "%dN + %dH" % (kn, kh), r["n_wipes"], r["morts_total"],
                     ("%.0f %%" % (100 * r["prepot_rate"]))
                     if r["prepot_rate"] is not None else "—"))
    h.append(table([X["th_week"], X["th_pulls"], X["th_kills"],
                    X["th_wipes"], X["th_deaths"], X["th_prepot"]], rows))

    # ---- players: percentile + ilvl charts (one series/week) + table
    h.append("<h2>%s</h2>" % X["th_player"].capitalize() + "s")

    def last_pctl(e):
        v = e["weeks"][-1]["median_percentile"]
        return -1 if v is None else v
    pres = [(nm, e) for nm, e in sorted(players.items(),
                                        key=lambda x: -last_pctl(x[1]))
            if e["weeks"][-1]["present"]]
    pnames = [nm for nm, _ in pres]
    if pnames:
        h.append("<h3>%s</h3>" % esc(X["evo_pctl_h"]))
        h.append('<div class="chartbox" style="height:%dpx">'
                 '<canvas id="evopctl"></canvas></div>'
                 % max(260, 26 * len(pnames)))
        js.append("tlChart('evopctl',%s);\n" % json.dumps({
            "type": "bar",
            "data": {"labels": pnames, "datasets": [
                {"label": wshort(w),
                 "data": [e["weeks"][i]["median_percentile"] for _, e in pres],
                 "backgroundColor": wcol(i)} for i, w in enumerate(weeks)]},
            "options": {"indexAxis": "y", "animation": False,
                        "responsive": True, "maintainAspectRatio": False,
                        "scales": {"x": {"min": 0, "max": 100,
                                         "ticks": {"color": "#8a93a2"},
                                         "grid": {"color": "#2a2f3a"}},
                                   "y": {"ticks": {"font": {"size": 9},
                                                   "autoSkip": False,
                                                   "color": "#8a93a2"},
                                         "grid": {"display": False}}},
                        "plugins": {"legend": {"labels": {"boxWidth": 10,
                                              "color": "#8a93a2"}}}}},
            ensure_ascii=False))
        ipres = sorted(pres, key=lambda x: -(x[1]["weeks"][-1]["ilvl"] or 0))
        inames = [nm for nm, _ in ipres]
        h.append("<h3>%s</h3>" % esc(X["evo_ilvl_h"]))
        h.append('<div class="chartbox" style="height:240px">'
                 '<canvas id="evoilvl"></canvas></div>')
        js.append("tlChart('evoilvl',%s);\n" % json.dumps({
            "type": "line",
            "data": {"labels": inames, "datasets": [
                {"label": wshort(w),
                 "data": [e["weeks"][i]["ilvl"] for _, e in ipres],
                 "borderColor": wcol(i), "pointRadius": 2, "tension": 0,
                 "spanGaps": True} for i, w in enumerate(weeks)]},
            "options": {"animation": False, "responsive": True,
                        "maintainAspectRatio": False,
                        "scales": {"x": {"ticks": {"font": {"size": 9},
                                                   "maxRotation": 80,
                                                   "minRotation": 45,
                                                   "color": "#8a93a2"},
                                         "grid": {"color": "#2a2f3a"}},
                                   "y": {"min": 540,
                                         "ticks": {"color": "#8a93a2"},
                                         "grid": {"color": "#2a2f3a"}}},
                        "plugins": {"legend": {"labels":
                                               {"color": "#8a93a2"}}}}},
            ensure_ascii=False))

    heads = [X["th_player"], X["th_role"]]
    for w in weeks:
        heads += ["%s %s" % (X["th_pctl"], wshort(w)),
                  "%s %s" % (X["th_ilvl"], wshort(w))]
    heads.append(X["th_deaths"])
    rows = []
    for nm, e in pres:
        lw = e["weeks"][-1]
        row = [esc(nm), esc(lw["role"] or "?")]
        for wv in e["weeks"]:
            row.append(wv["median_percentile"]
                       if wv["median_percentile"] is not None else "—")
            row.append(wv["ilvl"] or "—")
        row.append(lw["deaths"])
        rows.append(tuple(row))
    h.append(table(heads, rows))

    # ---- strict comparable: most-covered boss+difficulty across >=2 weeks
    # (raw DPS is only comparable same boss + same difficulty). Generic.
    cov = {}
    for wi, w in enumerate(weeks):
        for k in w["raid"]["kills"]:
            cov.setdefault((k["boss"], k["difficulty"]), set()).add(wi)
    cand = sorted(((len(wis), bd) for bd, wis in cov.items() if len(wis) >= 2),
                  reverse=True)
    if cand:
        boss, diff = cand[0][1]
        cwis = sorted(cov[(boss, diff)])
        dn = {3: "N", 4: "HM"}.get(diff, str(diff))
        pdps = {}
        for nm, e in players.items():
            for wi in cwis:
                for p in (e["weeks"][wi].get("parses") or []):
                    if (p["boss"] == boss and p["difficulty"] == diff
                            and p["metric"] == "dps"):
                        pdps.setdefault(nm, {})[wi] = p["amount"]
                        break
        first, lastc = cwis[0], cwis[-1]
        h.append("<h2>%s — %s %s</h2>" % (X["evo_cmp_h"], esc(boss), dn))
        h.append("<p class='mut'>%s</p>" % (X["evo_cmp_intro"] % (
            esc(boss), dn, ", ".join(wshort(weeks[wi]) for wi in cwis))))
        cn, cd, rows = [], [], []
        for nm in sorted(pdps, key=lambda x: -(pdps[x].get(lastc) or 0)):
            d = pdps[nm]
            if lastc not in d:
                continue
            a = d.get(first)
            dl = ("%+d %%" % round((d[lastc] - a) / a * 100)) if a else "—"
            rows.append((esc(nm), round(a / 1000) if a else "—",
                         round(d[lastc] / 1000), dl))
            if a:
                cn.append(nm)
                cd.append(round((d[lastc] - a) / a * 100))
        if cn:
            h.append('<div class="chartbox" style="height:220px">'
                     '<canvas id="evocmp"></canvas></div>')
            js.append("tlChart('evocmp',%s);\n" % json.dumps({
                "type": "bar",
                "data": {"labels": cn, "datasets": [{
                    "data": cd,
                    "backgroundColor": ["#3fb950" if x >= 0 else "#e0744f"
                                        for x in cd]}]},
                "options": {"animation": False, "responsive": True,
                            "maintainAspectRatio": False,
                            "plugins": {"legend": {"display": False}},
                            "scales": {"x": {"ticks": {"font": {"size": 9},
                                                       "maxRotation": 80,
                                                       "minRotation": 45,
                                                       "color": "#8a93a2"},
                                             "grid": {"color": "#2a2f3a"}},
                                       "y": {"ticks": {"color": "#8a93a2"},
                                             "grid": {"color": "#2a2f3a"}}}}},
                ensure_ascii=False))
        h.append(table([X["th_player"], X["th_dps_k"] % wshort(weeks[first]),
                        X["th_dps_k"] % wshort(weeks[lastc]), X["th_delta"]],
                       rows))

    # ---- gear (dynamic: latest week-over-week delta)
    ups = sorted(((v.get("delta") or 0, nm, v) for nm, v in gear.items()
                  if nm != "__departed__"), reverse=True)
    items = []
    for d, nm, v in ups[:10]:
        if not v.get("new_items") and not d:
            continue
        its = ", ".join("%s (%d)" % (x["name"], x["ilvl"])
                        for x in v["new_items"][:4])
        items.append("<li><strong>%s</strong> %+0.1f ilvl%s</li>" % (
            esc(nm), d, (" — " + esc(its)) if its else ""))
    if items:
        h.append("<h2>%s</h2><div class='panel'><ul>%s</ul></div>"
                 % (X["new_items"], "".join(items)))
    h.append("<h2>%s</h2>" % X["evo_roster_h"])
    h.append(table([X["th_week"], X["th_roster"]],
                   [(esc(w["label"]),
                     sum(1 for e in players.values()
                         if e["weeks"][i]["present"]))
                    for i, w in enumerate(weeks)]))
    dep = gear.get("__departed__") or []
    arr = [nm for nm, v in gear.items()
           if nm != "__departed__" and v.get("absent_prev")]
    h.append("<div class='panel'><p>%s : %s</p><p>%s : %s</p></div>" % (
        X["arrived"], esc(", ".join(arr)) or "—",
        X["departed"], esc(", ".join(dep)) or "—"))
    h.append("</main>")
    # tlChart() lives in pages.CHART_JS; prepend it when we emitted any chart.
    # Lazy import: pages imports pages_ext, so a top-level import would cycle.
    from pages import CHART_JS
    h.append(gen.foot((CHART_JS + "".join(js)) if js else ""))
    gen.write(os.path.join(gen.workdir, "pages", "evolution", "index.html"),
              "".join(h))
