# Exploitation et sécurité

Le [README](../README.md) présente l'architecture. Cette page explique comment accéder au serveur, ce que la supervision collecte et comment une mise à jour est validée puis déployée. Le [guide Terraform](terraform.md) détaille les commandes et les trois piles.

## Accès au serveur

Pour administrer le serveur, un poste autorisé rejoint NetBird puis utilise le tunnel privé pour OpenSSH et Dokploy. Le pare-feu Hetzner bloque l'accès SSH public et l'accès direct au port 3000, en IPv4 et IPv6. La connexion demande ensuite une clé SSH ou un compte Dokploy.

```sh
ssh <admin>@<server-name>.netbird.cloud
```

Le SSH accepte uniquement les clés, sans connexion root ni mot de passe. Le pare-feu public autorise TCP 80/443, 25/465/587/993 et UDP 443/51820. Traefik limite aussi la route HTTP de Dokploy aux réseaux NetBird, sans faire confiance à un `X-Forwarded-For` fourni par le client.

Les routes HTTPS de Keycloak et Stalwart, y compris leurs consoles d'administration, restent publiques avec leur propre authentification. Grafana est limité à NetBird. Garder un accès à la console du fournisseur pour rétablir la connexion si le VPN tombe.

Les règles figurent dans le [pare-feu](../terraform/main.tf), la [configuration SSH au premier démarrage](../terraform/cloud-init.yaml.tftpl), le [filtre Dokploy](../terraform/configure-private-admin.py), les [routes HTTPS](../stack/compose.prod.yaml) et la [politique d'accès privé](../terraform/PRIVATE-ADMIN.md).

## Supervision

Grafana utilise la [source Prometheus provisionnée](../stack/grafana/provisioning/datasources/prometheus.yml) et charge le [tableau de bord versionné](../stack/grafana/dashboards/serveur-tout-en-un.json). L'accès HTTP à Grafana passe par la route Traefik réservée à NetBird.

Dans la [configuration versionnée](../stack/prometheus/prometheus.yml), Prometheus ne collecte que ses propres métriques. Les panneaux prévus pour le serveur, les conteneurs, les sondes et les alertes nécessitent encore les exportateurs, les cibles et les règles correspondants.

Après un déploiement, l'[agent](../deploiement/agent.sh) contrôle les conteneurs et leurs healthchecks lorsqu'ils existent. Le workflow [CD](../.github/workflows/cd.yml) publie le statut du commit et du déploiement, puis crée ou actualise une issue tant qu'un échec est signalé. Ce contrôle porte sur le déploiement. Il ne surveille pas en continu toutes les requêtes applicatives.

## Cycle CVE et redéploiement

1. La [veille Trivy](../.github/workflows/veille-securite.yml) analyse les images épinglées, publie les résultats dans GitHub Security et maintient une issue pour les failles à corriger.
2. Une image reconstruite avec un correctif peut déclencher une PR qui met à jour son empreinte. Dependabot propose les nouvelles versions. Une CVE sans correctif disponible reste signalée et demande une décision d'exploitation.
3. La [CI](../.github/workflows/ci.yml) valide Terraform, Compose, les scripts et les workflows. Elle recherche les secrets dans l'historique, les mauvaises configurations et les vulnérabilités des images modifiées.
4. La [politique automatique](../.github/workflows/fusion-auto.yml) autorise la fusion des correctifs, de certaines reconstructions et des versions mineures d'actions GitHub ou de PostgreSQL, après la réussite des contrôles requis. Les autres évolutions demandent une revue.
5. Toutes les cinq minutes, l'agent serveur vérifie `main`. Il exige une CI réussie et refuse d'écraser des fichiers modifiés manuellement. Il applique les changements de `stack/`, lance le déploiement puis contrôle la santé. Un changement limité à la documentation ne redémarre pas les services.
6. GitHub reçoit le résultat. En cas d'échec, le statut et l'issue de déploiement restent visibles. La prochaine analyse examine les nouvelles images épinglées.

Les pipelines applicatifs construisent et vérifient leurs propres images. Le [flux Tabula](terraform.md#3-identité-et-mises-à-jour-tabula) ajoute une notification, un délai minimal de quatre heures pour intervenir, la possibilité de bloquer la promotion, deux images de même version et une sauvegarde avant déploiement. Ce délai ne s'applique pas à toutes les mises à jour de la stack ni aux mises à jour système `unattended-upgrades`.

## Configuration et données

`setup.py` charge les secrets nécessaires depuis un magasin extérieur au dépôt. Les états et plans Terraform peuvent contenir des secrets et restent privés.

Les paramètres Terraform règlent les sauvegardes et les protections du serveur. Terraform empêche la destruction du bucket Drive. Tabula conserve les données et l'identité du publieur, effectue une sauvegarde avant chaque mise à jour et ne restaure pas automatiquement les données de façon destructive.

L'ancien chemin de déploiement de `terraform/drive/` doit être migré vers le SSH privé et les images de la stack avant d'être réutilisé. Le [guide Terraform](terraform.md) détaille cette limite. Le README présente l'architecture actuelle des services.
