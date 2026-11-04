terraform {
  required_version = ">= 1.6"

  required_providers {
    # Le fournisseur hcloud ne gère pas les buckets Object Storage : on passe par l'API S3.
    minio = {
      source  = "aminueza/minio"
      version = "3.45.1"
    }
    random = {
      source  = "hashicorp/random"
      version = "3.9.1"
    }
    # Résout le dernier commit du dépôt du Drive sur GitHub (déploiement à un commit exact).
    http = {
      source  = "hashicorp/http"
      version = "3.6.3"
    }
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "5.28.0"
    }
  }

  # Même état distant que l'infra (bucket du projet du serveur), sous une clé à part :
  # le plan du serveur (../) ne voit jamais le Drive, et inversement.
  # Identifiants de l'état : TFSTATE_* du magasin de setup.py (pas les clés du Drive).
  backend "s3" {
    bucket                      = "tfstate-serveur-0c3d4523"
    key                         = "drive/terraform.tfstate"
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

# Clés S3 du projet Hetzner dédié au Drive : elles n'ouvrent que les buckets de ce projet.
provider "minio" {
  minio_server        = "${var.location}.your-objectstorage.com"
  minio_region        = var.location
  minio_ssl           = true
  minio_user          = var.drive_s3_access_key
  minio_password      = var.drive_s3_secret_key
  skip_bucket_tagging = true
}

provider "cloudflare" {
  api_token = var.cloudflare_api_token
}
