variable "allow_billed_architecture" {
  type        = bool
  default     = false
  description = "Explicit opt-in for the deferred GCS/Cloud Run architecture. Keep false for the $0 BigQuery sandbox."
}

resource "terraform_data" "billing_opt_in" {
  lifecycle {
    precondition {
      condition     = var.allow_billed_architecture
      error_message = "Paid-service architecture is disabled. The active $0 deployment uses scripts/sandbox_run.py with no billing account."
    }
  }
}
