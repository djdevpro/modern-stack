#!/usr/bin/env python3
"""Veille CVE des images de la stack avec Trivy (bibliothèque standard seulement).

  veille_images.py veille --sarif F --rapport F --maj F [--prs F]
      Analyse chaque image du dépôt : failles critiques et hautes qui ont un correctif.
      --sarif    alertes de l'onglet Security, placées sur la ligne qui épingle l'image
      --rapport  corps de l'issue « Failles de sécurité à corriger » (vide s'il n'y a rien à corriger)
      --maj      « ancienne<TAB>nouvelle » pour chaque image reconstruite par son éditeur (même étiquette,
                 nouvelle empreinte) qui corrige des failles, et pr.md à côté ; rien s'il n'y en a pas
      --prs      PR Dependabot ouvertes (gh pr list --json number,title,url), citées dans le rapport
  veille_images.py comparer --base REF
      Pour une PR : analyse les images changées depuis REF. Échoue si une nouvelle image apporte
      une faille critique corrigeable que l'ancienne n'avait pas.
  veille_images.py appliquer F
      Remplace dans le dépôt les références listées dans F (sortie de --maj).

TRIVY (commande, « trivy » par défaut) permet de lancer Trivy autrement, par exemple dans Docker.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
TRIVY = shlex.split(os.environ.get("TRIVY", "trivy"))
# Fichier -> motif qui y trouve les images (groupe 1)
SOURCES = {
    "stack/compose.yaml": re.compile(r"^\s*image:\s*[\"']?([^\s\"'#]+)", re.M),
    "stack/tools/Dockerfile": re.compile(r"^FROM\s+(?:--\S+\s+)*(\S+)", re.M),
    "stack/scripts/recree-traefik.sh": re.compile(r"\b(traefik:v[\w.-]+(?:@sha256:[0-9a-f]{64})?)"),
}
GRAVITES = ("CRITICAL", "HIGH")
NOMS = {"CRITICAL": "critique", "HIGH": "haute"}
DETAILS_MAX = 15
# Module principal d'un binaire Go compilé depuis un arbre modifié : Trivy ne connaît pas sa version et lui
# prête toutes les failles historiques (ex. Grafana 11.6.5 : CVE-2018-15727). Le même module, version lue
# dans les autres binaires de l'image, reste analysé.
VERSION_ILLISIBLE = re.compile(r"^v0\.0\.0-\d{14}-[0-9a-f]{12}\+dirty$")


class Image:
    def __init__(self, ref: str, fichier: str, ligne: int):
        self.ref, self.fichier, self.ligne = ref, fichier, ligne
        nom, _, self.empreinte = ref.partition("@")
        if ":" in nom.rsplit("/", 1)[-1]:
            self.nom, _, self.etiquette = nom.rpartition(":")
        else:
            self.nom, self.etiquette = nom, ""

    @property
    def court(self) -> str:
        return f"{self.nom}:{self.etiquette}" if self.etiquette else self.nom

    @property
    def affiche(self) -> str:
        return f"{self.court}@{self.empreinte[7:19]}" if self.empreinte else self.court


def lire_fichier(chemin: str) -> str:
    return (RACINE / chemin).read_text(encoding="utf-8")


def images(lire=lire_fichier) -> list[Image]:
    trouvees = []
    for chemin, motif in SOURCES.items():
        try:
            texte = lire(chemin)
        except (OSError, subprocess.CalledProcessError):
            continue
        for m in motif.finditer(texte):
            if "$" not in m.group(1):
                trouvees.append(Image(m.group(1), chemin, texte.count("\n", 0, m.start(1)) + 1))
    return trouvees


def score(v: dict) -> float:
    notes = [c.get("V3Score") or c.get("V40Score") or 0 for c in (v.get("CVSS") or {}).values()]
    return max(notes, default=0) or {"CRITICAL": 9.5, "HIGH": 8.0}.get(v.get("Severity"), 5.0)


_analyses: dict[str, list[dict]] = {}


def analyser(ref: str) -> list[dict]:
    """Failles critiques et hautes corrigeables de l'image, une par faille et par paquet."""
    if ref in _analyses:
        return _analyses[ref]
    sortie = subprocess.run(
        [*TRIVY, "image", "--quiet", "--format", "json", "--scanners", "vuln", "--image-src", "remote",
         "--severity", ",".join(GRAVITES), "--ignore-unfixed", ref],
        capture_output=True, encoding="utf-8", errors="replace", check=False)
    if sortie.returncode:
        raise SystemExit(f"Trivy a échoué sur {ref} :\n{sortie.stderr[-2000:]}")
    failles = {}
    for resultat in json.loads(sortie.stdout).get("Results") or []:
        for v in resultat.get("Vulnerabilities") or []:
            if VERSION_ILLISIBLE.match(v.get("InstalledVersion", "")):
                continue
            failles[(v["VulnerabilityID"], v["PkgName"])] = {
                "id": v["VulnerabilityID"],
                "gravite": v.get("Severity", "HIGH"),
                "paquet": v["PkgName"],
                "installe": v.get("InstalledVersion", ""),
                "corrige": v.get("FixedVersion", ""),
                "titre": v.get("Title") or (v.get("Description") or "")[:200],
                "lien": v.get("PrimaryURL", ""),
                "score": score(v),
            }
    _analyses[ref] = sorted(failles.values(), key=lambda f: (f["gravite"] != "CRITICAL", -f["score"], f["id"]))
    return _analyses[ref]


def ids(failles: list[dict], gravite: str | None = None) -> set[str]:
    return {f["id"] for f in failles if gravite is None or f["gravite"] == gravite}


def empreinte_actuelle(image: Image) -> str:
    """Empreinte actuelle de l'étiquette dans le registre (index multi-architecture)."""
    sortie = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", image.court, "--format", "{{json .Manifest}}"],
        capture_output=True, encoding="utf-8", errors="replace", check=False)
    if sortie.returncode:
        print(f"Empreinte de {image.court} illisible : {sortie.stderr.strip()[:300]}", file=sys.stderr)
        return ""
    return json.loads(sortie.stdout).get("digest", "")


def cellule(texte: str) -> str:
    return str(texte).replace("|", "\\|").replace("\n", " ")


def resume(texte: str) -> None:
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(texte + "\n")
    print(texte)


def ecrire_sarif(chemin: str, analyses: list) -> None:
    regles, resultats = {}, []
    for image, failles, _ in analyses:
        for f in failles:
            regles.setdefault(f["id"], {
                "id": f["id"],
                "name": f["id"],
                "shortDescription": {"text": f"{f['id']} ({f['paquet']})"},
                "fullDescription": {"text": f["titre"] or f["id"]},
                "helpUri": f["lien"] or f"https://avd.aquasec.com/nvd/{f['id'].lower()}",
                "properties": {"security-severity": f"{f['score']:.1f}", "precision": "very-high",
                               "tags": ["security", "vulnerability", f["gravite"]]},
            })
            resultats.append({
                "ruleId": f["id"],
                "level": "error",
                "message": {"text": f"{f['paquet']} {f['installe']} → {f['corrige']} dans {image.court} "
                                    f"({NOMS.get(f['gravite'], f['gravite'])})"},
                "locations": [{"physicalLocation": {"artifactLocation": {"uri": image.fichier},
                                                    "region": {"startLine": image.ligne}}}],
                "partialFingerprints": {"faille/v1": hashlib.sha256(
                    f"{image.nom}|{f['id']}|{f['paquet']}".encode()).hexdigest()},
            })
    sarif = {
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [{"tool": {"driver": {"name": "Trivy", "informationUri": "https://trivy.dev",
                                      "rules": list(regles.values())}},
                  "results": resultats}],
    }
    Path(chemin).write_text(json.dumps(sarif, ensure_ascii=False, indent=1), encoding="utf-8")


def correction(image: Image, reconstruite: dict | None, prs: list[dict]) -> str:
    if reconstruite:
        return "image reconstruite par l'éditeur : PR de sécurité automatique"
    liens = [f"[#{p['number']}]({p['url']})" for p in prs if image.nom in p["title"]]
    if liens:
        return "PR Dependabot " + ", ".join(liens)
    return "attend une nouvelle version de l'éditeur"


def ecrire_rapport(chemin: str, analyses: list, prs: list[dict]) -> str:
    a_corriger = [a for a in analyses if a[1]]
    if not a_corriger:
        Path(chemin).write_text("", encoding="utf-8")
        return "Aucune faille critique ou haute corrigeable dans les images de la stack."
    lignes = ["Failles **critiques** et **hautes** qui ont un correctif, dans les images de la stack (Trivy).", "",
              "| Image | Critiques | Hautes | Correction |", "|---|---|---|---|"]
    for image, failles, reconstruite in a_corriger:
        critiques = len(ids(failles, "CRITICAL"))
        lignes.append(f"| `{image.court}` | {critiques} | {len(failles) - critiques} | "
                      f"{correction(image, reconstruite, prs)} |")
    lignes += ["", "Les correctifs (patch) et les images reconstruites sont fusionnés et déployés seuls quand "
               "la CI passe ; une version mineure ou majeure attend ta fusion.", ""]
    for image, failles, _ in a_corriger:
        lignes += [f"<details><summary><code>{image.court}</code> : {len(failles)} faille(s)</summary>", "",
                   "| Faille | Gravité | Paquet | Installé | Corrigé dans |", "|---|---|---|---|---|"]
        for f in failles[:DETAILS_MAX]:
            nom = f"[{f['id']}]({f['lien']})" if f["lien"] else f["id"]
            lignes.append(f"| {nom} | {NOMS.get(f['gravite'], f['gravite'])} | `{cellule(f['paquet'])}` | "
                          f"{cellule(f['installe'])} | {cellule(f['corrige'])} |")
        if len(failles) > DETAILS_MAX:
            lignes.append(f"| … | | {len(failles) - DETAILS_MAX} autres dans l'onglet Security | | |")
        lignes += ["", "</details>", ""]
    tous = sorted({f["id"] for _, failles, _ in a_corriger for f in failles})
    lignes.append(f"<!-- failles: {' '.join(tous)} -->")
    texte = "\n".join(lignes) + "\n"
    Path(chemin).write_text(texte, encoding="utf-8")
    return texte


def veille(args: argparse.Namespace) -> int:
    prs = json.loads(Path(args.prs).read_text(encoding="utf-8")) if args.prs else []
    analyses, maj = [], []
    for image in images():
        failles = analyser(image.ref)
        reconstruite = None
        if image.empreinte and image.etiquette and failles:
            actuelle = empreinte_actuelle(image)
            if actuelle and actuelle != image.empreinte:
                nouvelles = analyser(f"{image.court}@{actuelle}")
                corrigees = ids(failles) - ids(nouvelles)
                if corrigees and not ids(nouvelles, "CRITICAL") - ids(failles, "CRITICAL"):
                    reconstruite = {"ref": f"{image.court}@{actuelle}", "corrigees": sorted(corrigees),
                                    "restantes": len(nouvelles)}
                    maj.append((image, reconstruite))
        analyses.append((image, failles, reconstruite))

    ecrire_sarif(args.sarif, analyses)
    resume(ecrire_rapport(args.rapport, analyses, prs))
    if maj:
        Path(args.maj).write_text("".join(f"{i.ref}\t{r['ref']}\n" for i, r in maj), encoding="utf-8")
        pr = ["Images reconstruites par leur éditeur (même version, nouvelle empreinte) qui corrigent des "
              "failles. Fusion automatique quand la CI passe, puis déploiement par l'agent du serveur.", "",
              "| Image | Failles corrigées | Restantes (critiques et hautes) |", "|---|---|---|"]
        for image, r in maj:
            liste = ", ".join(r["corrigees"][:8]) + (" …" if len(r["corrigees"]) > 8 else "")
            pr.append(f"| `{image.court}` | {len(r['corrigees'])} : {liste} | {r['restantes']} |")
        Path(args.maj).with_name("pr.md").write_text("\n".join(pr) + "\n", encoding="utf-8")
    return 0


def git_show(ref: str, chemin: str) -> str:
    return subprocess.run(["git", "-C", str(RACINE), "show", f"{ref}:{chemin}"],
                          capture_output=True, encoding="utf-8", errors="replace", check=True).stdout


def comparer(args: argparse.Namespace) -> int:
    avant = {i.nom: i for i in images(lambda chemin: git_show(args.base, chemin))}
    lignes, bloquant = [], False
    for nouvelle in images():
        ancienne = avant.get(nouvelle.nom)
        if ancienne and ancienne.ref == nouvelle.ref:
            continue
        fn = analyser(nouvelle.ref)
        fa = analyser(ancienne.ref) if ancienne else []
        apportees = ids(fn, "CRITICAL") - ids(fa, "CRITICAL")
        bloquant = bloquant or bool(apportees)
        lignes.append(f"| `{ancienne.affiche if ancienne else '—'}` → `{nouvelle.affiche}` | "
                      f"{len(ids(fa) - ids(fn))} | {len(fn)} | {', '.join(sorted(apportees)) or 'aucune'} |")
    if not lignes:
        resume("Aucune image changée par cette PR.")
        return 0
    resume("\n".join(["### CVE des images changées", "",
                      "| Image | Failles corrigées | Restantes (critiques et hautes) | Nouvelles critiques |",
                      "|---|---|---|---|", *lignes]))
    if bloquant:
        resume("\n**Refusé** : une nouvelle image apporte une faille critique corrigeable.")
    return 1 if bloquant else 0


def appliquer(args: argparse.Namespace) -> int:
    for ligne in Path(args.fichier).read_text(encoding="utf-8").splitlines():
        ancienne, nouvelle = ligne.split("\t")
        for chemin in SOURCES:
            fichier = RACINE / chemin
            if fichier.exists() and ancienne in (texte := fichier.read_text(encoding="utf-8")):
                fichier.write_text(texte.replace(ancienne, nouvelle), encoding="utf-8", newline="\n")
                print(f"{chemin} : {ancienne} → {nouvelle}")
    return 0


def main() -> int:
    parseur = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sous = parseur.add_subparsers(dest="commande", required=True)
    p = sous.add_parser("veille")
    p.add_argument("--sarif", required=True)
    p.add_argument("--rapport", required=True)
    p.add_argument("--maj", required=True)
    p.add_argument("--prs")
    p.set_defaults(fonction=veille)
    p = sous.add_parser("comparer")
    p.add_argument("--base", required=True)
    p.set_defaults(fonction=comparer)
    p = sous.add_parser("appliquer")
    p.add_argument("fichier")
    p.set_defaults(fonction=appliquer)
    args = parseur.parse_args()
    return args.fonction(args)


if __name__ == "__main__":
    sys.exit(main())
