# Only the existing infrastructure stack owns this bucket-wide policy.
# No bucket creation, no application credential gets Terraform permissions.
variable "state_s3_access_key" {
  type      = string
  sensitive = true
}

variable "state_s3_secret_key" {
  type      = string
  sensitive = true
}

variable "drive_s3_access_key" {
  type      = string
  sensitive = true
}

variable "drive_s3_secret_key" {
  type      = string
  sensitive = true
}

variable "object_storage_project_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]+$", var.object_storage_project_id))
    error_message = "Object storage project ID must be numeric."
  }
}

variable "state_bucket_previous_policy" {
  description = "Preserved existing statements, reviewed before first adoption; current bucket returned NoSuchBucketPolicy."
  type        = string
  sensitive   = true
  default     = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
  validation {
    condition     = can(jsondecode(var.state_bucket_previous_policy).Statement)
    error_message = "Existing policy must contain a Statement array."
  }
}

provider "minio" {
  alias               = "state_storage"
  minio_server        = "nbg1.your-objectstorage.com"
  minio_region        = "nbg1"
  minio_ssl           = true
  minio_user          = var.state_s3_access_key
  minio_password      = var.state_s3_secret_key
  skip_bucket_tagging = true
}

provider "minio" {
  alias               = "app_storage"
  minio_server        = "nbg1.your-objectstorage.com"
  minio_region        = "nbg1"
  minio_ssl           = true
  minio_user          = var.drive_s3_access_key
  minio_password      = var.drive_s3_secret_key
  skip_bucket_tagging = true
}

locals {
  state_bucket = "tfstate-serveur-0c3d4523"
  application_state_deny = {
    Sid    = "DenyApplicationKeyOnTerraformState"
    Effect = "Deny"
    Action = "s3:*"
    Principal = {
      AWS = "arn:aws:iam:::user/p${var.object_storage_project_id}:${var.drive_s3_access_key}"
    }
    Resource = ["arn:aws:s3:::${local.state_bucket}", "arn:aws:s3:::${local.state_bucket}/*"]
  }
}

resource "minio_s3_bucket_policy" "state_application_isolation" {
  provider = minio.state_storage
  bucket   = local.state_bucket
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      for statement in jsondecode(var.state_bucket_previous_policy).Statement : statement
      if try(statement.Sid, "") != local.application_state_deny.Sid
    ], [local.application_state_deny])
  })

  lifecycle {
    prevent_destroy = true
    precondition {
      condition     = length(var.drive_s3_access_key) > 0 && var.drive_s3_access_key != var.state_s3_access_key
      error_message = "Application and Terraform access keys must be present and distinct."
    }
  }
}
