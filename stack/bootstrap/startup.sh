#!/usr/bin/env bash
# CodeOps 1차 부트스트랩: Docker 설치, 데이터 디스크 마운트, 스택 기동
set -euo pipefail

LOG=/var/log/codeops-startup.log
exec > >(tee -a "$LOG") 2>&1
echo "=== codeops startup $(date -Is) ==="

BOOTSTRAP_BUCKET="${bootstrap_bucket}"
STACK_OBJECT="${stack_object}"
SCM_PROVIDER="${scm_provider}"
SCM_API_BASE="${scm_api_base}"
SCM_REPOS="${scm_repos}"
SCM_SECRET_NAME="${scm_secret_name}"
ALLOW_WRITE="${allow_write}"
PROJECT_ID="${project_id}"

DATA_MNT=/mnt/codeops
APP_DIR=/opt/codeops

# ---------- 1. 패키지 ----------
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y ca-certificates curl gnupg git unzip jq ripgrep

install -m 0755 -d /etc/apt/keyrings
if [ ! -f /etc/apt/keyrings/docker.gpg ]; then
  curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
fi
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker

# ---------- 2. 데이터 디스크 ----------
DISK=/dev/disk/by-id/google-codeops-data
if [ -b "$DISK" ]; then
  if ! blkid "$DISK" >/dev/null 2>&1; then
    echo "formatting data disk"
    mkfs.ext4 -m 0 -E lazy_itable_init=0,lazy_journal_init=0,discard "$DISK"
  fi
  mkdir -p "$DATA_MNT"
  grep -q "$DATA_MNT" /etc/fstab || echo "$DISK $DATA_MNT ext4 discard,defaults,nofail 0 2" >> /etc/fstab
  mount -a
else
  echo "WARN: data disk not found, using boot disk"
  mkdir -p "$DATA_MNT"
fi

mkdir -p "$DATA_MNT"/{repos,sonarqube/data,sonarqube/logs,sonarqube/extensions,postgres,reports,workspace}
# SonarQube 컨테이너는 uid 1000 으로 동작합니다.
chown -R 1000:1000 "$DATA_MNT"/sonarqube
chown -R 999:999 "$DATA_MNT"/postgres || true

# SonarQube Elasticsearch 요구사항
sysctl -w vm.max_map_count=524288
sysctl -w fs.file-max=131072
grep -q "vm.max_map_count" /etc/sysctl.conf || echo "vm.max_map_count=524288" >> /etc/sysctl.conf

# ---------- 3. 스택 내려받기 ----------
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR"
gcloud storage cp "gs://$BOOTSTRAP_BUCKET/$STACK_OBJECT" /tmp/stack.zip --quiet
unzip -o -q /tmp/stack.zip -d "$APP_DIR"

# ---------- 4. 환경 파일 ----------
SCM_TOKEN="$(gcloud secrets versions access latest --secret="$SCM_SECRET_NAME" --project="$PROJECT_ID" 2>/dev/null || echo "")"
if [ -z "$SCM_TOKEN" ]; then
  echo "WARN: SCM 토큰이 비어 있습니다. 시크릿에 값을 추가한 뒤 'systemctl restart codeops' 하세요."
fi

SONAR_DB_PASS="$(openssl rand -hex 24)"
if [ -f "$DATA_MNT/.sonar_db_pass" ]; then
  SONAR_DB_PASS="$(cat "$DATA_MNT/.sonar_db_pass")"
else
  echo "$SONAR_DB_PASS" > "$DATA_MNT/.sonar_db_pass"
  chmod 600 "$DATA_MNT/.sonar_db_pass"
fi

cat > "$APP_DIR/.env" <<EOF
DATA_MNT=$DATA_MNT
SCM_PROVIDER=$SCM_PROVIDER
SCM_API_BASE=$SCM_API_BASE
SCM_REPOS=$SCM_REPOS
SCM_TOKEN=$SCM_TOKEN
ALLOW_WRITE=$ALLOW_WRITE
REPO_ROOT=/data/repos
REPORT_ROOT=/data/reports
SONAR_DB_PASS=$SONAR_DB_PASS
SONAR_BASE_URL=http://sonarqube:9000
SONAR_TOKEN=
EOF
chmod 600 "$APP_DIR/.env"

# ---------- 5. 서비스 등록 ----------
cat > /etc/systemd/system/codeops.service <<'EOF'
[Unit]
Description=CodeOps stack (MCP servers + SonarQube)
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/codeops
ExecStart=/usr/bin/docker compose up -d --build
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=1800

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable codeops
systemctl restart codeops

echo "=== codeops startup done $(date -Is) ==="
