#!/bin/bash
# Installe (ou met à jour) l'agent de déploiement continu sur le serveur, en root :
#   sudo git clone https://github.com/djdevpro/modern-stack.git /var/lib/deploiement-stack/depot
#   sudo /var/lib/deploiement-stack/depot/deploiement/installe.sh <commit>
# <commit> : commit de main dont les fichiers de stack/ sont exactement ceux de /opt/stack (point de départ).
# La première fois, le script crée la clé de déploiement et s'arrête : ajoute la clé publique affichée au dépôt
# (Settings > Deploy keys, « Allow write access » : elle n'écrit que la branche « deploiements »), puis relance.
set -euo pipefail

DEPOT=djdevpro/modern-stack
ETAT=/var/lib/deploiement-stack
ICI=$(cd "$(dirname "$0")" && pwd)
base=${1:?"usage : installe.sh <commit de main déjà en place dans /opt/stack>"}

install -d -m 700 "$ETAT"
[ -f "$ETAT/cle" ] || ssh-keygen -q -t ed25519 -N '' -C "deploiement-stack@$(hostname)" -f "$ETAT/cle"
# Clés d'hôte de GitHub prises dans son API (HTTPS), pas acceptées à l'aveugle
curl -fsS https://api.github.com/meta | jq -r '.ssh_keys[] | "github.com " + .' >"$ETAT/known_hosts"

[ -d "$ETAT/depot/.git" ] || git clone -q "https://github.com/$DEPOT.git" "$ETAT/depot"
git -C "$ETAT/depot" fetch -q origin main
base=$(git -C "$ETAT/depot" rev-parse --verify "$base^{commit}")

if [ ! -d "$ETAT/rapports/.git" ]; then
	git init -q "$ETAT/rapports"
	git -C "$ETAT/rapports" remote add origin "git@github.com:$DEPOT.git"
fi
git -C "$ETAT/rapports" config core.sshCommand "ssh -i $ETAT/cle -o IdentitiesOnly=yes -o UserKnownHostsFile=$ETAT/known_hosts"
git -C "$ETAT/rapports" config user.name "Agent de déploiement"
git -C "$ETAT/rapports" config user.email "deploiement@serveur.invalid"
if ! git -C "$ETAT/rapports" ls-remote origin >/dev/null 2>&1; then
	echo "Ajoute cette clé de déploiement au dépôt $DEPOT (accès en écriture), puis relance :"
	cat "$ETAT/cle.pub"
	exit 2
fi
if git -C "$ETAT/rapports" ls-remote --exit-code origin refs/heads/deploiements >/dev/null; then
	git -C "$ETAT/rapports" fetch -q origin deploiements
	git -C "$ETAT/rapports" checkout -q -B deploiements FETCH_HEAD
else
	git -C "$ETAT/rapports" checkout -q --orphan deploiements
	printf '# Déploiements\n\n`etat.json` : dernier résultat de l agent du serveur (deploiement/ sur main).\n' >"$ETAT/rapports/README.md"
	git -C "$ETAT/rapports" add README.md
	git -C "$ETAT/rapports" commit -q -m "Rapports de l'agent de déploiement"
	git -C "$ETAT/rapports" push -q origin HEAD:deploiements
fi

[ -f "$ETAT/deploye" ] || echo "$base" >"$ETAT/deploye"
install -m 755 "$ICI/agent.sh" /usr/local/sbin/deploiement-stack
install -m 644 "$ICI/deploiement-stack.service" "$ICI/deploiement-stack.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable -q --now deploiement-stack.timer
echo "Agent installé, point de départ $(cat "$ETAT/deploye")"
