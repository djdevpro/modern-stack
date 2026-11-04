#!/bin/sh
# Déploie ou met à jour la stack sur le serveur, derrière le Traefik de Dokploy :
#   cd /opt/stack && sudo ./scripts/deploie-prod.sh
# Lit DOMAINE dans .env.
set -eu
cd "$(dirname "$0")/.."
DOMAINE=$(sed -n 's/^DOMAINE=//p' .env)
[ -n "$DOMAINE" ] || { echo "DOMAINE manquant dans .env" >&2; exit 1; }

# Écriture atomique hors du dossier surveillé : un fichier à moitié écrit ferait perdre un instant à Traefik
# le middleware netbird-seul, donc les routes réservées à NetBird.
sed -e "s/__DOMAINE__/$DOMAINE/g" traefik/prod-dynamic.yml > /etc/dokploy/traefik/tout-en-un.yml.tmp
mv /etc/dokploy/traefik/tout-en-un.yml.tmp /etc/dokploy/traefik/dynamic/tout-en-un.yml
docker compose -f compose.yaml -f compose.prod.yaml up -d --remove-orphans
