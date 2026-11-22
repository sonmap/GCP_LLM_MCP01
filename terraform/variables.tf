variable "project_id" {
  description = "배포 대상 GCP 프로젝트 ID"
  type        = string
}

variable "region" {
  description = "리전"
  type        = string
  default     = "asia-northeast3"
}

variable "zone" {
  description = "영역"
  type        = string
  default     = "asia-northeast3-a"
}

variable "name_prefix" {
  description = "모든 자원 이름 앞에 붙는 접두사"
  type        = string
  default     = "codeops"
}

variable "subnet_cidr" {
  description = "VM 서브넷 대역"
  type        = string
  default     = "10.50.0.0/24"
}

variable "machine_type" {
  description = "VM 머신 타입. SonarQube 가 메모리를 많이 쓰므로 e2-standard-4 이상 권장"
  type        = string
  default     = "e2-standard-4"
}

variable "boot_disk_gb" {
  description = "부팅 디스크 크기(GB)"
  type        = number
  default     = 50
}

variable "data_disk_gb" {
  description = "데이터 디스크 크기(GB). 저장소 클론과 SonarQube 데이터가 들어갑니다"
  type        = number
  default     = 200
}

variable "scm_provider" {
  description = "형상관리 종류: github | gitlab"
  type        = string
  default     = "github"

  validation {
    condition     = contains(["github", "gitlab"], var.scm_provider)
    error_message = "scm_provider 는 github 또는 gitlab 이어야 합니다."
  }
}

variable "scm_api_base" {
  description = "SCM API 기본 URL. 자체 호스팅 GitLab 이면 https://gitlab.example.com/api/v4"
  type        = string
  default     = "https://api.github.com"
}

variable "scm_repos" {
  description = "1차 대상 저장소 목록. 형식: owner/repo (GitHub) 또는 group/project (GitLab)"
  type        = list(string)
  default     = []
}

variable "allow_write" {
  description = "scm-mcp 의 쓰기 도구(브랜치 생성·PR) 활성화 여부. 1차는 false 권장"
  type        = bool
  default     = false
}

variable "labels" {
  description = "공통 라벨"
  type        = map(string)
  default = {
    system = "codeops"
    phase  = "1"
  }
}
