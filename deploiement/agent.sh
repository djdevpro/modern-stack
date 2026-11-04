#!/bin/bash
# Agent de déploiement continu de la stack, lancé toutes les 5 minutes par deploiement-stack.timer (root) :
#   1. récupère main de github.com/djdevpro/modern-stack (dépôt public : lecture sans jeton) ;
#   2. attend que le workflow « CI » de ce commit, ou de la PR qui l'a fusionné, soit vert ;
#   3. copie dans /opt/stack les fichiers de stack/ changés depuis le dernier déploiement (sauf stack/voxel/,
#      déployé depuis le dépôt de Voxel), et refuse si l'un d'eux a été modifié à la main sur le serveur ;
#   4. lance scripts/deploie-prod.sh puis attend que les conteneurs soient sains ;
#   5. publie le résultat dans etat.json sur la branche « deploiements » (clé de déploiement du dépôt),
#      que le workflow « Suivi du déploiement » reporte sur le commit et dans une issue en cas d'échec.
# Installation : deploiement/installe.sh. Journal : journalctl -u deploiement-stack.
# FORCER=1 deploiement-stack : écrase les fichiers modifiés à la main (sauvegardés dans /opt/backups).
set -euo pipefail

DEPOT=djdevpro/modern-stack
CIBLE=/opt/stack
ETAT=/var/lib/deploiement-stack
CLONE=$ETAT/depot
RAPPORTS=$ETAT/rapports
DEPLOYE=$ETAT/deploye  # commit dont les fichiers de stack/ sont dans /opt/stack
SIGNALE=$ETAT/signale  # dernier résultat publié (« résultat commit »)

exec 9>/run/deploiement-stack.lock
flock -n 9 || exit 0

api() {
	curl -fsS --max-time 20 -H 'Accept: application/vnd.github+json' "https://api.github.com/repos/$DEPOT/$1"
}

dernier_ci() {
	api "actions/runs?head_sha=$1&per_page=100" |
		jq -r '[.workflow_runs[] | select(.name == "CI")] | max_by(.created_at) | if . == null then "aucun" else "\(.status) \(.conclusion)" end'
}

# succes | attente | echec. Une fusion automatique faite par GITHUB_TOKEN ne relance pas la CI sur main :
# on prend alors la CI de la tête de la PR fusionnée (exigée verte par les règles de main).
etat_ci() {
	local res tete
	res=$(dernier_ci "$1")
	if [ "$res" = aucun ]; then
		tete=$(api "commits/$1/pulls" | jq -r '[.[] | select(.merged_at != null)][0].head.sha // empty')
		if [ -n "$tete" ]; then res=$(dernier_ci "$tete"); fi
	fi
	case $res in
	"completed success") echo succes ;;
	completed*) echo echec ;;
	*) echo attente ;;
	esac
}

# Publie $ETAT/etat.json sur la branche deploiements, s'il ne l'est pas déjà.
publie() {
	local cle
	cle=$(jq -r '"\(.resultat) \(.sha)"' "$ETAT/etat.json")
	[ "$(cat "$SIGNALE" 2>/dev/null)" != "$cle" ] || return 0
	git -C "$RAPPORTS" fetch -q origin deploiements
	git -C "$RAPPORTS" reset -q --hard FETCH_HEAD
	cp "$ETAT/etat.json" "$RAPPORTS/etat.json"
	git -C "$RAPPORTS" add etat.json
	git -C "$RAPPORTS" diff --cached --quiet || git -C "$RAPPORTS" commit -q -m "$cle"
	git -C "$RAPPORTS" push -q origin HEAD:deploiements
	echo "$cle" >"$SIGNALE"
}

# rapport <succes|attente|echec|refuse> <commit> <message> [détails]
rapport() {
	local resultat=$1 sha=$2 message=$3 details=${4:-}
	echo "$resultat ${sha:0:7} : $message${details:+$'\n'$details}"
	[ "$(cat "$SIGNALE" 2>/dev/null)" != "$resultat $sha" ] || return 0
	jq -n --arg sha "$sha" --arg resultat "$resultat" --arg message "$message" --arg details "$details" \
		--arg date "$(date -u +%FT%TZ)" '{sha: $sha, resultat: $resultat, message: $message, details: $details, date: $date}' \
		>"$ETAT/etat.json"
	publie
}

# Attend (au plus $1 secondes) que tous les conteneurs tournent et que ceux qui ont un healthcheck soient sains.
sante() {
	local fin=$((SECONDS + $1)) malades
	while :; do
		malades=$(cd "$CIBLE" && docker compose -f compose.yaml -f compose.prod.yaml ps --all --format json |
			jq -rs 'flatten | map(select(.State != "running" or (.Health != "" and .Health != "healthy")))
				| map("\(.Service) : \(.State) \(.Health)") | join("\n")') || malades="docker compose ps a échoué"
		[ -n "$malades" ] || return 0
		if [ "$SECONDS" -ge "$fin" ]; then
			echo "$malades"
			return 1
		fi
		sleep 10
	done
}

# Remplace l'agent et ses unités systemd par ceux du commit déployé (pris en compte au prochain passage).
mise_a_jour_agent() {
	local f
	git -C "$CLONE" diff --quiet "$ancien" "$nouveau" -- deploiement/ && return 0
	git -C "$CLONE" cat-file blob "$nouveau:deploiement/agent.sh" >/usr/local/sbin/.deploiement-stack.nouveau
	chmod 755 /usr/local/sbin/.deploiement-stack.nouveau
	mv -f /usr/local/sbin/.deploiement-stack.nouveau /usr/local/sbin/deploiement-stack
	for f in deploiement-stack.service deploiement-stack.timer; do
		git -C "$CLONE" cat-file blob "$nouveau:deploiement/$f" >"/etc/systemd/system/$f"
	done
	systemctl daemon-reload
	echo "Agent mis à jour"
}

# Un rapport resté non publié (GitHub injoignable) part au passage suivant.
if [ -f "$ETAT/etat.json" ]; then publie || echo "Rapport toujours pas publié" >&2; fi

git -C "$CLONE" fetch -q origin main
nouveau=$(git -C "$CLONE" rev-parse FETCH_HEAD)
ancien=$(cat "$DEPLOYE")
[ "$nouveau" != "$ancien" ] || exit 0
if ! git -C "$CLONE" cat-file -e "$ancien^{commit}"; then
	rapport echec "$nouveau" "Commit déployé $ancien introuvable dans main (historique réécrit ?)"
	exit 1
fi

case $(etat_ci "$nouveau") in
attente)
	rapport attente "$nouveau" "En attente de la CI"
	exit 0
	;;
echec)
	rapport refuse "$nouveau" "CI en échec : rien n'est déployé"
	exit 0
	;;
esac

liste=$(git -C "$CLONE" diff --no-renames --name-status "$ancien" "$nouveau" -- stack ':(exclude)stack/voxel')
changes=()
if [ -n "$liste" ]; then mapfile -t changes <<<"$liste"; fi

# Un fichier à remplacer doit être celui du dernier déploiement (ou déjà celui du nouveau commit).
conflits=()
for ligne in "${changes[@]}"; do
	statut=${ligne%%$'\t'*} chemin=${ligne#*$'\t'}
	fichier=$CIBLE/${chemin#stack/}
	if [ ! -e "$fichier" ]; then
		[ "$statut" != M ] || conflits+=("${chemin#stack/} (absent du serveur)")
	elif ! git -C "$CLONE" cat-file blob "$nouveau:$chemin" 2>/dev/null | cmp -s - "$fichier" &&
		! { [ "$statut" != A ] && git -C "$CLONE" cat-file blob "$ancien:$chemin" | cmp -s - "$fichier"; }; then
		conflits+=("${chemin#stack/}")
	fi
done
if [ ${#conflits[@]} -gt 0 ] && [ "${FORCER:-0}" != 1 ]; then
	rapport refuse "$nouveau" "Fichiers modifiés à la main dans $CIBLE : rien n'est déployé" "$(printf '%s\n' "${conflits[@]}")"
	exit 0
fi

sauvegarde=/opt/backups/stack-avant-force-$(date -u +%Y%m%d-%H%M%S)
for ligne in "${changes[@]}"; do
	statut=${ligne%%$'\t'*} chemin=${ligne#*$'\t'}
	fichier=$CIBLE/${chemin#stack/}
	if [ "${FORCER:-0}" = 1 ] && [ -e "$fichier" ]; then install -D -m 600 "$fichier" "$sauvegarde/${chemin#stack/}"; fi
	if [ "$statut" = D ]; then
		rm -f "$fichier"
	else
		mode=$(git -C "$CLONE" ls-tree "$nouveau" -- "$chemin" | cut -d' ' -f1)
		install -d "$(dirname "$fichier")"
		git -C "$CLONE" cat-file blob "$nouveau:$chemin" >"$fichier.deploiement"
		chmod "${mode: -3}" "$fichier.deploiement"
		mv -f "$fichier.deploiement" "$fichier"
	fi
done
echo "$nouveau" >"$DEPLOYE"
mise_a_jour_agent

if [ ${#changes[@]} -eq 0 ]; then
	rapport succes "$nouveau" "Rien à déployer : aucun fichier de stack/ modifié"
	exit 0
fi

modifies=$(printf '%s\n' "${changes[@]}" | cut -f2)
if grep -q '^stack/tools/' <<<"$modifies"; then
	docker build -q -t tout-en-un/tools:local "$CIBLE/tools" >/dev/null
fi
for s in appli cf-dns sw; do
	if grep -qx "stack/scripts/$s" <<<"$modifies"; then install -m 755 "$CIBLE/scripts/$s" "/usr/local/bin/$s"; fi
done

if ! sortie=$("$CIBLE/scripts/deploie-prod.sh" 2>&1); then
	rapport echec "$nouveau" "deploie-prod.sh a échoué" "$(tail -n 40 <<<"$sortie")"
	exit 1
fi
if ! malades=$(sante 600); then
	rapport echec "$nouveau" "Conteneurs en mauvaise santé après le déploiement" "$malades"
	exit 1
fi
rapport succes "$nouveau" "${#changes[@]} fichier(s) de stack/ déployé(s)" "$modifies"
