# La voix de Michel

Une page, pour que Michel parle de la même façon dans tous les modules.
Les tests `tests/test_voice.py` et `tests/test_look.py` vérifient ce qui peut l'être.

## Qui parle, à qui

- **Michel dit *tu*.** À un membre, à un admin, dans le tableau de bord aussi.
  Un message public dit *tu* aussi : il s'adresse à la personne qui le lit
  (« Clique sur **Participer** pour tenter ta chance ! »).
  Le *vous* de politesse n'existe pas, et le *vous* pluriel ne sert qu'à de rares
  annonces de groupe : on préfère tourner la phrase autrement
  (« Chaque participant vient de recevoir en MP… »).
- **Michel dit *je*** quand il n'a pas pu faire quelque chose :
  « Je n'arrive pas à lire les nouvelles pour l'instant : réessaie. »
  Jamais « Une erreur est survenue ».

## Comment

- **Des phrases courtes.** Une idée par phrase.
- **Dire quoi faire ensuite.** Un refus nomme le bon salon, le droit qui manque,
  qui peut corriger, ou un exemple de ce qui est attendu :
  - « 🎵 /addsong se lance dans #musique. »
  - « 🔒 Il te faut « Gérer les messages » dans #annonces. »
  - « 📅 Je n'ai pas compris « 31/02 ». Exemple : 24/12/2026. »
- **Le premier emoji dit ce qui s'est passé**, avec les fonctions de
  `src/discord_ext/look.py` :
  - `ok("…")` : ✅, c'est fait ;
  - `oops("…", icon)` : ❌ par défaut, ou un emoji plus parlant (🔒 un droit,
    📅 une date, ⏱️ une durée, l'emoji du module pour « pas ici ») ;
  - `ask("…")` : ❓, une question à laquelle on répond avec les boutons.

  `send_success()` et `send_error()` (`src/discord_ext/messages.py`) les utilisent.
- **La typographie française** : une espace avant `:`, `?` et `!`
  (« Total : 12 »), des guillemets « … », « 1er », « 12 345 » avec une espace fine
  (`thousands()`).
- **Nommer, pas identifier.** Un message montre un nom, jamais un ID Discord
  (le pied de page d'un sondage disait « Créé par x (ID: … ) »).

## L'apparence

Tout embed vient de `card(module, kind, …)` (`src/discord_ext/look.py`) :

- **la couleur du module** pour une information, ou celle du statut :
  `success` (vert), `warning` (orange), `error` (rouge), `neutral` (gris, ce qui
  est fini ou annulé). La palette est adoucie : chaque couleur garde au moins 3:1
  sur le fond des embeds, en thème sombre comme en thème clair ;
- **l'icône et le nom du module** en ligne d'auteur (`author=False` quand l'embed
  nomme quelqu'un : l'auteur d'un message au starboard, un article RSS) ;
- **le pied de page « Michel · Module »**, suivi de ce que la carte ajoute
  (`card(footer="Page 2/3")`, ou `sign(embed, module, "…")` après coup).

Un embed dont les admins choisissent l'apparence (Gestionnaire d'Embeds) vient de
`custom()`. Une couleur qu'un admin a choisie pour une carte (celle d'un flux RSS)
passe par `card(accent=…)`.
