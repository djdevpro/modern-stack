# Règles de ce dossier

Ce dossier est le dépôt **public** https://github.com/djdevpro/modern-stack.

- **Jamais de secret ni de donnée personnelle dans le dépôt** : pas de jeton, mot de passe ou clé ; pas d'adresse mail de boîte réelle ; pas de sauvegarde DNS. Les secrets vivent dans le magasin de `setup.py` (`%USERPROFILE%\.config\djidjelli\secrets.env`) et, sur le serveur, dans `/opt/stack/.env`. `dns/` est ignoré et reste sur ce PC.
- **La prod se déploie depuis `main`.** Une modification de `stack/` (sauf `stack/voxel/`) se commite et se pousse sur `main` : l'agent du serveur (`deploiement/`) la déploie dans les 5 minutes une fois la CI verte. Ne plus copier de fichiers de `stack/` dans `/opt/stack` à la main : l'agent refuse alors de déployer (issue « Déploiement bloqué sur le serveur »).
- Les images de `stack/compose.yaml` sont épinglées (étiquette et empreinte) et tenues à jour par Dependabot ; les correctifs sont fusionnés et déployés seuls. Pour changer une version, modifier l'étiquette ET l'empreinte (`docker buildx imagetools inspect <image:étiquette>`).
- Avant chaque push, relire `git diff --cached` : la CI rejette tout secret (gitleaks) et GitHub bloque les secrets connus.
