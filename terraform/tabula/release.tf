variable "release" {
  description = "Optional immutable native release. Apply before deploying the matching stack files."
  type = object({
    database_name = string
    web_image     = string
    module_image  = string
    manager_image = string
  })
  default = null
  validation {
    condition = var.release == null ? true : (
      can(regex("^tabula(-[a-z0-9]+)*$", var.release.database_name)) &&
      alltrue([for component in ["web", "module", "manager"] :
        can(regex("^ghcr\\.io/[a-z0-9_.-]+/tabula-${component}@sha256:[0-9a-f]{64}$", var.release["${component}_image"]))
      ])
    )
    error_message = "Use a Tabula catalogue name and immutable GHCR digests for all three release images."
  }
}

resource "terraform_data" "release" {
  count            = var.release == null ? 0 : 1
  triggers_replace = [var.release, filesha256("${path.module}/configure-runtime.py")]
  depends_on       = [terraform_data.attachment_env]

  connection {
    type        = "ssh"
    host        = var.ssh_host
    host_key    = var.ssh_host_key
    user        = var.admin_user
    private_key = file(pathexpand(var.ssh_private_key_path))
  }
  provisioner "file" {
    content     = jsonencode(var.release)
    destination = "/home/${var.admin_user}/.tabula-terraform/release.json"
  }
  provisioner "file" {
    source      = "${path.module}/configure-runtime.py"
    destination = "/home/${var.admin_user}/.tabula-terraform/configure-runtime.py"
  }
  provisioner "remote-exec" {
    inline = ["sudo -n python3 /home/${var.admin_user}/.tabula-terraform/configure-runtime.py /home/${var.admin_user}/.tabula-terraform/release.json"]
  }
}
