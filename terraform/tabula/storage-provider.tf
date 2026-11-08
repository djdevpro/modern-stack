variable "existing_bucket_name" {
  description = "Existing private application bucket. Tabula only writes beneath tabula/ and never creates a bucket."
  type        = string
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", var.existing_bucket_name))
    error_message = "Use the name of the existing private application bucket."
  }
}

variable "drive_s3_access_key" {
  description = "Existing DRIVE_S3_ACCESS_KEY_ID supplied by the server secret store."
  type        = string
  sensitive   = true
}

variable "drive_s3_secret_key" {
  description = "Existing DRIVE_S3_SECRET_ACCESS_KEY supplied by the server secret store."
  type        = string
  sensitive   = true
}

variable "s3_location" {
  description = "Hetzner Object Storage region of the existing application bucket."
  type        = string
  default     = "nbg1"
  validation {
    condition     = contains(["nbg1", "fsn1", "hel1"], var.s3_location)
    error_message = "Use a supported Object Storage region: nbg1, fsn1 or hel1."
  }
}
