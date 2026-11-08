variable "ssh_host" {
  description = "SSH hostname or address of the existing server."
  type        = string
}

variable "ssh_host_key" {
  description = "Trusted SSH host public key, obtained over an already verified connection."
  type        = string
}

variable "admin_user" {
  description = "SSH user with passwordless sudo."
  type        = string
  validation {
    condition     = can(regex("^[a-z_][a-z0-9_-]*$", var.admin_user))
    error_message = "Use a valid Linux account name."
  }
}

variable "ssh_private_key_path" {
  description = "Local SSH private key path."
  type        = string
  default     = "~/.ssh/id_ed25519"
}

variable "configuration" {
  description = "Public Tabula identity, application and SMTP settings. Secrets stay on the server."
  type = object({
    keycloak_url = string
    frontend_url = string
    backend_url  = string
    admin_email  = string
    smtp_host    = string
    smtp_port    = optional(number, 465)
    smtp_user    = string
    smtp_from    = string
    smtp_name    = optional(string, "Tabula")
  })
  validation {
    condition = alltrue([
      for url in [var.configuration.keycloak_url, var.configuration.frontend_url, var.configuration.backend_url] :
      can(regex("^https://[a-zA-Z0-9.-]+$", url))
    ])
    error_message = "Public URLs must be HTTPS origins without paths or trailing slashes."
  }
  validation {
    condition     = contains([465, 587], var.configuration.smtp_port)
    error_message = "SMTP requires TLS: port 465 or STARTTLS port 587."
  }
}
