terraform {
  required_version = ">= 1.6"

  required_providers {
    minio = {
      source  = "aminueza/minio"
      version = "3.45.1"
    }
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = ">= 1.57, < 2.0"
    }
  }

  # État distant : Hetzner Object Storage (bucket privé et versionné).
  # Identifiants : passés par setup.py, hors du dépôt.
  backend "s3" {
    bucket                      = "tfstate-serveur-0c3d4523"
    key                         = "infra/terraform.tfstate"
    region                      = "nbg1"
    endpoints                   = { s3 = "https://nbg1.your-objectstorage.com" }
    skip_credentials_validation = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
    skip_metadata_api_check     = true
    skip_s3_checksum            = true
    use_path_style              = true
  }
}

provider "hcloud" {
  token = var.hcloud_token
}
