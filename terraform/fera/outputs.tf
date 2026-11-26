output "application" {
  description = "Adresse de l'interface de FERA."
  value       = "https://${var.host}"
}

output "identifiant" {
  description = "Identifiant de connexion à l'interface."
  value       = local.admin_email
}

output "mot_de_passe" {
  description = "Où lire le mot de passe de l'interface, créé sur le serveur."
  value       = "sudo grep FERA_ADMIN_PASSWORD /opt/fera/secrets.env"
}

output "sdk" {
  description = "Script à charger sur les sites suivis. La capture répond sous le même nom (/e, /r, /d)."
  value       = "https://${var.host}/sdk/fera.js"
}

output "stockage" {
  description = "Emplacement des replays et des source maps dans le bucket partagé."
  value       = "s3://${var.existing_bucket_name}/${var.s3_prefix}"
}

output "image" {
  description = "Image déployée."
  value       = var.image
}
