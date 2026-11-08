# Autorité unique pour la configuration du bucket applicatif déjà existant.
# Aucun bucket, ACL ni règle de nettoyage globale n'est créé ou modifié ici.
variable "existing_app_bucket" {
  description = "Bucket applicatif existant à configurer ; null laisse le stockage inchangé."
  type        = string
  default     = null

  validation {
    condition     = var.existing_app_bucket == null || can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.existing_app_bucket))
    error_message = "Le nom doit désigner un bucket S3 existant."
  }
}

variable "existing_app_bucket_cors_rules" {
  description = "Copie exacte des règles CORS présentes avant gestion partagée (dont voxel), hors règle Tabula gérée ci-dessous."
  type = list(object({
    id              = optional(string)
    allowed_origins = list(string)
    allowed_methods = list(string)
    allowed_headers = optional(list(string))
    expose_headers  = optional(list(string))
    max_age_seconds = optional(number)
  }))
  default = []

  validation {
    condition = alltrue([
      for rule in var.existing_app_bucket_cors_rules :
      length(rule.allowed_origins) > 0 && length(rule.allowed_methods) > 0 &&
      alltrue([for method in rule.allowed_methods : contains(["GET", "HEAD", "PUT", "POST", "DELETE"], method)]) &&
      rule.id != "tabula-native"
    ])
    error_message = "Préserver les règles existantes complètes ; l'identifiant tabula-native est réservé."
  }
}

variable "additional_app_bucket_origins" {
  description = "Origines HTTPS Tabula ajoutées aux règles conservées, pour GET/HEAD/POST uniquement."
  type        = set(string)
  default     = []

  validation {
    condition = alltrue([
      for origin in var.additional_app_bucket_origins :
      can(regex("^https://[a-zA-Z0-9][a-zA-Z0-9.-]*(:[0-9]{1,5})?$", origin))
    ])
    error_message = "Chaque origine Tabula doit être une origine HTTPS exacte, sans chemin ni joker."
  }
}

locals {
  shared_app_cors_rules = concat(var.existing_app_bucket_cors_rules, length(var.additional_app_bucket_origins) == 0 ? [] : [{
    id              = "tabula-native"
    allowed_origins = sort(tolist(var.additional_app_bucket_origins))
    allowed_methods = ["GET", "HEAD", "POST"]
    allowed_headers = ["*"]
    expose_headers  = ["ETag"]
    max_age_seconds = 600
  }])
}

resource "minio_s3_bucket_cors" "shared_application" {
  count    = var.existing_app_bucket == null ? 0 : 1
  provider = minio.app_storage
  bucket   = var.existing_app_bucket

  dynamic "cors_rule" {
    for_each = local.shared_app_cors_rules
    content {
      id              = cors_rule.value.id
      allowed_origins = cors_rule.value.allowed_origins
      allowed_methods = cors_rule.value.allowed_methods
      allowed_headers = cors_rule.value.allowed_headers
      expose_headers  = cors_rule.value.expose_headers
      max_age_seconds = cors_rule.value.max_age_seconds
    }
  }

  lifecycle {
    prevent_destroy = true
    precondition {
      condition     = length(var.existing_app_bucket_cors_rules) > 0
      error_message = "Renseigner les règles CORS réellement lues avant de gérer ce bucket partagé."
    }
  }
}

# La lecture préalable a confirmé l'absence de lifecycle sur le bucket existant.
# Ne pas ajouter d'expiration ni d'abandon multipart sans filtre sur le bucket partagé.
resource "minio_s3_bucket_lifecycle" "shared_application" {
  count    = var.existing_app_bucket == null ? 0 : 1
  provider = minio.app_storage
  bucket   = var.existing_app_bucket

  rule {
    id     = "tabula-staging-one-day"
    status = "Enabled"
    filter {
      prefix = "tabula/staging/"
    }
    expiration {
      days = 1
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

output "shared_app_storage" {
  description = "Configuration applicative partagée consommée par les piles applicatives, sans secret."
  value = var.existing_app_bucket == null ? null : {
    bucket                = var.existing_app_bucket
    cors_origins          = distinct(flatten([for rule in local.shared_app_cors_rules : rule.allowed_origins]))
    temporary_file_prefix = "tabula/staging/"
  }
  depends_on = [minio_s3_bucket_cors.shared_application, minio_s3_bucket_lifecycle.shared_application]
}
