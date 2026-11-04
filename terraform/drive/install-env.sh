#!/bin/sh
# Installe le .env du Drive déposé par Terraform, et y ajoute le secret du client OIDC lu sur le
# serveur (écrit par universal-drive/deploy/keycloak/configure.mjs) : il n'entre jamais dans l'état.
# Usage (root) : install-env.sh <fichier déposé>
set -eu
src="$1"
dir=/opt/universal-drive
secret="$dir/secrets/drive-client.secret"

[ -s "$secret" ] || { echo "install-env: secret OIDC absent ($secret)" >&2; exit 1; }
umask 077
install -d -m 755 "$dir"
{ cat "$src"; printf 'UD_OIDC_CLIENT_SECRET=%s\n' "$(cat "$secret")"; } > "$dir/.env.new"
chmod 600 "$dir/.env.new"
mv "$dir/.env.new" "$dir/.env"
rm -f "$src"
echo "install-env: $dir/.env écrit"
