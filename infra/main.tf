locals {
  apis = toset(["run.googleapis.com", "artifactregistry.googleapis.com", "bigquery.googleapis.com", "storage.googleapis.com", "iam.googleapis.com"])
}
resource "google_project_service" "api" {
  for_each           = local.apis
  service            = each.value
  disable_on_destroy = false
}
resource "google_service_account" "runtime" {
  account_id   = "${var.name}-job"
  display_name = "Synthetic underwriting batch runtime"
  depends_on   = [google_project_service.api]
}
resource "google_storage_bucket" "data" {
  for_each                    = toset(["raw", "artifacts"])
  name                        = "${var.project_id}-${var.name}-${each.key}"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  versioning { enabled = true }
  depends_on = [google_project_service.api]
}
resource "google_storage_bucket_iam_member" "create" {
  for_each = google_storage_bucket.data
  bucket   = each.value.name
  role     = "roles/storage.objectCreator"
  member   = "serviceAccount:${google_service_account.runtime.email}"
}
resource "google_bigquery_dataset" "risk" {
  dataset_id                  = replace(var.name, "-", "_")
  location                    = var.region
  description                 = "Synthetic underwriting demo, isolated tables per run"
  delete_contents_on_destroy  = false
  default_table_expiration_ms = 2592000000
  depends_on                  = [google_project_service.api]
}
resource "google_bigquery_dataset_iam_member" "writer" {
  dataset_id = google_bigquery_dataset.risk.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.runtime.email}"
}
resource "google_project_iam_member" "query" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}
resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = var.name
  format        = "DOCKER"
  depends_on    = [google_project_service.api]
}
resource "google_cloud_run_v2_job" "batch" {
  count               = var.image == "" ? 0 : 1
  name                = var.name
  location            = var.region
  deletion_protection = true
  template {
    task_count  = 1
    parallelism = 1
    template {
      service_account = google_service_account.runtime.email
      max_retries     = 0
      timeout         = "1800s"
      containers {
        image = var.image
        resources { limits = { cpu = "1", memory = "1Gi" } }
        env {
          name  = "RISK_PROJECT_ID"
          value = var.project_id
        }
        env {
          name  = "RISK_REGION"
          value = var.region
        }
        env {
          name  = "RISK_RAW_BUCKET"
          value = google_storage_bucket.data["raw"].name
        }
        env {
          name  = "RISK_ARTIFACT_BUCKET"
          value = google_storage_bucket.data["artifacts"].name
        }
        env {
          name  = "RISK_BQ_DATASET"
          value = google_bigquery_dataset.risk.dataset_id
        }
      }
    }
  }
  depends_on = [google_storage_bucket_iam_member.create, google_bigquery_dataset_iam_member.writer, google_project_iam_member.query]
}
