output "raw_bucket" { value = google_storage_bucket.data["raw"].name }
output "artifact_bucket" { value = google_storage_bucket.data["artifacts"].name }
output "dataset" { value = google_bigquery_dataset.risk.dataset_id }
output "runtime_identity" { value = google_service_account.runtime.email }
output "image_repository" { value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}/risk" }
output "job_name" { value = try(google_cloud_run_v2_job.batch[0].name, null) }
