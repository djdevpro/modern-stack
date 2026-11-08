variable "hcloud_token" {
  description = "Jeton API Hetzner (Projet > Security > API tokens, droits Read & Write). Passe-le via TF_VAR_hcloud_token."
  type        = string
  sensitive   = true
}

variable "location" {
  description = "Datacenter Hetzner : nbg1 (Nuremberg), hel1 (Helsinki) ou fsn1 (Falkenstein)."
  type        = string
  default     = "nbg1"
}

variable "image" {
  description = "Image OS, lue seulement à la création."
  type        = string
  default     = "ubuntu-26.04"
}

variable "ssh_public_key" {
  description = "Ta clé publique SSH (contenu de ~/.ssh/id_ed25519.pub)."
  type        = string
}

variable "admin_user" {
  description = "Utilisateur admin (sudo) créé sur le serveur. root est désactivé en SSH."
  type        = string
  default     = "admin"
}

# Compatibility with existing private tfvars; intentionally grants no access.
# tflint-ignore: terraform_unused_declarations
variable "admin_ips" {
  description = "Deprecated compatibility input; public SSH and Dokploy are always blocked."
  type        = list(string)
  default     = []
}

variable "netbird_setup_key" {
  description = "Clé d'installation NetBird Cloud (setup key, usage unique ou réutilisable, expiration courte), lue seulement à la création. Vide = NetBird installé mais non connecté."
  type        = string
  default     = ""
  sensitive   = true
}

variable "claude_code_oauth_token" {
  description = "Jeton `claude setup-token` (abonnement Claude, valable 1 an) pour Claude Code de l'admin sur le serveur, lu seulement à la création. Vide = Claude Code installé mais non connecté (lancer `claude` puis /login)."
  type        = string
  default     = ""
  sensitive   = true
}

variable "netbird_ssh" {
  description = "Active le serveur SSH de NetBird à la création (à autoriser aussi dans le tableau de bord). Inutile pour un SSH classique via l'IP NetBird."
  type        = bool
  default     = false
}

variable "server_name" {
  description = "Nom du serveur chez Hetzner et dans NetBird (ex: serveur -> serveur.netbird.cloud)."
  type        = string
  default     = "serveur"
}

variable "server_type" {
  description = "Type Hetzner. cpx22 (2 vCPU AMD, 4 Go, 80 Go NVMe) avec KEYCLOAK_MEM_LIMIT=1g dans la stack ; cpx32 (8 Go) pour plus de marge."
  type        = string
  default     = "cpx22"
}

variable "backups" {
  description = "Backups automatiques Hetzner (+20 % du prix du serveur)."
  type        = bool
  default     = true
}

variable "protected" {
  description = "Protection contre la suppression et la réinstallation (même par terraform destroy)."
  type        = bool
  default     = true
}

variable "install_dokploy" {
  description = "Installe Dokploy (et Docker) à la création."
  type        = bool
  default     = true
}

variable "mail_fqdn" {
  description = "Nom d'hôte complet du serveur mail (ex: mail.mondomaine.fr). Devient le hostname et le reverse DNS (PTR), indispensable pour la délivrabilité."
  type        = string

  validation {
    condition     = can(regex("^([a-z0-9-]+[.])+[a-z]{2,}$", var.mail_fqdn))
    error_message = "mail_fqdn doit être un nom complet en minuscules, ex: mail.mondomaine.fr."
  }
}
