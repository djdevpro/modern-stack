# Les clés arrivent de setup.py (magasin global DRIVE_S3_*), jamais d'un fichier du dossier.
variable "drive_s3_access_key" {
  description = "Clé d'accès S3 du projet Hetzner dédié au Drive."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.drive_s3_access_key) > 0
    error_message = "Clé d'accès S3 du Drive manquante (python setup.py secrets set DRIVE_S3_ACCESS_KEY_ID)."
  }
}

variable "drive_s3_secret_key" {
  description = "Clé secrète S3 du projet Hetzner dédié au Drive."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.drive_s3_secret_key) > 0
    error_message = "Clé secrète S3 du Drive manquante (python setup.py secrets set DRIVE_S3_SECRET_ACCESS_KEY)."
  }
}

variable "location" {
  description = "Emplacement Object Storage : nbg1 (même centre que le serveur), fsn1 ou hel1."
  type        = string
  default     = "nbg1"

  validation {
    condition     = contains(["nbg1", "fsn1", "hel1"], var.location)
    error_message = "location doit valoir nbg1, fsn1 ou hel1."
  }
}

variable "bucket_name" {
  description = "Nom du bucket du Drive (unique chez Hetzner). S'il existe déjà : python setup.py tf drive import minio_s3_bucket.drive <nom>."
  type        = string
  default     = "mydrive"
}

variable "public_origin" {
  description = "Origine publique du Drive : règle CORS (envois et aperçus en direct) et UD_PUBLIC_URL."
  type        = string
  default     = "https://drive.djidjelli.fr"
}

variable "incomplete_upload_days" {
  description = "Envois multipart abandonnés supprimés par Hetzner après ce nombre de jours (ils sont facturés tant qu'ils existent)."
  type        = number
  default     = 3
}

variable "instance_name" {
  description = "Nom affiché de l'instance (UD_INSTANCE_NAME)."
  type        = string
  default     = "Drive djidjelli"
}

variable "oidc_issuer" {
  description = "Émetteur OIDC : realm Keycloak du Drive (définitif, il entre dans l'identité des personnes)."
  type        = string
  default     = "https://iam.djidjelli.fr/realms/djidjelli"
}

variable "oidc_client_id" {
  description = "Client OIDC du Drive dans ce realm."
  type        = string
  default     = "drive"
}

variable "abuse_email" {
  description = "Adresse de signalement d'abus (UD_ABUSE_EMAIL), vide tant qu'elle n'est pas choisie."
  type        = string
  default     = ""
}

variable "admin_user" {
  description = "Compte d'administration du serveur (sudo sans mot de passe)."
  type        = string
  default     = "albert"
}

variable "ssh_private_key_path" {
  description = "Clé SSH privée de l'admin, sur ce PC."
  type        = string
  default     = "~/.ssh/id_ed25519_perso"
}
