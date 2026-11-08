<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/ms-dark.svg">
    <img src="assets/ms.svg" alt="ms" width="220">
  </picture>
</p>

<h1 align="center">modern-stack</h1>

<p align="center">Messagerie, fichiers et collaboration sur un serveur géré avec Terraform.</p>

**modern-stack** réunit une messagerie, un Drive et Tabula sur un seul serveur. L'accès SSH passe par NetBird, Keycloak gère les identités et les mises à jour passent par la CI avant leur déploiement.

## Un accès privé pour administrer le serveur

Pour accéder à SSH ou Dokploy, le poste de l'administrateur doit rejoindre le tunnel privé chiffré **NetBird**.

```mermaid
graph LR
    A["Internet"]:::chamois -->|"poste autorisé"| B["NetBird"]:::beige
    B --> C["Tunnel privé chiffré"]:::beige
    C --> D["SSH par clé"]:::blanc
    C --> E["Dokploy privé"]:::blanc
    A --> F["Pare-feu Hetzner"]:::beige
    F -->|"HTTPS et mail"| G["Services publics"]:::blanc
    F -->|"TCP 22 / 3000"| H["Accès direct bloqué"]:::blanc

    classDef chamois fill:#d4a574,color:#3b2314,stroke:#b8895a,stroke-width:2px
    classDef blanc fill:#fefefe,color:#3b2314,stroke:#d4a574,stroke-width:2px
    classDef beige fill:#f5e6d3,color:#3b2314,stroke:#d4a574,stroke-width:2px
```

Le **pare-feu Hetzner bloque l'accès public direct à SSH et Dokploy**, en IPv4 et IPv6. L'administrateur se connecte ensuite avec sa clé SSH ou son compte Dokploy. Les applications publiques et la messagerie restent accessibles depuis Internet.

## Services et gestion des identités

**Traefik** fournit le point d'entrée HTTPS. **Keycloak** gère l'authentification OIDC et les rôles applicatifs de Drive et Tabula, avec PKCE et des redirections contrôlées.

```mermaid
graph LR
    A["Traefik · HTTPS"]:::chamois --> B["Voxel · webmail"]:::beige
    B --> C["Stalwart · messagerie"]:::blanc
    A --> D["Drive · fichiers"]:::beige
    D --> E["Stockage S3 privé"]:::blanc
    A --> F["Tabula · données collaboratives"]:::beige
    F --> G["SpacetimeDB · temps réel"]:::blanc
    H["Keycloak · identité"]:::chamois -.->|"OIDC / PKCE"| D
    H -.->|"OIDC / PKCE"| F

    classDef chamois fill:#d4a574,color:#3b2314,stroke:#b8895a,stroke-width:2px
    classDef blanc fill:#fefefe,color:#3b2314,stroke:#d4a574,stroke-width:2px
    classDef beige fill:#f5e6d3,color:#3b2314,stroke:#d4a574,stroke-width:2px
```

[Voxel](https://github.com/djdevpro/voxel) fournit l'interface de messagerie de Stalwart ; [Drive](https://github.com/djdevpro/s3drive) conserve les fichiers dans un stockage S3 privé ; Tabula s'appuie sur SpacetimeDB pour les données collaboratives en temps réel. Keycloak stocke les identités dans PostgreSQL.

## Supervision avec Prometheus et Grafana

**Grafana** affiche les métriques collectées par **Prometheus** dans des tableaux de bord accessibles via NetBird. Les contrôles de santé des conteneurs servent aussi à vérifier l'état des services après un déploiement.

```mermaid
graph LR
    A["Métriques configurées"]:::chamois --> B["Prometheus"]:::beige
    B --> C["Grafana · tableaux de bord"]:::blanc
    D["Administrateur"]:::chamois --> E["Tunnel privé NetBird"]:::beige
    E --> C
    F["Santé des conteneurs"]:::chamois --> G["Agent de déploiement"]:::beige
    G --> H["Statuts et alertes GitHub"]:::blanc

    classDef chamois fill:#d4a574,color:#3b2314,stroke:#b8895a,stroke-width:2px
    classDef blanc fill:#fefefe,color:#3b2314,stroke:#d4a574,stroke-width:2px
    classDef beige fill:#f5e6d3,color:#3b2314,stroke:#d4a574,stroke-width:2px
```

Les tableaux de bord et leur source de données sont versionnés. GitHub affiche l'état des déploiements et ouvre une issue en cas d'échec. Dans la [configuration actuelle](docs/operations.md#supervision), Prometheus ne collecte pour l'instant que ses propres métriques.

## D'une CVE au redéploiement vérifié

**Trivy** analyse chaque nuit les images épinglées. Les vulnérabilités sont signalées ; les images reconstruites et les mises à jour proposées par **Dependabot** passent par une revue et les contrôles de la CI.

```mermaid
graph LR
    A["Veille CVE · Trivy"]:::chamois --> B["Alerte de sécurité"]:::blanc
    A -->|"correctif disponible"| C["PR de mise à jour"]:::beige
    C --> D{"CI validée ?"}:::chamois
    D -->|"non"| E["Déploiement bloqué"]:::blanc
    D -->|"oui, après fusion"| G["Redéploiement"]:::beige
    G --> H["Santé et rapport"]:::blanc

    classDef chamois fill:#d4a574,color:#3b2314,stroke:#b8895a,stroke-width:2px
    classDef blanc fill:#fefefe,color:#3b2314,stroke:#d4a574,stroke-width:2px
    classDef beige fill:#f5e6d3,color:#3b2314,stroke:#d4a574,stroke-width:2px
```

La CI vérifie l'infrastructure, les scripts, les workflows et la sécurité des images. Les mises à jour prévues par la politique de fusion automatique peuvent être intégrées après validation. Les autres demandent une revue.

Le serveur **récupère lui-même les changements approuvés**. Il refuse d'écraser des modifications manuelles conflictuelles, redéploie les services concernés et remonte leur état de santé. GitHub Actions n'a besoin d'aucun accès SSH à la production.

Pour Tabula, les images sont construites, testées et analysées avant l'envoi d'une notification. La promotion attend alors **au moins quatre heures**, pour laisser le temps d'intervenir. Le serveur sauvegarde les données avant le déploiement.

## Configuration et exploitation

**Terraform** définit l'infrastructure, les accès privés et la configuration des applications. **Docker Compose** décrit les services, leurs données persistantes, leurs images épinglées et les limites de ressources configurées. Les secrets sont fournis hors de Git ; les états Terraform et les sauvegardes restent privés.

- Le [guide Terraform](docs/terraform.md) détaille les commandes, les schémas de provisionnement et les prérequis.
- Le guide [Exploitation et sécurité](docs/operations.md) décrit les accès réseau, la couverture de supervision et le cycle CVE/redéploiement.
- Le guide d'[administration privée](terraform/PRIVATE-ADMIN.md) explique comment se connecter et rétablir un accès.

Les schémas Mermaid se modifient directement dans le README. Ils doivent évoluer dans la même PR que l'architecture.
