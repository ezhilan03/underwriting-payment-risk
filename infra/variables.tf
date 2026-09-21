variable "project_id" {
  type        = string
  description = "Existing explicitly selected, billing-enabled GCP project."
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "Supply an existing valid GCP project ID."
  }
}
variable "region" {
  type    = string
  default = "us-central1"
}
variable "image" {
  type        = string
  default     = ""
  description = "Immutable Artifact Registry image digest; empty creates infrastructure without a job for bootstrap."
  validation {
    condition     = var.image == "" || can(regex("@sha256:[a-f0-9]{64}$", var.image))
    error_message = "Use a sha256 image digest, not a mutable tag."
  }
}
variable "name" {
  type    = string
  default = "underwriting-risk"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,21}$", var.name))
    error_message = "Name must be 3-22 lowercase letters, digits or hyphens, starting with a letter."
  }
}
