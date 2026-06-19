# Extraction manifest — plan exhaustif AVANT de traiter

**Pourquoi ce document existe.** Un CR raté commence par une extraction silencieusement
incomplète : une dimension qu'on néglige de tirer (ex : ne prendre que les *complétions*
de cast ennemi — sous-loggées quand plusieurs adds incantent en même temps — sans les
events de DÉGÂT du sort, donc « est-ce passé ? » irreconstructible après coup) ne se voit pas — la
page se génère, les chiffres « existent », et on découvre le trou des semaines plus tard
en voulant une dimension (scoreboard kicks). **La parade : déclarer en TÊTE de CR tout ce
qu'on doit extraire + à quoi ça sert, puis ASSERTER après ingest que chaque champ déclaré
a une couverture non-nulle.** Mieux vaut un manifeste explicite qu'un drop découvert à J+7.

> Règle d'or : **on extrait LARGE, une fois, au début** (le cache `wcl_raw` rend les
> re-runs gratuits). Ne jamais filtrer une dimension « parce qu'on n'en a pas besoin
> maintenant » — c'est ce filtrage qui crée les trous. Cf [[interpretation-traps]] (trap
> « champ droppé »), `feedback-crosscheck-mechanic-before-display`.

## Étape 0 du pipeline (à faire AVANT analyze/pages)

1. **Dérouler le manifeste ci-dessous** : pour chaque dimension, vérifier que la commande
   d'ingest correspondante a tourné ET que les champs requis sont peuplés.
2. **Lancer l'assertion de complétude** (`ingest.py verify` / probe) : toute dimension
   déclarée dont un champ-clé est à 0 / NULL sur >90 % des lignes = **ALARME**, pas un
   silence. Valider par un POSITIF attendu (un tank a forcément des dégâts subis ; un kill
   avec des adds casters a forcément des interruptions), jamais par l'absence d'erreur.
3. Noter explicitement ce qui est **absent par nature** (mécanique non loggée, sort sans
   event de cast) — un trou connu et écrit ≠ un trou ignoré.

## Manifeste des dimensions WCL (champs requis → section servie)

| Dimension | dataType / source | Champs REQUIS (ne jamais merger/dropper) | Sert |
|---|---|---|---|
| **Pulls / fights** | `fights`, `table Summary` | start/end, kill, boss%, phase, durée | pacing, scoping |
| **Dégâts subis** | `events DamageTaken` (FULL, filtrer code-side) | ts, target, **source**, ability, amount, mitigated/absorbed, buffs | évitable, qui-encaisse |
| **Dégâts infligés** | `events DamageDone` / `table` | source, **target (mob)**, ability, amount | switch cibles prioritaires |
| **Morts** | `Deaths` + recap | player, ts, killing-blow, recap avant-mort | dominos, deaths |
| **Soins** | `Healing` events + `table` | source, target, amount, **overheal**, tick | HPS, overheal, mana |
| **Ressources** | `graph Resources abilityID:100` | mana % timeline par acteur | mana floor, OOM |
| **Auras / buffs** | `Buffs`/`Debuffs` | applies/refresh/removes, **stacks**, ts | CDs, uptime, dispels-cibles |
| **Casts joueurs** | `events Casts` (PAS filtré CD-only !) | source, target, ability, ts, **begincast** | CDs, **kick-attempts** |
| **Casts ennemis** | `deep_aura kind='enemy_cast'` (begincast **ET** cast) | source(mob), ability, **begincast ts**, type | **opportunités de kick**, réaction — ⚠️ la *complétion* (`type='cast'`) est SOUS-LOGGÉE (cf gotcha), ne JAMAIS en déduire « passé » |
| **Interruptions** | `deep_aura kind='interrupt'` (ingéré) | ts, **source_id(kicker)**, **target_id(mob)**, **ability_id = SORT COUPÉ** (le sort de kick se redérive via `deep_cast`) | scoreboard kicks, couverture |
| **Frappe des sorts dangereux** | `deep_dmg_taken` par **`ability_id`** | ts, target, ability, amount | **« passé » = damage-as-landing** (vérité, complétions sous-loggées) |
| **Dispels** | `events Dispels` + `table` | ts, source, target, **extraAbility (sort dissipé)** | dispel-cibles, Mark |
| **Interrupt/Dispel agrégat** | `table Interrupts/Dispels` | par sort : **spellsBegun / spellsCompleted** | couverture par sort |
| **CombatantInfo** | `events CombatantInfo` | flask/food/gear au pull | conso, bench |
| **Trash** | fights sans encounterID | morts (qui/quoi/quand), top dmg | trash coût caché |
| **Top parses** | `characterRankings` | top1 spec/diff/taille | benchmark débit |

### Kicks — modèle de mesure (gravé, validé EdR 06-18/06-19) — cf [[kicks]]

Source = **tables INGÉRÉES propres**, scopées report+fight. NE JAMAIS re-parser `wcl_raw` :
l'approche re-parse (`extract_interrupts.py`) a été **jetée** (contamination inter-report), et
le « sort cast-less id 32747 / gate cible-incantait / `extraAbilityGameID` séparé » qu'elle
trimballait était un **artefact de cette approche, pas une vérité du log**. Les 4 sources :
- `deep_aura kind='enemy_cast'` type `begincast`/`cast` — débuts / complétions de cast ennemi ;
- `deep_aura kind='interrupt'` — coupures (`source_id`=kicker, `target_id`=mob, `ability_id`= **le sort COUPÉ**) ;
- `deep_cast` `ability_id` ∈ sorts d'interrupt **DÉDIÉS** — tentatives joueur ;
- `deep_dmg_taken` `ability_id`=<sort dangereux> — **la frappe** (damage-as-landing).

**Méthode de mesure** :
- *coupé* = event `interrupt` (autorité WCL).
- *tentative* = cast d'un sort d'interrupt **DÉDIÉ** (Contresort 2139, Pummel 6552, Wind Shear
  57994, Mind Freeze 47528, Skull Bash 106839, Rebuke 96231, Spear Hand Strike 116705,
  Counter Shot 147362, Silence 15487…). **Avenger's Shield (31935) n'est PAS dans la liste
  dédiée** (sort de DÉGÂT sur CD) : ses coupures sont créditées via les events `interrupt`
  → efficacité 100 %, 0 gaspillé. **Pas de « gate cible-incantait »** : l'exclusion on-CD se
  fait par la LISTE des sorts, jamais par une fenêtre temporelle (≤4 s = abandonné).
- *passé* = le sort a **FRAPPÉ** : event de DÉGÂT (`deep_dmg_taken ability_id`) **OU** complétion
  `type='cast'`, en **UNION**. La complétion seule sous-compte (cf gotcha « complétions ennemies
  sous-loggées » : Sha 273 begincast → 89 complétions) → le DÉGÂT est la vérité.
- *sans frappe* = ni coupé ni dégât loggé : add tué/CC, ou cast simultané non résolu au log.
  Vérifié sans dégât = **n'a touché personne** (PAS un kick raté à imputer).
- *couverture* (KPI raid) = coupé / (coupé + passé).
- *efficacité* = coupé / tentatives (sur sorts dédiés uniquement).

> Anti-patterns prouvés : (a) compter TOUS les casts d'Avenger's Shield comme tentatives →
> « 7 % d'efficacité, 139 gaspillés » (FAUX, AS est un sort de dégât) ; correctif = AS hors
> liste dédiée, créditée via interrupts → 100 %. (b) déduire « passé » du seul compte de
> complétions sous-compte massivement → toujours unir avec les events de DÉGÂT.
