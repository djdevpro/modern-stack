locals {
  attachment_env = {
    S3_ENDPOINT          = "https://${var.s3_location}.your-objectstorage.com"
    S3_REGION            = var.s3_location
    S3_BUCKET            = var.existing_bucket_name
    S3_ACCESS_KEY_ID     = var.drive_s3_access_key
    S3_SECRET_ACCESS_KEY = var.drive_s3_secret_key
    S3_FORCE_PATH_STYLE  = "false"
  }
}

# The existing bucket remains managed by its original storage stack. Its CORS
# and lifecycle rules must include Tabula without replacing the other applications.
resource "terraform_data" "attachment_env" {
  triggers_replace = [timestamp()]
  depends_on       = [terraform_data.tabula]

  connection {
    type        = "ssh"
    host        = var.ssh_host
    host_key    = var.ssh_host_key
    user        = var.admin_user
    private_key = file(pathexpand(var.ssh_private_key_path))
  }
  provisioner "file" {
    content     = jsonencode(local.attachment_env)
    destination = "/home/${var.admin_user}/.tabula-terraform/storage.json"
  }
  provisioner "file" {
    source      = "${path.module}/configure-storage.py"
    destination = "/home/${var.admin_user}/.tabula-terraform/configure-storage.py"
  }
  provisioner "remote-exec" {
    inline = ["sudo -n python3 /home/${var.admin_user}/.tabula-terraform/configure-storage.py /home/${var.admin_user}/.tabula-terraform/storage.json"]
  }
}

output "attachment_storage" {
  value = {
    bucket   = var.existing_bucket_name
    endpoint = "https://${var.s3_location}.your-objectstorage.com"
    region   = var.s3_location
    prefix   = "tabula/"
  }
}
