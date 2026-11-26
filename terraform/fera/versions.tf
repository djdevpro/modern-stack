terraform {
  required_version = ">= 1.6"

  # Même état distant que l'infra, sous une clé à part : le plan du serveur ne voit jamais cette appli.
  # Identifiants de l'état : TFSTATE_* du magasin de setup.py.
  backend "s3" {
    bucket                      = "tfstate-serveur-0c3d4523"
    key                         = "fera/terraform.tfstate"
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
