#!/bin/sh
# Recrée le conteneur dokploy-traefik comme Dokploy, avec les deux ajouts de la prod :
#   - le jeton Cloudflare du résolveur letsencrypt-dns (/etc/dokploy/traefik/cloudflare.env) ;
#   - le réseau traefik-public (IPv4 et IPv6), qui publie les ports 80 et 443. Sur dokploy-network seul, Traefik publie
#     ses ports par docker_gwbridge, sans IPv6 : Docker relaie l'IPv6 par docker-proxy et tous ces clients arrivent en 172.18.0.1.
# À relancer si Dokploy recrée Traefik (réglages Traefik de son interface). Coupe les sites quelques secondes.
#   sudo /opt/stack/scripts/recree-traefik.sh
set -eu
IMAGE=$(docker inspect -f '{{.Config.Image}}' dokploy-traefik 2>/dev/null || echo traefik:v3.6.25)

# Passerelle IPv4 fixe : les conteneurs qui joignent un site par l'IP publique arrivent en 172.30.0.1,
# adresse autorisée dans x:AllowedIp de Stalwart. Sous-réseau IPv6 : ULA choisie par Docker.
docker network inspect traefik-public >/dev/null 2>&1 ||
  docker network create --ipv6 --subnet 172.30.0.0/24 --gateway 172.30.0.1 traefik-public >/dev/null

docker rm -f dokploy-traefik >/dev/null 2>&1 || true
# gw-priority : la route par défaut et les ports publiés passent par traefik-public, pas par docker_gwbridge
docker run -d --name dokploy-traefik --restart always \
  --network name=traefik-public,gw-priority=1 --network dokploy-network \
  -p 80:80/tcp -p 443:443/tcp -p 443:443/udp \
  -v /etc/dokploy/traefik/traefik.yml:/etc/traefik/traefik.yml \
  -v /etc/dokploy/traefik/dynamic:/etc/dokploy/traefik/dynamic \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  --env-file /etc/dokploy/traefik/cloudflare.env \
  "$IMAGE"
