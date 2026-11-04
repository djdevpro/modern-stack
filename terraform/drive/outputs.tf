output "bucket" {
  description = "Nom du bucket du Drive."
  value       = minio_s3_bucket.drive.bucket
}

output "server_env" {
  description = "Fichier d'environnement du Drive déposé par Terraform sur le serveur."
  value       = "/opt/universal-drive/.env (root, 0600)"
}
