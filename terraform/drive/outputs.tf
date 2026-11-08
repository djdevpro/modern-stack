output "bucket" {
  description = "Bucket existant partagé, configuré par la pile infra."
  value       = var.bucket_name
  depends_on  = [terraform_data.shared_storage]
}

output "server_env" {
  description = "Fichier d'environnement du Drive déposé par Terraform sur le serveur."
  value       = "/opt/universal-drive/.env (root, 0600)"
}
