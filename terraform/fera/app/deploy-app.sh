#!/bin/sh
# Déploie FERA : secrets créés une seule fois sur le serveur, clés S3 déposées par Terraform, configuration
# de ClickHouse, images tirées, puis docker compose. Les anciens conteneurs tournent jusqu'à ce que les
# nouvelles images soient tirées.
# Usage (root) : deploy-app.sh <dossier déposé>
set -eu
stage="$1"
dir=/opt/fera

for f in compose.yaml s3.env clickhouse.xml; do
  [ -s "$stage/$f" ] || { echo "deploy-app: $stage/$f absent" >&2; exit 1; }
done

install -d -m 750 "$dir" "$dir/clickhouse"
umask 077

# Secrets propres à ce serveur : créés au premier déploiement, jamais réécrits ni envoyés à Terraform.
if [ ! -s "$dir/secrets.env" ]; then
  gen() { tr -dc 'A-Za-z0-9' < /dev/urandom | head -c "$1"; }
  {
    echo "FERA_ADMIN_PASSWORD=$(gen 24)"
    echo "FERA_SECRET=$(gen 48)"
    echo "CLICKHOUSE_PASSWORD=$(gen 32)"
    echo "POSTGRES_PASSWORD=$(gen 32)"
  } > "$dir/secrets.env"
  chmod 600 "$dir/secrets.env"
  echo "deploy-app: secrets créés dans $dir/secrets.env (mot de passe de l'interface : FERA_ADMIN_PASSWORD)"
fi

# Le .env lu par docker compose : secrets du serveur + clés S3 du jour.
cat "$dir/secrets.env" "$stage/s3.env" > "$dir/.env.nouveau"
chmod 600 "$dir/.env.nouveau"
mv "$dir/.env.nouveau" "$dir/.env"
rm -f "$stage/s3.env"

umask 022
install -m 644 "$stage/clickhouse.xml" "$dir/clickhouse/low-memory.xml"
if [ -f "$dir/compose.yaml" ]; then cp "$dir/compose.yaml" "$dir/compose.yaml.precedent"; fi
install -m 644 "$stage/compose.yaml" "$dir/compose.yaml"

cd "$dir"
if ! docker compose pull --quiet; then
  echo "deploy-app: une image ne se tire pas. Le serveur doit avoir un accès en lecture au registre de ces images." >&2
  if [ -f compose.yaml.precedent ]; then mv compose.yaml.precedent compose.yaml; fi
  exit 1
fi

# La migration passe avant les autres services ; en cas d'échec, ses journaux disent pourquoi.
if ! docker compose up -d --remove-orphans; then
  echo "deploy-app: démarrage en échec ; journaux de la migration :" >&2
  docker logs --tail 60 fera-migrate >&2 || true
  exit 1
fi

i=0
attente=""
while [ "$i" -lt 90 ]; do
  attente=""
  for service in fera-capture fera-api; do
    state="$(docker inspect -f '{{.State.Health.Status}}' "$service" 2>/dev/null || echo absent)"
    [ "$state" = healthy ] || attente="$attente $service"
  done
  if [ -z "$attente" ]; then
    rm -f compose.yaml.precedent
    docker image prune -f > /dev/null 2>&1 || true
    echo "deploy-app: FERA en marche"
    exit 0
  fi
  i=$((i + 1))
  sleep 2
done
echo "deploy-app: pas sain après trois minutes :$attente ; derniers journaux :" >&2
for service in $attente fera-worker-replay fera-worker-errors; do docker logs --tail 40 "$service" >&2 || true; done
exit 1
