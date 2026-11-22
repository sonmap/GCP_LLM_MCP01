#!/usr/bin/env bash
# GCP 자원 인벤토리 수집
#
# Cloud Shell 에서 실행하세요. 설치·로그인 불필요합니다.
#   bash gcp-inventory.sh                 # 접근 가능한 모든 프로젝트
#   bash gcp-inventory.sh pjt-c-admin     # 특정 프로젝트만
#
# 결과: gcp-inventory-<날짜>.txt  (Cloud Shell 에서 '다운로드'로 내려받아 공유)
set -uo pipefail

OUT="gcp-inventory-$(date +%Y%m%d-%H%M).txt"
exec > >(tee "$OUT") 2>&1

run() {  # run "제목" 명령...
  local title="$1"; shift
  echo
  echo "### $title"
  "$@" 2>&1 | sed 's/^/    /' || echo "    (조회 실패 또는 권한 없음)"
}

echo "===== GCP 자원 인벤토리  $(date -Is) ====="
echo "계정: $(gcloud config get-value account 2>/dev/null)"

if [ $# -gt 0 ]; then
  PROJECTS=("$@")
else
  mapfile -t PROJECTS < <(gcloud projects list --format="value(projectId)" 2>/dev/null)
fi

echo
echo "===== 결제 계정 ====="
gcloud billing accounts list 2>&1 | sed 's/^/    /'
echo
echo "===== 프로젝트별 결제 연결 ====="
for p in "${PROJECTS[@]}"; do
  echo "    $p -> $(gcloud billing projects describe "$p" --format='value(billingAccountName,billingEnabled)' 2>/dev/null || echo '조회 실패')"
done

for P in "${PROJECTS[@]}"; do
  echo
  echo "=================================================="
  echo "  프로젝트: $P"
  echo "=================================================="

  # Cloud Asset Inventory 가 켜져 있으면 이것 하나로 거의 전부 보입니다.
  run "전체 자산 요약 (Asset Inventory)" \
    gcloud asset search-all-resources --scope="projects/$P" \
      --format="table(assetType,displayName,location)" --limit=500

  run "사용 중인 API" \
    gcloud services list --enabled --project="$P" --format="value(config.name)"

  run "VM 인스턴스" \
    gcloud compute instances list --project="$P" \
      --format="table(name,zone,machineType.basename(),status,scheduling.provisioningModel)"

  run "디스크 (미연결 디스크가 숨은 비용입니다)" \
    gcloud compute disks list --project="$P" \
      --format="table(name,zone,sizeGb,type.basename(),users.len():label=ATTACHED)"

  run "스냅샷" \
    gcloud compute snapshots list --project="$P" \
      --format="table(name,diskSizeGb,storageBytes,creationTimestamp.date())"

  run "고정 IP (미사용 IP 는 과금됩니다)" \
    gcloud compute addresses list --project="$P" \
      --format="table(name,region,address,status)"

  run "로드밸런서 전달 규칙" \
    gcloud compute forwarding-rules list --project="$P" \
      --format="table(name,region,IPAddress,target)"

  run "Cloud NAT / 라우터" \
    gcloud compute routers list --project="$P" --format="table(name,region,network)"

  run "GKE 클러스터" \
    gcloud container clusters list --project="$P" \
      --format="table(name,location,currentNodeCount,status)"

  run "Cloud Run 서비스" \
    gcloud run services list --project="$P" --region=asia-northeast3 \
      --format="table(metadata.name,status.url)"

  run "GCS 버킷" \
    gcloud storage buckets list --project="$P" \
      --format="table(name,location,storageClass)"

  run "Cloud SQL / AlloyDB" \
    gcloud sql instances list --project="$P" \
      --format="table(name,databaseVersion,tier,region,state)"

  run "Vertex AI 인덱스 엔드포인트 (상시 과금 주의)" \
    gcloud ai index-endpoints list --project="$P" --region=asia-northeast3 \
      --format="table(displayName,name)"

  run "미사용 자원 추천 (Recommender)" \
    gcloud recommender recommendations list --project="$P" \
      --location=asia-northeast3-a \
      --recommender=google.compute.instance.IdleResourceRecommender \
      --format="table(description,priority)"
done

echo
echo "===== 수집 완료: $OUT ====="
echo "이 파일을 대화에 첨부하면 비용 관점으로 분석해 드립니다."
