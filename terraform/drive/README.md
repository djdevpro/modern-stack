# Stockage du Drive (drive.djidjelli.fr)

Terraform gère le bucket Hetzner Object Storage du Drive, sa règle CORS, le nettoyage des envois abandonnés, **et le `.env` du Drive sur le serveur** (`/opt/universal-drive/.env`, root, 0600). L'état est dans le même bucket que l'infra (`tfstate-serveur-0c3d4523`), sous la clé `drive/terraform.tfstate` : le plan du serveur (`../`) n'est pas touché.

Aucun secret dans ce dossier : tout passe par `../../setup.py` et son magasin global (`%USERPROFILE%\.config\djidjelli\secrets.env`).

## Pourquoi un projet Hetzner à part

Chez Hetzner, une clé S3 ouvre **tous** les buckets de son projet. Dans le projet du serveur, les clés du Drive ouvriraient aussi l'état Terraform. Le Drive a donc son propre projet ; le prix de base de l'Object Storage est par compte, pas par projet ni par bucket. `setup.py` refuse de planifier ou d'appliquer si les clés du Drive voient le bucket de l'état.

## Mise en place

1. Console Hetzner : **New project** (par exemple « drive »), puis dans ce projet **Object Storage > S3 credentials > Generate credentials**.
2. Depuis `Mon_serveur` :

```powershell
python setup.py secrets set DRIVE_S3_ACCESS_KEY_ID
python setup.py secrets set DRIVE_S3_SECRET_ACCESS_KEY
python setup.py check-drive          # les clés ne doivent voir que les buckets du Drive
python setup.py tf drive init
python setup.py plan drive
python setup.py apply drive
```

Si le bucket existe déjà dans le projet du Drive (créé dans la console) : `python setup.py tf drive import minio_s3_bucket.drive <nom>` avant le `plan`, et `bucket_name` à ce nom.

## Ce que dépose Terraform sur le serveur

`UD_MODE`, `UD_CONFIG`, `UD_TERMS_FILE`, `UD_PUBLIC_URL`, `UD_TRUST_PROXY`, `UD_INSTANCE_NAME`, `UD_SECRET_KEY` (générée une fois et gardée dans l'état, qui en est la sauvegarde), l'émetteur et le client OIDC, et tout `UD_S3_*`. Le secret du client OIDC n'entre jamais dans l'état : `install-env.sh` le lit sur le serveur (`/opt/universal-drive/secrets/drive-client.secret`). Le fichier est redéposé dès que son contenu change.

## Points importants

- `prevent_destroy` protège le bucket et `UD_SECRET_KEY`.
- La règle CORS est gérée ici : ne pas lancer `universal-drive/deploy/s3-cors.mjs` en plus.
- Pas encore ici : versioning (avec la corbeille et les versions du Drive), chiffrement côté serveur, réplication.
