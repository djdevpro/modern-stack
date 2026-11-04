#!/bin/bash
# Ouvre une PR qui applique les nouvelles références d'images (ancienne<TAB>nouvelle, une par ligne), lance la CI
# dessus (une PR ouverte avec GITHUB_TOKEN ne la déclenche pas) et active la fusion automatique.
#   pr-images.sh <maj.tsv> <corps.md> <étiquette> <titre> <branche>
# Ne fait rien s'il n'y a rien à mettre à jour ou si une PR de la même étiquette est déjà ouverte.
set -euo pipefail
maj=$1 corps=$2 etiquette=$3 titre=$4 branche=$5

[ -s "$maj" ] || { echo "Rien à mettre à jour."; exit 0; }
if [ "$(gh pr list --state open --label "$etiquette" --json number --jq length)" != 0 ]; then
	echo "Une PR « $etiquette » est déjà ouverte."
	exit 0
fi
python3 .github/scripts/veille_images.py appliquer "$maj"
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git switch -c "$branche"
git commit -qam "$titre"
auth=$(printf 'x-access-token:%s' "$GH_TOKEN" | base64 -w0)
git -c "http.https://github.com/.extraheader=AUTHORIZATION: basic $auth" push -q origin "HEAD:refs/heads/$branche"
gh pr create --base main --head "$branche" --label "$etiquette" --title "$titre" --body-file "$corps"
gh workflow run ci.yml --ref "$branche"
gh pr merge "$branche" --auto --squash
