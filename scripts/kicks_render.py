#!/usr/bin/env python3
"""Kicks timeline renderer — canonical (imported by pages.py).

Renders the gold `kicks.json` (built by kicks.py) as per-cast timelines:
  - one cast = one horizontal LANE, x-axis = reaction time from the begincast;
  - bar: kicked -> to the kick (no 0.3 s floor); landed -> FULL width; add died
    -> to the death; no-hit -> stub (CSS min-width);
  - dots = every kick laid (filled = the one that cut, ring = wasted), left
    clamped >= 0 (a kick can't render before its cast), "en avance" if reaction
    < 0 else "en retard" (-0.0 normalized).

`nominative` param (the contract — see references/kicks.md):
  - per-cast lanes carry the kicker NAME on BOTH public and officers (explicit
    user decision: execution is nominative on the public page);
  - nominative=True (officers annex ONLY) ALSO renders the SCOREBOARD
    (efficiency / wasted ranking) and the "never kicked" list — a ranking is
    blame, never public.

Public boss page calls kicks_section(..., nominative=False); the officers annex
calls it with nominative=True. NEVER drop the param.

Self-contained: no DB, no `self`. Caller passes the loaded `kicks.json` dict and
a `name_fn(ability_id) -> localized spell name` (e.g. pages.P.tr_name wrapper).
"""
import html
from collections import defaultdict

KICK_SCALE_CAP = 6.0

# WoW class colors (composition.class is the English class name).
CLASS_COLOR = {
    "DeathKnight": "#C41F3B", "Druid": "#FF7D0A", "Hunter": "#ABD473",
    "Mage": "#69CCF0", "Monk": "#00FF96", "Paladin": "#F58CBA",
    "Priest": "#E6E6E6", "Rogue": "#FFF569", "Shaman": "#0070DE",
    "Warlock": "#9482C9", "Warrior": "#C79C6E",
}
DEFAULT_COLOR = "#8a93a2"


def esc(s):
    return html.escape(str(s), quote=True)


def abil_name(ab, name_fn):
    try:
        ab = int(ab)
    except (TypeError, ValueError):
        return str(ab)
    if name_fn:
        n = name_fn(ab)
        if n and not str(n).startswith("sort #"):
            return n
    return "#%s" % ab


def _table(headers, rows):
    th = "".join("<th>%s</th>" % h for h in headers)
    body = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % c for c in r)
                   for r in rows)
    return ("<table class='tbl'><thead><tr>%s</tr></thead><tbody>%s</tbody>"
            "</table>" % (th, body))


def _lane_scale(insts):
    vals = [1.5]
    for i in insts:
        vals.append(i.get("dur") or 0)
        vals += [kk["lat"] for kk in i["kicks"] if kk["lat"] is not None]
    return min(max(vals), KICK_SCALE_CAP)


def _kick_axis(scale):
    step = 1.0 if scale > 3 else 0.5
    out, s = [], 0.0
    while s <= scale + 1e-6:
        out.append("<span class='klx' style='left:%.2f%%'><i>%g s</i></span>"
                   % (min(s / scale, 1) * 100, s))
        s += step
    return "<div class='klaxis'>%s</div>" % "".join(out)


def _kick_lane(inst, scale, color_fn):
    out = inst["out"]
    # bar length = how far the cast got BEFORE resolution (consistent with dots):
    #   kicked -> stops AT the kick (lat); landed -> FULL bar (cast completed);
    #   add died -> stops at the death; residual -> stub (CSS min-width).
    if out == "kicked":
        barlen = inst["lat"] if inst["lat"] is not None else 0.0
        fillcls = "kc-kick"
    elif out == "leaked":
        barlen = scale
        fillcls = "kc-leak"
    elif inst.get("stopkind") == "died":
        barlen = inst.get("dur") or 0.0
        fillcls = "kc-stop"
    else:
        barlen = 0.0
        fillcls = "kc-stop"
    fillw = (min(barlen / scale, 1) * 100) if scale else 0
    dots = ""
    for kk in sorted(inst["kicks"], key=lambda x: x["land"]):
        lat = kk["lat"] if kk["lat"] is not None else 0
        if lat == 0:
            lat = 0.0  # normalize -0.0 -> no "-0.00 en retard"
        left = max(0.0, min(lat / scale, 1)) * 100  # never before the cast start
        col = color_fn(kk["pl"])
        bits = [kk["pl"]]
        if kk.get("k"):
            bits.append(kk["k"])
        bits.append("%.2f s" % lat)
        miss = "" if kk["land"] else (" — en avance (gâché)" if lat < 0
                                      else " — en retard (gâché)")
        tip = " · ".join(bits) + miss
        st = (("left:%.2f%%;background:%s" % (left, col)) if kk["land"]
              else ("left:%.2f%%;border-color:%s" % (left, col)))
        dots += "<i class='klk %s' style='%s' title='%s'></i>" % (
            "km-l" if kk["land"] else "km-o", st, esc(tip))
    wasted = sum(1 for kk in inst["kicks"] if not kk["land"])
    if out == "kicked":
        who = (" · %s" % esc(inst["by"])) if inst["by"] else ""
        lab = "<span class='kpi-g'>coupé %.1f s</span>%s" % (inst["lat"] or 0, who)
        if wasted:
            lab += " <span class='mut'>· +%d gâché%s</span>" % (
                wasted, "s" if wasted > 1 else "")
    elif out == "leaked":
        lab = "<span class='kpi-r'>PASSÉ</span>"
        early = sum(1 for kk in inst["kicks"]
                    if not kk["land"] and (kk["lat"] or 0) < 0)
        late = wasted - early
        segs = (([("%d en retard" % late)] if late else [])
                + ([("%d en avance" % early)] if early else []))
        if segs:
            lab += " <span class='mut'>· %s</span>" % ", ".join(segs)
    elif inst.get("stopkind") == "died":
        lab = ("<span class='mut'>add tué pendant le cast (%.1f s) — pas de "
               "frappe</span>" % (inst.get("dur") or 0))
    else:
        lab = ("<span class='mut'>sans frappe — ni coupé ni dégât loggé "
               "(add neutralisé/CC, ou cast simultané non résolu au log)</span>")
    return ("<div class='klane'><span class='klt'>@%.0f s</span>"
            "<span class='klbar'><i class='klfill %s' style='width:%.1f%%'></i>%s"
            "</span><span class='kll'>%s</span></div>"
            % (inst["t"], fillcls, fillw, dots, lab))


def _kick_detail(ab, cast, gmap):
    order = sorted((cast.get("pulls") or {}), key=lambda p: int(p))
    rows, n = [], 0
    for p in order:
        pd = cast["pulls"][p]
        for inst in pd["inst"]:
            n += 1
            t = inst["t"]
            prevs = [x for x in gmap.get(p, []) if x < t - 1e-6]
            delta = ("+%.0f s" % (t - max(prevs))) if prevs else "—"
            if inst["out"] == "kicked":
                who = (" par %s" % esc(inst["by"])) if inst["by"] else ""
                lat = ((" <span class='mut'>%.1f s</span>" % inst["lat"])
                       if inst["lat"] is not None else "")
                res = "<span class='kpi-g'>coupé</span>%s%s" % (who, lat)
            elif inst["out"] == "leaked":
                res = "<span class='kpi-r'>passé</span>"
            else:
                res = "<span class='mut'>stoppé (add tué / sans frappe)</span>"
            rows.append("<tr><td>pull %s%s</td><td>t=%.0f s</td><td>%s</td>"
                        "<td>%s</td></tr>"
                        % (p, " · KILL" if pd["kill"] else "", t, delta, res))
    if not rows:
        return ""
    return ("<details class='kick-list'><summary class='mut'>détail cast par cast"
            " (%d) — ordre chronologique, Δ = délai depuis le cast kickable "
            "précédent</summary><table class='tbl'><thead><tr><th>quand</th>"
            "<th>t (combat)</th><th>Δ préc.</th><th>issue</th></tr></thead>"
            "<tbody>%s</tbody></table></details>" % (n, "".join(rows)))


def kick_cast_block(ab, cast, bid, gmap, name_fn, color_fn):
    add = cast.get("add") or "add ?"
    name = abil_name(int(ab), name_fn)
    tot = cast["totals"]
    denom = tot["kicked"] + tot["leaked"]
    pct = round(100 * tot["kicked"] / denom) if denom else 0
    tier = ("<span class='ktag ok'>confirmé</span>" if cast["tier"] == "confirmed"
            else "<span class='ktag warn'>kickable — jamais coupé ici</span>")
    pcls = "kpi-g" if pct >= 80 else ("kpi-r" if pct < 40 else "")
    head = ("<div class='kick-head'><b>%s</b> <span class='mut'>› %s "
            "<span class='kid'>#%s</span></span> %s<span class='kick-stat'>"
            "%d casts · %d coupés · %d passés · <b class='%s'>%d%% coupé</b>"
            "</span></div>"
            % (esc(add), esc(name), ab, tier, tot["casts"], tot["kicked"],
               tot["leaked"], pcls, pct))
    pulls = cast.get("pulls") or {}
    if not pulls:
        return ("<div class='kick-cast'>%s<p class='mut'>aucune instance "
                "observée</p></div>" % head)
    order = sorted(pulls, key=lambda p: int(p))
    kill_p = next((p for p in order if pulls[p]["kill"]), order[-1])
    uid = "k%s_%s" % (bid, ab)
    opts = "".join(
        "<option value='%s_%s'%s>pull %s%s (%ds)</option>"
        % (uid, p, " selected" if p == kill_p else "", p,
           " · KILL" if pulls[p]["kill"] else "", round(pulls[p]["dur_s"]))
        for p in order)
    tracks = ""
    for p in order:
        insts = pulls[p]["inst"]
        scale = _lane_scale(insts)
        lanes = "".join(_kick_lane(i, scale, color_fn)
                        for i in sorted(insts, key=lambda x: x["t"]))
        tracks += "<div class='kick-track' data-pull='%s_%s'%s>%s%s</div>" % (
            uid, p, "" if p == kill_p else " style='display:none'", lanes,
            _kick_axis(scale))
    return ("<div class='kick-cast'>%s<div class='kick-selrow'><select "
            "class='kick-sel' onchange='kickSel(this)'>%s</select></div>"
            "<div class='kick-tracks'>%s</div>%s</div>"
            % (head, opts, tracks, _kick_detail(ab, cast, gmap)))


_KICK_JS = ("<script>function kickSel(s){var b=s.closest('.kick-cast')"
            ".querySelector('.kick-tracks');b.querySelectorAll('.kick-track')"
            ".forEach(function(t){t.style.display=(t.dataset.pull===s.value)?"
            "'block':'none';});}</script>")

_KICK_CSS = ("<style>"
    ".kleg{display:inline-flex;gap:14px;align-items:center;flex-wrap:wrap;font-size:12px;color:var(--mut);margin:2px 0 10px}"
    ".kleg i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}"
    ".kleg i.kc-kick{background:#3fb950}.kleg i.kc-leak{background:#e0744f}.kleg i.kc-stop{background:#4a5160}"
    ".kick-cast{border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin:10px 0;background:var(--panel)}"
    ".kick-head{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;font-size:14px}"
    ".kick-head .kid{font-size:11px;color:var(--mut)}"
    ".kick-stat{margin-left:auto;font-size:12px;color:var(--mut)}"
    ".ktag{font-size:11px;border-radius:5px;padding:1px 7px;border:1px solid var(--line)}"
    ".ktag.ok{color:#3fb950;border-color:#27572f}.ktag.warn{color:var(--acc);border-color:var(--accd)}"
    ".kick-selrow{margin:8px 0 2px}"
    ".kick-sel{background:#232838;color:var(--tx);border:1px solid var(--line);border-radius:6px;padding:3px 8px;font-size:12px}"
    ".kick-track{margin:8px 0 6px}"
    ".klane{display:flex;align-items:center;gap:8px;margin:4px 0;font-size:12px}"
    ".klt{flex:0 0 46px;text-align:right;color:var(--mut);font-variant-numeric:tabular-nums}"
    ".klbar{position:relative;flex:1;min-width:120px;height:14px;background:#1b1f27;border-radius:7px}"
    ".klfill{position:absolute;left:0;top:0;bottom:0;min-width:3px;border-radius:7px 0 0 7px;opacity:.5}"
    ".kc-kick{background:#3fb950}.kc-leak{background:#e0744f}.kc-stop{background:#454c5b}"
    ".klk{position:absolute;top:50%;width:10px;height:10px;margin:-5px 0 0 -5px;border-radius:50%;box-sizing:border-box;border:2px solid transparent;z-index:2}"
    ".klk:hover{transform:scale(1.4);z-index:5}"
    ".km-l{box-shadow:0 0 0 1.5px #0b0d10,0 0 3px rgba(0,0,0,.7)}"
    ".km-o{background:transparent!important}"
    ".kll{flex:0 0 auto;min-width:160px;color:var(--mut)}"
    ".kleg .km{display:inline-block;width:10px;height:10px;border-radius:50%;border:2px solid transparent;margin-right:4px;vertical-align:-1px}"
    ".kleg .km-l{background:#9aa3b2}.kleg .km-o{border-color:#9aa3b2}"
    ".klaxis{position:relative;height:13px;margin:3px 0 2px 54px}"
    ".klx{position:absolute;top:0;width:1px;height:4px;background:#2a2f3a}"
    ".klx i{position:absolute;top:5px;left:2px;font-size:9px;color:var(--mut);font-style:normal;white-space:nowrap}"
    ".kdot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;vertical-align:-1px}"
    ".kick-list{margin:8px 0}"
    "</style>")


def _boss_key(data, boss):
    return next((k for k in data if (data[k] or {}).get("boss") == boss), None)


def has_kicks(data, boss):
    key = _boss_key(data or {}, boss)
    if not key:
        return False
    casts = (data[key].get("casts") or {})
    return any(c["totals"]["casts"] > 0 for c in casts.values())


def kicks_section(data, boss, nominative, name_fn=None, label=None):
    """Kicks section for one boss. nominative=False (public boss page) = lanes +
    legend (named, no scoreboard); nominative=True (officers annex) = + scoreboard
    + "never kicked". Returns "" when the boss has no kickable casts."""
    data = data or {}
    key = _boss_key(data, boss)
    if not key:
        return ""
    kk = data[key]
    casts = {a: c for a, c in (kk.get("casts") or {}).items()
             if c["totals"]["casts"] > 0}
    if not casts:
        return ""
    bid = "".join(ch for ch in (boss or "b") if ch.isalnum()) or "b"
    roster = {s["pl"]: s.get("class") for s in (kk.get("scoreboard") or [])}

    def color_fn(nm):
        return CLASS_COLOR.get(roster.get(nm), DEFAULT_COLOR)

    legend = ("<span class='kleg'><i class='kc-kick'></i>coupé "
              "<i class='kc-leak'></i>passé (leak) <i class='kc-stop'></i>"
              "add tué / sans frappe <i class='km km-l'></i>kick qui a coupé "
              "<i class='km km-o'></i>tenté sans couper (gâché)</span>")
    intro = ("<p class='mut'><b>1 ligne horizontale = 1 cast</b> du sort "
             "dangereux ; l'axe = temps de réaction depuis le début de "
             "l'incantation. La barre s'avance jusqu'au kick (<b>vert = coupé</b>),"
             " est <b>pleine rouge si PASSÉ</b> (le sort a frappé — dégât ou "
             "complétion loggés), <b>courte grise si SANS FRAPPE</b> (add tué/CC, "
             "vérifié sans dégât). Pastilles = chaque kick tenté, à sa réaction : "
             "plein = celui qui a coupé, anneau = tenté sans couper — <b>réaction "
             "négative = en avance</b>, positive = en retard. Couleur = classe ; "
             "nom + sort au survol. « Lancés » = sorts d'interrupt dédiés "
             "(Avenger's Shield, dégât on-CD, exclu des tentatives mais ses "
             "coupures comptent ; les Prêtres ombre coupent via Silence).%s</p>"
             % ("" if nominative
                else " <em>Tableau de bord efficacité/gaspillage : annexe "
                     "officiers.</em>"))
    blab = label or boss
    parts = ["<h3 id='kicks'>Kicks — %s <small>qui interrompt quoi</small></h3>%s%s%s"
             % (esc(blab), _KICK_CSS, legend, _KICK_JS), intro]

    # scoreboard (officers only)
    if nominative and kk.get("scoreboard"):
        rows = []
        for s in kk["scoreboard"]:
            col = CLASS_COLOR.get(s.get("class"), DEFAULT_COLOR)
            ecls = "kpi-g" if s["eff"] >= 75 else ("kpi-r" if s["eff"] < 45 else "")
            rows.append((
                "<span class='kdot' style='background:%s'></span>%s"
                % (col, esc(s["pl"])),
                s["attempts"], s["landed"],
                "<span class='%s'>%d %%</span>" % (ecls, s["eff"]),
                "%.2f s" % s["react"] if s["react"] is not None else "—",
                s["wasted"]))
        parts.append("<h4>Tableau des kickers <small>réaction = latence médiane "
                     "cast→kick ; gaspillés = kicks lancés sans couper (doublon / "
                     "trop tard)</small></h4>")
        parts.append(_table(["Joueur", "Lancés", "Réussis", "Efficacité",
                             "Réaction méd.", "Gaspillés"], rows))

    # global per-pull chrono map (all kickable instants) -> Δ "since previous"
    gmap = defaultdict(list)
    for c in casts.values():
        for p, pd in (c.get("pulls") or {}).items():
            for inst in pd["inst"]:
                gmap[p].append(inst["t"])
    for p in gmap:
        gmap[p].sort()

    by_add = defaultdict(list)
    for ab, c in casts.items():
        by_add[c.get("add") or "add ?"].append((ab, c))
    for add, lst in by_add.items():
        for ab, c in sorted(lst, key=lambda x: -x[1]["totals"]["casts"]):
            parts.append(kick_cast_block(ab, c, bid, gmap, name_fn, color_fn))

    # never kicked (officers only)
    if nominative and kk.get("never"):
        names = ", ".join(
            "%s <span class='mut'>(%s)</span>"
            % (esc(n["pl"]), esc(n["spec"] or n["class"] or "?"))
            for n in kk["never"])
        parts.append("<p class='mut'><b>Jamais kické de la soirée</b> "
                     "<small>(spec capable, 0 kick lancé sur ce boss — à "
                     "confirmer comme (non-)assignation)</small> : %s</p>" % names)
    return "".join(parts)
