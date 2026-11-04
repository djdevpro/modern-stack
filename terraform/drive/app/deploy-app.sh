#!/bin/sh
# Déploie le Drive : code de GitHub au commit demandé, configuration déposée par Terraform, puis
# docker compose. Garde l'ancien code tant que le nouveau conteneur n'est pas sain.
# Usage (root) : deploy-app.sh <dossier déposé> <dépôt owner/nom> <commit>
set -eu
stage="$1"
repo="$2"
sha="$3"
dir=/opt/universal-drive

case "$sha" in "" | *[!0-9a-f]*) echo "deploy-app: commit invalide ($sha)" >&2; exit 1 ;; esac
[ -s "$dir/.env" ] || { echo "deploy-app: $dir/.env absent" >&2; exit 1; }

install -d -m 755 "$dir/config"
install -m 644 "$stage/compose.yaml" "$dir/compose.yaml"
install -m 644 "$stage/drive.config.yaml" "$dir/config/drive.config.yaml"
install -m 644 "$stage/terms.md" "$dir/config/terms.md"

tmp="$(mktemp -d)"
curl -fsSL "https://codeload.github.com/$repo/tar.gz/$sha" | tar -xz -C "$tmp" --strip-components=1
rm -rf "$dir/src.old"
if [ -d "$dir/src" ]; then mv "$dir/src" "$dir/src.old"; fi
mv "$tmp" "$dir/src"
echo "$sha" > "$dir/src/.commit"

cd "$dir"
docker compose build --pull drive
docker compose up -d drive

i=0
while [ "$i" -lt 40 ]; do
  state="$(docker inspect -f '{{.State.Health.Status}}' universal-drive 2>/dev/null || echo absent)"
  if [ "$state" = healthy ]; then
    rm -rf "$dir/src.old"
    docker image prune -f > /dev/null 2>&1 || true
    echo "deploy-app: universal-drive sain au commit $sha"
    exit 0
  fi
  i=$((i + 1))
  sleep 3
done
echo "deploy-app: le conteneur n'est pas sain après 2 min ; derniers journaux :" >&2
docker compose logs --tail 40 drive >&2
exit 1
