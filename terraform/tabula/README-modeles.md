# Modèle de prospection Tabula

Le fichier [templates/prospection.json](templates/prospection.json) décrit une base vierge : six tables, leurs champs, six relations bidirectionnelles, deux cumuls, une formule de suivi et douze vues. L'importeur utilise l'API v3 existante de Tabula. Il crée une nouvelle base dans un espace du compte administrateur configuré, sans modifier les autres bases. Les cinq lignes de la table « Paramètres » servent de point de départ ; elles ne créent aucune automatisation.

## Installer

1. Ajouter l'objet suivant au fichier local ignoré `terraform/tabula/terraform.tfvars.json`, en conservant toutes ses autres clés :

   ```json
   "database_template": {
     "installation_id": "prospection-v1"
   }
   ```

   `installation_id` distingue les installations et ne doit pas être réutilisé pour un autre espace ou un modèle modifié. Le propriétaire est `configuration.admin_email`. Terraform choisit automatiquement un espace où ce compte peut créer des bases, ou en crée un si nécessaire. On peut aussi fixer un espace avec `"workspace_id": "wsp…"`. Pour un autre JSON, ajouter `"template_path": "chemin/vers/modele.json"` ; le chemin est résolu depuis le dossier `terraform/tabula` au lancement de Terraform.
2. Depuis PowerShell, à la racine du projet, vérifier le modèle puis le plan, et appliquer uniquement cette ressource :

   ```powershell
   python terraform/tabula/import_template.py --check
   python setup.py tf tabula plan '-target=terraform_data.database_template'
   python setup.py tf tabula apply '-target=terraform_data.database_template'
   ```

   Le ciblage évite de relancer la configuration d'identité et de mise à jour de la pile Tabula. Sur le serveur, l'import utilise les accès initiaux déjà présents dans `/opt/tabula/bootstrap.json`, se connecte au compte configuré avec le flux OIDC et PKCE existant, puis crée un jeton API temporaire valable 20 minutes, révoqué en fin d'import. Aucun mot de passe ni jeton n'est envoyé à Terraform ou enregistré dans son état. Le mot de passe n'est jamais réinitialisé : si l'accès initial a changé ou si une action interactive est requise, l'import s'arrête.

3. Ouvrir la nouvelle base et vérifier les vues. Compléter « Paramètres » avec les vrais critères du client idéal et les délais souhaités.

## Adapter le JSON

Le format utilisé ici est celui de cet importeur, pas un export JSON natif de Tabula. `version`, `key` et `title` identifient le modèle. `tables` contient les tables et leurs `fields` ; chaque champ possède une clé technique, un titre, un type et, pour les sélections, une liste `choices`. La première colonne de chaque table est son champ principal texte. Les colonnes usuelles de texte, nombre, case à cocher, date, sélection et formule sont prises en charge.

`relations` décrit les liens avec la table cible, la cardinalité et le nom du champ inverse créé par Tabula. `computed` permet les cumuls `Rollup` et les formules ajoutées après les relations, dans l'ordre déclaré. `views` configure les grilles, Kanban et calendriers, leurs colonnes, tris et filtres ; les filtres peuvent combiner `all` et `any` sur trois niveaux et utiliser des choix ou des dates relatives. Chaque table doit avoir une vue grille marquée `default`. `seeds` contient d'éventuelles lignes initiales, identifiées de manière stable. Les clés dans les vues et les relations référencent les clés techniques du modèle, non les identifiants propres à une installation.

Pour vérifier un autre modèle avant son installation : définir temporairement `TABULA_MODEL_FILE` vers le fichier, lancer `python terraform/tabula/import_template.py --check`, puis retirer cette variable. La vérification complète des formules et des droits reste effectuée par Tabula à l'import.

## Utilisation et limites

- Renseigner les sources dans « Sources et vérifications » et les lier au prospect ou à l'entreprise. Les champs « Fiabilité » distinguent faits vérifiés et hypothèses ; un signal ne démontre pas une intention d'achat.
- Vérifier les doublons par email professionnel, profil LinkedIn puis nom et entreprise avant de créer ou d'importer un contact. Le champ « Contrôle des doublons » garde le résultat visible ; l'interface actuelle ne rend pas cette vérification obligatoire et ne fusionne pas automatiquement les doublons.
- Remplir le score, sa justification, les éléments manquants, la qualification et la prochaine action. Les critères et délais de la table « Paramètres » sont modifiables, mais ne calculent pas eux-mêmes le score et ne planifient pas de recherche ou de relance. La période de 30 jours des « Signaux récents » se règle dans le filtre de cette vue.
- Saisir chaque interaction, son résultat, puis la lier au prospect. Une réponse, un rendez-vous fixé ou un refus alimentent les compteurs du prospect. Les vues « À contacter » et « À traiter aujourd’hui » excluent les relances après une réponse, et tous les contacts après un refus. La case « Refus de contact » permet aussi de bloquer le contact directement. Pour démarrer une nouvelle séquence décidée après une réponse, reporter le nombre actuel de réponses dans « Réponses au début de la séquence » : une réponse ultérieure suspend à nouveau la relance, et un refus reste bloquant. Les interactions sans lien ou sans résultat ne peuvent pas alimenter ces compteurs.
- Mettre à jour la date du dernier contact et la prochaine action après chaque échange. Toute intégration d'envoi doit relire les interactions, les refus, les préférences et la validation du message exact juste avant l'envoi. La case de validation n'est pas remise à zéro automatiquement lors d'une modification du brouillon. Tabula n'impose pas ces règles à une automatisation extérieure ; aucun message n'est envoyé par cet import.
- « À traiter aujourd’hui » comprend également les actions en retard. Le calendrier est basé sur la date de prochaine action ; un rendez-vous doit être enregistré comme prochaine action avec sa date.
- Les dates relatives sont évaluées dans le fuseau du navigateur dans l'interface, mais en UTC par l'API actuelle. Une automatisation doit tenir compte de cet écart autour de minuit.

## Reprise et protection des données

L'import crée la base une fois et conserve un reçu sur le serveur dans `/opt/tabula/models/`. Après réussite, les applications suivantes vérifient son accès et ne réécrivent pas le schéma ni les données, même si l'utilisateur les a modifiés. Un changement de JSON demande un nouvel `installation_id` et un titre de base différent ; aucune migration en place n'est effectuée. Retirer la ressource Terraform ou exécuter `terraform destroy` ne supprime pas la base Tabula.

Si l'import s'interrompt, le relancer avec le même JSON et le même `installation_id`. Il reprend les objets déjà créés dans cette base et refuse les conflits. Si la réponse de création de base a été perdue, l'import s'arrête pour éviter un doublon : vérifier l'identifiant de la base réellement créée, le fournir explicitement dans `database_template.recovery_base_id`, relancer l'apply ciblé puis retirer cette option. Si un fichier `.lock` subsiste après l'arrêt du processus, ne le retirer qu'après avoir vérifié qu'aucun import n'est en cours. Après un refus de connexion, un fichier `.login-failed` bloque les nouvelles tentatives pour éviter de verrouiller le compte : vérifier les accès avant de le retirer.

Conserver le reçu avec l'état d'installation local. S'il est perdu, une base de même nom n'est pas adoptée automatiquement. Les modifications des formulaires, règles de score, séquences ou automatisations futures restent sous le contrôle de l'utilisateur.
