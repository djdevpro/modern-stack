terraform {
  required_version = ">= 1.6"

  required_providers {
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = ">= 1.57, < 2.0"
    }
  }

  # État distant : Hetzner Object Storage (bucket privé et versionné, créé le 2026-11-02).
  # Identifiants : AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY dans .env.
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
