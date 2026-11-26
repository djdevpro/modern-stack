variable "ssh_host" {
  description = "Nom ou adresse SSH du serveur existant."
  type        = string
}

variable "ssh_host_key" {
  description = "Clé publique SSH du serveur, relevée sur une connexion déjà vérifiée."
  type        = string
}

variable "admin_user" {
  description = "Utilisateur SSH, avec sudo sans mot de passe."
  type        = string
}

variable "ssh_private_key_path" {
  description = "Chemin de la clé privée SSH de cet utilisateur."
  type        = string
}

variable "host" {
  description = "Nom public de FERA (fera.app.example.com). Sous le joker des applis, aucun réglage DNS. L'interface, le SDK et la capture partagent ce nom."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", var.host))
    error_message = "Donner un nom d'hôte complet, en minuscules, sans schéma ni chemin."
  }
}

variable "image" {
  description = "Image de FERA, par son empreinte (ghcr.io/<compte>/fera@sha256:...). Le serveur doit pouvoir la tirer."
  type        = string

  validation {
    condition     = can(regex("^ghcr\\.io/[a-z0-9_.-]+/fera@sha256:[0-9a-f]{64}$", var.image))
    error_message = "Donner l'image par son empreinte immuable, pas par une étiquette."
  }
}

variable "cert_resolver" {
  description = "Résolveur de certificats de Traefik pour ce nom public."
  type        = string
  default     = "letsencrypt"
}

variable "admin_email" {
  description = "Identifiant de connexion à l'interface. Par défaut admin@<host>. Le mot de passe est créé sur le serveur."
  type        = string
  default     = null

  validation {
    condition     = var.admin_email == null || can(regex("^[^@ ]+@[^@ ]+$", var.admin_email))
    error_message = "Donner une adresse de la forme nom@domaine."
  }
}

variable "existing_bucket_name" {
  description = "Bucket applicatif privé déjà existant. FERA écrit seulement sous s3_prefix et ne crée ni ne règle aucun bucket."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", var.existing_bucket_name))
    error_message = "Donner le nom du bucket applicatif existant."
  }
}

variable "s3_location" {
  description = "Région Hetzner Object Storage du bucket existant."
  type        = string
  default     = "nbg1"

  validation {
    condition     = contains(["nbg1", "fsn1", "hel1"], var.s3_location)
    error_message = "Régions possibles : nbg1, fsn1 ou hel1."
  }
}

variable "s3_prefix" {
  description = "Dossier de FERA dans le bucket partagé (replays et source maps)."
  type        = string
  default     = "fera/"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]*/$", var.s3_prefix)) && var.s3_prefix != "tabula/"
    error_message = "Un seul dossier, terminé par une barre oblique, distinct de celui de Tabula."
  }
}

variable "drive_s3_access_key" {
  description = "DRIVE_S3_ACCESS_KEY_ID du magasin de setup.py, déjà utilisée par les autres applis du bucket."
  type        = string
  sensitive   = true
}

variable "drive_s3_secret_key" {
  description = "DRIVE_S3_SECRET_ACCESS_KEY du magasin de setup.py."
  type        = string
  sensitive   = true
}

variable "site_project" {
  description = "Projet créé au premier déploiement avec une clé publique fixée d'avance, pour l'écrire dans le site suivi. La clé est publique (elle est dans le site). Les origines limitent la capture à ce site."
  type = object({
    name       = string
    public_key = string
    origins    = list(string)
  })
  default = null

  validation {
    condition     = var.site_project == null || can(regex("^pk_[A-Za-z0-9_-]{8,64}$", var.site_project.public_key))
    error_message = "La clé publique ressemble à pk_ suivi de 8 à 64 lettres, chiffres, - ou _."
  }

  validation {
    condition     = var.site_project == null || alltrue([for o in var.site_project.origins : can(regex("^https://[a-z0-9.-]+$", o))])
    error_message = "Chaque origine est une adresse HTTPS exacte, sans chemin."
  }
}

variable "rate_limit_per_second" {
  description = "Évènements acceptés par seconde et par projet."
  type        = number
  default     = 300

  validation {
    condition     = var.rate_limit_per_second >= 10 && var.rate_limit_per_second <= 10000
    error_message = "Entre 10 et 10000."
  }
}
