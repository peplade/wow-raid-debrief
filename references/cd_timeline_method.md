# Méthode — timelines de cooldowns par boss (référence top-parse)

Produit, **par boss**, une frise temporelle montrant où un **top-parse de la spé**
pose ses cooldowns (lanes étiquetées) + des **lignes verticales** pour les moments
clés du combat (phases, abilités majeures, kills d'adds, seuils de PV), **tout
sourcé du log** (aucun timing inventé). Exemple live : `sim.elade.eu/raids/bourlingo`
(Moine Mistweaver). Implémentation de référence : `~/raids/bourlingo-mw/extract_hm_timelines.py`
(extraction → `boss_timelines.json`) + `build_bourlingo.py` (rendu HTML).

Généralise à **n'importe quelle spé** (DPS/heal/tank) : changer className/specName,
la liste des sorts CD, et la config des repères de combat.

## Pipeline

1. **Top-parses par boss** — `worldData.encounter(id).characterRankings(metric:<dps|hps>,
   difficulty, size, className, specName)` → pour chaque boss, `rankings[i].report.{code,fightID}`
   + `name` du joueur. (zone encounter ids : `worldData.zone(id).encounters{id name}`.)
2. **Bornes + acteurs** — `report.fights(fightIDs).{startTime,endTime,phaseTransitions{id startTime}}`
   + `masterData.actors{id name type}` + `masterData.abilities{gameID name}`.
   - id joueur = acteur **type=="Player"** ; id boss/add = **type=="NPC"**.
     ⚠️ une map nom→id sur TOUS les types provoque des collisions d'homonymes
     (joueur masqué par un NPC) → casts joueur vides. Toujours filtrer par type.
3. **Casts CD du joueur** — `events(dataType:Casts, sourceID:<playerId>)`, garder `type=="cast"`,
   `(timestamp-startTime)` → secondes. Mapper les guid de CD voulus.
4. **Repères de combat (lignes verticales), par source SOURCÉE :**
   - **Phases** : `phaseTransitions{id startTime}`, garder `id>=2` (id 1 = pull). Fiable, universel.
   - **Cast du boss** : `events(dataType:Casts, hostilityType:Enemies)` filtré sur `abilityGameID`.
     Nom via `masterData.abilities`. (récurrent → toutes les occurrences.)
   - **Mort de NPC** : `events(dataType:Deaths, hostilityType:Enemies)`, `targetID`→nom
     (kills de mini-boss, "tour clear" = mort d'un NPC nommé, etc.).
   - **Pop d'adds (transition)** : 1er cast par acteur ennemi dont le nom matche un préfixe
     (ex. "Embodied"), puis **cluster** (gap>5 s) → 1 ligne par vague.
   - **Seuil de PV du boss** : `graph(dataType:DamageDone, targetID:<bossId>)`.
     ⚠️ les séries sont des **tableaux de valeurs par bucket** (floats), PAS des paires
     `[t,v]` ; l'axe x est implicite (startTime→endTime, N buckets). Sommer les séries
     élément par élément, cumuler ; franchissement de HP `thr%` quand cumul ≥ `(100-thr)%`
     du total ; `t = idx/N * durée`.
   - **Abilité sans cast** (environnementale/scriptée, ex. Imploding Energy Malkorok) :
     `events(dataType:DamageTaken)` filtré sur `abilityGameID`, dédupe par gap>5 s.
   - **Span** (fenêtre, ex. Blood Rage 20 s) : cast déclencheur + durée fixe → bande.
5. **Rendu** — CSS : `track` relatif ; pills CD en **lanes** (un top:px par type) positionnées
   à `left:t/durée%` ; lignes = `position:absolute;top:0;bottom:0;border-left:dotted`
   (label affiché **une fois par type** via un `seen`) ; span = div translucide. Survol = timing.

## Pièges WCL gravés (vérifiés sur ce projet)

- **Noms de sorts retail sur MoP Classic** : WCL affiche Uplift comme **"Vivify" (116670)** ;
  **130316** = une 2e variante d'Uplift jouée par d'autres builds. Pour « Uplift / soin actif »,
  compter **les deux ids**. Vérifier l'identité par hits/cast (≈7 cibles/cast = bien le multi-cible RM).
- **`table`** : données sous **`.data.entries`** (pas `.entries`).
- **DamageTaken + targetID** renvoie **0 silencieusement** → fetch complet + filtre, ou passer par `graph`.
- **cast id ≠ buff id** : Renewing Mist cast=115151 / buff=119611 ; Mana Tea cast=115294 / buff=115867.
- **Vérifier la méca avant d'étiqueter** : nom via `masterData.abilities` ou Wowhead, jamais deviné.
- **Ne pas inventer** : si un signal ne résout pas proprement (ex. seuils PV par-boss de Fallen
  Protectors qui shadow-phase ; "tour 1" Galakras), le **retirer** ou demander le bon signal au
  domain-expert (l'user a fourni : transitions FP = pops d'adds Embodied ; tours Galakras = morts
  de Lieutenant Krugruk / Master Cannoneer Dagryn).
- **Config par boss** : un dict `RULES[encId] = {phase, cast[], dmg[], death/deathmap, spawn, span[], hp}`
  rend l'ensemble lisible et auditable (voir extract_hm_timelines.py).

## Coût / robustesse

~4-6 appels WCL par boss (rankings, fights+masterData, casts joueur, casts/deaths ennemis, graph).
Cache `wcl_raw` (lzma) → re-runs gratuits. Tout est reconstructible depuis les events.
