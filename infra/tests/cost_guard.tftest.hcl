mock_provider "google" {}

run "default_denies_billed_architecture" {
  command = plan
  variables {
    project_id = "example-risk-project"
  }
  expect_failures = [terraform_data.billing_opt_in]
}
