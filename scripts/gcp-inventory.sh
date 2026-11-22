#!/usr/bin/env bash
# GCP 자원 인벤토리 수집 (v2)
#
# Cloud Asset Inventory 를 주 경로로 사용합니다.
# 서비스별 권한이 없어도 자산 목록이 나오고, 대화형 프롬프트로 멈추지 않습니다.
#
# Cloud Shell 에서 실행하세요.
#   bash gcp-inventory.sh                    # 접근 가능한 모든 프로젝트
#   bash gcp-inventory.sh pjt-c-admin ...    # 지정한 프로젝트만
#   ORG_ID=123456789012 bash gcp-inventory.sh   # 조직 전체를 한 번에 (가장 빠름)
#
# 결과: gcp-inventory-<날짜>.txt / gcp-assets-<날짜>.csv
set -uo pipefail

STAMP="$(date +%Y%m%d-%H%M)"
OUT="gcp-inventory-$STAMP.txt"
CSV="gcp-assets-$STAMP.csv"
ORG_ID="${ORG_ID:-}"
TIMEOUT=90

# 모든 호출에 공통 적용: 프롬프트 금지(--quiet), 짧은 타임아웃
g() { timeout "$TIMEOUT" gcloud --quiet "$@"; }

exec > >(tee "$OUT") 2>&1

echo "===== GCP 자원 인벤토리 (v2)  $(date -Is) ====="
echo "계정: $(g config get-value account 2>/dev/null)"
echo

# ---------------------------------------------------------------- 대상 결정
if [ $# -gt 0 ]; then
  PROJECTS=("$@")
elif [ -n "$ORG_ID" ]; then
  PROJECTS=()
else
  mapfile -t PROJECTS < <(g projects list --format="value(projectId)" 2>/dev/null)
fi

# ---------------------------------------------------------------- 자산 수집
echo "assetType,project,displayName,location,name" > "$CSV"

collect() {  # collect <scope>
  local scope="$1"
  g asset search-all-resources \
    --scope="$scope" \
    --order-by="assetType" \
    --page-size=500 \
    --format="csv[no-heading](assetType,project,displayName,location,name)" \
    2>/dev/null >> "$CSV"
}

if [ -n "$ORG_ID" ]; then
  echo "### 조직 전체 자산 수집 (organizations/$ORG_ID)"
  collect "organizations/$ORG_ID"
else
  echo "### 프로젝트별 자산 수집 (${#PROJECTS[@]}개)"
  for P in "${PROJECTS[@]}"; do
    printf '    %-40s' "$P"
    before=$(wc -l < "$CSV")
    collect "projects/$P"
    after=$(wc -l < "$CSV")
    echo "$((after - before)) 건"
  done
fi

TOTAL=$(( $(wc -l < "$CSV") - 1 ))
echo
echo "총 자산 $TOTAL 건 → $CSV"

# ---------------------------------------------------------------- 요약
echo
echo "===== 자산 유형별 합계 ====="
tail -n +2 "$CSV" | cut -d, -f1 | sed 's#.*/##' | sort | uniq -c | sort -rn | head -40

echo
echo "===== 프로젝트별 합계 ====="
tail -n +2 "$CSV" | cut -d, -f2 | sort | uniq -c | sort -rn

echo
echo "===== 비용이 큰 자원만 추려보기 ====="
# 상시 과금되거나 잊히기 쉬운 유형
PATTERN='Instance$|Disk$|Address$|ForwardingRule$|Router$|Cluster$|Service$|Bucket$|IndexEndpoint|Subscription|Snapshot|Topic'
tail -n +2 "$CSV" \
  | awk -F, -v p="$PATTERN" '$1 ~ p || $1 ~ /compute|container|run|sql|redis|aiplatform|dataproc|composer/' \
  | grep -v 'serviceusage.googleapis.com' \
  | sort -t, -k1,1 -k2,2 \
  | awk -F, '{printf "    %-55s %-28s %-22s %s\n", $1, $2, $4, $3}' \
  | head -200

echo
echo "===== 참고 ====="
echo "- 결제 금액은 이 스크립트로 조회되지 않습니다(조직에 VPC Service Controls 적용)."
echo "  콘솔 → 결제 → 보고서에서 CSV 를 내려받아 함께 공유하세요."
echo "- 조직 ID 를 알면 ORG_ID=<id> 로 한 번에 수집하는 편이 훨씬 빠릅니다."
echo "  조직 ID 확인: gcloud organizations list"
echo
echo "===== 수집 완료 ====="
echo "  $OUT  (요약)"
echo "  $CSV  (전체 자산 목록)"
echo "두 파일을 대화에 첨부하면 비용 관점으로 분석해 드립니다."
