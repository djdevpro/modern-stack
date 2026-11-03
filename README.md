# Mon serveur

Un seul serveur Hetzner (CPX22, Nuremberg) pour tes applis et ton mail.

## Qui fait quoi

| Couche | Outil | Où | Rôle |
|---|---|---|---|
| Serveur | Terraform (`terraform/`) | Hetzner | Crée le serveur, le pare-feu et le reverse DNS ; à la création, installe l'hôte (NetBird, Dokploy, Docker, Claude Code, outils Cloudflare) |
| Infrastructure | Docker Compose (`stack/`) | `/opt/stack` sur le serveur | Mail (Stalwart), identité (Keycloak), supervision (Prometheus, Grafana) |
| Tes applis | Dokploy | interface `https://dokploy.djidjelli.fr` | Déploie tes applis (Git, image Docker, compose) avec HTTPS automatique |
| Frontal web | Traefik de Dokploy | ports 80 et 443 | Partagé par Dokploy et la stack ; certificats Let's Encrypt |
| Accès privé | NetBird | appareils connectés à ton compte NetBird | Tout ce qui est sensible n'est joignable que par lui |
| DNS | Cloudflare | `cf-dns` et `appli` sur le serveur | Joker seulement sur `*.app.djidjelli.fr` ; ailleurs, un nom par appli |

La stack d'infrastructure ne passe pas par Dokploy, volontairement : le mail, l'identité et la supervision ne doivent pas tomber si une appli ou Dokploy se trompe. Dokploy reste pour ce que tu déploies souvent.

## Adresses

| Adresse | Pour quoi | Accès |
|---|---|---|
| `https://dokploy.djidjelli.fr` | Déployer tes applis | NetBird |
| `https://grafana.djidjelli.fr` | Supervision (presque vide, voir `stack/README.md`) | NetBird |
| `https://mail.djidjelli.fr` | Portail de compte mail ; admin Stalwart sur `/admin` | public |
| `https://webmail.djidjelli.fr` | Webmail (Voxel) ; l'ancienne adresse `email.app.djidjelli.fr` y renvoie | public |
| `https://iam.djidjelli.fr` | Connexion unique de tes applis (Keycloak) ; console d'admin sur `/admin` (voir `stack/README.md`) | public |
| `ssh serveur` | Console du serveur (`albert`, sudo et docker) | NetBird, ou `ssh serveur-ip` depuis ta box |

Identifiants et mots de passe : `ssh serveur cat acces.txt`.

## Déployer une appli

**Publique** (tout le monde peut l'ouvrir) :
- Le plus simple : dans Dokploy, *Domains > Add Domain* : `monappli.app.djidjelli.fr`, port du conteneur, HTTPS activé, certificat `letsencrypt`. Rien à faire dans le DNS : le joker `*.app.djidjelli.fr` pointe déjà sur le serveur (testé le 2026-11-03).
- Pour un nom court (`monappli.djidjelli.fr`) : d'abord `appli publique monappli` sur le serveur, puis le même domaine dans Dokploy.

Piège : *Container Port* doit être le port de l'appli dans son conteneur (80 pour nginx, `traefik/whoami`), pas les 3000 proposés par défaut.

**Réservée à NetBird** (seuls tes appareils) :
1. Sur le serveur : `appli netbird monappli 3000` (3000 = port de l'appli dans son conteneur). La commande crée le nom DNS et affiche les labels à coller.
2. Dans Dokploy : ne crée **pas** de domaine. Colle les labels dans *Advanced > Swarm Settings > Labels* (appli) ou dans le compose (service branché au réseau externe `dokploy-network`), puis redéploie.

Pourquoi des labels : le formulaire de domaine de Dokploy ne sait pas filtrer par adresse IP et réécrit ses fichiers Traefik à chaque modification. Les labels ajoutent la règle `netbird-seul` et un certificat validé par le DNS Cloudflare (Let's Encrypt ne peut pas joindre une adresse NetBird). Testé le 2026-11-02 : HTTP 200 via NetBird, 403 depuis Internet.

Autre domaine que djidjelli.fr : `CF_ZONE=autredomaine.fr appli publique site`.

## Mail

Domaines : djidjelli.fr, apportly.app, formadiagimmo.fr (MX `mail.djidjelli.fr`) ; une boîte par personne et un fourre-tout par domaine (liste : `sw x:Account/get` sur le serveur). Une boîte technique sert aux tests automatiques de Voxel (identifiants dans `acces.txt`), à supprimer quand ils ne servent plus. DKIM, SPF, DMARC (`p=quarantine`) et certificat sont en place ; le journal DNS et l'état d'origine de chaque zone sont dans `dns/`, gardé sur le PC (hors dépôt : données personnelles).

Applis mail : IMAP `mail.djidjelli.fr` 993 et SMTP 465, en SSL, identifiant = adresse complète (pas de port 587).

Webmail : Voxel, anciennement Universal Email (`https://webmail.djidjelli.fr`, dépôt public https://github.com/djdevpro/voxel, dossier local `universal-email`), configuration de prod dans `stack/voxel`, déployé par `stack/scripts/deploie-voxel.sh`. Le navigateur parle JMAP directement à Stalwart ; les boîtes IMAP d'autres fournisseurs passent par sa petite passerelle, qui ne garde aucun mot de passe. Il remplace Roundcube en plus moderne, avec les mêmes fonctions : filtres et répondeur, contacts, agenda, quota, réglages communs aux appareils, notifications (détail dans le README du dépôt voxel).

## CI/CD et sécurité

Ce dossier est le dépôt public https://github.com/djdevpro/modern-stack : aucun secret (magasin de `setup.py`, `.env` du serveur) ni donnée personnelle.

| Étape | Ce qui se passe | Fichiers |
|---|---|---|
| Contrôles | À chaque push et PR : secrets (gitleaks), Terraform (format, validation, TFLint), compose, ShellCheck, workflows (actionlint, zizmor), mauvaises configurations (Trivy) ; sur une PR, CVE des images changées (refus si elle apporte une faille critique) | `.github/workflows/ci.yml` |
| Veille CVE | Chaque nuit et à la demande : Trivy analyse toutes les images, alertes dans l'onglet *Security*, issue « Failles de sécurité à corriger » ; une image reconstruite par son éditeur avec les correctifs donne une PR fusionnée seule | `veille-securite.yml` |
| Mises à jour | Dependabot : images, Terraform, actions, outils de la CI. Correctifs (patch) et images reconstruites fusionnés seuls si la CI passe ; versions mineures et majeures à fusionner à la main | `.github/dependabot.yml`, `fusion-auto.yml` |
| Déploiement | L'agent du serveur tire `main` toutes les 5 minutes, attend la CI, met à jour `/opt/stack`, vérifie la santé des conteneurs ; état sur chaque commit, issue si ça bloque | `deploiement/`, `suivi-deploiement.yml` |

Corriger une faille sans attendre la nuit : *Actions > Veille sécurité > Run workflow* (aussi depuis l'appli GitHub). On ne modifie plus `/opt/stack` à la main : on pousse sur `main` (voir `deploiement/README.md`). Versions des outils de la CI : `.github/outils/Dockerfile`, tenu à jour par Dependabot.

## À savoir

- **Traefik de Dokploy modifié à la main** : résolveur `letsencrypt-dns` ajouté dans `/etc/dokploy/traefik/traefik.yml`, et conteneur recréé par `stack/scripts/recree-traefik.sh` avec le jeton Cloudflare (`/etc/dokploy/traefik/cloudflare.env`) et le réseau IPv6 `traefik-public` (sans lui, tous les visiteurs IPv6 arrivent avec la même adresse, 172.18.0.1). Si tu changes les réglages Traefik dans l'interface Dokploy, il peut réécrire ce fichier ou recréer le conteneur : remets le résolveur et relance `sudo /opt/stack/scripts/recree-traefik.sh`, sinon les noms NetBird perdent leur certificat au renouvellement.
- **IPv6** : Stalwart et Traefik voient la vraie adresse des clients IPv6 depuis le 2026-11-03 (réseaux Docker en IPv6, détail dans `stack/README.md`).
- **Claude Code** est installé directement sur l'hôte, sans conteneur (`ssh serveur` puis `claude`, compte `albert`), connecté à ton abonnement par le jeton de `~/.config/claude/oauth-token`. L'agent Hermes et Twake Mail ont été retirés le 2026-11-03 (archives dans `/opt/backups` sur le serveur).
- **Sauvegardes** : aucune en dehors des backups Hetzner du disque. Les boîtes mail et les volumes Docker (dont la base de Keycloak) ne sont copiés nulle part ailleurs.
- **Recréer le serveur** : `terraform/README.md`. La stack se redéploie avec `stack/scripts/deploie-prod.sh`, puis l'agent de déploiement s'installe avec `deploiement/installe.sh`.
