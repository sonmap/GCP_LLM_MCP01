locals {
  vm_tag       = "${var.name_prefix}-vm"
  stack_object = "stack-${data.archive_file.stack.output_md5}.zip"
}

resource "google_service_account" "vm" {
  account_id   = "${var.name_prefix}-vm-sa"
  display_name = "CodeOps VM service account"
}

resource "google_project_iam_member" "vm_roles" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/secretmanager.secretAccessor",
  ])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.vm.email}"
}

# SCM 접근 토큰. 값은 Terraform 으로 넣지 않고 콘솔이나 gcloud 로 직접 추가합니다.
resource "google_secret_manager_secret" "scm_token" {
  secret_id = "${var.name_prefix}-scm-token"
  labels    = var.labels

  replication {
    auto {}
  }

  depends_on = [google_project_service.required]
}

resource "google_storage_bucket" "bootstrap" {
  name                        = "${var.project_id}-${var.name_prefix}-bootstrap"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true
  labels                      = var.labels

  versioning {
    enabled = true
  }
}

resource "google_storage_bucket_iam_member" "vm_read" {
  bucket = google_storage_bucket.bootstrap.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.vm.email}"
}

data "archive_file" "stack" {
  type        = "zip"
  source_dir  = "${path.module}/../stack"
  output_path = "${path.module}/.build/stack.zip"
}

resource "google_storage_bucket_object" "stack" {
  name   = local.stack_object
  bucket = google_storage_bucket.bootstrap.name
  source = data.archive_file.stack.output_path
}

resource "google_compute_disk" "data" {
  name   = "${var.name_prefix}-data"
  type   = "pd-balanced"
  zone   = var.zone
  size   = var.data_disk_gb
  labels = var.labels
}

resource "google_compute_instance" "vm" {
  name         = "${var.name_prefix}-vm"
  machine_type = var.machine_type
  zone         = var.zone
  tags         = [local.vm_tag]
  labels       = var.labels

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = var.boot_disk_gb
      type  = "pd-balanced"
    }
  }

  attached_disk {
    source      = google_compute_disk.data.id
    device_name = "codeops-data"
  }

  network_interface {
    subnetwork = google_compute_subnetwork.main.id
    # 외부 IP 없음. 인터넷 egress 는 Cloud NAT 경유.
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  service_account {
    email  = google_service_account.vm.email
    scopes = ["cloud-platform"]
  }

  metadata = {
    enable-oslogin = "TRUE"
    startup-script = templatefile("${path.module}/../stack/bootstrap/startup.sh", {
      bootstrap_bucket = google_storage_bucket.bootstrap.name
      stack_object     = local.stack_object
      scm_provider     = var.scm_provider
      scm_api_base     = var.scm_api_base
      scm_repos        = join(",", var.scm_repos)
      scm_secret_name  = google_secret_manager_secret.scm_token.secret_id
      allow_write      = var.allow_write ? "true" : "false"
      project_id       = var.project_id
    })
  }

  allow_stopping_for_update = true

  depends_on = [
    google_storage_bucket_object.stack,
    google_compute_router_nat.nat,
  ]
}
