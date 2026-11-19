# SIDADR — Dictionnaire des données synthétiques

> Toutes les données décrites ici sont **fictives**, générées par `src/sidadr/synthetic/`.
> Aucune donnée BEAC, bancaire ou client réelle n'est utilisée.

## 1. Contenu de `data/synthetic/`

| Fichier | Rôle |
|---|---|
| `alpha_T2.csv` | Opérations de la Banque Alpha (export « système A ») |
| `beta_T2.xlsx` | Opérations de la Banque Beta (export Excel, feuille `Opérations`) |
| `gamma_T2.csv` | Opérations de la Banque Gamma (export « système C ») |
| `reference/banks.csv` | Référentiel des banques |
| `reference/branches.csv` | Référentiel des agences (sert à détecter les codes agence inconnus) |
| `reference/currencies.csv` | Devises acceptées (XAF, EUR, USD) |
| `metadata.json` | Graine, période, date de référence, volumes, nombre de défauts par type |
| `_truth/defects_manifest.csv` | **Vérité terrain** : chaque défaut injecté et son emplacement exact |
| `_truth/customers.csv` | **Vérité terrain** : identité réelle (`person_id`) derrière chaque fiche client |

Les fichiers `_truth/` ne sont **jamais** lus par le moteur : ils servent uniquement à mesurer ses performances (précision, rappel).

Régénération : `python scripts/generate_synthetic_data.py --rows 20000 --seed 42 --out data/synthetic`

## 2. Schéma canonique des opérations

| Champ canonique | Type | Obligatoire | Description | Exemple |
|---|---|---|---|---|
| `transaction_id` | texte | oui | Identifiant unique de l'opération dans sa banque | `TX-BALP-0001942` |
| `bank_code` | texte | oui | Code de la banque | `BALP` |
| `branch_code` | texte | oui | Agence où l'opération a été passée | `BALP-018` |
| `branch_city` | texte | non | Ville de l'agence | `Douala` |
| `customer_id` | texte | oui | Identifiant client **local à la banque** (deux banques ne partagent pas d'identifiant) | `C-BALP-00412` |
| `customer_name` | texte | oui | Nom tel que saisi dans la banque | `MANGA Jean Pierre` |
| `birth_date` | date | non | Date de naissance | `1984-03-12` |
| `phone` | texte | non | Téléphone, indicatif fictif `+999` | `+999 628 19 59 95` |
| `account_number` | texte | oui | Compte (format inventé : agence-numéro-clé) | `BALP-018-0000022-75` |
| `operation_type` | énumération | oui | `DEPOSIT`, `WITHDRAWAL`, `TRANSFER_IN`, `TRANSFER_OUT`, `FEE` | `DEPOSIT` |
| `transaction_date` | date | oui | Date d'opération, période 01/04/2025 – 30/06/2025 | `2025-06-18` |
| `value_date` | date | non | Date de valeur (0 à 2 jours après l'opération) | `2025-06-19` |
| `amount` | décimal | oui | Montant **positif** ; le sens découle du type d'opération | `250000` |
| `currency` | code ISO 4217 | oui | Devise | `XAF` |

Date de référence du contrôle : **30/06/2025** (toute date postérieure est « future »). Plafond des frais : **50 000 XAF**.

## 3. Correspondance des colonnes par fichier

Les trois banques exportent les mêmes informations sous des formes différentes : c'est ce que le module d'import (Phase 3) devra résoudre.

| Champ canonique | `alpha_T2.csv` | `beta_T2.xlsx` | `gamma_T2.csv` |
|---|---|---|---|
| `transaction_id` | Ref_Operation | Transaction ID | txn_id |
| `bank_code` | Code_Banque | Bank | banque |
| `branch_code` | Code_Agence | Branch | agence |
| `branch_city` | Ville_Agence | Branch City | ville |
| `customer_id` | Num_Client | Customer ID | client_id |
| `customer_name` | Nom_Client | Customer_Name | Client |
| `birth_date` | Date_Naissance | Birth Date | naissance |
| `phone` | Telephone | Phone | tel |
| `account_number` | Num_Compte | Account Number | compte |
| `operation_type` | Type_Operation | Operation | type_op |
| `transaction_date` | Date_Operation | Transaction_Date | date |
| `value_date` | Date_Valeur | Value Date | date_valeur |
| `amount` | Montant | Amount | montant |
| `currency` | Devise | Currency | devise |
| — | — | — | commentaire (colonne toujours vide) |

| Convention | `alpha_T2.csv` | `beta_T2.xlsx` | `gamma_T2.csv` |
|---|---|---|---|
| Encodage | UTF-8 avec BOM | — (XLSX) | Windows-1252 |
| Séparateur | `;` | — | `,` |
| Dates | `JJ/MM/AAAA` | cellules date Excel | `AAAA-MM-JJ` |
| Décimales | virgule | nombre natif | point |
| Nom client | `NOM Prénoms` | `Prénoms NOM` | `NOM PRÉNOMS` |
| Téléphone | `+999 6XX XX XX XX` | `6XXXXXXXX` | `009996XXXXXXXX` |

## 4. Défauts injectés (`_truth/defects_manifest.csv`)

Colonnes : `defect_id`, `defect_type`, `file`, `row` (numéro de ligne de données, 1 = première ligne après l'en-tête), `transaction_id`, `field`, `original_value`, `injected_value`, `related_rows` (pour les doublons : `fichier:ligne` de la ligne d'origine).

| `defect_type` | Description | Taux par défaut | Constat attendu (phase) |
|---|---|---|---|
| `exact_duplicate_row` | Ligne recopiée à l'identique dans le même fichier | 0,6 % | doublon exact (4) |
| `duplicate_key` | Même `transaction_id`, montant différent de 1 à 10 % | 0,3 % | doublon de clé (4) |
| `cross_file_duplicate` | Ligne recopiée dans le fichier d'une autre banque | 0,1 % | doublon inter-fichiers (4) |
| `missing_mandatory` | Champ obligatoire vidé (nom, montant, date, devise, agence ou compte) | 1,2 % | complétude (3) |
| `invalid_date` | Date calendaire impossible (30 février, mois 13…) | 0,15 % | validité (3) |
| `future_date` | Date d'opération postérieure à la date de référence | 0,1 % | règle métier (8) |
| `unknown_currency` | Devise hors référentiel (`XFA`, `CFA`, `FCFA`, `EURO`) | 0,15 % | règle métier (8) |
| `negative_amount` | Dépôt à montant négatif | 0,2 % | règle métier (8) |
| `fee_above_cap` | Frais supérieurs au plafond de 50 000 XAF | 0,1 % | règle métier (8) |
| `unknown_branch` | Code agence absent du référentiel | 0,2 % | règle métier (8) |
| `value_date_before_transaction` | Date de valeur antérieure à la date d'opération | 0,2 % | règle métier (8) |
| `amount_not_numeric` | Montant non numérique (`N/A`, `#VALEUR!`, `12O 000`, `-`) | 0,1 % | validité (3) |
| `atypical_amount` | Montant XAF au moins 1,5 fois au-dessus du plafond des montants sains du même type | 0,4 % | anomalie statistique (7) |

Une ligne ne porte qu'un seul défaut injecté. Les montants sains sont écrêtés à `médiane × e^(3σ)` par type d'opération : les seules valeurs extrêmes du jeu sont donc celles injectées, ce qui rend la mesure du rappel des détecteurs d'anomalies interprétable.

## 5. Identités et noms (`_truth/customers.csv`)

Colonnes : `bank_code`, `customer_id`, `person_id`, `recorded_name`, `name_variant`, `homonym_of`.

Deux fiches qui partagent un `person_id` désignent **la même personne** ; deux `person_id` différents désignent **deux personnes distinctes**, même si leurs noms sont identiques.

| Situation | Proportion | Ce que le moteur doit faire |
|---|---|---|
| Client présent dans **deux banques** | 4 % des personnes | les rapprocher (module E), avec ou sans variante de nom |
| Client enregistré **deux fois dans la même banque** sous une variante | 2 % des personnes | les rapprocher (module D) |
| **Homonymes** : personnes distinctes au nom identique, dates de naissance différentes | 1 paire pour 500 lignes | **ne pas** les confondre (faux positifs à mesurer) |

Variantes de nom (`name_variant`) : `swapped` (ordre inversé), `hyphenated` (Jean-Pierre), `collapsed` (Jeanpierre, NGOBIYONG), `initials` (J. P. MANGA), `no_accents`, `typo` (une lettre modifiée, supprimée ou inversée), `extra_space`. Même sans variante (`none`), deux banques écrivent le même nom différemment, car leurs styles diffèrent (§ 3). Sur la seconde fiche d'une même personne, la date de naissance et le téléphone sont absents dans 30 % des cas, pour simuler une corroboration incomplète.

## 6. Confidentialité et limites

- **Noms** : combinaisons aléatoires de prénoms et noms courants ; toute ressemblance avec une personne réelle serait fortuite.
- **Téléphones** : indicatif `+999`, réservé par l'UIT et non attribué ; aucun numéro ne peut être réel.
- **Banques, agences, comptes** : inventés. Les villes sont réelles mais seulement utilisées comme libellés.
- **Montants** : distributions log-normales arbitraires, sans calibrage sur des données réelles. Les performances mesurées sur ce jeu ne garantissent pas les mêmes performances sur des données réelles.
- **Reproductibilité** : à graine égale, les CSV et JSON sont identiques octet par octet ; le XLSX a un contenu identique (l'archive ZIP contient des horodatages internes qui peuvent varier).
