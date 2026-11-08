# Apply the HTTP defense as well as the cloud firewall: a Host header is not access control.
resource "terraform_data" "private_admin" {
  triggers_replace = [timestamp()]
  depends_on       = [hcloud_firewall.this]

  provisioner "local-exec" {
    interpreter = ["python"]
    command     = "${path.module}/configure-private-admin.py"
    environment = {
      ADMIN_SSH_HOST = "${var.server_name}.netbird.cloud"
      ADMIN_SSH_USER = var.admin_user
    }
  }
}
