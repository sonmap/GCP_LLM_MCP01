terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  # 1차에서는 로컬 state 로 시작하고, 검증 후 아래 주석을 풀어 GCS 백엔드로 옮기세요.
  # backend "gcs" {
  #   bucket = "gcs-codeops-tfstate"
  #   prefix = "codeops/phase1"
  # }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
