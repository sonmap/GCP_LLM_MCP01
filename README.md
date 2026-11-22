# GCP LLM MCP — 소스 인스펙션·개선 환경 (1차)

GCE VM 한 대 위에 MCP 서버와 정적 분석 스택을 올려, VS Code 에서 바로 붙여 쓰는 구성입니다.
**1차는 LLM 연결 없이 도구 계층만 만듭니다.** Vertex AI 및 LLM 연동은 2차 범위입니다.

## 구성

```
GitHub (소스)
   │
   ▼
┌─────────────────────────────────────────────┐
│ codeops-vm  (외부 IP 없음, IAP 로만 접근)     │
│                                             │
│  scm-mcp      :8081   저장소 읽기·검색·PR     │
│  inspect-mcp  :8082   Semgrep·Ruff·Sonar     │
│  sonarqube    :9000   웹 UI                  │
│  postgres             Sonar 저장소           │
└─────────────────────────────────────────────┘
   ▲                         │
   │ IAP 터널                 ▼ Cloud NAT (egress 전용)
 VS Code                   인터넷
```

| 구성요소 | 역할 | 1차 상태 |
|---|---|---|
| `scm-mcp` | 저장소 목록·동기화·파일 읽기·코드 검색·커밋 로그·diff / (선택) 브랜치·PR | 읽기 전용 기본 |
| `inspect-mcp` | Semgrep, Ruff 실행 및 SonarQube 이슈·지표 조회, 결과 집계 | 전체 동작 |
| SonarQube | 지속적 코드 품질 분석 | 전체 동작 |
| LLM 연동 | 분석 결과로 수정안 생성 | **2차** |

설계 원칙 두 가지입니다.

1. **쓰기는 PR 로만.** `main` 직접 푸시 도구는 만들지 않았습니다. `apply_patch` 는 기본 브랜치를 거부합니다.
2. **분석기가 먼저, LLM 은 나중에.** 저장소 전체를 컨텍스트에 밀어넣지 않고 `search_code` 와
   `summarize_findings` 로 범위를 좁힌 뒤 해당 파일만 읽는 흐름을 전제로 설계했습니다.

## 구축 순서

### 1. 사전 준비

```bash
gcloud auth application-default login
gcloud config set project <PROJECT_ID>
```

GitHub 에서 Fine-grained PAT 를 발급합니다. 1차에 필요한 권한은 **Contents: Read** 뿐입니다.
PR 생성까지 쓸 때만 **Pull requests: Write** 를 추가하세요.

### 2. Terraform 적용

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
# project_id 와 scm_repos 를 수정
terraform init
terraform apply
```

만들어지는 것: VPC, 서브넷, Cloud NAT, IAP 방화벽, 서비스 계정, Secret Manager 시크릿(값 없음),
부트스트랩 GCS 버킷, 데이터 디스크, VM.

### 3. SCM 토큰 주입

```bash
printf '%s' '<GITHUB_PAT>' | gcloud secrets versions add codeops-scm-token --data-file=-
gcloud compute ssh codeops-vm --zone asia-northeast3-a --tunnel-through-iap \
  --command 'sudo systemctl restart codeops'
```

### 4. 기동 확인

```bash
gcloud compute ssh codeops-vm --zone asia-northeast3-a --tunnel-through-iap
sudo tail -f /var/log/codeops-startup.log
sudo docker compose -f /opt/codeops/docker-compose.yml ps
```

### 5. SonarQube 초기 설정

```bash
gcloud compute start-iap-tunnel codeops-vm 9000 --local-host-port=localhost:9000 \
  --zone asia-northeast3-a
```

`http://localhost:9000` 접속 → 초기 계정 `admin` / `admin` → 비밀번호 변경 → 토큰 발급.
발급한 토큰을 VM 의 `/opt/codeops/.env` 의 `SONAR_TOKEN=` 에 넣고 `sudo systemctl restart codeops`.

### 6. VS Code 연결

터널을 띄웁니다.

```bash
gcloud compute start-iap-tunnel codeops-vm 8081 --local-host-port=localhost:8081 --zone asia-northeast3-a &
gcloud compute start-iap-tunnel codeops-vm 8082 --local-host-port=localhost:8082 --zone asia-northeast3-a &
```

`.vscode/mcp.json` 을 작업 공간에 복사하면 MCP 클라이언트가 두 서버를 인식합니다.

## 도구 목록

### scm-mcp

| 도구 | 설명 | 쓰기 |
|---|---|---|
| `list_repos` | 허용 저장소와 동기화 상태 | |
| `sync_repo` | 클론 또는 최신화 | |
| `list_files` | 추적 파일 목록 | |
| `read_file` | 줄 번호 포함 파일 읽기 | |
| `search_code` | ripgrep 코드 검색 | |
| `git_log` / `git_diff` | 이력·변경 내용 | |
| `create_branch` | 작업 브랜치 생성 | ✓ |
| `apply_patch` | 패치 적용·커밋·푸시 (기본 브랜치 거부) | ✓ |
| `open_pull_request` | PR 생성 | ✓ |

쓰기 도구는 `allow_write = true` 로 재적용해야 켜집니다.

### inspect-mcp

| 도구 | 설명 |
|---|---|
| `run_semgrep` | 보안·버그 패턴 검사 |
| `run_ruff` | 파이썬 린트 |
| `sonar_issues` | SonarQube 미해결 이슈 |
| `sonar_measures` | 커버리지·중복도 등 지표 |
| `list_reports` | 저장된 리포트 목록 |
| `summarize_findings` | 파일·규칙 단위 집계 |

## 보안 메모

- VM 에 외부 IP 가 없습니다. SSH·HTTP 모두 IAP TCP 포워딩으로만 접근합니다.
- 방화벽 ingress 는 `35.235.240.0/20`(IAP) 만 허용하고 나머지는 전부 거부합니다.
- SCM 토큰은 Secret Manager 에 두고 VM 기동 시에만 읽습니다. 저장소에 커밋하지 마세요.
- `allow_write = false` 가 기본값입니다. PR 리뷰 용도로 2~4주 운영한 뒤에 켜는 것을 권장합니다.
- **분석 대상 소스는 신뢰할 수 없는 입력입니다.** 2차에서 LLM 을 붙일 때, 코드 주석이나
  분석 결과에 들어 있는 지시문을 따르지 않도록 시스템 프롬프트에 명시해야 합니다.

## 2차 예정 범위

- Vertex AI / LLM 어댑터 연결 (Gemini · Claude · OpenAI 선택)
- PR 웹훅 기반 자동 리뷰 코멘트
- Cloud Build 샌드박스에서 수정 → 테스트 → PR 자동 생성
- 제안 채택률 BigQuery 적재

## 비용 메모

고정비는 VM(e2-standard-4), 데이터 디스크 200GB, Cloud NAT 입니다.
야간·주말에 VM 을 정지하면 상당 부분 절감됩니다.

```bash
gcloud compute instances stop codeops-vm --zone asia-northeast3-a
```
