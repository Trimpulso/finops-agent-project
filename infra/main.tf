resource "google_discovery_engine_data_store" "finops_ds" {
  project     = "project-5f47ed36-9aec-4f46-a30"
  location    = "global"
  data_store_id = "finops-data-store"
  display_name = "FinOps Knowledge Base"
  industry_vertical = "GENERIC"
  content_config = "NO_CONTENT"
}
