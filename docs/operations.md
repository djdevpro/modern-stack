# Exploitation et sécurité

Le [README](../README.md) présente l'architecture. Cette page précise ses frontières, la couverture de supervision et le fonctionnement des mises à jour. Les commandes Terraform et les trois piles sont détaillées dans le [guide Terraform](terraform.md).

## Accès au serveur

Un poste autorisé rejoint NetBird, puis utilise le tunnel privé pour OpenSSH et Dokploy. Le pare-feu Hetzner n'autorise aucun SSH public ni accès direct au port 3000, en IPv4 comme en IPv6. NetBird transporte le réseau privé ; il ne remplace ni la clé SSH ni la connexion applicative Dokploy.

```sh
ssh <admin>@<server-name>.netbird.cloud
```

Le SSH est configuré par clé uniquement, sans connexion root ni mot de passe. Le pare-feu public autorise TCP 80/443, 25/465/587/993 et UDP 443/51820. Traefik protège également la route HTTP de Dokploy par une liste de réseaux NetBird, sans faire confiance à un `X-Forwarded-For` fourni par le client.

Les routes HTTPS de Keycloak et de l'interface mail Stalwart, consoles applicatives comprises, restent publiques avec leur propre authentification. Grafana est limité à NetBird. Ne pas confondre ces interfaces applicatives avec l'accès SSH au serveur. Garder la console du fournisseur disponible pour récupérer un accès si le VPN tombe.

Sources : [pare-feu](../terraform/main.tf), [SSH au premier démarrage](../terraform/cloud-init.yaml.tftpl), [filtre Dokploy](../terraform/configure-private-admin.py), [routes HTTPS](../stack/compose.prod.yaml), [politique privée](../terraform/PRIVATE-ADMIN.md).

## Supervision

Grafana utilise la [source Prometheus provisionnée](../stack/grafana/provisioning/datasources/prometheus.yml) et charge le [tableau de bord versionné](../stack/grafana/dashboards/serveur-tout-en-un.json). L'accès HTTP à Grafana passe par la route Traefik réservée à NetBird.

**Couverture versionnée actuelle :** le [fichier Prometheus](../stack/prometheus/prometheus.yml) ne déclare que la collecte de Prometheus lui-même. Le tableau de bord comporte aussi des panneaux pour le serveur, les conteneurs, les sondes et les alertes ; ils nécessitent les exportateurs, cibles et règles correspondants. La présence d'un panneau ne prouve pas que ses métriques sont collectées. Cette documentation ne suppose donc pas une couverture complète du serveur et des applications.

La vérification des déploiements est distincte : l'[agent](../deploiement/agent.sh) contrôle les conteneurs et leurs healthchecks lorsqu'ils existent. Le workflow [CD](../.github/workflows/cd.yml) publie le statut du commit et du déploiement, et crée ou actualise une issue tant qu'un échec est signalé. Cela ne constitue pas une surveillance continue de toutes les requêtes applicatives.

## Cycle CVE et redéploiement

1. **Détecter :** la [veille Trivy](../.github/workflows/veille-securite.yml) analyse les images épinglées, publie les résultats dans GitHub Security et maintient une issue pour les failles à corriger.
2. **Proposer :** une image reconstruite avec un correctif peut déclencher une PR d'empreinte ; Dependabot propose les nouvelles versions. Une CVE sans correctif disponible reste signalée et exige une décision d'exploitation.
3. **Valider :** la [CI](../.github/workflows/ci.yml) vérifie les secrets de l'historique, Terraform, Compose, scripts, workflows, mauvaises configurations et vulnérabilités des images modifiées.
4. **Fusionner :** la [politique automatique](../.github/workflows/fusion-auto.yml) couvre les correctifs, certaines reconstructions et les versions mineures d'actions GitHub ou de PostgreSQL. Les autres évolutions restent soumises à une revue. Les contrôles requis doivent réussir.
5. **Redéployer :** l'agent serveur vérifie `main` toutes les cinq minutes, exige une CI réussie et refuse d'écraser des fichiers modifiés manuellement. Il applique les changements de `stack/`, lance le déploiement puis contrôle la santé. Un commit documentaire seul ne redémarre pas les services.
6. **Rendre compte :** l'état revient dans GitHub ; un échec reste visible via le statut et l'issue de déploiement. La prochaine analyse examine les nouvelles images épinglées.

Les pipelines applicatifs construisent et vérifient leurs propres images. Tabula dispose en plus d'une notification, d'un délai minimal de quatre heures, d'un veto, d'images appariées et d'une sauvegarde avant déploiement : voir le [flux Tabula](terraform.md#3-identité-et-mises-à-jour--tabula). Ce délai ne s'applique pas à toutes les mises à jour de la stack ni aux mises à jour système `unattended-upgrades`.

## Configuration et données

`setup.py` charge les secrets nécessaires depuis un magasin extérieur au dépôt. Les états et plans Terraform peuvent contenir des secrets et restent privés. Les sauvegardes et protections du serveur dépendent des paramètres Terraform ; le bucket Drive interdit sa destruction par Terraform. Tabula conserve données et identité du publieur, effectue une sauvegarde avant mise à jour et ne restaure pas automatiquement les données de façon destructive.

L'ancien chemin de déploiement de `terraform/drive/` doit encore être migré pour respecter le SSH privé et le déploiement par image de la stack ; sa limite est documentée dans le [guide Terraform](terraform.md). L'architecture du README décrit les services actuels, pas ce provisioner historique.
