# Optional billing-enabled GCP batch delivery

**Active deployment:** the dedicated project `underwriting-risk-ez-2026` uses BigQuery Sandbox with no linked billing account. See [SANDBOX.md](SANDBOX.md) for the verified $0 path.

The Cloud Run/GCS architecture below is an optional future upgrade. It has not been deployed. Terraform refuses to apply its resources unless `allow_billed_architecture=true` is explicitly provided. Keep the default false for the current zero-dollar constraint. Creating or linking billing is outside that constraint.

## Resources and identity

Terraform provisions two regional GCS buckets with object versioning, uniform
bucket access and public access prevention, a BigQuery dataset with 30-day table
expiration, a Docker Artifact Registry repository, and an optional Cloud Run job.
The job runs once on demand with one task, no automatic retries, a 30-minute
timeout, 1 CPU and 1 GiB memory. There is no scheduler, public endpoint or public
IAM binding. The image must be pinned by sha256 digest. Python runs as UID 10001.

The runtime service account can create objects only in the two buckets, edit
tables only within the dedicated dataset, and submit BigQuery jobs in the chosen
project. It cannot delete or read GCS objects. BigQuery dataEditor is dataset
scoped but permits table deletion; run isolation is an application guard, not a
warehouse immutability policy. Deployers need separate resource provisioning and
IAM permissions, plus service-account actAs and Artifact Registry upload access.
Same-project Cloud Run image pulls use the Google-managed Cloud Run service
agent. No key files or credentials belong in images or repository files.

## Deployment procedure

Run from the repository root with authenticated deployment credentials. The
commands below are operator steps, not a record of completed deployment.

```sh
# Only after explicitly choosing a billing-enabled upgrade:
export TF_VAR_allow_billed_architecture=true
export TF_VAR_project_id='YOUR_EXISTING_PROJECT_ID'
terraform -chdir=infra init
terraform -chdir=infra plan -out=bootstrap.tfplan
terraform -chdir=infra apply bootstrap.tfplan
```

The first apply creates the registry and storage, but no job while `image` is
empty. Build and push after retrieving the exact repository URL:

```sh
RISK_IMAGE_REPOSITORY=$(terraform -chdir=infra output -raw image_repository)
gcloud auth configure-docker us-central1-docker.pkg.dev
docker build --platform linux/amd64 -t "$RISK_IMAGE_REPOSITORY:v1" .
docker push "$RISK_IMAGE_REPOSITORY:v1"
```

Resolve the pushed image digest from Artifact Registry, then set
`TF_VAR_image` to `REPOSITORY@sha256:DIGEST` and apply a second reviewed plan.
If changing region, use the matching registry host and Cloud Run region.

```sh
terraform -chdir=infra plan -out=job.tfplan
terraform -chdir=infra apply job.tfplan
gcloud run jobs execute underwriting-risk --project "$TF_VAR_project_id" --region us-central1 --wait
```

The runner invokes `python -m risk_platform run --output TEMP_DIRECTORY`, then
uploads each artifact under a fresh UUID run prefix. `raw/` files go to the raw
bucket; remaining files go to the artifact bucket. Every upload uses generation
match zero, so an existing live object cannot be overwritten. Versioning adds
recovery protection; it is not a locked retention policy.

Each nonempty `tables/*.jsonl` file becomes a separate BigQuery table suffixed
with the run UUID, using WRITE_EMPTY. All rows are scanned locally to infer a
consistent schema: integer money remains INT64, booleans remain BOOL, missing
values remain NULL, nested objects/arrays are preserved as JSON text STRING,
and null-only columns are nullable STRING. Mixed scalar types, overflow,
nonfinite numbers and invalid identifiers fail closed. Empty files are recorded
as skipped rather than inventing a schema. These are run snapshot tables;
canonical transformations and marts are built by dbt against those run-specific BigQuery tables.

Every loaded table gets a count verification query with a 100 MB billing limit.
Only after all loads, checks and the BigQuery dbt build succeed is `cloud-verification.json` created in
the artifact run prefix. It includes object hashes/generations, BigQuery table
IDs and load/query job IDs. A failed run may leave partial immutable artifacts
and tables but has no completion marker. A rerun produces a new isolated run,
not an automatic resume. Collect the Cloud Run execution ID and this marker to
support a remote delivery claim.

## Validation, cost and recovery

Local checks: `python scripts/cloud_test.py` and `terraform -chdir=infra fmt -check`.
Provider-aware validation requires successful `terraform init -backend=false`,
then `terraform validate`. The original restricted-network initialization failure
was resolved on September 21. Cloud Run runtime IAM and GCS delivery remain unverified. BigQuery Sandbox loads, dbt execution and source recovery are separately verified in SANDBOX.md. CI includes
offline conversion checks, application tests, a synthetic run and Terraform
validation, but its remote execution has not been observed.

Cloud Run has no standing compute charge between executions, but storage,
registry, query, network and other applicable charges can remain. No dollar
budget is enforced by this repository. Table expiration limits dataset retention;
GCS objects persist until an authorized operator deletes them. Preserve the
Terraform state securely; generated local state and plans must stay out of git.

For recovery, retrieve a versioned GCS run using an operator identity with read
access and compare SHA-256 values with the completion marker. Recreate tables
from those exact files into a new run namespace and compare row counts and
report totals. This is a documented procedure, not a completed recovery drill.
Bucket force_destroy is false and job deletion protection is enabled. Cleanup
requires an explicit operator decision, removing that guard before job deletion,
and separately emptying retained buckets/dataset contents as appropriate.

Primary references: [Cloud Run job identity](https://docs.cloud.google.com/run/docs/configuring/jobs/service-identity),
[GCS generation preconditions](https://docs.cloud.google.com/storage/docs/request-preconditions),
[BigQuery JSON loads and write dispositions](https://docs.cloud.google.com/bigquery/docs/loading-data-cloud-storage-json),
[Terraform Cloud Run jobs](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/cloud_run_v2_job).
