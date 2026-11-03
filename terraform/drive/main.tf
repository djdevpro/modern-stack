# Stockage du Drive (drive.djidjelli.fr, mode instance) : un bucket privé, sa règle CORS, le nettoyage
# des envois abandonnés, et le .env du Drive déposé sur le serveur. Les fichiers des personnes vivent
# sous users/<id>/ et commun/.

resource "minio_s3_bucket" "drive" {
  bucket         = var.bucket_name
  acl            = "private"
  object_locking = false
  force_destroy  = false

  # Le bucket contient les fichiers des utilisateurs : jamais de destruction par Terraform.
  lifecycle {
    prevent_destroy = true
  }
}

# Envois et aperçus vont en direct du navigateur au bucket par URL présignées : seule l'origine du
# Drive est autorisée ; ETag (multipart) et Retry-After (reprises) sont lisibles par le front.
resource "minio_s3_bucket_cors" "drive" {
  bucket = minio_s3_bucket.drive.id

  cors_rule {
    id              = "universal-drive"
    allowed_origins = [var.public_origin]
    allowed_methods = ["GET", "PUT", "HEAD"]
    allowed_headers = ["*"]
    expose_headers  = ["ETag", "Retry-After"]
    max_age_seconds = 600
  }
}

resource "minio_s3_bucket_lifecycle" "drive" {
  bucket = minio_s3_bucket.drive.id

  rule {
    id = "envois-incomplets"

    abort_incomplete_multipart_upload {
      days_after_initiation = var.incomplete_upload_days
    }
  }
}

# ---------- .env du Drive sur le serveur ----------

# Clé maîtresse du Drive (sessions, délégations) : générée une fois, gardée dans l'état distant
# (bucket privé et versionné), qui en est donc la sauvegarde. La perdre = tout le monde se reconnecte.
resource "random_bytes" "ud_secret_key" {
  length = 32

  lifecycle {
    prevent_destroy = true
  }
}

data "terraform_remote_state" "infra" {
  backend = "s3"
  config = {
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

locals {
  deploy_dir = "/home/${var.admin_user}/.ud-deploy"

  # Le secret du client OIDC n'y est pas : install-env.sh l'ajoute sur le serveur.
  server_env = <<-EOT
    # Écrit par Terraform (Mon_serveur/terraform/drive) : ne pas modifier à la main,
    # relancer « python setup.py apply drive ».
    UD_MODE=instance
    UD_CONFIG=/config/drive.config.yaml
    UD_TERMS_FILE=/config/terms.md
    UD_PUBLIC_URL=${var.public_origin}
    UD_TRUST_PROXY=1
    UD_INSTANCE_NAME=${var.instance_name}
    UD_SECRET_KEY=${random_bytes.ud_secret_key.base64}
    UD_OIDC_ISSUER=${var.oidc_issuer}
    UD_OIDC_CLIENT_ID=${var.oidc_client_id}
    UD_S3_ENDPOINT=https://${var.location}.your-objectstorage.com
    UD_S3_REGION=${var.location}
    UD_S3_ADDRESSING=virtual
    UD_S3_BUCKET=${minio_s3_bucket.drive.bucket}
    UD_S3_ACCESS_KEY_ID=${var.drive_s3_access_key}
    UD_S3_SECRET_ACCESS_KEY=${var.drive_s3_secret_key}
    UD_ABUSE_EMAIL=${var.abuse_email}
  EOT
}

# Redéposé dès que le contenu change (seule son empreinte est gardée dans l'état).
resource "terraform_data" "server_env" {
  triggers_replace = sha256(local.server_env)

  connection {
    type        = "ssh"
    host        = data.terraform_remote_state.infra.outputs.serveur.ipv4
    user        = var.admin_user
    private_key = file(pathexpand(var.ssh_private_key_path))
  }

  # Dossier lisible par l'admin seul : le .env n'y reste que le temps de l'installer.
  provisioner "remote-exec" {
    inline = ["install -d -m 700 ${local.deploy_dir}"]
  }

  provisioner "file" {
    content     = local.server_env
    destination = "${local.deploy_dir}/drive.env"
  }

  provisioner "file" {
    source      = "${path.module}/install-env.sh"
    destination = "${local.deploy_dir}/install-env.sh"
  }

  provisioner "remote-exec" {
    inline = ["sudo -n sh ${local.deploy_dir}/install-env.sh ${local.deploy_dir}/drive.env"]
  }
}
