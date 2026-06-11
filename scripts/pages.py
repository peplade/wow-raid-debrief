#!/usr/bin/env python3
"""Static report generator: hub + one page per boss + one card per player +
unlisted officers annex. Reads digests/analysis/*.json + written content
fragments from <workdir>/content/ (see layout below). Output:
<workdir>/pages/<label>/... fully static (host anywhere).

Content fragments (HTML, written by the analyst/skill — ALL OPTIONAL, pages
render with auto sections only if absent):
    content/hub/hero.html              hub header block
    content/hub/body.html              hub body; placeholders __BOSS_TABLE__,
                                       __PLAYERS_LINKS__, __PACING__ are replaced
    content/boss/<enc>_<N|H>/synthesis.html      "Synthèse" panel
    content/boss/<enc>_<N|H>/intro.html          before the pull-by-pull list
    content/boss/<enc>_<N|H>/pull_<n>.html       note panel under pull #n chart
    content/boss/<enc>_<N|H>/sections.html       after pulls, before auto sections
    content/boss/<enc>_<N|H>/stats_extra.html    extra hero stat tiles
    content/players/<Name>/verdict.html          player verdict panel
    content/players/<Name>/sections.html
    content/players/<Name>/stats_extra.html
    content/officers/hero.html + body.html       officers annex (noindex)

Boss display names: refs/zone.json optional {"boss_names": {"<enc_id>": "..."}}
(localized); fallback = encounter name from the log (English).

CLI:
    python3 pages.py [--only hub|boss|players|officers]
"""
import argparse
import html
import json
import os
import re
import secrets
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wcl import Backend, load_config, load_env, save_config, workdir_from_args
from ingest import RAID_CDS

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
THEME_DEFAULT = os.path.join(os.path.dirname(SCRIPTS_DIR), "themes", "default.css")

# ------------------------------------------------------------------ UI strings

LOCALES = {
    "en": {
        "raid_report": "Raid report", "back_hub": "← back to report",
        "night_of": "night of", "pulls": "pulls", "kill": "kill", "kills": "kills",
        "combat_time": "combat time", "deaths": "deaths", "wipe": "wipe",
        "fight_remaining": "% of fight remaining", "log": "log ↗",
        "synthesis": "Synthesis", "no_death": "No death.",
        "th_time": "t", "th_player": "player", "th_killing_blow": "killing blow",
        "th_last10": "last 10 seconds (damage taken)",
        "avoidable_title": "Avoidable damage — who eats what",
        "avoidable_sub": "sum over pulls; classification cross-checked vs zone refs",
        "avoidable_legend": ("“Avoidable” = strict individual dodge (telegraphed "
                             "zone); “reducible” = volume you can manage (timing, "
                             "position); assigned soaks are NOT fails. Tanks: part of "
                             "their intake is structural (post obligation)."),
        "cls_avoidable": "avoidable", "cls_reducible": "reducible", "cls_soak": "soak",
        "exec_title": "Execution — DPS & tanks",
        "exec_sub": "DoT uptime on main target, GCD downtime; average over the boss's pulls",
        "th_spec": "spec", "th_cpm": "casts/min", "th_downtime": "downtime",
        "th_dot_uptimes": "DoT uptimes",
        "exec_legend": ("Downtime = gaps between GCDs outside dead windows; includes "
                        "forced movement — compare within a role, not in absolute."),
        "heal_title": "Healing", "heal_sub": "overheal, tempo, mana, target split (per pull)",
        "th_pull": "pull", "th_healer": "healer", "th_healing": "healing",
        "th_overheal": "overheal", "th_mana_end": "mana end (min)",
        "th_heal_split": "to tanks/healers/dps",
        "heal_legend": ("High overheal on a short wipe pull is normal (panic heal); "
                        "read it on kills. Parenthesis = lowest mana touched."),
        "vs_tops_title": "Vs the best players of the spec",
        "vs_tops_sub": ("top1/top2 WarcraftLogs classic, same boss, same size, SAME "
                        "formulas — kill duration shown because it weighs on everything"),
        "th_boss": "boss", "th_duration": "duration", "th_key_uptimes": "key uptimes",
        "vs_tops_legend": ("A 2x slower kill mechanically inflates some uptimes and "
                           "changes CD ratios: compare executions, not raw numbers."),
        "deaths_night": "Deaths of the night",
        "avoid_taken_title": "Avoidable damage taken",
        "th_mechanic": "mechanic", "th_class": "class", "th_hits": "hits", "th_total": "total",
        "verdict": "Verdict",
        "deaths_on_kill": "deaths on kills", "first_deaths_wipe": "first deaths of wipes",
        "pulls_played": "pulls played",
        "result": "result", "diff": "diff", "fight": "fight",
        "analysis": "analysis →", "boss": "boss",
        "footer": ("Report generated from the WarcraftLogs log {wcl} — raw events. "
                   "Charter: factual analysis; every claim is dated (pull, timestamp) "
                   "and verifiable in the log."),
        "officers_title": "Officers annex",
        "pacing_legend": "boss · trash · out of combat — hover = segment detail",
        "longest_idles": "Longest out-of-combat gaps",
        "idle_between": "between", "idle_and": "and", "start": "start", "end": "end",
        "player_card": "player card", "dps_label": "DPS", "hps_label": "HPS",
        "taken_per_min": "taken/min",
    },
    "fr": {
        "raid_report": "CR de raid", "back_hub": "← retour au CR",
        "night_of": "soirée du", "pulls": "pulls", "kill": "kill", "kills": "kills",
        "combat_time": "temps de combat", "deaths": "morts", "wipe": "wipe",
        "fight_remaining": "% du combat restant", "log": "log ↗",
        "synthesis": "Synthèse", "no_death": "Aucune mort.",
        "th_time": "t", "th_player": "joueur", "th_killing_blow": "coup fatal",
        "th_last10": "10 dernières secondes (dégâts encaissés)",
        "avoidable_title": "Dégâts évitables — qui mange quoi",
        "avoidable_sub": "somme des pulls ; classification recoupée référentiel de zone",
        "avoidable_legend": ("« Évitable » = esquive individuelle stricte (zone "
                             "télégraphiée) ; « réductible » = volume pilotable (cadence, "
                             "position) ; les soaks assignés ne sont PAS des fails. "
                             "Tanks : une part des hits est structurelle (obligation de poste)."),
        "cls_avoidable": "évitable", "cls_reducible": "réductible", "cls_soak": "à encaisser (soak)",
        "exec_title": "Exécution — DPS & tanks",
        "exec_sub": "uptimes DoT sur cible principale, inactivité GCD ; moyenne des pulls du boss",
        "th_spec": "spec", "th_cpm": "casts/min", "th_downtime": "inactivité",
        "th_dot_uptimes": "uptimes DoT",
        "exec_legend": ("Inactivité = trous entre GCD hors périodes mortes ; comprend les "
                        "déplacements forcés — comparer entre joueurs du même rôle, pas "
                        "dans l'absolu."),
        "heal_title": "Soins", "heal_sub": "overheal, rythme, mana, répartition des cibles (par pull)",
        "th_pull": "pull", "th_healer": "soigneur", "th_healing": "soins",
        "th_overheal": "overheal", "th_mana_end": "mana fin (min)",
        "th_heal_split": "vers tanks/heals/dps",
        "heal_legend": ("Overheal élevé sur pull court de wipe = normal (panic heal) ; "
                        "à lire sur les kills surtout. Parenthèse = minimum de mana touché."),
        "vs_tops_title": "Vs les meilleurs joueurs de la spec",
        "vs_tops_sub": ("top1/top2 WarcraftLogs classic, même boss, même taille, MÊMES "
                        "formules — la durée de kill est affichée car elle pèse sur tout"),
        "th_boss": "boss", "th_duration": "durée", "th_key_uptimes": "uptimes clés",
        "vs_tops_legend": ("Un kill 2× plus lent gonfle mécaniquement certains uptimes et "
                           "change les ratios de CDs : comparer les exécutions, pas les "
                           "valeurs brutes seules."),
        "deaths_night": "Morts de la soirée",
        "avoid_taken_title": "Dégâts évitables encaissés",
        "th_mechanic": "mécanique", "th_class": "classe", "th_hits": "hits", "th_total": "total",
        "verdict": "Verdict",
        "deaths_on_kill": "morts sur kill", "first_deaths_wipe": "1res morts de wipe",
        "pulls_played": "pulls joués",
        "result": "résultat", "diff": "diff", "fight": "combat",
        "analysis": "analyse →", "boss": "boss",
        "footer": ("Compte-rendu généré depuis le log WarcraftLogs {wcl} — events "
                   "bruts. Charte : analyse factuelle, chaque constat est daté (pull, "
                   "timestamp) et vérifiable dans le log."),
        "officers_title": "Annexe officiers",
        "pacing_legend": "boss · trash · hors combat — survol = détail segment",
        "longest_idles": "Plus longs temps morts",
        "idle_between": "entre", "idle_and": "et", "start": "début", "end": "fin",
        "player_card": "fiche joueur", "dps_label": "DPS", "hps_label": "HPS",
        "taken_per_min": "pris/min",
    },
}

HEAD = """<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">{robots}
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@3.0.1/dist/chartjs-plugin-annotation.min.js"></script>
<style>{css}</style></head><body>
"""

FOOT = """<footer>{footer}</footer>
<script>{js}</script></body></html>"""

CHART_JS = """
function tlChart(id, cfg){
  const el = document.getElementById(id); if(!el) return;
  const io = new IntersectionObserver((es)=>{es.forEach(e=>{
    if(e.isIntersecting){ new Chart(el, cfg); io.disconnect(); }})},{rootMargin:'200px'});
  io.observe(el);
}
"""


# ------------------------------------------------------------------ generator

class Gen:
    def __init__(self, workdir):
        self.workdir = workdir
        self.be = Backend(workdir)
        self.cfg = load_config(workdir)
        self.code = self.cfg["report"]
        self.label = self.cfg["label"]
        self.lang = (self.cfg.get("lang") or "en").lower()
        self.L = LOCALES.get(self.lang, LOCALES["en"])
        self.wcl_url = f"https://classic.warcraftlogs.com/reports/{self.code}"
        self.an = os.path.join(workdir, "digests", "analysis")
        self.content = os.path.join(workdir, "content")
        self.out_pub = os.path.join(workdir, "pages", self.label)
        tok = self.cfg.get("officers_token")
        if not tok:
            tok = secrets.token_hex(4)
            self.cfg["officers_token"] = tok
            save_config(workdir, self.cfg)
        self.out_off = os.path.join(workdir, "pages",
                                    f"officers-{self.label}-{tok}")
        # Theme: workdir/theme.css overrides the bundled default.
        theme_p = os.path.join(workdir, "theme.css")
        if not os.path.exists(theme_p):
            theme_p = THEME_DEFAULT
        self.css = open(theme_p, encoding="utf-8").read() if os.path.exists(theme_p) else ""
        # Spell names: by id (refs/spell_names.json) + EN-name fallback map
        # built by crossing player_ability (EN names) x spell_names (id->local).
        sn_p = os.path.join(workdir, "refs", "spell_names.json")
        self.spell_names = ({int(k): v for k, v in
                             json.load(open(sn_p, encoding="utf-8")).items() if v}
                            if os.path.exists(sn_p) else {})
        self.name_map = {}
        for r in self.be.con.execute(
                "SELECT DISTINCT ability_name, ability_id FROM player_ability "
                "WHERE ability_name IS NOT NULL AND ability_id IS NOT NULL"):
            loc = self.spell_names.get(r["ability_id"])
            if loc and r["ability_name"] not in self.name_map:
                self.name_map[r["ability_name"]] = loc
        for fn in ("mechanics_ref.json", "spec_kpis.json"):
            p = os.path.join(workdir, "refs", fn)
            if not os.path.exists(p):
                continue

            def walk(o):
                if isinstance(o, dict):
                    if isinstance(o.get("id"), int) and o.get("name"):
                        loc = self.spell_names.get(o["id"])
                        if loc:
                            self.name_map.setdefault(o["name"], loc)
                    for v in o.values():
                        walk(v)
                elif isinstance(o, list):
                    for v in o:
                        walk(v)
            walk(json.load(open(p, encoding="utf-8")))
        # Boss display names: refs/zone.json boss_names (localized) else log name.
        self.boss_names = {}
        zp = os.path.join(workdir, "refs", "zone.json")
        if os.path.exists(zp):
            z = json.load(open(zp, encoding="utf-8"))
            self.boss_names = {int(k): v for k, v in
                               (z.get("boss_names") or {}).items()}
        self.spec_names = {}
        if os.path.exists(zp):
            z = json.load(open(zp, encoding="utf-8"))
            self.spec_names = z.get("spec_names") or {}
        # Night date from session.
        s = self.be.con.execute("SELECT start_ts FROM raid_session WHERE report=?",
                                (self.code,)).fetchone()
        self.night = ""
        if s and s["start_ts"]:
            from datetime import datetime, timezone
            self.night = datetime.fromtimestamp(
                s["start_ts"] / 1000, tz=timezone.utc).strftime("%d/%m/%Y")
        self.guild = self.cfg.get("guild") or ""

    # ------------------------------------------------------------- helpers

    def J(self, name, default=None):
        p = os.path.join(self.an, name)
        if not os.path.exists(p):
            return default
        return json.load(open(p, encoding="utf-8"))

    def frag(self, *parts):
        p = os.path.join(self.content, *parts)
        if os.path.exists(p):
            return open(p, encoding="utf-8").read()
        return ""

    def tr_name(self, en, ability_id=None):
        if ability_id and ability_id in self.spell_names:
            return self.spell_names[ability_id]
        return self.name_map.get(en, en)

    def tr_spec(self, s):
        return self.spec_names.get(s, s)

    def boss_name(self, enc_id, log_name):
        return self.boss_names.get(enc_id, log_name)

    def boss_slug(self, enc_id, diff, log_name):
        base = slugify(self.boss_name(enc_id, log_name))
        return base + ("-heroic" if diff == 4 else "")

    def write(self, path, content):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  wrote {os.path.relpath(path, self.workdir)} ({len(content) // 1024} kb)")

    def head(self, title, robots=""):
        return HEAD.format(lang=self.lang, title=esc(title), css=self.css,
                           robots=robots)

    def foot(self, js=""):
        wcl = f'<a href="{self.wcl_url}">{self.code}</a>'
        return FOOT.format(footer=self.L["footer"].format(wcl=wcl), js=js)

    # --------------------------------------------------------- chart pieces

    def timeline_pull_chart(self, cid, pull, dtps_series, h=240):
        dur = pull["duration_s"]
        tot = next((s for s in dtps_series if s.get("name") == "Total"), None)
        if not tot:
            return "", ""
        data = tot.get("data") or []
        n = max(1, len(data))
        step = dur / n
        b = {}
        for i, v in enumerate(data):
            b[int(i * step // 2) * 2] = b.get(int(i * step // 2) * 2, 0) + (v or 0)
        pts = [{"x": x, "y": round(b[x] / 2)} for x in sorted(b)]
        ann = {}
        for i, ph in enumerate(pull.get("phases") or []):
            if ph["t"] < 1:
                continue
            ann[f"ph{i}"] = {"type": "line", "xMin": ph["t"], "xMax": ph["t"],
                             "borderColor": "#e8b923", "borderWidth": 2,
                             "label": {"display": True, "content": ph["phase"],
                                       "rotation": -90, "position": "start",
                                       "color": "#e8b923",
                                       "backgroundColor": "rgba(20,23,28,.85)",
                                       "font": {"size": 10}}}
        for i, d in enumerate(pull.get("deaths") or []):
            ann[f"d{i}"] = {"type": "line", "xMin": d["t"], "xMax": d["t"],
                            "borderColor": "rgba(224,116,79,.8)", "borderWidth": 1,
                            "borderDash": [4, 3],
                            "label": {"display": True, "content": "☠ " + str(d["player"]),
                                      "rotation": -90, "position": "end",
                                      "color": "#e0744f",
                                      "backgroundColor": "rgba(20,23,28,.7)",
                                      "font": {"size": 9}}}
        for i, c in enumerate(pull.get("cds") or []):
            ann[f"c{i}"] = {"type": "point", "xValue": c["t"], "yValue": 0,
                            "backgroundColor": "#6db3f2", "radius": 4}
        cfg = {
            "type": "line",
            "data": {"datasets": [{"data": pts, "parsing": False,
                                   "borderColor": "#e0744f",
                                   "backgroundColor": "rgba(224,116,79,.15)",
                                   "fill": True, "pointRadius": 0,
                                   "borderWidth": 1.6, "tension": .25}]},
            "options": {
                "animation": False, "responsive": True, "maintainAspectRatio": False,
                "scales": {
                    "x": {"type": "linear", "min": 0, "max": round(dur),
                          "ticks": {"color": "#8a93a2", "callback": "__TICK__"},
                          "grid": {"color": "#2a2f3a"}},
                    "y": {"ticks": {"color": "#8a93a2"}, "grid": {"color": "#2a2f3a"}}},
                "plugins": {"legend": {"display": False},
                            "annotation": {"annotations": ann}}}}
        js = json.dumps(cfg, ensure_ascii=False)
        js = js.replace('"__TICK__"',
                        "v=>`${Math.floor(v/60)}:${String(v%60).padStart(2,'0')}`")
        return (f'<div class="chartbox" style="height:{h}px">'
                f'<canvas id="{cid}"></canvas></div>',
                f"tlChart('{cid}',{js});\n")

    def deaths_table(self, deaths, label_col=None):
        L = self.L
        if not deaths:
            return f'<p class="ok">{L["no_death"]}</p>'
        rows = []
        for d in deaths:
            win = " · ".join(f"{esc(self.tr_name(w['name']))} {fmt_n(w['total'])}"
                             for w in (d.get("window") or [])[:3] if w.get("total"))
            ph = (f' <span class="mut">[{esc(d.get("phase") or "")}]</span>'
                  if d.get("phase") else "")
            pre = f"{esc(d['label'])} · " if d.get("label") else ""
            rows.append(f"<tr><td>{pre}{fmt_dur(d['t'])}{ph}</td>"
                        f"<td><b>{esc(d['player'])}</b></td>"
                        f"<td class='ab-b'>{esc(self.tr_name(d.get('kb') or '?', d.get('kb_id')))}</td>"
                        f"<td class='mut'>{win}</td></tr>")
        return (f"<table class='tbl'><thead><tr><th>{L['th_time']}</th>"
                f"<th>{L['th_player']}</th><th>{L['th_killing_blow']}</th>"
                f"<th>{L['th_last10']}</th></tr></thead><tbody>"
                + "".join(rows) + "</tbody></table>")

    # --------------------------------------------------------- auto sections

    def auto_avoidable_section(self, boss_log_name, suffix):
        L = self.L
        av = self.J("avoidable.json", [])
        rows = [r for r in av if r["boss"] == boss_log_name and r["diff"] == suffix]
        if not rows:
            return ""
        mechs, players_, cell = {}, {}, {}
        for r in rows:
            mk = (r["ability"], r["class"], r["ability_id"])
            mechs[mk] = mechs.get(mk, 0) + (r["total"] or 0)
            players_.setdefault(r["player"], r["role"])
            c = cell.setdefault((r["player"], mk), [0, 0])
            c[0] += r["hits"]
            c[1] += r["total"] or 0
        top_m = [k for k, _ in sorted(mechs.items(), key=lambda kv: -kv[1])[:6]]
        mx = max((cell.get((p, m), [0, 0])[1] for p in players_ for m in top_m),
                 default=1) or 1
        cls_l = {"avoidable": L["cls_avoidable"], "reducible": L["cls_reducible"],
                 "soak": L["cls_soak"]}
        out = [f"<h2>{L['avoidable_title']} <small>{L['avoidable_sub']}</small></h2>",
               f"<div class='panel'><table class='tbl heatmap'><thead><tr>"
               f"<th>{L['th_player']}</th>"]
        for (mn, mc, mid) in top_m:
            out.append(f"<th>{esc(self.tr_name(mn, mid))}<br>"
                       f"<span class='mut'>{cls_l.get(mc, mc)}</span></th>")
        out.append("</tr></thead><tbody>")
        order = sorted(players_.items(), key=lambda kv: -sum(
            cell.get((kv[0], m), [0, 0])[1] for m in top_m))
        for pl, role in order:
            out.append(f"<tr><td><b>{esc(pl)}</b> <span class='mut'>{role}</span></td>")
            for m in top_m:
                c = cell.get((pl, m))
                if not c:
                    out.append("<td class='hm0'>—</td>")
                else:
                    lvl = min(4, 1 + int(3.0 * c[1] / mx))
                    out.append(f"<td class='hm{lvl}'>{c[0]}×<br>"
                               f"<span class='mut'>{fmt_n(c[1])}</span></td>")
            out.append("</tr>")
        out.append(f"</tbody></table><div class='legend'>{L['avoidable_legend']}"
                   f"</div></div>")
        return "".join(out)

    def auto_exec_section(self, boss_log_name, suffix):
        L = self.L
        ex = self.J("execution.json", [])
        rows = [r for r in ex if r["boss"] == boss_log_name and r["diff"] == suffix
                and r["role"] in ("dps", "tank")]
        if not rows:
            return ""
        agg = {}
        for r in rows:
            a = agg.setdefault(r["player"], {"spec": r["spec"], "act": 0, "cpm": 0,
                                             "down": 0, "dots": {}})
            w = r["active_s"]
            a["act"] += w
            a["cpm"] += r["cpm"] * w
            a["down"] += r["downtime_pct"] * w
            for k, v in (r.get("dots") or {}).items():
                d = a["dots"].setdefault(k, [0, 0])
                d[0] += v * w
                d[1] += w
        out = [f"<h2>{L['exec_title']} <small>{L['exec_sub']}</small></h2>",
               f"<div class='panel'><table class='tbl'><thead><tr>"
               f"<th>{L['th_player']}</th><th>{L['th_spec']}</th>"
               f"<th>{L['th_cpm']}</th><th>{L['th_downtime']}</th>"
               f"<th>{L['th_dot_uptimes']}</th></tr></thead><tbody>"]
        for pl, a in sorted(agg.items(),
                            key=lambda kv: -kv[1]["cpm"] / max(1, kv[1]["act"])):
            if not a["act"]:
                continue
            cpm = a["cpm"] / a["act"]
            down = a["down"] / a["act"]
            dots = " · ".join(
                f"{esc(self.tr_name(k))} <span class='{grade(v[0] / max(1, v[1]), 70, 90)}'>"
                f"{v[0] / max(1, v[1]):.0f}%</span>"
                for k, v in a["dots"].items() if v[1])
            out.append(f"<tr><td><b>{esc(pl)}</b></td>"
                       f"<td class='mut'>{esc(self.tr_spec(a['spec']))}</td>"
                       f"<td>{cpm:.0f}</td>"
                       f"<td class='{grade(down, 15, 30, invert=True)}'>{down:.0f}%</td>"
                       f"<td>{dots or '—'}</td></tr>")
        out.append(f"</tbody></table><div class='legend'>{L['exec_legend']}</div></div>")
        return "".join(out)

    def auto_heal_section(self, boss_log_name, suffix):
        L = self.L
        he = self.J("heals.json", [])
        rows = [r for r in he if r["boss"] == boss_log_name and r["diff"] == suffix
                and "heal_total" in r]
        if not rows:
            return ""
        out = [f"<h2>{L['heal_title']} <small>{L['heal_sub']}</small></h2>",
               f"<div class='panel'><table class='tbl'><thead><tr>"
               f"<th>{L['th_pull']}</th><th>{L['th_healer']}</th>"
               f"<th>{L['th_healing']}</th><th>{L['th_overheal']}</th>"
               f"<th>{L['th_cpm']}</th><th>{L['th_mana_end']}</th>"
               f"<th>{L['th_heal_split']}</th></tr></thead><tbody>"]
        for r in rows:
            ht = r.get("heal_to") or {}
            rep = f"{ht.get('tank', 0)} / {ht.get('healer', 0)} / {ht.get('dps', 0)} %"
            mana = (f"{r.get('mana_end_pct')}% ({r.get('mana_min_pct')}%)"
                    if r.get("mana_end_pct") is not None else "—")
            out.append(
                f"<tr><td>#{r['pull']}</td><td><b>{esc(r['player'])}</b> "
                f"<span class='mut'>{esc(self.tr_spec(r['spec']))}</span></td>"
                f"<td>{fmt_n(r['heal_total'])}</td>"
                f"<td class='{grade(r.get('overheal_pct', 0), 35, 50, invert=True)}'>"
                f"{r.get('overheal_pct', 0):.0f}%</td>"
                f"<td>{r.get('cpm', 0):.0f}</td><td>{mana}</td>"
                f"<td class='mut'>{rep}</td></tr>")
        out.append(f"</tbody></table><div class='legend'>{L['heal_legend']}</div></div>")
        return "".join(out)

    # ---------------------------------------------------------------- pages

    def encounters(self):
        return [dict(r) for r in self.be.con.execute(
            "SELECT encounter_id, boss, difficulty FROM pull WHERE report=? "
            "GROUP BY encounter_id, boss, difficulty ORDER BY MIN(start_time)",
            (self.code,))]

    def page_boss(self, enc_id, diff, log_name):
        L = self.L
        suffix = "H" if diff == 4 else "N"
        bd = self.J(f"boss_{enc_id}_{suffix}.json")
        if not bd:
            return
        slug = self.boss_slug(enc_id, diff, log_name)
        name = self.boss_name(enc_id, log_name)
        ckey = f"{enc_id}_{suffix}"
        pulls_ = bd["pulls"]
        kills = sum(1 for p in pulls_ if p["kill"])
        tot_s = sum(p["duration_s"] for p in pulls_)
        deaths_n = sum(len(p["deaths"]) for p in pulls_)
        size = self.cfg.get("size") or ""
        dlab = ("Heroic" if diff == 4 else "Normal") if self.lang == "en" else \
               ("Héroïque" if diff == 4 else "Normal")
        title = f"{name} {suffix}{size} — {L['raid_report']} {self.guild} {self.night}"
        h = [self.head(title)]
        h.append(f"""<header class="hero"><h1><em>{esc(name)}</em> — {dlab} {size}</h1>
<div class="sub">{L['night_of']} {self.night} · <a href="../index.html">{L['back_hub']}</a></div>
<div class="stats">
<div class="stat"><b class="{'r' if not kills else 'g'}">{len(pulls_)}</b><span>{L['pulls']}</span></div>
<div class="stat"><b class="{'g' if kills else 'r'}">{kills or '0'}</b><span>{L['kill']}</span></div>
<div class="stat"><b>{fmt_dur(tot_s)}</b><span>{L['combat_time']}</span></div>
<div class="stat"><b class="r">{deaths_n}</b><span>{L['deaths']}</span></div>
{self.frag('boss', ckey, 'stats_extra.html')}
</div></header><main>""")
        js_all = [CHART_JS]
        syn = self.frag("boss", ckey, "synthesis.html")
        if syn:
            h.append(f"<h2>{L['synthesis']}</h2><div class='panel'>{syn}</div>")
        h.append(self.frag("boss", ckey, "intro.html"))
        for p in pulls_:
            res = ("KILL" if p["kill"] else
                   f"{L['wipe']} — {p['fight_pct']:.1f} {L['fight_remaining']}")
            cls = "kill" if p["kill"] else "wipe"
            h.append(f"""<article class="boss"><header>
<span class="pbadge {cls}">#{p['pull']}</span>
<h3>{fmt_dur(p['duration_s'])} · {res}
 <a class="mut" href="{self.wcl_url}#fight={p['fight_id']}">{L['log']}</a></h3>
</header><div class="bbody">""")
            ch, js = self.timeline_pull_chart(
                f"tl{enc_id}{suffix}{p['pull']}", p, self.dtps_for(p["fight_id"]))
            h.append(ch)
            js_all.append(js)
            note = self.frag("boss", ckey, f"pull_{p['pull']}.html")
            if note:
                h.append(f"<div class='panel note'>{note}</div>")
            h.append(self.deaths_table(p["deaths"]))
            h.append("</div></article>")
        h.append(self.frag("boss", ckey, "sections.html"))
        h.append(self.auto_avoidable_section(bd.get("boss"), suffix))
        h.append(self.auto_exec_section(bd.get("boss"), suffix))
        h.append(self.auto_heal_section(bd.get("boss"), suffix))
        h.append("</main>")
        h.append(self.foot("".join(js_all)))
        self.write(os.path.join(self.out_pub, slug, "index.html"), "".join(h))

    _dtps_cache = None

    def dtps_for(self, fid):
        if self._dtps_cache is None:
            self._dtps_cache = {}
            for r in self.be.con.execute(
                    "SELECT fight_id, payload FROM deep_graph "
                    "WHERE report=? AND kind='dtps'", (self.code,)):
                self._dtps_cache[r["fight_id"]] = json.loads(r["payload"])
        return self._dtps_cache.get(fid) or []

    def hub_boss_table(self):
        L = self.L
        rows = self.be.con.execute(
            "SELECT encounter_id, boss, difficulty, COUNT(*) pulls, SUM(kill) kills, "
            "SUM(duration_s) dur, MIN(fight_pct) best, MIN(start_time) st "
            "FROM pull WHERE report=? GROUP BY encounter_id, difficulty ORDER BY st",
            (self.code,)).fetchall()
        dj = self.J("deaths.json", [])
        dcount = {}
        for d in dj:
            k = (d["boss"], d["diff"])
            dcount[k] = dcount.get(k, 0) + 1
        size = self.cfg.get("size") or ""
        out = [f"<div class='panel'><table class='tbl'><thead><tr>"
               f"<th>{L['th_boss']}</th><th>{L['diff']}</th><th>{L['pulls']}</th>"
               f"<th>{L['result']}</th><th>{L['fight']}</th><th>{L['deaths']}</th>"
               f"<th></th></tr></thead><tbody>"]
        for r in rows:
            enc, diff = r["encounter_id"], r["difficulty"]
            sl = self.boss_slug(enc, diff, r["boss"])
            nm = self.boss_name(enc, r["boss"])
            suffix = "H" if diff == 4 else "N"
            res = (f"<span class='ok'>{L['kill']}</span>" if r["kills"]
                   else f"<span class='kpi-r'>{r['best']:.0f} {L['fight_remaining']}</span>")
            dn = dcount.get((r["boss"], suffix), 0)
            dl = (f"<span class='kpi-r'>H{size}</span>" if diff == 4 else f"N{size}")
            out.append(f"<tr><td><a href='{sl}/'><b>{esc(nm)}</b></a></td>"
                       f"<td>{dl}</td><td>{r['pulls']}</td><td>{res}</td>"
                       f"<td>{fmt_dur(r['dur'])}</td><td>{dn}</td>"
                       f"<td><a href='{sl}/'>{L['analysis']}</a></td></tr>")
        out.append("</tbody></table></div>")
        return "".join(out)

    def roster(self):
        return [r["player_name"] for r in self.be.con.execute(
            "SELECT DISTINCT player_name FROM composition WHERE report=? "
            "ORDER BY player_name", (self.code,)) if r["player_name"]]

    def hub_players_links(self):
        items = []
        for p in self.roster():
            slug = slugify(p)
            if os.path.exists(os.path.join(self.out_pub, "players", slug, "index.html")):
                items.append(f"<a class='tag' href='players/{slug}/'>{esc(p)}</a>")
            else:
                items.append(f"<span class='tag'>{esc(p)}</span>")
        return "<div class='panel'><div class='flex'>" + " ".join(items) + "</div></div>"

    def hub_pacing(self):
        L = self.L
        pacing = self.J("pacing.json") or {}
        tot = pacing.get("totals_s") or {}
        segs = pacing.get("segments") or []
        total = sum(tot.values()) or 1
        bar = ["<div style='display:flex;height:26px;border-radius:8px;overflow:hidden;"
               "border:1px solid var(--line)'>"]
        colors = {"boss": "#e0744f", "trash": "#8a93a2", "idle": "#232838"}
        for s in segs:
            w = 100.0 * (s["e"] - s["s"]) / total
            t = f"{s.get('name', 'idle')} ({fmt_dur(s['e'] - s['s'])})"
            bar.append(f"<div title=\"{esc(t)}\" style='width:{w:.2f}%;"
                       f"background:{colors[s['kind']]}'></div>")
        bar.append("</div>")
        leg = (f"■ boss ({fmt_dur(tot.get('boss', 0))}) · "
               f"■ trash ({fmt_dur(tot.get('trash', 0))}) · "
               f"■ idle ({fmt_dur(tot.get('idle', 0))}) — {L['pacing_legend']}")
        idles = sorted((s for s in segs if s["kind"] == "idle"),
                       key=lambda x: x["e"] - x["s"], reverse=True)[:6]
        li = []
        for i in idles:
            prev = [s for s in segs if s["e"] <= i["s"] and s["kind"] != "idle"]
            nxt = [s for s in segs if s["s"] >= i["e"] and s["kind"] != "idle"]
            li.append(f"<li><b>{fmt_dur(i['e'] - i['s'])}</b> {L['idle_between']} "
                      f"« {esc(prev[-1]['name'] if prev else L['start'])} » "
                      f"{L['idle_and']} « {esc(nxt[0]['name'] if nxt else L['end'])} »</li>")
        return (f"<div class='panel'>{''.join(bar)}"
                f"<div class='legend'>{leg}</div>"
                f"<h4 style='margin-top:14px'>{L['longest_idles']}</h4>"
                f"<ul class='tight'>{''.join(li)}</ul></div>")

    def page_hub(self):
        L = self.L
        hero = self.frag("hub", "hero.html") or (
            f"<header class='hero'><h1>{L['raid_report']} — <em>{esc(self.guild)}</em>"
            f" · {esc(self.cfg.get('zone_name') or '')}</h1>"
            f"<div class='sub'>{L['night_of']} {self.night} · "
            f"<a href='{self.wcl_url}'>WCL ↗</a></div></header>")
        body = self.frag("hub", "body.html") or (
            "__BOSS_TABLE__ __PLAYERS_LINKS__ __PACING__")
        body = (body.replace("__BOSS_TABLE__", self.hub_boss_table())
                    .replace("__PLAYERS_LINKS__", self.hub_players_links())
                    .replace("__PACING__", self.hub_pacing()))
        title = (f"{L['raid_report']} — {self.guild} · "
                 f"{self.cfg.get('zone_name') or ''} · {self.night}")
        h = [self.head(title), hero, "<main>", body, "</main>",
             self.foot(CHART_JS)]
        self.write(os.path.join(self.out_pub, "index.html"), "".join(h))

    def page_officers(self):
        L = self.L
        hero = self.frag("officers", "hero.html")
        body = self.frag("officers", "body.html")
        if not (hero or body):
            return
        h = [self.head(f"{L['officers_title']} — {self.label}",
                       robots='\n<meta name="robots" content="noindex,nofollow">'),
             hero, "<main>", body, "</main>", self.foot()]
        self.write(os.path.join(self.out_off, "index.html"), "".join(h))

    def page_player(self, name):
        L = self.L
        bench = [b for b in self.J("bench.json", []) if b["player"] == name]
        ex = [r for r in self.J("execution.json", []) if r["player"] == name]
        av = [r for r in self.J("avoidable.json", []) if r["player"] == name]
        dj = [d for d in self.J("deaths.json", []) if d["player"] == name]
        if not ex:
            return
        specs = sorted({r["spec"] for r in ex})
        role = ex[0]["role"]
        kill_fids = {r["fight_id"] for r in self.be.con.execute(
            "SELECT fight_id FROM pull WHERE report=? AND kill=1", (self.code,))}
        # Qualified deaths: on a KILL (always significant) vs first deaths of a
        # wipe (seq<=2, probable trigger) — dying in the collective wipe is NOT
        # an individual fail; the raw counter lies.
        d_kill = [d for d in dj if d["fight_id"] in kill_fids]
        d_first = [d for d in dj if d["fight_id"] not in kill_fids and d["seq"] <= 2]
        title = f"{name} — {L['player_card']} {self.night} · {self.guild}"
        h = [self.head(title)]
        h.append(f"""<header class="hero"><h1><em>{esc(name)}</em> — \
{esc(' / '.join(self.tr_spec(s) for s in specs))}</h1>
<div class="sub">{L['night_of']} {self.night} · \
<a href="../../index.html">{L['back_hub']}</a></div>
<div class="stats">
<div class="stat"><b class="{'r' if d_kill else 'g'}">{len(d_kill)}</b>\
<span>{L['deaths_on_kill']}</span></div>
<div class="stat"><b class="{'r' if d_first else 'g'}">{len(d_first)}</b>\
<span>{L['first_deaths_wipe']}</span></div>
<div class="stat"><b>{len({r['fight_id'] for r in ex})}</b>\
<span>{L['pulls_played']}</span></div>
{self.frag('players', name, 'stats_extra.html')}
</div></header><main>""")
        verdict = self.frag("players", name, "verdict.html")
        if verdict:
            h.append(f"<h2>{L['verdict']}</h2><div class='panel'>{verdict}</div>")
        if bench:
            boss_by_log = {r["boss"]: (r["encounter_id"], r["difficulty"])
                           for r in self.be.con.execute(
                               "SELECT DISTINCT boss, encounter_id, difficulty "
                               "FROM pull WHERE report=?", (self.code,))}
            h.append(f"<h2>{L['vs_tops_title']} <small>{L['vs_tops_sub']}</small></h2>")
            h.append(f"<div class='panel'><table class='tbl'><thead><tr>"
                     f"<th>{L['th_boss']}</th>"
                     f"<th>{L['hps_label'] if role == 'healer' else L['dps_label']}</th>"
                     f"<th>{L['th_duration']}</th><th>{L['th_downtime']}</th>"
                     f"<th>{L['th_key_uptimes']}</th></tr></thead><tbody>")
            for b in bench:
                m = b["mine"]
                rows = [("<b>" + esc(name) + "</b>", m.get("amount_ps"),
                         b["duration_s"], m.get("downtime_pct"),
                         m.get("dots") or m.get("buffs") or {})]
                for t in b["tops"]:
                    rows.append((f"<span class='mut'>top{t['rank']} {esc(t['player'])}</span>",
                                 t.get("amount_ps"), t.get("duration_s"),
                                 t.get("downtime_pct"), t.get("dots") or t.get("buffs") or {}))
                first = True
                enc_diff = boss_by_log.get(b["boss"])
                bslug = (self.boss_slug(enc_diff[0], enc_diff[1], b["boss"])
                         if enc_diff else "")
                for (who, aps, dur, down, ups) in rows:
                    up_s = " · ".join(f"{esc(self.tr_name(k))} {v:.0f}%"
                                      for k, v in list(ups.items())[:3])
                    bcell = (f"<td rowspan='{len(rows)}'><a href='../../{bslug}/'>"
                             f"{esc(self.boss_name(enc_diff[0], b['boss']) if enc_diff else b['boss'])}"
                             f"</a></td>") if first else ""
                    first = False
                    h.append(f"<tr>{bcell}<td>{who} — {fmt_n(aps)}/s</td>"
                             f"<td>{fmt_dur(dur or 0)}</td>"
                             f"<td>{'' if down is None else f'{down:.0f}%'}</td>"
                             f"<td>{up_s}</td></tr>")
            h.append(f"</tbody></table><div class='legend'>{L['vs_tops_legend']}"
                     f"</div></div>")
        if dj:
            h.append(f"<h2>{L['deaths_night']}</h2><div class='panel'>")
            h.append(self.deaths_table(
                [{"t": d["t"], "player": f"{d['boss']} #{d['pull']}{d['diff']}",
                  "kb": d["killing_blow"], "kb_id": d.get("kb_id"),
                  "window": [{"name": w["name"], "total": w["total"]}
                             for w in d["window_damage"]]} for d in dj]))
            h.append("</div>")
        if av:
            agg = {}
            for r in av:
                k = (r["boss"], r["ability"], r["class"], r["ability_id"])
                a = agg.setdefault(k, [0, 0])
                a[0] += r["hits"]
                a[1] += r["total"] or 0
            cls_l = {"avoidable": L["cls_avoidable"], "reducible": L["cls_reducible"],
                     "soak": L["cls_soak"]}
            h.append(f"<h2>{L['avoid_taken_title']}</h2><div class='panel'>"
                     f"<table class='tbl'><thead><tr><th>{L['th_boss']}</th>"
                     f"<th>{L['th_mechanic']}</th><th>{L['th_class']}</th>"
                     f"<th>{L['th_hits']}</th><th>{L['th_total']}</th></tr></thead><tbody>")
            for (boss, ab, cl, mid), (hits, tot) in sorted(agg.items(),
                                                           key=lambda kv: -kv[1][1]):
                h.append(f"<tr><td>{esc(self.boss_name_by_log(boss))}</td>"
                         f"<td class='ab-b'>{esc(self.tr_name(ab, mid))}</td>"
                         f"<td class='mut'>{cls_l.get(cl, cl)}</td>"
                         f"<td>{hits}</td><td>{fmt_n(tot)}</td></tr>")
            h.append("</tbody></table></div>")
        h.append(self.frag("players", name, "sections.html"))
        h.append("</main>")
        h.append(self.foot())
        self.write(os.path.join(self.out_pub, "players", slugify(name),
                                "index.html"), "".join(h))

    _log_to_enc = None

    def boss_name_by_log(self, log_name):
        if self._log_to_enc is None:
            self._log_to_enc = {r["boss"]: r["encounter_id"]
                                for r in self.be.con.execute(
                                    "SELECT DISTINCT boss, encounter_id FROM pull "
                                    "WHERE report=?", (self.code,))}
        enc = self._log_to_enc.get(log_name)
        return self.boss_name(enc, log_name) if enc else log_name


# -------------------------------------------------------------------- helpers

def esc(s):
    return html.escape(str(s if s is not None else ""))


def slugify(s):
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or "x"


def fmt_dur(s):
    return f"{int(s // 60)}:{int(s % 60):02d}"


def fmt_n(v):
    if v is None:
        return "—"
    if v >= 1e6:
        return f"{v / 1e6:.1f} M"
    if v >= 1e3:
        return f"{v / 1e3:.0f} k"
    return str(int(v))


def grade(v, lo, hi, invert=False):
    if v is None:
        return ""
    if invert:
        return "kpi-g" if v <= lo else ("kpi-o" if v <= hi else "kpi-r")
    return "kpi-g" if v >= hi else ("kpi-o" if v >= lo else "kpi-r")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--only", default=None,
                    choices=[None, "hub", "boss", "players", "officers"])
    args = ap.parse_args()
    wd = workdir_from_args(args)
    load_env(wd)
    g = Gen(wd)
    if args.only in (None, "boss"):
        for e in g.encounters():
            g.page_boss(e["encounter_id"], e["difficulty"], e["boss"])
    if args.only in (None, "players"):
        for p in g.roster():
            g.page_player(p)
    if args.only in (None, "hub"):     # hub last: player links resolve
        g.page_hub()
    if args.only in (None, "officers"):
        g.page_officers()
    print("pages ok ->", g.out_pub)


if __name__ == "__main__":
    main()
