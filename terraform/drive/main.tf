# Universal Drive (mode instance), entièrement par Terraform. Valeurs propres au serveur : terraform.tfvars.
#   1. bucket privé, règle CORS, nettoyage des envois abandonnés (projet Hetzner du Drive) ;
#   2. client « drive » du realm Keycloak, configuré sur le serveur (configure.mjs) ;
#   3. .env du Drive déposé sur le serveur ;
#   4. entrées DNS du Drive (Cloudflare) ;
#   5. code de GitHub au commit résolu, construit et lancé par docker compose.

locals {
  deploy_dir  = "/home/${var.admin_user}/.ud-deploy"
  server_ipv4 = data.terraform_remote_state.infra.outputs.serveur.ipv4
  server_ipv6 = data.terraform_remote_state.infra.outputs.serveur.ipv6
  oidc_issuer = "${var.keycloak_url}/realms/${var.keycloak_realm}"
  kc_host     = regex("^https?://([^/:]+)", var.keycloak_url)[0]
  app_commit  = jsondecode(data.http.app_commit.response_body).sha

  public_host   = "${var.dns_name}.${var.dns_zone}"
  public_origin = "https://${local.public_host}"
  oidc_label    = var.oidc_label != "" ? var.oidc_label : local.kc_host
  # Conditions réelles hors du dépôt (terms_file), sinon le modèle à compléter.
  terms_path = var.terms_file != "" ? var.terms_file : "${path.module}/app/terms.md"
  compose    = templatefile("${path.module}/app/compose.yaml.tftpl", { host = local.public_host })
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

# ---------- 1. Stockage ----------

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
    allowed_origins = [local.public_origin]
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

# ---------- 2. Keycloak ----------

# Le mot de passe d'admin de Keycloak reste sur le serveur : run.sh le lit dans /opt/stack/.env.
resource "terraform_data" "keycloak" {
  triggers_replace = [
    filesha256("${path.module}/keycloak/configure.mjs"),
    filesha256("${path.module}/keycloak/run.sh"),
    var.keycloak_url, var.keycloak_realm, var.oidc_client_id, local.public_origin,
  ]

  connection {
    type        = "ssh"
    host        = local.server_ipv4
    user        = var.admin_user
    private_key = file(pathexpand(var.ssh_private_key_path))
  }

  provisioner "remote-exec" {
    inline = ["install -d -m 700 ${local.deploy_dir}/keycloak"]
  }

  provisioner "file" {
    source      = "${path.module}/keycloak/configure.mjs"
    destination = "${local.deploy_dir}/keycloak/configure.mjs"
  }

  provisioner "file" {
    source      = "${path.module}/keycloak/run.sh"
    destination = "${local.deploy_dir}/keycloak/run.sh"
  }

  provisioner "remote-exec" {
    inline = [join(" ", [
      "sudo -n sh ${local.deploy_dir}/keycloak/run.sh ${local.deploy_dir}/keycloak",
      var.keycloak_url, local.kc_host, local.server_ipv4, var.keycloak_realm, local.public_origin, var.oidc_client_id,
    ])]
  }
}

# ---------- 3. .env du Drive sur le serveur ----------

# Clé maîtresse du Drive (sessions, délégations) : générée une fois, gardée dans l'état distant
# (bucket privé et versionné), qui en est donc la sauvegarde. La perdre = tout le monde se reconnecte.
resource "random_bytes" "ud_secret_key" {
  length = 32

  lifecycle {
    prevent_destroy = true
  }
}

locals {
  # Le secret du client OIDC n'y est pas : install-env.sh l'ajoute sur le serveur.
  server_env = <<-EOT
    # Écrit par Terraform (Mon_serveur/terraform/drive) : ne pas modifier à la main,
    # relancer « python setup.py apply drive ».
    UD_MODE=instance
    UD_CONFIG=/config/drive.config.yaml
    UD_TERMS_FILE=/config/terms.md
    UD_PUBLIC_URL=${local.public_origin}
    UD_TRUST_PROXY=1
    UD_INSTANCE_NAME=${var.instance_name}
    UD_SECRET_KEY=${random_bytes.ud_secret_key.base64}
    UD_OIDC_ISSUER=${local.oidc_issuer}
    UD_OIDC_CLIENT_ID=${var.oidc_client_id}
    UD_OIDC_LABEL=${local.oidc_label}
    UD_S3_ENDPOINT=https://${var.location}.your-objectstorage.com
    UD_S3_REGION=${var.location}
    UD_S3_ADDRESSING=virtual
    UD_S3_BUCKET=${minio_s3_bucket.drive.bucket}
    UD_S3_ACCESS_KEY_ID=${var.drive_s3_access_key}
    UD_S3_SECRET_ACCESS_KEY=${var.drive_s3_secret_key}
    UD_ALLOWED_ENDPOINTS=*.your-objectstorage.com
    UD_ABUSE_EMAIL=${var.abuse_email}
  EOT
}

# Redéposé dès que le contenu change (seule son empreinte est gardée dans l'état).
resource "terraform_data" "server_env" {
  triggers_replace = sha256(local.server_env)
  depends_on       = [terraform_data.keycloak]

  connection {
    type        = "ssh"
    host        = local.server_ipv4
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

# ---------- 4. DNS ----------

data "cloudflare_zone" "dns" {
  filter = {
    name = var.dns_zone
  }
}

resource "cloudflare_dns_record" "drive_a" {
  zone_id = data.cloudflare_zone.dns.zone_id
  name    = "${var.dns_name}.${var.dns_zone}"
  type    = "A"
  content = local.server_ipv4
  ttl     = 1
  proxied = false
  comment = "Universal Drive (Terraform, Mon_serveur/terraform/drive)"
}

resource "cloudflare_dns_record" "drive_aaaa" {
  zone_id = data.cloudflare_zone.dns.zone_id
  name    = "${var.dns_name}.${var.dns_zone}"
  type    = "AAAA"
  content = local.server_ipv6
  ttl     = 1
  proxied = false
  comment = "Universal Drive (Terraform, Mon_serveur/terraform/drive)"
}

# ---------- 5. Application ----------

# Commit exact à déployer : la pointe de var.app_ref sur GitHub au moment du plan.
data "http" "app_commit" {
  url             = "https://api.github.com/repos/${var.app_repo}/commits/${var.app_ref}"
  request_headers = { Accept = "application/vnd.github+json" }

  lifecycle {
    postcondition {
      condition     = self.status_code == 200
      error_message = "Commit « ${var.app_ref} » introuvable sur github.com/${var.app_repo}."
    }
  }
}

# Redéployé à chaque nouveau commit, nouvelle configuration ou nouveau .env.
resource "terraform_data" "app" {
  triggers_replace = [
    local.app_commit,
    sha256(local.compose),
    filesha256("${path.module}/app/drive.config.yaml"),
    filesha256(local.terms_path),
    filesha256("${path.module}/app/deploy-app.sh"),
    terraform_data.server_env.id,
  ]
  depends_on = [
    terraform_data.server_env,
    minio_s3_bucket_cors.drive,
    cloudflare_dns_record.drive_a,
    cloudflare_dns_record.drive_aaaa,
  ]

  connection {
    type        = "ssh"
    host        = local.server_ipv4
    user        = var.admin_user
    private_key = file(pathexpand(var.ssh_private_key_path))
  }

  provisioner "remote-exec" {
    inline = ["install -d -m 700 ${local.deploy_dir}/app"]
  }

  provisioner "file" {
    content     = local.compose
    destination = "${local.deploy_dir}/app/compose.yaml"
  }

  provisioner "file" {
    source      = "${path.module}/app/drive.config.yaml"
    destination = "${local.deploy_dir}/app/drive.config.yaml"
  }

  provisioner "file" {
    source      = local.terms_path
    destination = "${local.deploy_dir}/app/terms.md"
  }

  provisioner "file" {
    source      = "${path.module}/app/deploy-app.sh"
    destination = "${local.deploy_dir}/app/deploy-app.sh"
  }

  provisioner "remote-exec" {
    inline = ["sudo -n sh ${local.deploy_dir}/app/deploy-app.sh ${local.deploy_dir}/app ${var.app_repo} ${local.app_commit}"]
  }
}
