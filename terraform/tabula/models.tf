# Provision a new application base once. No destroy provisioner is installed:
# removing this Terraform resource never deletes the base or its records.
variable "database_template" {
  description = "Optional Tabula model installation for the configured administrator. Secrets remain on the server."
  type = object({
    workspace_id     = optional(string)
    installation_id  = optional(string, "prospection-v1")
    template_path    = optional(string)
    recovery_base_id = optional(string)
  })
  default = null

  validation {
    condition = var.database_template == null ? true : (
      (var.database_template.workspace_id == null ? true : can(regex("^wsp[0-9a-hjkmnp-tv-z]{14}$", var.database_template.workspace_id))) &&
      (var.database_template.recovery_base_id == null ? true : can(regex("^bas[0-9a-hjkmnp-tv-z]{14}$", var.database_template.recovery_base_id))) &&
      can(regex("^[a-zA-Z0-9_-]{1,80}$", var.database_template.installation_id))
    )
    error_message = "Use valid optional Tabula workspace and recovery base IDs, and a simple installation_id."
  }
}

locals {
  model_database         = var.release == null ? "tabula" : var.release.database_name
  model_manager_url      = var.release == null ? null : "${var.configuration.frontend_url}/native"
  database_template_path = try(var.database_template.template_path, null) != null ? var.database_template.template_path : "${path.module}/templates/prospection.json"
}

resource "terraform_data" "database_template" {
  count = var.database_template == null ? 0 : 1

  input = {
    workspace_id     = var.database_template.workspace_id
    installation_id  = var.database_template.installation_id
    model_sha256     = filesha256(local.database_template_path)
    importer_sha256  = filesha256("${path.module}/import_template.py")
    bootstrap_sha256 = filesha256("${path.module}/bootstrap_model.py")
    api              = var.configuration.backend_url
    database         = local.model_database
    manager          = local.model_manager_url
  }
  triggers_replace = [
    coalesce(var.database_template.workspace_id, "automatic"),
    coalesce(var.database_template.recovery_base_id, "none"),
    var.database_template.installation_id,
    filesha256(local.database_template_path),
    filesha256("${path.module}/import_template.py"),
    filesha256("${path.module}/bootstrap_model.py"),
    var.configuration.keycloak_url,
    var.configuration.frontend_url,
    var.configuration.backend_url,
    var.configuration.admin_email,
    local.model_database,
    coalesce(local.model_manager_url, "legacy"),
  ]

  connection {
    type        = "ssh"
    host        = var.ssh_host
    host_key    = var.ssh_host_key
    user        = var.admin_user
    private_key = file(pathexpand(var.ssh_private_key_path))
  }

  provisioner "remote-exec" {
    inline = ["install -d -m 700 /home/${var.admin_user}/.tabula-terraform/models/${var.database_template.installation_id}"]
  }
  provisioner "file" {
    content = jsonencode({
      keycloak_url       = var.configuration.keycloak_url
      frontend_url       = var.configuration.frontend_url
      backend_url        = var.configuration.backend_url
      database_name      = local.model_database
      native_manager_url = local.model_manager_url
      admin_email        = var.configuration.admin_email
      workspace_id       = var.database_template.workspace_id
      recovery_base_id   = var.database_template.recovery_base_id
    })
    destination = "/home/${var.admin_user}/.tabula-terraform/models/${var.database_template.installation_id}/configuration.json"
  }
  provisioner "file" {
    source      = local.database_template_path
    destination = "/home/${var.admin_user}/.tabula-terraform/models/${var.database_template.installation_id}/model.json"
  }
  provisioner "file" {
    source      = "${path.module}/import_template.py"
    destination = "/home/${var.admin_user}/.tabula-terraform/models/${var.database_template.installation_id}/import_template.py"
  }
  provisioner "file" {
    source      = "${path.module}/bootstrap_model.py"
    destination = "/home/${var.admin_user}/.tabula-terraform/models/${var.database_template.installation_id}/bootstrap_model.py"
  }
  provisioner "remote-exec" {
    inline = ["sudo -n python3 /home/${var.admin_user}/.tabula-terraform/models/${var.database_template.installation_id}/bootstrap_model.py /home/${var.admin_user}/.tabula-terraform/models/${var.database_template.installation_id}/configuration.json /home/${var.admin_user}/.tabula-terraform/models/${var.database_template.installation_id}/model.json /opt/tabula/models/${var.database_template.installation_id}.json"]
  }
}
