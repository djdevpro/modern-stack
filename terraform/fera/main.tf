# FERA (flags, évènements, replays, alertes) derrière le Traefik du serveur.
# Terraform dépose le fichier compose, la configuration basse mémoire de ClickHouse et les clés S3, puis lance la pile.
# Les secrets propres à FERA (mot de passe, clés internes) sont créés sur le serveur au premier déploiement,
# jamais dans l'état Terraform. Valeurs propres au serveur dans terraform.tfvars.
# Une nouvelle version se déploie en changeant l'empreinte de l'image, puis "python setup.py apply fera".

locals {
  deploy_dir  = "/home/${var.admin_user}/.fera-deploy"
  admin_email = var.admin_email != null ? var.admin_email : "admin@${var.host}"
  compose = templatefile("${path.module}/app/compose.yaml.tftpl", {
    host          = var.host
    image         = var.image
    cert_resolver = var.cert_resolver
    admin_email   = local.admin_email
    s3_endpoint   = "https://${var.s3_location}.your-objectstorage.com"
    s3_region     = var.s3_location
    s3_bucket     = var.existing_bucket_name
    s3_prefix     = var.s3_prefix
    rate_limit    = var.rate_limit_per_second
    site_project  = var.site_project
  })
  # Valeurs littérales pour docker compose (guillemets simples), fusionnées dans /opt/fera/.env par deploy-app.sh.
  s3_env = join("", [
    "S3_ACCESS_KEY='${replace(var.drive_s3_access_key, "'", "")}'\n",
    "S3_SECRET_KEY='${replace(var.drive_s3_secret_key, "'", "")}'\n",
  ])
}

# Redéployé à chaque nouvelle image, nouvelle adresse, nouvelle clé ou nouveau script.
resource "terraform_data" "app" {
  triggers_replace = [
    sha256(local.compose),
    sha256(local.s3_env),
    filesha256("${path.module}/app/deploy-app.sh"),
    filesha256("${path.module}/app/clickhouse.xml"),
  ]

  connection {
    type        = "ssh"
    host        = var.ssh_host
    host_key    = var.ssh_host_key
    user        = var.admin_user
    private_key = file(pathexpand(var.ssh_private_key_path))
  }

  provisioner "remote-exec" {
    inline = ["install -d -m 700 ${local.deploy_dir}"]
  }

  provisioner "file" {
    content     = local.compose
    destination = "${local.deploy_dir}/compose.yaml"
  }

  provisioner "file" {
    content     = local.s3_env
    destination = "${local.deploy_dir}/s3.env"
  }

  provisioner "file" {
    source      = "${path.module}/app/clickhouse.xml"
    destination = "${local.deploy_dir}/clickhouse.xml"
  }

  provisioner "file" {
    source      = "${path.module}/app/deploy-app.sh"
    destination = "${local.deploy_dir}/deploy-app.sh"
  }

  provisioner "remote-exec" {
    inline = ["chmod 600 ${local.deploy_dir}/s3.env", "sudo -n sh ${local.deploy_dir}/deploy-app.sh ${local.deploy_dir}"]
  }
}
