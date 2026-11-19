# SIDADR — Direction artistique et design system

> Statut : **direction « moderne épurée » validée** (Phase 1, 19/11/2026). Tokens : [`tokens.css`](tokens.css). Maquettes : [`mockups/index.html`](mockups/index.html).

## 1. Direction artistique

**Positionnement : un logiciel financier moderne, sobre et digne de confiance.** SIDADR emprunte la clarté des produits SaaS actuels (Linear, Stripe, Ramp) tout en gardant la rigueur d'un outil de contrôle : chiffres alignés, sévérités explicites, décisions tracées. La qualité perçue vient de la finition (espacement, alignement, états complets), pas de la décoration.

| On fait | On évite |
|---|---|
| Fond gris très clair, cartes blanches, bordures très légères | Dégradés, verre dépoli décoratif, halos lumineux |
| Barre latérale claire ; le bleu marque l'élément actif | Grands aplats de couleur sans fonction |
| Couleur = signification (statut, sévérité) | Couleurs décoratives, graphiques multicolores sans raison |
| Chiffres tabulaires alignés à droite, devise en petit à côté | Chiffres proportionnels dans les tableaux |
| Tableaux comme élément central, aérés | Murs de cartes KPI |
| Icônes Lucide (SVG, trait 1,75 px) | Emojis |
| Mouvement ≤ 180 ms, seulement pour le retour d'action | Animations d'entrée, compteurs animés |

Références d'inspiration (non copiées) : clarté et finition de Stripe, cohérence de navigation de Linear, expérience financière de Ramp.

**Historique de la décision** : une première direction « institutionnelle » (barre latérale bleu nuit, IBM Plex, coins 4–8 px, forte densité) a été maquettée puis écartée au profit de cette variante, jugée plus moderne. Un thème sombre avec accent vert et police Fira Code a aussi été envisagé, puis écarté : il était contraire au brief (fond clair, bleu institutionnel), et le vert signifie ici « contrôle validé », pas « action ».

## 2. Couleurs

| Rôle | Token | Valeur | Usage |
|---|---|---|---|
| Primaire | `--blue-700` | `#1F4E8C` | bouton principal, lien, élément de navigation actif |
| Accent fort | `--navy-950` | `#0B1B33` | logo, filtre actif |
| Fond d'app | `--gray-25` | `#F7F8FA` | arrière-plan |
| Barre latérale | `--gray-10` | `#FBFBFC` | navigation |
| Surface | `--gray-0` | `#FFFFFF` | cartes, tableaux |
| Bordure | `--gray-200` | `#E6E9EE` | contours de cartes et champs |
| Texte | `--gray-900` | `#0F1B2D` | texte principal |
| Texte secondaire | `--gray-600` | `#4A5768` | libellés |
| Texte tertiaire | `--gray-500` | `#5F6B7A` | métadonnées (≥ 4,5:1 sur toutes les surfaces, vérifié par script) |

**Couleurs sémantiques** (trio texte / fond / bordure) : succès = contrôle validé ; avertissement ; danger = erreur critique ; information ; « revue » (indigo) = décision humaine attendue. Toutes ≥ 5:1 texte sur fond.

**Sévérités SIDADR** — toujours *icône + libellé + couleur* :

| Sévérité | Couleur | Icône Lucide |
|---|---|---|
| Erreur certaine | rouge | `octagon-x` |
| Incohérence probable | orange | `triangle-alert` |
| Valeur atypique | bleu | `activity` |
| Alerte à examiner | indigo | `user-search` |

## 3. Typographie

**Inter** (interface) + **JetBrains Mono** (identifiants, codes de règle, valeurs brutes). Les montants restent en Inter avec chiffres tabulaires (`font-variant-numeric: tabular-nums`), plus lisibles qu'une police mono pour de grands nombres.

| Niveau | Taille / graisse | Usage |
|---|---|---|
| Titre de page | 24 / 600, interlettrage −0,02 em | un par écran |
| Titre de carte | 15 / 600 | cartes, panneaux |
| Interface | 14 / 400–500 | texte courant, boutons, navigation |
| Tableau | 13–14 / 400–500 | cellules (titre en 500, sous-ligne 12 px tertiaire) |
| Métadonnée | 12 / 500 | badges, en-têtes de colonne |
| Valeur clé | 30 / 600 | score qualité, compteurs |

Aucun texte sous 12 px.

## 4. Espacements, rayons, ombres

- Grille de 4 px ; marges de page 32 px, cartes 22 px, espacement entre cartes 20 px.
- Rayons : 6 px (champs), 8 px (boutons, éléments de liste), 12 px (cartes, modales), pilule (badges, filtres, pastille de session).
- Ombres : quasi invisibles sur les cartes ; ombre marquée réservée aux éléments flottants.

## 5. Composants

| Composant | Règles |
|---|---|
| **Bouton** | Primaire (bleu plein, légère ombre), secondaire (blanc bordé), lien (texte bleu). Hauteur 36 px. Focus : anneau bleu visible. |
| **Champ** | Libellé visible au-dessus, jamais de placeholder seul ; aide et erreur sous le champ. |
| **Badge de sévérité** | Pilule, fond clair, texte foncé, icône 14 px, libellé complet. |
| **Statut** | Point coloré + texte (Ouvert, Confirmé, Rejeté, Conservés, Corrigé). |
| **Tableau** | En-tête 12 px gris sur fond presque blanc ; lignes de 56 px avec titre + sous-ligne ; chiffres à droite ; codes de règle en étiquette mono ; avatar d'initiales pour les banques ; filtres en pastilles au-dessus ; pagination en bas avec total. Au-delà de 1 000 lignes : virtualisation. Un mode compact (lignes de 40 px) pourra être ajouté pour le traitement en masse. |
| **Carte** | Blanche, bordure 1 px, rayon 12 px, en-tête avec titre + action à droite. |
| **Contrôle segmenté** | Fond gris, segment actif blanc avec ombre légère (onglets, niveaux de similarité). |
| **Jauge** | Anneau pour le score qualité, toujours accompagné de la décomposition chiffrée (jamais l'anneau seul). |
| **Modale** | Confirmations irréversibles et décisions avec motif obligatoire ; bouton désactivé tant que le motif est vide. |
| **Notification** | Toast pour les confirmations ; bannière pour les avertissements persistants. |
| **États** | Chaque vue définit : chargement (squelettes), vide (explication + action suivante), erreur (message + reprise), résultat partiel. |
| **Navigation** | Barre latérale claire en 4 groupes, recherche globale `Ctrl K`, pied indiquant l'état du moteur ; barre supérieure avec fil d'Ariane et pastille de session toujours visible. |

## 6. Navigation

```
Vue d'ensemble
Données        Import · Qualité des données
Détection      Doublons · Noms & identités · Anomalies · Règles métier
Traitement     Corrections · Historique
Sorties        Rapports & exports
```

Les 11 pages du brief sont regroupées en 10 entrées : « Doublons de noms » et « Multi-banques » sont deux onglets de **Noms & identités**, car c'est le même flux de décision.

## 7. Stratégie de validation visuelle

1. Chaque écran est lancé réellement et inspecté dans le navigateur intégré (captures à 1440 px, 1280 px et 1024 px).
2. Vérifications : alignements, grille de 4 px, contraste (script), navigation au clavier (ordre de tabulation, focus visible), états vide/erreur/chargement forcés.
3. Tableaux testés avec le jeu synthétique complet (20 000 lignes).
4. Aucun écran déclaré « validé » sans capture réelle.
