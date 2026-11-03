# Déploiement continu

Le serveur se déploie tout seul depuis `main` : aucun accès entrant, aucun secret dans GitHub.

1. `deploiement-stack.timer` lance `agent.sh` (installé en `/usr/local/sbin/deploiement-stack`) toutes les 5 minutes.
2. L'agent récupère `main` (dépôt public, sans jeton) et attend que le workflow **CI** de ce commit soit vert. Après une fusion automatique, il prend la CI de la PR fusionnée.
3. Il copie dans `/opt/stack` les fichiers de `stack/` changés depuis le dernier déploiement (`stack/voxel/` exclu : Voxel se déploie depuis son dépôt), reconstruit l'image de `sw` si `stack/tools/` a changé, met à jour `appli`, `cf-dns` et `sw` dans `/usr/local/bin`.
4. Il lance `scripts/deploie-prod.sh` et attend jusqu'à 10 minutes que tous les conteneurs tournent et soient sains.
5. Il publie le résultat dans `etat.json` sur la branche `deploiements`, avec une clé de déploiement propre au serveur, la seule chose qu'il puisse écrire dans le dépôt. Toutes les 15 minutes, le workflow **Suivi du déploiement** le reporte sur le commit (statut `deploiement/serveur`) et ouvre l'issue « Déploiement bloqué sur le serveur » tant que le dernier déploiement échoue.

Le `.env` de `/opt/stack` (secrets) n'est jamais touché. L'agent met aussi à jour son propre code quand `deploiement/` change.

## Fichier modifié à la main sur le serveur

L'agent refuse d'écraser un fichier de `/opt/stack` qui n'est ni la version déployée ni la nouvelle (issue « Déploiement bloqué », résultat `refuse`). Deux sorties :

- garder la modification : la reporter dans le dépôt et pousser sur `main` ; l'agent l'accepte puisque le fichier du serveur est alors celui du nouveau commit ;
- l'abandonner : `sudo FORCER=1 deploiement-stack` (les fichiers écrasés sont copiés dans `/opt/backups/stack-avant-force-*`).

## Commandes

| Quoi | Commande (sur le serveur) |
|---|---|
| Journal | `journalctl -u deploiement-stack -n 50` |
| Lancer tout de suite | `sudo systemctl start deploiement-stack` |
| Commit déployé | `sudo cat /var/lib/deploiement-stack/deploye` |
| Mettre en pause / reprendre | `sudo systemctl disable --now deploiement-stack.timer` / `sudo systemctl enable --now deploiement-stack.timer` |

## Installation (serveur recréé)

Une fois la stack en place dans `/opt/stack` (`.env` compris) :

```bash
sudo git clone https://github.com/djdevpro/modern-stack.git /var/lib/deploiement-stack/depot
sudo /var/lib/deploiement-stack/depot/deploiement/installe.sh <commit de main déjà en place>
```

La première fois, le script affiche une clé publique à ajouter au dépôt (*Settings > Deploy keys*, accès en écriture), puis se relance de la même façon.
