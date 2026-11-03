# Infra Hetzner : un seul serveur pour tout

Terraform ne gère que la clé SSH, le pare-feu Hetzner, le serveur et son reverse DNS ; à la création, cloud-init installe l'hôte. La stack d'infrastructure (`../stack` : mail, supervision, identité) se déploie ensuite dans `/opt/stack` ; Dokploy sert à déployer tes applis. Vue d'ensemble : `../README.md`.

Le stockage du Drive (drive.djidjelli.fr) a son propre dossier, `drive/` : un bucket Object Storage dans un projet Hetzner séparé, avec le même état distant sous une autre clé. Ce dossier-ci n'est pas touché (`.\tf.ps1 plan` reste à « No changes »).

| Serveur | Type | Exposé sur Internet | Installé à la création |
|---|---|---|---|
| `serveur` | CPX22 (2 vCPU AMD, 4 Go, 80 Go NVMe), Nuremberg | 80, 443 (applis) ; 25, 465, 993 (mail) et 587 (ouvert, sans service) ; 22 (SSH) et 3000 (Dokploy) depuis `admin_ips` | Docker + Dokploy, NetBird, Claude Code, wrangler, `cf-dns`, `appli`, `sw` |

Base : root désactivé en SSH, clé SSH uniquement, fail2ban, mises à jour de sécurité auto, swap 4 Go, backups Hetzner, protection contre la suppression, reverse DNS = `mail_fqdn`. L'admin (`albert`) est dans les groupes `sudo` et `docker`.

Taille : la stack consomme ~650 Mo, dont ~500 Mo pour Keycloak et sa base (mesuré le 2026-11-03) ; Dokploy et le système ~1,5 Go. Sur 4 Go, garde `KEYCLOAK_MEM_LIMIT=1g` dans le `.env` de la stack. Si la mémoire ou le swap saturent souvent, passe en `cpx32` (8 Go) : change `server_type` puis `.\tf.ps1 apply` (redémarrage de quelques minutes).

## Prérequis

1. Terraform ≥ 1.6.
2. Jeton API Hetzner : https://console.hetzner.com > ton projet > **Security > API tokens** > *Generate API token*, droits **Read & Write**. Il n'est affiché qu'une fois.
3. Clé NetBird : https://app.netbird.io/setup-keys > *Create Setup Key* (usage unique, expiration 1 jour).
4. Un domaine dont tu gères le DNS (Cloudflare).
5. Optionnel : `claude setup-token` sur ton PC, pour que Claude Code soit connecté à ton abonnement dès la création (secret `CLAUDE_CODE_OAUTH_TOKEN`).

## Utilisation

Aucun `.env` dans ce dossier : les secrets vivent dans le magasin global de `../setup.py` (`%USERPROFILE%\.config\djidjelli\secrets.env`), qui les passe à Terraform. `tf.ps1` passe par `setup.py` (PowerShell, dans ce dossier) :

```powershell
cp terraform.tfvars.example terraform.tfvars   # puis édite-le (mail_fqdn obligatoire)
python ..\setup.py secrets                     # état ; « python ..\setup.py secrets set NOM » pour en saisir un
.\tf.ps1 init
.\tf.ps1 plan                                  # ou : python ..\setup.py plan infra
.\tf.ps1 apply
```

Secrets de cette pile : `HCLOUD_TOKEN`, `TFSTATE_ACCESS_KEY_ID`, `TFSTATE_SECRET_ACCESS_KEY` (clés de l'état distant), et à la création seulement `NETBIRD_SETUP_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`.

Sur le serveur actuel, `.\tf.ps1 plan` répond « No changes ».

Si `apply` échoue avec `resource_unavailable` (rupture de stock), mets `location = "hel1"` et relance.

L'installation se poursuit ~5 min après `apply`. Suivi : `ssh serveur 'sudo tail -f /var/log/cloud-init-output.log'` (entrée `serveur` de `~/.ssh/config`).

## Après la création

1. **Jeton Cloudflare** dans `~/.config/cloudflare/env` sur le serveur, puis reconnecte-toi : `wrangler`, `cf-dns` et `appli` marchent.
2. **Stack** : voir « Déployer en prod » dans `../stack/README.md` (`sudo ./scripts/deploie-prod.sh`). Elle publie aussi `https://dokploy.<domaine>`, réservé à NetBird, et Keycloak sur `https://iam.<domaine>` (crée d'abord son DNS : `appli publique iam`, voir la sortie `dns_records_a_creer`).
3. **Dokploy** : ouvre `https://dokploy.<domaine>` (ou `http://serveur.netbird.cloud:3000` avant la stack) et crée le compte admin tout de suite : le premier visiteur le prend.
4. **Mail** : les domaines, boîtes, DKIM et certificat se créent dans Stalwart (voir `../stack/README.md`) ; DNS de référence dans `../dns/dns-djidjelli.fr.md`.

Sur ce compte Hetzner, le port 25 sortant n'est pas bloqué (vérifié le 2026-11-02) : Stalwart envoie directement.

## Points importants

- **Modifier `cloud-init.yaml.tftpl` n'affecte pas le serveur existant** (volontaire, pour ne jamais recréer la prod). Pour l'appliquer : `.\tf.ps1 apply -replace='hcloud_server.this'` (passe d'abord `protected = false`). Ce qui y a été ajouté après la création (Claude Code, `appli`, `sw`, groupe `docker`) a été fait à la main sur le serveur actuel.
- La **protection contre la suppression** fait échouer même `terraform destroy`. C'est voulu.
- Le **state** contient la clé NetBird, les IP et le jeton Claude éventuel. Il est stocké dans le bucket Object Storage `tfstate-serveur-0c3d4523` (nbg1, privé, versionné) : voir `versions.tf`. Ne supprime pas ce bucket.
- Snapshot manuel avant une grosse modif : `hcloud server create-image --type snapshot serveur`.
