#!/bin/sh
# Déploie Voxel (le webmail) : code du dépôt voxel dans /opt/universal-email-v3 (version précédente dans -prev),
# configuration de prod dans /opt/stack/voxel (compose avec les labels Traefik, config.json des domaines).
# Depuis le PC, dans le dépôt voxel :
#   tar czf voxel.tgz --exclude=node_modules --exclude=dist --exclude=dev-dist --exclude=.git --exclude=data --exclude='.env*' --exclude=./config.json .
#   scp voxel.tgz serveur:/tmp/voxel.tgz
# Puis sur le serveur : sudo /opt/stack/scripts/deploie-voxel.sh /tmp/voxel.tgz
# Le webmail est coupé quelques secondes, le temps de recréer le conteneur.
set -eu
ARCHIVE=${1:?usage : deploie-voxel.sh /tmp/voxel.tgz}
CODE=/opt/universal-email-v3

rm -rf "$CODE-new"
mkdir "$CODE-new"
tar xzf "$ARCHIVE" -C "$CODE-new" --no-same-owner
chown -R root:root "$CODE-new"
rm -rf "$CODE-prev"
if [ -d "$CODE" ]; then mv "$CODE" "$CODE-prev"; fi
mv "$CODE-new" "$CODE"
rm -f "$ARCHIVE"

cd /opt/stack/voxel
docker compose build
docker compose up -d
