# "Copyright 2021 by Google. 
# Your use of any copyrighted material and any warranties, if applicable, are subject to your agreement with Google."

provider "google" {
  project = var.project_id
  region = var.region
}

locals {
  project_id = [
  "<PROJECT TO BIND SA ROLES>",
  ]
  project_id_list = toset(local.project_id)
  projects = join(",", local.project_id_list)
}

resource "google_service_account" "service_account" {
  account_id   = "sa-quotas"
  display_name = "Service Account for Cloud Function"
  project = var.project_id
}

resource "google_project_iam_binding" "network" {
  for_each = local.project_id_list
  project = each.value
  role =  "roles/compute.networkViewer"
  members = [
  format("serviceAccount:%s", google_service_account.service_account.email)]
}


resource "google_project_iam_binding" "monitor" {
  for_each = local.project_id_list
  project = each.value
  role =  "roles/monitoring.viewer"
  members = [
  format("serviceAccount:%s", google_service_account.service_account.email)]
}

resource "google_project_iam_binding" "metric" {
  for_each = local.project_id_list
  project = each.value
  role =  "roles/monitoring.metricWriter"
  members = [
  format("serviceAccount:%s", google_service_account.service_account.email)]
}

resource "google_pubsub_topic" "topic" {
  name = "quotas-topic"
}

# storage 
data "archive_file" "zip_file" {
  type        = "zip"
  source_dir = "function_files"
  output_path = "metric-quotas.zip"
  depends_on = [google_storage_bucket.bucket]
}

resource "google_storage_bucket" "bucket" {
  name = "quotas-bucket"
  location = "EU"
}

resource "google_storage_bucket_object" "archive" {
  name   = "metric-quotas.zip"
  bucket = google_storage_bucket.bucket.name
  source = "metric-quotas.zip"
}

# trigger

resource "google_project_service_identity" "sm_sa" {
  provider = google-beta
  project = var.project_id
  service = "secretmanager.googleapis.com"
}

resource "google_project_iam_member" "pubsub_publisher" {
  project = var.project_id
  role    = "roles/pubsub.publisher"
  member  = format("serviceAccount:%s", google_project_service_identity.sm_sa.email)
}

resource "google_secret_manager_secret" "secret-basic" {
  secret_id = "secret"
  replication {
	automatic = true
  }
  topics {
	  name = "projects/${var.project_id}/topics/${google_pubsub_topic.topic.name}"
  }
  rotation {
	next_rotation_time = timeadd(timestamp(), "10m")
  	rotation_period = "3600s"
  }
project = var.project_id
depends_on = [google_project_iam_member.pubsub_publisher]
}

resource "google_cloudfunctions_function" "function" {
  name        = "quotas-function"
  description = "Function which creates metric to show effective number of subnet IP ranges in a peering group."
  runtime     = "python39"

  available_memory_mb   = 256
  source_archive_bucket = google_storage_bucket.bucket.name
  source_archive_object = google_storage_bucket_object.archive.name
  service_account_email = google_service_account.service_account.email
  event_trigger  {
      event_type = "google.pubsub.topic.publish"
      resource = "projects/${var.project_id}/topics/${google_pubsub_topic.topic.name}"
  }
  timeout               = 60
  entry_point           = "quotas"

  environment_variables = {
    TF_VAR_PROJECT = var.project_id
  }
}

resource "google_cloudfunctions_function_iam_member" "invoker" {
  project        = google_cloudfunctions_function.function.project
  region         = google_cloudfunctions_function.function.region
  cloud_function = google_cloudfunctions_function.function.name

  role   = "roles/cloudfunctions.invoker"
  member = "allUsers"
}