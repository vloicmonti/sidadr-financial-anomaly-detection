# SIDADR — Spécifications du moteur

> Statut : **proposition Phase 1, en attente de validation.**
> Chaque module liste ce qu'il fait, ses paramètres, et ses **critères d'acceptation** (ce que les tests doivent prouver).

---

## 0. Schéma de référence (données synthétiques)

Champs métier canoniques. Les fichiers importés y sont rattachés via l'étape de correspondance des colonnes.

| Champ canonique | Type | Obligatoire | Exemple fictif |
|---|---|---|---|
| `transaction_id` | texte | oui | `TX-BA-0001942` |
| `bank_code` | texte | oui | `BALP` |
| `bank_name` | texte | non | `Banque Alpha` |
| `branch_code` | texte | oui | `BALP-012` |
| `branch_city` | texte | non | `Douala` |
| `customer_id` | texte (local à la banque) | oui | `C-BALP-00412` |
| `customer_name` | texte | oui | `MANGA Jean Pierre` |
| `birth_date` | date | non | `1984-03-12` |
| `phone` | texte | non | `+999 6XX XX XX XX` (indicatif non attribué) |
| `account_number` | texte | oui | `BALP-012-0000412-77` (format inventé) |
| `operation_type` | énumération | oui | `DEPOSIT`, `WITHDRAWAL`, `TRANSFER_IN`, `TRANSFER_OUT`, `FEE` |
| `transaction_date` | date | oui | `2025-06-18` |
| `value_date` | date | non | `2025-06-19` |
| `amount` | décimal | oui | `250000.00` |
| `currency` | code ISO 4217 | oui | `XAF`, `EUR`, `USD` |

Le dictionnaire de données complet sera produit en Phase 2 (`docs/data_dictionary/`).

---

## Phase 2 — Générateur de données synthétiques

`python scripts/generate_synthetic_data.py --rows 20000 --seed 42 --out data/synthetic/`

- 4 à 6 banques fictives, 3 à 8 agences chacune, clients fictifs construits à partir de listes de prénoms/noms courants.
- Plusieurs fichiers de sortie (un par banque, formats et noms de colonnes **volontairement hétérogènes** : `Nom_Client` / `Customer_Name` / `Client`, séparateur `;` ou `,`, encodage UTF-8 ou cp1252, un fichier en XLSX).
- **Défauts injectés de façon contrôlée** : doublons exacts, doublons inter-fichiers, variantes de noms (inversion, tiret, initiales, espace manquant, accents, casse), clients réellement présents dans 2 banques, **homonymes réellement distincts** (pour mesurer les faux positifs), valeurs manquantes, montants atypiques, dates impossibles, devises inconnues, montants négatifs incohérents, codes agence inconnus.
- **Manifeste de vérité terrain** `defects_manifest.csv` : chaque défaut injecté avec son type et ses `record_id`. Il permet de mesurer précision et rappel de chaque détecteur.

**Acceptation** : même graine → CSV et JSON identiques octet par octet, XLSX au contenu identique ; le manifeste couvre 100 % des défauts injectés ; aucune donnée réelle (téléphones sur l'indicatif non attribué `+999`).

**Statut : livré en Phase 2** — voir `docs/data_dictionary/DATA_DICTIONARY.md`.

---

## Module A — Importation (Phase 3)

- Formats : CSV, XLSX ; plusieurs fichiers par session.
- Détection : encodage (charset-normalizer), séparateur (`csv.Sniffer` + repli sur `; , \t |`), ligne d'en-tête, colonnes vides, types (entier, décimal, date, texte, booléen) avec taux de conformité par colonne.
- Montants : gestion de `1 250 000,50` et `1,250,000.50` ; dates : formats ISO, `JJ/MM/AAAA`, `MM/JJ/AAAA` **ambigus signalés**, jamais devinés silencieusement.
- **Correspondance des colonnes** : proposition automatique (similarité du nom + table de synonymes configurable) avec un score de confiance ; **validation explicite obligatoire** avant traitement. Aucune correspondance n'est appliquée sans confirmation.
- Rapport d'import : lignes lues, lignes rejetées (avec motif), colonnes non rattachées.
- Sécurité : extensions et taille limitées, protection zip bomb, lecture XLSX `read_only`.

**Acceptation** : chaque fichier synthétique hétérogène est lu correctement ; un fichier tronqué, binaire renommé en `.csv`, vide ou trop gros produit une erreur claire, jamais une exception non gérée.

**Statut : livré en Phase 3.** Écarts assumés par rapport à la spécification :
- Détection d'encodage par règle simple (UTF-8 valide, sinon Windows-1252, sinon Latin-1 avec avertissement) plutôt que `charset-normalizer` : explicable, sans dépendance, suffisant pour les exports bancaires courants.
- Similarité des en-têtes avec `difflib` (bibliothèque standard) ; RapidFuzz arrivera en Phase 5 pour les noms de clients.
- L'ordre jour/mois d'une colonne est décidé à la majorité ; jusqu'à 5 % de valeurs contradictoires sont signalées comme dates impossibles au lieu de rendre la colonne ambiguë.

## Module B — Profilage et score qualité (Phase 3)

Par colonne : nb valeurs, manquantes, uniques, doublons, type dominant et taux de conformité, min/max/médiane/quantiles, distribution, dates hors plage.

**Score qualité** (0–100), configurable dans `config/quality_score.yaml` :

| Dimension | Mesure (par défaut) | Poids par défaut |
|---|---|---|
| Complétude | 1 − part des valeurs manquantes sur les champs obligatoires | 0,30 |
| Unicité | 1 − part des lignes impliquées dans un doublon exact | 0,20 |
| Validité | 1 − part des valeurs hors format/type/référentiel | 0,30 |
| Cohérence | 1 − part des lignes violant au moins une règle métier | 0,20 |

Le score est toujours affiché **avec sa décomposition** et la mention « indicateur interne paramétrable ». Les valeurs atypiques et alertes de similarité **ne pénalisent pas** le score (ce ne sont pas des erreurs).

**Acceptation** : un fichier parfait obtient 100 ; chaque défaut injecté fait baisser la bonne dimension ; changer les poids change le score de façon calculable à la main.

**Statut : livré en Phase 3.** Mesuré sur le jeu de référence : 100 % des valeurs obligatoires manquantes, des dates impossibles et des montants non numériques injectés sont retrouvés, sans faux positif. Limites actuelles :
- La cohérence (règles métier) n'est pas encore évaluée : son poids est redistribué et le rapport le dit.
- L'unicité compare des lignes complètes après normalisation des dates et montants ; les téléphones ne sont pas normalisés, donc la plupart des doublons inter-fichiers échappent à cette mesure. Ils seront détectés par clé en Phase 4.
- Les défauts injectés étant rares (environ 1 % des lignes), le score du jeu de référence est élevé (99,6). C'est attendu : le score mesure une proportion, pas un nombre d'alertes.

## Module C — Doublons exacts (Phase 4)

Quatre niveaux : ligne complète ; clé métier (`transaction_id`) ; combinaison de champs choisie ; inter-fichiers (même clé dans deux imports). Comparaison après normalisation légère (espaces de bord, casse) **paramétrable**. Restitution par groupe : enregistrements, source, nb de répétitions, champs identiques/différents.

**Acceptation** : rappel 100 % sur les doublons exacts du manifeste, 0 faux positif ; aucun enregistrement supprimé.

**Statut : livré en Phase 4.** Mesuré sur le jeu de référence : 120/120 lignes identiques, 60/60 doublons d'identifiant et 20/20 doublons inter-fichiers retrouvés, avec exactement les bons enregistrements, et aucune alerte de combinaison parasite.

| Niveau | Sévérité | Raison |
|---|---|---|
| Lignes strictement identiques | Erreur certaine | un identifiant d'opération doit être unique |
| Même identifiant, valeurs différentes | Erreur certaine | idem ; les champs divergents sont listés avec leurs valeurs d'origine |
| Même identifiant dans plusieurs fichiers | Incohérence probable | une même opération ne devrait relever que d'une source, mais un recouvrement d'extractions est possible |
| Même combinaison compte / date / type / montant / devise | Alerte à examiner | deux opérations identiques peuvent être légitimes |

Choix : la clé et les combinaisons sont configurables (`config/duplicates.yaml`) ; la comparaison des identifiants ignore la casse par défaut ; une combinaison déjà expliquée par un doublon d'identifiant n'est pas signalée une seconde fois. L'unicité du score qualité utilise désormais ce détecteur (lignes impliquées dans un doublon d'identifiant) ; les alertes de combinaison ne la pénalisent pas.

## Module D — Noms similaires (Phase 5)

1. **Normalisation** : NFKD + suppression des accents, majuscules, tirets/apostrophes/points → espaces, espaces multiples réduits. Version « collée » sans espaces conservée en parallèle.
2. **Blocking** : on ne compare que les paires partageant une clé (ex. jeton de nom le plus long sur ses 3 premières lettres, ou initiales triées). Objectif : passer de O(n²) à un volume gérable ; le taux de paires perdues par le blocking est mesuré.
3. **Scores** (RapidFuzz, 0–100) : `token_sort_ratio` (inversions), `token_set_ratio` (prénoms en plus), Jaro-Winkler (fautes de frappe), comparaison des formes collées (`JEANPIERRE` / `JEAN PIERRE`), compatibilité d'initiales (`J. P. MANGA`).
4. **Score composite** = maximum des stratégies applicables, avec **la stratégie gagnante enregistrée** dans l'explication.
5. **Catégories** (seuils dans `config/matching.yaml`) : élevée ≥ 92, intermédiaire 80–92, faible 70–80, ignorée < 70. Les seuils par défaut seront **calibrés sur le jeu synthétique** (courbe précision/rappel) et la justification documentée.
6. **Champs corroborants** : date de naissance ou téléphone identiques → confiance renforcée ; différents → confiance réduite et mention explicite.
7. Toute correspondance reste `review_alert` jusqu'à décision humaine. Avertissement permanent : « une similarité de nom ne prouve pas une identité ».

**Acceptation** : les 5 variantes du brief (`JEAN PIERRE MANGA`, `Jean-Pierre Manga`, `JEANPIERRE MANGA`, `J. P. MANGA`, `MANGA JEAN PIERRE`) sont regroupées ; les homonymes distincts du manifeste avec date de naissance différente ne sont **pas** classés en similarité élevée ; précision et rappel publiés par catégorie.

**Statut : livré en Phase 5** — méthode, calibration et limites détaillées dans [`NAME_MATCHING.md`](NAME_MATCHING.md). Seuils retenus après calibration : 92 / 88 / 80 (au lieu de 92 / 80 / 70 prévus), avec deux plafonds : non vérifiable → au plus intermédiaire, contredit → au plus faible.

## Module E — Clients multi-banques / multi-agences (Phase 6)

Regroupe les correspondances du module D (et les identifiants exacts éventuels) **entre `bank_code` ou `branch_code` différents**, en groupes (composantes connexes). Pour chaque groupe : banques, agences, champs concordants/discordants, score, stratégie. Décision par groupe : valider, rejeter, scinder.

**Acceptation** : les clients injectés comme multi-banques sont retrouvés ; un groupe n'est jamais présenté comme « même personne » sans validation.

**Statut : livré en Phase 6.** Écart volontaire : le regroupement transitif (composantes connexes) n'utilise **que les correspondances élevées**, c'est-à-dire corroborées. Une première version regroupait aussi les correspondances intermédiaires : des homonymes sans champ corroborant s'enchaînaient alors en groupes de 14 fiches, inexploitables. Les correspondances intermédiaires restent donc des **paires isolées**.

Mesuré sur le jeu de référence (100 personnes réellement clientes de deux banques) :

| Correspondances utilisées | Personnes retrouvées | Pureté des groupes multi-banques |
|---|---:|---:|
| Élevées seulement (`min_category: high`) | 85 % | **100 %** |
| Élevées et intermédiaires (défaut) | **100 %** | 63 % |

La pureté est la part des groupes dont toutes les fiches désignent réellement la même personne. Le réglage par défaut privilégie le rappel : aucun client multi-banques n'est oublié, au prix de paires d'homonymes à examiner, toujours signalées comme « similarité intermédiaire ». Chaque groupe affiche la concordance de la date de naissance et du téléphone (concordant, concordant là où renseigné, divergent, non renseigné) et son taux de liens directs. L'agence d'une fiche est l'agence la plus fréquente de ses opérations, faute d'agence de rattachement dans les données.

## Module F — Anomalies statistiques (Phase 7)

- **z-score robuste** : `0,6745 × (x − médiane) / MAD`, seuil par défaut 3,5 (Iglewicz & Hoaglin).
- **IQR** : hors `[Q1 − k·IQR ; Q3 + k·IQR]`, k = 3 par défaut.
- Calcul **par groupe** (type d'opération × devise) : comparer un virement en EUR à un dépôt en XAF n'a pas de sens.
- **Bornes métier** configurables (ex. frais > 1 000 000 XAF).
- Option : Isolation Forest / LOF, désactivés par défaut, présentés comme « score d'atypicité multivarié », jamais comme preuve.
- Sévérité : `atypical_value` pour les méthodes statistiques ; `probable_inconsistency` pour les bornes métier.

**Acceptation** : montants atypiques injectés détectés ; chaque constat contient valeur, méthode, seuil chiffré, groupe, explication et recommandation.

**Statut : livré en Phase 7.** Choix et écarts :
- Les méthodes travaillent sur le **logarithme** des montants. Sur l'échelle brute, le z-score robuste signalait 1 530 opérations, presque toutes saines : la queue droite naturelle des montants financiers suffit à dépasser le seuil.
- Les **bornes métier** (ex. plafond des frais) sont déplacées vers le moteur de règles (Phase 8). Ce sont des règles, pas des statistiques, et les dupliquer ici créerait deux constats pour une même cause.
- **Isolation Forest et LOF ne sont pas implémentés.** Étudiés puis écartés : les défauts du jeu de référence sont univariés, donc une méthode multivariée ne pourrait pas être validée ; ses scores sont aussi plus difficiles à expliquer à un contrôleur. À reconsidérer avec des données réelles et des variables contextuelles (historique du client, canal).
- Les groupes de moins de 30 opérations ne sont pas analysés et sont listés (ici, les devises invalides).

Mesuré sur le jeu de référence (z-score robuste 3,5 et IQR k = 3, sur le logarithme, par type d'opération × devise) :

| Graine | Atypiques injectés retrouvés | Signalements | Dont frais au-dessus du plafond | Sans défaut injecté |
|---|---:|---:|---:|---:|
| 42 | 77 / 80 (96 %) | 122 | 20 | 25 |
| 123 | 79 / 80 (99 %) | 134 | 19 | 36 |
| 2024 | 79 / 80 (99 %) | 132 | 19 | 34 |

- Les atypiques **oubliés** sont des virements injectés à environ 1,7 fois le maximum des montants sains, sous le seuil standard de 3,5. Abaisser le seuil à 3,0 les retrouverait tous, mais doublerait les signalements sans défaut.
- Les **signalements sans défaut injecté** sont surtout de petits frais (« valeur basse »). Dans le générateur, les frais suivent une loi uniforme et non log-normale : l'hypothèse log-normale les rend atypiques côté bas. C'est une vraie limite de la méthode, à surveiller sur des données réelles.
- L'IQR avec k = 3 ne signale rien que le z-score robuste n'ait déjà vu ; il est conservé comme seconde lecture, plus intuitive (« hors de l'intervalle habituel »).

## Module G — Règles métier (Phase 8)

Règles déclaratives dans `config/rules/*.yaml` :

```yaml
- id: R-AMOUNT-NEG-DEPOSIT
  description: Un dépôt ne peut pas avoir un montant négatif
  applies_to: { operation_type: [DEPOSIT] }
  check: { field: amount, op: ">=", value: 0 }
  severity: probable_inconsistency
  recommendation: Vérifier le sens de l'opération dans le système source
```

Opérateurs prévus : comparaisons, `in` / `not_in` (référentiels), `not_null`, `regex`, comparaison de deux champs (`value_date >= transaction_date`), date ≤ date de référence. Pas d'`eval` de code arbitraire.

Règles livrées : montant négatif interdit selon le type, date future, code agence inconnu, champ obligatoire manquant, devise hors référentiel, date de valeur antérieure à la date d'opération, incohérence type/montant (ex. `FEE` > plafond).

**Acceptation** : chaque règle a un test positif et négatif ; un YAML invalide est rejeté au démarrage avec un message précis.

**Statut : livré en Phase 8.** Fichier [`config/rules/transactions.yaml`](../../config/rules/transactions.yaml), commande `sidadr rules`.

| Règle | Sévérité | Dimension | Violations trouvées / injectées |
|---|---|---|---:|
| `R-MANDATORY` champs obligatoires | Erreur certaine | complétude | 240 / 240 |
| `R-CURRENCY-ISO` devise du référentiel | Erreur certaine | cohérence | 30 / 30 |
| `R-DATE-FUTURE` date ≤ date d'arrêté | Erreur certaine | cohérence | 20 / 20 |
| `R-AMOUNT-NEG-DEPOSIT` dépôt ≥ 0 | Incohérence probable | cohérence | 40 / 40 |
| `R-FEE-CAP` frais ≤ 50 000 XAF | Incohérence probable | cohérence | 20 / 20 |
| `R-VALUE-DATE` date de valeur ≥ date d'opération | Incohérence probable | cohérence | 40 / 40 |
| `R-BRANCH-REF` agence du référentiel | Incohérence probable | cohérence | 40 / 40 |

Chaque règle retrouve **exactement** les enregistrements injectés, sans aucun en trop. La dimension « cohérence » du score qualité est désormais évaluée (190 lignes sur 20 200 violent au moins une règle de cohérence) ; le score n'a plus de poids redistribué.

Choix :
- Les règles sont **typées** : une comparaison de dates compare des dates (format de chaque fichier respecté), une comparaison de montants compare des nombres. Une valeur absente ou illisible est comptée « non évaluable » au lieu d'être jugée ; elle est déjà mesurée par la complétude ou la validité.
- Une règle porte sa **dimension** : la règle des champs obligatoires relève de la complétude et n'est donc pas comptée une seconde fois dans la cohérence.
- Les paramètres (`$reference_date`, `$fee_cap_xaf`) et les référentiels (CSV) sont déclarés dans le fichier ; la date d'arrêté peut être remplacée en ligne de commande (`--reference-date`).
- Validation complète au chargement : champ, opérateur, sévérité, type de valeur, paramètre, référentiel, expression régulière, identifiant en double. Aucune expression n'est jamais exécutée comme du code.
- Les **bornes métier** prévues au module F (Phase 7) sont exprimées ici (`R-FEE-CAP`).

## Module H — Correction et journal d'audit (Phase 9)

Actions : modifier une cellule, exclure un enregistrement, confirmer / rejeter un constat, conserver les deux, annuler une correction. **Motif obligatoire.** Données originales intactes (voir `ARCHITECTURE.md` § 4.4). Toute action écrite dans `audit_log`.

**Acceptation** : après N corrections puis annulation de toutes, la vue de travail est identique à l'original ; le journal contient 2N entrées ; une action sans motif est refusée.

**Statut : livré en Phase 9.** Les trois critères d'acceptation sont couverts par des tests.

Fonctionnement :
- `sidadr session-create` importe les fichiers, puis enregistre un **instantané en lecture seule** (`original.parquet`) avec son empreinte SHA-256. L'empreinte est vérifiée à chaque ouverture : si l'original a été modifié, la session est bloquée.
- **Vue de travail** = original + corrections actives − enregistrements exclus. `sidadr analyze` relance tous les détecteurs sur cette vue et enregistre les constats (`findings.json`).
- **Décisions** (`confirmed`, `rejected`, `kept_both`, `deferred`) : elles sont rattachées à une **clé stable** (type de constat, règle, enregistrements), pas au numéro du constat, qui change d'une analyse à l'autre. Une décision survit donc à une nouvelle analyse. Une décision ne modifie jamais les données.
- **Corrections** typées : un montant doit être un nombre, une date une date valide, un type d'opération une valeur autorisée ; un champ obligatoire ne peut pas être vidé. Motif d'au moins 5 caractères obligatoire.
- **Exclusion** : l'enregistrement est retiré de la vue de travail, pas supprimé.
- **Annulation** (`sidadr undo C12` ou `D3`) : toute correction, exclusion ou décision est réversible, et l'annulation est elle-même journalisée.

Journal d'audit (`journal.sqlite`) :
- en **ajout seul**, imposé par des déclencheurs SQLite (toute modification ou suppression est refusée) ; les corrections et décisions ne peuvent pas non plus être supprimées, seulement annulées ;
- **chaîné par empreintes** : chaque entrée contient l'empreinte de la précédente. `sidadr history --verify` recalcule la chaîne et indique la première entrée altérée, même si quelqu'un a contourné les déclencheurs en modifiant le fichier directement ;
- chaque entrée : horodatage UTC, session, auteur, type d'action, cible, enregistrement, champ, ancienne et nouvelle valeur, motif, constat.

Limites : prototype mono-utilisateur sans authentification. L'auteur est déclaré (`--actor`, ou l'utilisateur du système) et non vérifié. La chaîne d'empreintes détecte une falsification, mais ne l'empêche pas : quelqu'un qui réécrirait toute la chaîne depuis le début ne serait pas détecté, faute de signature externe.

## Module I — Exports et rapports (Phase 10)

- Exports CSV et XLSX : dataset de travail, constats (par famille), doublons, corrections.
- Chaque export comporte une **fiche de traçabilité** (onglet XLSX ou fichier `.meta.json` pour CSV) : fichiers sources, date, version de SIDADR, version des règles, seuils utilisés, nombre de corrections, limites.
- Rapport qualité HTML autonome (score décomposé, synthèse des constats, décisions prises).
- Neutralisation de l'injection de formules.

**Acceptation** : un export réimporté dans SIDADR redonne les mêmes valeurs ; la fiche de traçabilité est toujours présente.

---

## Plan et estimation

| Phase | Contenu | Estimation | Dépend de |
|---|---|---|---|
| 1 | Architecture, specs, design system, maquettes | 1–2 j | — |
| 2 | Générateur synthétique + manifeste + dictionnaire | 1–1,5 j | 1 |
| 3 | Import + mapping colonnes + profilage + score | 2–3 j | 2 |
| 4 | Doublons exacts | 0,5–1 j | 3 |
| 5 | Noms similaires + calibration des seuils | 2–3 j | 3 |
| 6 | Multi-banques | 1 j | 5 |
| 7 | Anomalies | 1–1,5 j | 3 |
| 8 | Moteur de règles | 1,5 j | 3 |
| 9 | Corrections + audit | 1,5–2 j | 4–8 |
| 10 | Exports + rapport HTML + CLI → **v0.1 publiable** | 1,5 j | 9 |
| 11 | API FastAPI + frontend React (7 écrans) + validation visuelle | 6–9 j | 10 |
| 12–14 | Sécurité, perf, docs, captures, publication | 2–3 j | 11 |

Total indicatif : **22 à 30 jours de travail effectif**. Avec le M2 en parallèle, la v0.1 moteur (phases 2–10) est réaliste avant la mi-décembre ; l'interface complète vers fin janvier. La v0.1 est publiable seule et constitue déjà une preuve Data Engineering défendable.
