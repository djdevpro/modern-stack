terraform {
  required_version = ">= 1.6"

  required_providers {
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
      version = "5.28.1"
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

provider "cloudflare" {
  api_token = var.cloudflare_api_token
}
