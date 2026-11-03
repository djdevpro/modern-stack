locals {
  any = ["0.0.0.0/0", "::/0"]

  # Domaine principal, déduit de mail_fqdn (mail.djidjelli.fr -> djidjelli.fr)
  domaine = join(".", slice(split(".", var.mail_fqdn), 1, length(split(".", var.mail_fqdn))))

  # Règles entrantes. Tout ce qui n'est pas listé est bloqué.
  # (Le pare-feu Hetzner est externe : Docker ne peut pas le contourner, contrairement à ufw.)
  # Les ports du mail correspondent à ce que publie stack/compose.yaml.
  firewall_rules = concat(
    length(var.admin_ips) > 0 ? [
      { description = "SSH admin", protocol = "tcp", port = "22", source_ips = var.admin_ips },
      # Panneau Dokploy : uniquement depuis tes IP (sinon passe par NetBird)
      { description = "Dokploy UI", protocol = "tcp", port = "3000", source_ips = var.admin_ips },
    ] : [],
    [
      # NetBird (WireGuard) en connexion directe (sinon il passe par ses relais, ça marche quand même)
      { description = "NetBird", protocol = "udp", port = "51820", source_ips = local.any },

      # Applis (Traefik de Dokploy, certificats Let's Encrypt)
      { description = "HTTP", protocol = "tcp", port = "80", source_ips = local.any },
      { description = "HTTPS", protocol = "tcp", port = "443", source_ips = local.any },
      { description = "HTTP/3", protocol = "udp", port = "443", source_ips = local.any },

      # Mail (Stalwart)
      { description = "SMTP", protocol = "tcp", port = "25", source_ips = local.any },
      { description = "SMTPS", protocol = "tcp", port = "465", source_ips = local.any },
      { description = "Submission", protocol = "tcp", port = "587", source_ips = local.any },
      { description = "IMAPS", protocol = "tcp", port = "993", source_ips = local.any },
    ]
  )
}

resource "hcloud_ssh_key" "admin" {
  name       = "${var.admin_user}-key"
  public_key = var.ssh_public_key
}

resource "hcloud_firewall" "this" {
  name = "fw-${var.server_name}"

  dynamic "rule" {
    for_each = local.firewall_rules
    content {
      direction   = "in"
      description = rule.value.description
      protocol    = rule.value.protocol
      port        = rule.value.port
      source_ips  = rule.value.source_ips
    }
  }

  labels = { managed-by = "terraform" }
}

# Un seul serveur pour tout : applis (Dokploy) ; mail, supervision et identité (stack/compose.yaml).
resource "hcloud_server" "this" {
  name        = var.server_name
  server_type = var.server_type
  image       = var.image
  location    = var.location
  backups     = var.backups

  ssh_keys     = [hcloud_ssh_key.admin.id]
  firewall_ids = [hcloud_firewall.this.id]

  # Empêche la suppression / réinstallation accidentelle (même par `terraform destroy`).
  delete_protection  = var.protected
  rebuild_protection = var.protected

  public_net {
    ipv4_enabled = true
    ipv6_enabled = true
  }

  user_data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    name              = var.server_name
    fqdn              = var.mail_fqdn
    admin_user        = var.admin_user
    ssh_public_key    = trimspace(var.ssh_public_key)
    netbird_setup_key = var.netbird_setup_key
    netbird_ssh       = var.netbird_ssh
    cf_dns            = trimspace(file("${path.module}/../stack/scripts/cf-dns"))
    appli             = trimspace(file("${path.module}/../stack/scripts/appli"))
    sw                = trimspace(file("${path.module}/../stack/scripts/sw"))
    cf_zone           = local.domaine
    install_dokploy   = var.install_dokploy

    claude_code_oauth_token = var.claude_code_oauth_token
  })

  labels = { managed-by = "terraform" }

  lifecycle {
    # cloud-init ne s'exécute qu'à la création : modifier le template ne doit pas
    # détruire et recréer un serveur en production.
    ignore_changes = [user_data, image, ssh_keys]
  }
}

# Reverse DNS (PTR) = nom du serveur mail : indispensable pour ne pas finir en spam.
resource "hcloud_rdns" "v4" {
  server_id  = hcloud_server.this.id
  ip_address = hcloud_server.this.ipv4_address
  dns_ptr    = var.mail_fqdn
}

resource "hcloud_rdns" "v6" {
  server_id  = hcloud_server.this.id
  ip_address = hcloud_server.this.ipv6_address
  dns_ptr    = var.mail_fqdn
}
