#!/usr/bin/env python3
"""Point d'entrée unique de l'infra : un seul magasin de secrets, puis Terraform.

Les secrets vivent dans UN fichier, hors de tout projet :
    %USERPROFILE%\\.config\\modern-stack\\secrets.env   (ou le chemin de MODERN_STACK_SECRETS)
Aucun .env dans les dossiers Terraform ni dans les dépôts : setup.py passe à Terraform les
variables dont chaque pile a besoin, et Terraform dépose lui-même le .env du Drive sur le serveur.

    python setup.py secrets                     état des secrets (présents ou manquants, jamais les valeurs)
    python setup.py secrets set NOM             saisie masquée d'un secret
    python setup.py import FICHIER [--map ANCIEN=NOUVEAU ...] [--strip | --delete]
                                                reprend les secrets d'un ancien .env
    python setup.py check-drive                 vérifie que les clés du Drive n'ouvrent pas l'état Terraform
    python setup.py plan  infra|drive
    python setup.py apply infra|drive [--yes]
    python setup.py output infra|drive [NOM]
    python setup.py tf infra|drive ARGS...      terraform brut avec les secrets chargés

Python 3.10 ou plus, bibliothèque standard seulement.
"""

from __future__ import annotations

import datetime
import getpass
import hashlib
import hmac
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STORE = Path(os.environ.get("MODERN_STACK_SECRETS") or Path.home() / ".config" / "modern-stack" / "secrets.env")

STACKS = {
    "infra": ROOT / "terraform",
    "drive": ROOT / "terraform" / "drive",
}

# Nom dans le magasin -> (variable passée à Terraform, piles qui l'utilisent, obligatoire, description)
SECRETS: dict[str, tuple[str, set[str], bool, str]] = {
    "HCLOUD_TOKEN": ("TF_VAR_hcloud_token", {"infra"}, True,
                     "jeton API Hetzner du projet du serveur (Read & Write)"),
    "NETBIRD_SETUP_KEY": ("TF_VAR_netbird_setup_key", {"infra"}, False,
                          "clé NetBird (utile seulement à la création du serveur)"),
    "CLAUDE_CODE_OAUTH_TOKEN": ("TF_VAR_claude_code_oauth_token", {"infra"}, False,
                                "jeton Claude Code de l'admin (création du serveur seulement)"),
    "TFSTATE_ACCESS_KEY_ID": ("AWS_ACCESS_KEY_ID", {"infra", "drive"}, True,
                              "clé S3 du bucket de l'état Terraform"),
    "TFSTATE_SECRET_ACCESS_KEY": ("AWS_SECRET_ACCESS_KEY", {"infra", "drive"}, True,
                                  "clé secrète S3 du bucket de l'état Terraform"),
    "DRIVE_S3_ACCESS_KEY_ID": ("TF_VAR_drive_s3_access_key", {"drive"}, True,
                               "clé S3 du projet Hetzner du Drive"),
    "DRIVE_S3_SECRET_ACCESS_KEY": ("TF_VAR_drive_s3_secret_key", {"drive"}, True,
                                   "clé secrète S3 du projet Hetzner du Drive"),
    "CLOUDFLARE_API_TOKEN": ("TF_VAR_cloudflare_api_token", {"drive"}, True,
                             "jeton API Cloudflare (DNS de la zone du Drive)"),
}

# Noms des anciens .env de Terraform -> noms du magasin.
LEGACY = {tf: name for name, (tf, _, _, _) in SECRETS.items()}

STATE_BUCKET = "tfstate-serveur-0c3d4523"
S3_HOST = "nbg1.your-objectstorage.com"
S3_REGION = "nbg1"

LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def fail(message: str) -> None:
    print(f"setup: {message}", file=sys.stderr)
    sys.exit(1)


# ---------- Magasin de secrets ----------

def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.lstrip().startswith("#"):
            continue
        match = LINE.match(raw)
        if match:
            values[match.group(1)] = match.group(2).strip().strip('"').strip("'")
    return values


def load_store() -> dict[str, str]:
    """Magasin global ; un secret absent du magasin peut venir de l'environnement sous le même nom."""
    store = parse_env(STORE) if STORE.exists() else {}
    for name in SECRETS:
        if not store.get(name) and os.environ.get(name):
            store[name] = os.environ[name]
    return store


def stored_names() -> set[str]:
    return {k for k, v in parse_env(STORE).items() if v} if STORE.exists() else set()


def save_store(values: dict[str, str]) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Secrets de l'infra (setup.py). Ne jamais copier dans un dépôt ni dans un dossier de projet.\n"]
    lines += [f"{name}={values[name]}\n" for name in sorted(values) if values[name]]
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text("".join(lines), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass  # Windows : le dossier du profil n'est lisible que par son propriétaire.
    tmp.replace(STORE)


def cmd_secrets(args: list[str]) -> None:
    store = load_store() if args[:1] != ["set"] else (parse_env(STORE) if STORE.exists() else {})
    if args[:1] == ["set"]:
        if len(args) != 2 or args[1] not in SECRETS:
            fail(f"usage : secrets set NOM, avec NOM parmi {', '.join(SECRETS)}")
        value = getpass.getpass(f"{args[1]} ({SECRETS[args[1]][3]}) : ").strip()
        if not value:
            fail("valeur vide, rien n'est enregistré")
        store[args[1]] = value
        save_store(store)
        print(f"{args[1]} enregistré dans {STORE}")
        return
    print(f"Magasin : {STORE}{'' if STORE.exists() else ' (absent)'}")
    in_store = stored_names()
    for name, (_, stacks, required, description) in SECRETS.items():
        if store.get(name):
            state = "présent" if name in in_store else "environnement"
        else:
            state = "MANQUANT" if required else "absent (facultatif)"
        print(f"  {name:28} {state:20} {'/'.join(sorted(stacks)):12} {description}")


def cmd_import(args: list[str]) -> None:
    if not args:
        fail("usage : import FICHIER [--map ANCIEN=NOUVEAU ...] [--strip | --delete]")
    source = Path(args[0])
    if not source.exists():
        fail(f"{source} introuvable")
    mapping = dict(LEGACY)
    strip = delete = False
    rest = args[1:]
    while rest:
        option = rest.pop(0)
        if option == "--map" and rest:
            old, _, new = rest.pop(0).partition("=")
            if new not in SECRETS:
                fail(f"--map : {new} n'est pas un secret connu ({', '.join(SECRETS)})")
            mapping[old] = new
        elif option == "--strip":
            strip = True
        elif option == "--delete":
            delete = True
        else:
            fail(f"option inconnue : {option}")
    found = {mapping[k]: v for k, v in parse_env(source).items() if k in mapping and v}
    if not found:
        fail(f"aucun secret connu dans {source}")
    store = parse_env(STORE) if STORE.exists() else {}  # sans les valeurs venues de l'environnement
    store.update(found)
    save_store(store)
    print(f"{len(found)} secret(s) repris de {source} : {', '.join(sorted(found))}")
    imported = {k for k in mapping if mapping[k] in found}
    if delete:
        source.unlink()
        print(f"{source} supprimé")
    elif strip:
        kept = [line for line in source.read_text(encoding="utf-8").splitlines(keepends=True)
                if not ((m := LINE.match(line)) and m.group(1) in imported)]
        source.write_text("".join(kept), encoding="utf-8")
        print(f"lignes retirées de {source} : {', '.join(sorted(imported))}")


# ---------- Garde-fou : les clés du Drive ne doivent pas ouvrir l'état Terraform ----------

def list_buckets(access_key: str, secret_key: str) -> list[str]:
    """ListBuckets signé SigV4 (bibliothèque standard), pour vérifier la portée des clés."""
    now = datetime.datetime.now(datetime.timezone.utc)
    amz_date, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    payload = hashlib.sha256(b"").hexdigest()
    headers = f"host:{S3_HOST}\nx-amz-content-sha256:{payload}\nx-amz-date:{amz_date}\n"
    signed = "host;x-amz-content-sha256;x-amz-date"
    request = "\n".join(["GET", "/", "", headers, signed, payload])
    scope = f"{day}/{S3_REGION}/s3/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(request.encode()).hexdigest()])

    def mac(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    key = mac(mac(mac(mac(("AWS4" + secret_key).encode(), day), S3_REGION), "s3"), "aws4_request")
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    http = urllib.request.Request(f"https://{S3_HOST}/", headers={
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload,
        "Authorization": f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed}, Signature={signature}",
    })
    try:
        with urllib.request.urlopen(http, timeout=20) as response:
            body = response.read().decode()
    except urllib.error.HTTPError as error:
        fail(f"ListBuckets refusé par Hetzner (HTTP {error.code}) : clés du Drive invalides ?")
    return re.findall(r"<Name>([^<]+)</Name>", body)


def check_drive(store: dict[str, str]) -> None:
    access, secret = store.get("DRIVE_S3_ACCESS_KEY_ID"), store.get("DRIVE_S3_SECRET_ACCESS_KEY")
    if not access or not secret:
        fail("clés du Drive manquantes : python setup.py secrets set DRIVE_S3_ACCESS_KEY_ID (puis la clé secrète)")
    if access == store.get("TFSTATE_ACCESS_KEY_ID"):
        fail("les clés du Drive sont celles de l'état Terraform : crée-les dans un projet Hetzner séparé")
    buckets = list_buckets(access, secret)
    if STATE_BUCKET in buckets:
        fail(f"les clés du Drive ouvrent aussi {STATE_BUCKET} (même projet Hetzner). Crée le bucket du Drive "
             "et ses clés dans un projet séparé : le prix de base de l'Object Storage est par compte, pas par projet.")
    print(f"clés du Drive : OK, buckets visibles : {', '.join(buckets) or '(aucun)'}")


# ---------- Terraform ----------

def terraform(stack: str, args: list[str]) -> int:
    if stack not in STACKS:
        fail(f"pile inconnue : {stack} (infra ou drive)")
    store = load_store()
    missing = [n for n, (_, stacks, required, _) in SECRETS.items() if stack in stacks and required and not store.get(n)]
    if missing:
        fail(f"secrets manquants pour {stack} : {', '.join(missing)} (python setup.py secrets set NOM)")
    if stack == "drive" and args[:1] in (["plan"], ["apply"]):
        check_drive(store)
    env = dict(os.environ)
    for name, (variable, stacks, _, _) in SECRETS.items():
        if stack in stacks and store.get(name):
            env[variable] = store[name]
    return subprocess.run(["terraform", f"-chdir={STACKS[stack]}", *args], env=env).returncode


def main(argv: list[str]) -> None:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")  # accents lisibles dans tous les terminaux
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return
    command, args = argv[0], argv[1:]
    if command == "secrets":
        cmd_secrets(args)
    elif command == "import":
        cmd_import(args)
    elif command == "check-drive":
        check_drive(load_store())
    elif command in ("plan", "apply", "output") and args:
        extra = ["-input=false"] if command != "output" else []
        if command == "apply" and "--yes" in args:
            args.remove("--yes")
            extra.append("-auto-approve")
        sys.exit(terraform(args[0], [command, *extra, *args[1:]]))
    elif command == "tf" and args:
        sys.exit(terraform(args[0], args[1:]))
    else:
        fail(f"commande inconnue ou incomplète : {' '.join(argv)} (python setup.py help)")


if __name__ == "__main__":
    main(sys.argv[1:])
