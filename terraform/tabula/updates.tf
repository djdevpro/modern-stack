# GitHub promotes only tested releases after notification; systemd consumes latest.
# No release is promoted or deployed by this resource itself.
variable "update_policy" {
  description = "Automatic releases: GitHub repository, notice period and server polling."
  type = object({
    repository             = string
    grace_hours            = optional(number, 4)
    check_interval_minutes = optional(number, 15)
    enabled                = optional(bool, true)
  })
  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.update_policy.repository))
    error_message = "Use an owner/repository GitHub name."
  }
  validation {
    condition     = var.update_policy.grace_hours >= 4 && var.update_policy.grace_hours <= 168 && floor(var.update_policy.grace_hours) == var.update_policy.grace_hours
    error_message = "The grace period must be an integer between 4 and 168 hours."
  }
  validation {
    condition     = var.update_policy.check_interval_minutes >= 5 && var.update_policy.check_interval_minutes <= 60 && floor(var.update_policy.check_interval_minutes) == var.update_policy.check_interval_minutes
    error_message = "The polling interval must be an integer between 5 and 60 minutes."
  }
}

resource "terraform_data" "tabula_updates" {
  triggers_replace = [timestamp()]
  depends_on       = [terraform_data.tabula]

  connection {
    type        = "ssh"
    host        = var.ssh_host
    host_key    = var.ssh_host_key
    user        = var.admin_user
    private_key = file(pathexpand(var.ssh_private_key_path))
  }

  provisioner "local-exec" {
    interpreter = ["python"]
    command     = "${path.module}/configure-release.py"
    environment = {
      TABULA_UPDATE_REPOSITORY  = var.update_policy.repository
      TABULA_UPDATE_GRACE_HOURS = tostring(var.update_policy.grace_hours)
    }
  }
  provisioner "file" {
    content     = jsonencode(var.update_policy)
    destination = "/home/${var.admin_user}/.tabula-terraform/updates.json"
  }
  provisioner "file" {
    source      = "${path.module}/configure-updates.py"
    destination = "/home/${var.admin_user}/.tabula-terraform/configure-updates.py"
  }
  provisioner "remote-exec" {
    inline = ["sudo -n python3 /home/${var.admin_user}/.tabula-terraform/configure-updates.py /home/${var.admin_user}/.tabula-terraform/updates.json"]
  }
}

output "automatic_updates" {
  value = var.update_policy
}
