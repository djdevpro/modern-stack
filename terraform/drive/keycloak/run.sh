#!/bin/sh
# Lance configure.mjs dans un conteneur Node sur le serveur : le mot de passe d'admin de Keycloak est
# lu dans /opt/stack/.env et ne quitte jamais le serveur ; le secret du client « drive » est écrit
# dans /opt/universal-drive/secrets/drive-client.secret (lu ensuite par install-env.sh).
# Usage (root) : run.sh <dossier déposé> <url Keycloak> <hôte Keycloak> <IPv4 du serveur> <realm> <url du Drive> <client>
set -eu
stage="$1"
kc_url="$2"
kc_host="$3"
server_ip="$4"
realm="$5"
public_url="$6"
client="$7"

password="$(sed -n 's/^KEYCLOAK_ADMIN_PASSWORD=//p' /opt/stack/.env | head -1 | sed -e 's/^"//' -e 's/"$//')"
[ -n "$password" ] || { echo "keycloak: KEYCLOAK_ADMIN_PASSWORD absent de /opt/stack/.env" >&2; exit 1; }
install -d -m 700 /opt/universal-drive/secrets
UD_KC_ADMIN_PASSWORD="$password"
export UD_KC_ADMIN_PASSWORD

# Le nom de Keycloak est forcé sur l'IP publique : dans un conteneur, il pourrait se résoudre en 127.0.1.1.
docker run --rm --add-host "$kc_host:$server_ip" \
  -v "$stage/configure.mjs:/configure.mjs:ro" -v /opt/universal-drive/secrets:/out \
  -e UD_KC_ADMIN_PASSWORD -e UD_KC_URL="$kc_url" -e UD_KC_REALM="$realm" \
  -e UD_PUBLIC_URL="$public_url" -e UD_OIDC_CLIENT_ID="$client" \
  node:24-alpine node /configure.mjs --secret-file /out/drive-client.secret
