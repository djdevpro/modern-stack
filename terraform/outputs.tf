output "serveur" {
  description = "IP publiques et commande SSH du serveur."
  value = {
    ipv4 = hcloud_server.this.ipv4_address
    ipv6 = hcloud_server.this.ipv6_address
    ssh  = "ssh ${var.admin_user}@${var.server_name}.netbird.cloud   # via NetBird (ou @${hcloud_server.this.ipv4_address} si ton IP est dans admin_ips)"
  }
}

output "dns_records_a_creer" {
  description = "Enregistrements DNS à créer chez Cloudflare (SPF, DKIM et DMARC : voir dns/dns-djidjelli.fr.md)."
  value = {
    (var.mail_fqdn)                 = "A ${hcloud_server.this.ipv4_address} / AAAA ${hcloud_server.this.ipv6_address}"
    "ton domaine (MX)"              = "MX 10 ${var.mail_fqdn}"
    "iam.${local.domaine}"          = "A ${hcloud_server.this.ipv4_address} / AAAA ${hcloud_server.this.ipv6_address}   (Keycloak : appli publique iam sur le serveur)"
    "chaque appli (sur le serveur)" = "appli publique <nom>  |  appli netbird <nom> <port>"
  }
}
