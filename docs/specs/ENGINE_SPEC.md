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
| `phone` | texte | non | `+237 6XX XX XX XX` (format fictif) |
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

**Acceptation** : même graine → fichiers identiques octet par octet ; le manifeste couvre 100 % des défauts injectés ; aucune donnée réelle.

---

## Module A — Importation (Phase 3)

- Formats : CSV, XLSX ; plusieurs fichiers par session.
- Détection : encodage (charset-normalizer), séparateur (`csv.Sniffer` + repli sur `; , \t |`), ligne d'en-tête, colonnes vides, types (entier, décimal, date, texte, booléen) avec taux de conformité par colonne.
- Montants : gestion de `1 250 000,50` et `1,250,000.50` ; dates : formats ISO, `JJ/MM/AAAA`, `MM/JJ/AAAA` **ambigus signalés**, jamais devinés silencieusement.
- **Correspondance des colonnes** : proposition automatique (similarité du nom + table de synonymes configurable) avec un score de confiance ; **validation explicite obligatoire** avant traitement. Aucune correspondance n'est appliquée sans confirmation.
- Rapport d'import : lignes lues, lignes rejetées (avec motif), colonnes non rattachées.
- Sécurité : extensions et taille limitées, protection zip bomb, lecture XLSX `read_only`.

**Acceptation** : chaque fichier synthétique hétérogène est lu correctement ; un fichier tronqué, binaire renommé en `.csv`, vide ou trop gros produit une erreur claire, jamais une exception non gérée.

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

## Module C — Doublons exacts (Phase 4)

Quatre niveaux : ligne complète ; clé métier (`transaction_id`) ; combinaison de champs choisie ; inter-fichiers (même clé dans deux imports). Comparaison après normalisation légère (espaces de bord, casse) **paramétrable**. Restitution par groupe : enregistrements, source, nb de répétitions, champs identiques/différents.

**Acceptation** : rappel 100 % sur les doublons exacts du manifeste, 0 faux positif ; aucun enregistrement supprimé.

## Module D — Noms similaires (Phase 5)

1. **Normalisation** : NFKD + suppression des accents, majuscules, tirets/apostrophes/points → espaces, espaces multiples réduits. Version « collée » sans espaces conservée en parallèle.
2. **Blocking** : on ne compare que les paires partageant une clé (ex. jeton de nom le plus long sur ses 3 premières lettres, ou initiales triées). Objectif : passer de O(n²) à un volume gérable ; le taux de paires perdues par le blocking est mesuré.
3. **Scores** (RapidFuzz, 0–100) : `token_sort_ratio` (inversions), `token_set_ratio` (prénoms en plus), Jaro-Winkler (fautes de frappe), comparaison des formes collées (`JEANPIERRE` / `JEAN PIERRE`), compatibilité d'initiales (`J. P. MANGA`).
4. **Score composite** = maximum des stratégies applicables, avec **la stratégie gagnante enregistrée** dans l'explication.
5. **Catégories** (seuils dans `config/matching.yaml`) : élevée ≥ 92, intermédiaire 80–92, faible 70–80, ignorée < 70. Les seuils par défaut seront **calibrés sur le jeu synthétique** (courbe précision/rappel) et la justification documentée.
6. **Champs corroborants** : date de naissance ou téléphone identiques → confiance renforcée ; différents → confiance réduite et mention explicite.
7. Toute correspondance reste `review_alert` jusqu'à décision humaine. Avertissement permanent : « une similarité de nom ne prouve pas une identité ».

**Acceptation** : les 5 variantes du brief (`JEAN PIERRE MANGA`, `Jean-Pierre Manga`, `JEANPIERRE MANGA`, `J. P. MANGA`, `MANGA JEAN PIERRE`) sont regroupées ; les homonymes distincts du manifeste avec date de naissance différente ne sont **pas** classés en similarité élevée ; précision et rappel publiés par catégorie.

## Module E — Clients multi-banques / multi-agences (Phase 6)

Regroupe les correspondances du module D (et les identifiants exacts éventuels) **entre `bank_code` ou `branch_code` différents**, en groupes (composantes connexes). Pour chaque groupe : banques, agences, champs concordants/discordants, score, stratégie. Décision par groupe : valider, rejeter, scinder.

**Acceptation** : les clients injectés comme multi-banques sont retrouvés ; un groupe n'est jamais présenté comme « même personne » sans validation.

## Module F — Anomalies statistiques (Phase 7)

- **z-score robuste** : `0,6745 × (x − médiane) / MAD`, seuil par défaut 3,5 (Iglewicz & Hoaglin).
- **IQR** : hors `[Q1 − k·IQR ; Q3 + k·IQR]`, k = 3 par défaut.
- Calcul **par groupe** (type d'opération × devise) : comparer un virement en EUR à un dépôt en XAF n'a pas de sens.
- **Bornes métier** configurables (ex. frais > 1 000 000 XAF).
- Option : Isolation Forest / LOF, désactivés par défaut, présentés comme « score d'atypicité multivarié », jamais comme preuve.
- Sévérité : `atypical_value` pour les méthodes statistiques ; `probable_inconsistency` pour les bornes métier.

**Acceptation** : montants atypiques injectés détectés ; chaque constat contient valeur, méthode, seuil chiffré, groupe, explication et recommandation.

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

## Module H — Correction et journal d'audit (Phase 9)

Actions : modifier une cellule, exclure un enregistrement, confirmer / rejeter un constat, conserver les deux, annuler une correction. **Motif obligatoire.** Données originales intactes (voir `ARCHITECTURE.md` § 4.4). Toute action écrite dans `audit_log`.

**Acceptation** : après N corrections puis annulation de toutes, la vue de travail est identique à l'original ; le journal contient 2N entrées ; une action sans motif est refusée.

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
