output "vm_name" {
  value = google_compute_instance.vm.name
}

output "ssh_command" {
  description = "IAP 터널로 VM 에 접속"
  value       = "gcloud compute ssh ${google_compute_instance.vm.name} --zone ${var.zone} --project ${var.project_id} --tunnel-through-iap"
}

output "tunnel_sonarqube" {
  description = "SonarQube 웹 UI 를 로컬 9000 포트로 연결"
  value       = "gcloud compute start-iap-tunnel ${google_compute_instance.vm.name} 9000 --local-host-port=localhost:9000 --zone ${var.zone} --project ${var.project_id}"
}

output "tunnel_scm_mcp" {
  description = "scm-mcp 를 로컬 8081 포트로 연결 (VS Code 가 여기에 붙습니다)"
  value       = "gcloud compute start-iap-tunnel ${google_compute_instance.vm.name} 8081 --local-host-port=localhost:8081 --zone ${var.zone} --project ${var.project_id}"
}

output "tunnel_inspect_mcp" {
  description = "inspect-mcp 를 로컬 8082 포트로 연결"
  value       = "gcloud compute start-iap-tunnel ${google_compute_instance.vm.name} 8082 --local-host-port=localhost:8082 --zone ${var.zone} --project ${var.project_id}"
}

output "scm_token_secret" {
  description = "여기에 SCM 토큰 값을 직접 추가해야 합니다"
  value       = "gcloud secrets versions add ${google_secret_manager_secret.scm_token.secret_id} --data-file=- --project ${var.project_id}"
}

output "bootstrap_bucket" {
  value = google_storage_bucket.bootstrap.name
}
