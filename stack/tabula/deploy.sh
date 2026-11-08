#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
umask 077
if [ "${TABULA_LOCK_HELD:-0}" != 1 ]; then
  exec 9>/run/lock/tabula-deploy.lock
  flock -n 9 || exit 0
fi
compose() { docker compose --env-file /opt/tabula/.env -f compose.yaml "$@"; }
compose config --quiet
compose pull database web publish manager
# Every executable component belongs to the same immutable, validated release.
release=''
while IFS= read -r image; do
  [[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] || { echo 'Immutable image digest required' >&2; exit 1; }
  label=$(docker image inspect --format '{{index .Config.Labels "io.tabula.release"}}' "$image")
  [[ "$label" =~ ^[0-9a-f]{40}-[0-9]+-[0-9]+$ ]] || { echo 'Validated release label required' >&2; exit 1; }
  if [ -n "$release" ] && [ "$release" != "$label" ]; then echo 'Mixed component releases refused' >&2; exit 1; fi
  release=$label
done < <(compose config --images)
[ -n "$release" ] || { echo 'No validated component images found' >&2; exit 1; }
backup_dir=/opt/backups/tabula
install -d -m 700 "$backup_dir"
database_id=$(compose ps -q database)
compose stop web manager
if [ -n "$database_id" ]; then
  # Preserve existing signing keys before replacing a container from older deployments.
  docker exec "$database_id" sh -ec 'if [ ! -f /home/spacetime/data/identity/id_ecdsa ]; then mkdir -p /home/spacetime/data/identity; chmod 700 /home/spacetime/data/identity; cp /home/spacetime/.config/spacetime/id_ecdsa* /home/spacetime/data/identity/; chmod 600 /home/spacetime/data/identity/id_ecdsa; fi'
  backup="$backup_dir/$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
  compose stop database
  trap 'compose start database >/dev/null || true' EXIT
  compose run --rm --no-deps -T --entrypoint tar database -czf - -C /home/spacetime/data . >"$backup.partial"
  tar -tzf "$backup.partial" >/dev/null
  mv "$backup.partial" "$backup"
  # Publisher identity and native definitions/recovery journals must match this backup.
  compose run --rm --no-deps -T --entrypoint tar publish -czf - -C /home/spacetime/.config/spacetime . >"$backup.publisher.partial"
  tar -tzf "$backup.publisher.partial" >/dev/null
  mv "$backup.publisher.partial" "$backup.publisher.tar.gz"
  compose run --rm --no-deps -T --entrypoint tar manager -czf - -C /app/infra/native-data . >"$backup.manager.partial"
  tar -tzf "$backup.manager.partial" >/dev/null
  mv "$backup.manager.partial" "$backup.manager.tar.gz"
  cp /opt/tabula/.env "$backup.env"
  if [ -f /opt/tabula/.env.previous ]; then cp /opt/tabula/.env.previous "$backup.previous.env"; fi
fi
compose run --rm --no-deps -T --entrypoint sh database -ec 'mkdir -p /home/spacetime/data/identity; chmod 700 /home/spacetime/data/identity'
compose up -d database
ready=false
for _attempt in $(seq 1 60); do
  if curl -fsS --max-time 2 http://127.0.0.1:13300/v1/ping >/dev/null; then ready=true; break; fi
  sleep 2
done
[ "$ready" = true ] || { echo 'Database did not become ready' >&2; exit 1; }
compose run --rm -T publish
# Rebuild existing children from the same release source without changing their manifests or rows.
compose run --rm --no-deps -T --entrypoint node manager --import tsx apps/native-manager/src/upgrade-runtime.ts
compose up -d manager web
for _attempt in $(seq 1 60); do
  web_id=$(compose ps -q web)
  manager_id=$(compose ps -q manager)
  if [ -n "$web_id" ] && [ -n "$manager_id" ] \
    && [ "$(docker inspect --format '{{.State.Health.Status}}' "$web_id")" = healthy ] \
    && [ "$(docker inspect --format '{{.State.Health.Status}}' "$manager_id")" = healthy ]; then
    # Manual deployments and timer updates share the same success marker.
    # Rename within the same directory so the updater never reads a partial release.
    release_next=$(mktemp /opt/tabula/.release.XXXXXX)
    if ! printf '%s\n' "$release" >"$release_next" || ! mv -f "$release_next" /opt/tabula/release; then
      rm -f "$release_next"
      echo 'Could not record healthy release' >&2
      exit 1
    fi
    trap - EXIT
    # Terraform owns the update timer and its policy; deployment must not reset them.
    echo 'Tabula deployment healthy'
    exit 0
  fi
  sleep 2
done
echo 'Web or manager health check failed; backups retained, no destructive rollback attempted' >&2
exit 1
