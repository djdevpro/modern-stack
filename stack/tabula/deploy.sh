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
compose pull database web publish
backup_dir=/opt/backups/tabula
install -d -m 700 "$backup_dir"
database_id=$(compose ps -q database)
if [ -n "$database_id" ]; then
  backup="$backup_dir/$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
  compose stop database
  trap 'compose start database >/dev/null || true' EXIT
  compose run --rm --no-deps -T --entrypoint tar database -czf - -C /home/spacetime/data . >"$backup.partial"
  tar -tzf "$backup.partial" >/dev/null
  mv "$backup.partial" "$backup"
  cp /opt/tabula/.env "$backup.env"
  if [ -f /opt/tabula/.env.previous ]; then cp /opt/tabula/.env.previous "$backup.previous.env"; fi
fi
compose up -d database
ready=false
for _attempt in $(seq 1 60); do
  if curl -fsS --max-time 2 http://127.0.0.1:13300/v1/ping >/dev/null; then ready=true; break; fi
  sleep 2
done
[ "$ready" = true ] || { echo 'Database did not become ready' >&2; exit 1; }
compose run --rm -T publish
compose up -d web
for _attempt in $(seq 1 60); do
  web_id=$(compose ps -q web)
  if [ -n "$web_id" ] && [ "$(docker inspect --format '{{.State.Health.Status}}' "$web_id")" = healthy ]; then
    trap - EXIT
    if [ -f tabula-update.service ]; then
      install -m 644 tabula-update.service tabula-update.timer /etc/systemd/system/
      systemctl daemon-reload
      systemctl enable --now tabula-update.timer
    fi
    echo 'Tabula deployment healthy'
    exit 0
  fi
  sleep 2
done
echo 'Web health check failed; backup retained, no destructive rollback attempted' >&2
exit 1
