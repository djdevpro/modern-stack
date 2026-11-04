# Valeurs propres à ce serveur : terraform.tfvars (non versionné, voir terraform.tfvars.example).
# Les secrets arrivent de setup.py (magasin global), jamais d'un fichier du dossier.

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

variable "cloudflare_api_token" {
  description = "Jeton API Cloudflare avec le droit DNS:Edit sur la zone (secret CLOUDFLARE_API_TOKEN de setup.py)."
  type        = string
  sensitive   = true
}

variable "location" {
  description = "Emplacement Object Storage : nbg1, fsn1 ou hel1 (le même centre que le serveur, de préférence)."
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
}

variable "incomplete_upload_days" {
  description = "Envois multipart abandonnés supprimés par Hetzner après ce nombre de jours (ils sont facturés tant qu'ils existent)."
  type        = number
  default     = 3
}

variable "dns_zone" {
  description = "Zone Cloudflare du Drive (example.com)."
  type        = string
}

variable "dns_name" {
  description = "Nom du Drive dans la zone : le Drive répond sur https://<dns_name>.<dns_zone>."
  type        = string
  default     = "drive"
}

variable "instance_name" {
  description = "Nom affiché de l'instance (UD_INSTANCE_NAME)."
  type        = string
  default     = "S3 Drive"
}

variable "keycloak_url" {
  description = "Adresse publique de Keycloak (https://iam.example.com)."
  type        = string
}

variable "keycloak_realm" {
  description = "Realm du Drive (définitif : il entre dans l'identité des personnes)."
  type        = string

  validation {
    condition     = var.keycloak_realm != "master"
    error_message = "Le realm master est réservé à l'administration de Keycloak."
  }
}

variable "oidc_client_id" {
  description = "Client OIDC du Drive dans ce realm."
  type        = string
  default     = "drive"
}

variable "oidc_label" {
  description = "Libellé du bouton « Se connecter avec … » ; vide = nom d'hôte de Keycloak."
  type        = string
  default     = ""
}

variable "abuse_email" {
  description = "Adresse de signalement d'abus (UD_ABUSE_EMAIL), vide tant qu'elle n'est pas choisie."
  type        = string
  default     = ""
}

variable "terms_file" {
  description = "Conditions d'utilisation en Markdown ; vide = modèle app/terms.md, à compléter (le texte réel peut vivre hors du dépôt, dans notes/)."
  type        = string
  default     = ""
}

variable "admin_user" {
  description = "Compte d'administration du serveur (sudo sans mot de passe)."
  type        = string
}

variable "ssh_private_key_path" {
  description = "Clé SSH privée de l'admin, sur ce PC."
  type        = string
  default     = "~/.ssh/id_ed25519"
}

variable "app_repo" {
  description = "Dépôt GitHub du Drive (code construit sur le serveur)."
  type        = string
  default     = "djdevpro/s3drive"
}

variable "app_ref" {
  description = "Branche, étiquette ou commit à déployer ; une branche est résolue en commit à chaque plan."
  type        = string
  default     = "main"
}
