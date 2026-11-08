terraform {
  required_version = ">= 1.6"
}

# The existing stack keeps administrator and SMTP credentials on the server.
# Reconciliation runs on every apply, including repair of changes made in the UI.
resource "terraform_data" "tabula" {
  triggers_replace = [timestamp()]

  connection {
    type        = "ssh"
    host        = var.ssh_host
    host_key    = var.ssh_host_key
    user        = var.admin_user
    private_key = file(pathexpand(var.ssh_private_key_path))
  }

  provisioner "remote-exec" {
    inline = ["install -d -m 700 /home/${var.admin_user}/.tabula-terraform"]
  }
  provisioner "file" {
    content     = jsonencode(var.configuration)
    destination = "/home/${var.admin_user}/.tabula-terraform/configuration.json"
  }
  provisioner "file" {
    source      = "${path.module}/configure.py"
    destination = "/home/${var.admin_user}/.tabula-terraform/configure.py"
  }
  provisioner "file" {
    source      = "${path.module}/mail.py"
    destination = "/home/${var.admin_user}/.tabula-terraform/mail.py"
  }
  provisioner "remote-exec" {
    inline = ["sudo -n python3 /home/${var.admin_user}/.tabula-terraform/configure.py /home/${var.admin_user}/.tabula-terraform/configuration.json"]
  }
}

output "application" {
  value = var.configuration.frontend_url
}

output "realm" {
  value = "${var.configuration.keycloak_url}/realms/tabula"
}

output "initial_password_location" {
  value = "Server: ~/acces.txt for the SSH administrator; root copy in /opt/tabula/bootstrap.json. Initial password only, never reset on subsequent applies."
}
