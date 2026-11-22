"""inspect-mcp — 정적 분석 실행과 결과 조회를 MCP 도구로 노출한다.

LLM 에게 저장소를 통째로 읽히지 않고, 먼저 분석기를 돌린 결과를 넘겨주기 위한 서버다.
"무엇을 고쳐야 하는가"는 분석기가 찾고, "어떻게 고치는가"만 2차에서 LLM 이 맡는다.

1차 범위
  run_semgrep        : 보안·버그 패턴 검사
  run_ruff           : 파이썬 린트 (저장소에 파이썬이 있을 때)
  sonar_issues       : SonarQube 이슈 조회
  sonar_measures     : 커버리지·중복도 등 지표 조회
  summarize_findings : 결과를 파일·규칙 단위로 집계 (LLM 입력 길이를 줄이는 용도)
"""

from __future__ import annotations

import json
import os
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

REPO_ROOT = Path(os.getenv("REPO_ROOT", "/data/repos"))
REPORT_ROOT = Path(os.getenv("REPORT_ROOT", "/data/reports"))
SONAR_BASE_URL = os.getenv("SONAR_BASE_URL", "http://sonarqube:9000").rstrip("/")
SONAR_TOKEN = os.getenv("SONAR_TOKEN", "")

mcp = FastMCP("inspect-mcp")


class ToolError(RuntimeError):
    pass


def _repo_dir(repo: str) -> Path:
    d = REPO_ROOT / repo.replace("/", "__")
    if not (d / ".git").exists():
        raise ToolError(f"로컬에 클론되지 않은 저장소입니다: {repo}. scm-mcp 의 sync_repo 를 먼저 호출하세요.")
    return d


def _save_report(repo: str, tool: str, data: Any) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = REPORT_ROOT / repo.replace("/", "__")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{tool}-{stamp}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


@mcp.tool()
def run_semgrep(repo: str, config: str = "auto", subdir: str = "", timeout_sec: int = 900) -> dict[str, Any]:
    """Semgrep 으로 보안·버그 패턴을 검사하고 요약과 리포트 경로를 돌려준다."""
    d = _repo_dir(repo)
    target = str(d / subdir) if subdir else str(d)

    proc = subprocess.run(
        ["semgrep", "scan", "--config", config, "--json", "--quiet", "--metrics", "off", target],
        capture_output=True,
        text=True,
        timeout=timeout_sec,
    )
    if proc.returncode not in (0, 1):
        raise ToolError(f"semgrep 실패: {proc.stderr.strip()[:1500]}")

    data = json.loads(proc.stdout or "{}")
    results = data.get("results", [])
    for r in results:
        r["path"] = r.get("path", "").replace(f"{d}/", "")

    report = _save_report(repo, "semgrep", data)
    severity = Counter(r.get("extra", {}).get("severity", "UNKNOWN") for r in results)
    return {
        "repo": repo,
        "total": len(results),
        "by_severity": dict(severity),
        "report_path": str(report),
        "top": [
            {
                "rule": r.get("check_id"),
                "path": r.get("path"),
                "line": r.get("start", {}).get("line"),
                "severity": r.get("extra", {}).get("severity"),
                "message": (r.get("extra", {}).get("message") or "")[:300],
            }
            for r in results[:30]
        ],
    }


@mcp.tool()
def run_ruff(repo: str, subdir: str = "") -> dict[str, Any]:
    """파이썬 린트를 실행한다. 파이썬 파일이 없으면 빈 결과를 돌려준다."""
    d = _repo_dir(repo)
    target = str(d / subdir) if subdir else str(d)

    proc = subprocess.run(
        ["ruff", "check", "--output-format", "json", target],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode not in (0, 1):
        raise ToolError(f"ruff 실패: {proc.stderr.strip()[:1500]}")

    items = json.loads(proc.stdout or "[]")
    for i in items:
        i["filename"] = i.get("filename", "").replace(f"{d}/", "")

    report = _save_report(repo, "ruff", items)
    codes = Counter(i.get("code") for i in items)
    return {
        "repo": repo,
        "total": len(items),
        "by_rule": dict(codes.most_common(20)),
        "report_path": str(report),
        "top": [
            {
                "code": i.get("code"),
                "path": i.get("filename"),
                "line": i.get("location", {}).get("row"),
                "message": (i.get("message") or "")[:300],
            }
            for i in items[:30]
        ],
    }


def _sonar_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    if not SONAR_TOKEN:
        raise ToolError(
            "SONAR_TOKEN 이 비어 있습니다. SonarQube 에서 토큰을 발급해 /opt/codeops/.env 에 넣고 "
            "'systemctl restart codeops' 하세요."
        )
    with httpx.Client(timeout=60) as client:
        resp = client.get(f"{SONAR_BASE_URL}{path}", params=params, auth=(SONAR_TOKEN, ""))
    if resp.status_code >= 300:
        raise ToolError(f"SonarQube 조회 실패 ({resp.status_code}): {resp.text[:500]}")
    return resp.json()


@mcp.tool()
def sonar_issues(
    project_key: str,
    severities: str = "BLOCKER,CRITICAL,MAJOR",
    types: str = "BUG,VULNERABILITY,CODE_SMELL",
    page_size: int = 100,
) -> dict[str, Any]:
    """SonarQube 의 미해결 이슈를 조회한다."""
    data = _sonar_get(
        "/api/issues/search",
        {
            "componentKeys": project_key,
            "severities": severities,
            "types": types,
            "statuses": "OPEN,CONFIRMED,REOPENED",
            "ps": min(page_size, 500),
        },
    )
    issues = data.get("issues", [])
    return {
        "project": project_key,
        "total": data.get("total", len(issues)),
        "by_severity": dict(Counter(i.get("severity") for i in issues)),
        "by_type": dict(Counter(i.get("type") for i in issues)),
        "items": [
            {
                "key": i.get("key"),
                "rule": i.get("rule"),
                "severity": i.get("severity"),
                "type": i.get("type"),
                "component": (i.get("component") or "").split(":", 1)[-1],
                "line": i.get("line"),
                "message": (i.get("message") or "")[:300],
                "effort": i.get("effort"),
            }
            for i in issues[:50]
        ],
    }


@mcp.tool()
def sonar_measures(
    project_key: str,
    metrics: str = "bugs,vulnerabilities,code_smells,coverage,duplicated_lines_density,ncloc,sqale_index",
) -> dict[str, Any]:
    """커버리지·중복도 같은 프로젝트 지표를 조회한다."""
    data = _sonar_get(
        "/api/measures/component",
        {"component": project_key, "metricKeys": metrics},
    )
    measures = data.get("component", {}).get("measures", [])
    return {m.get("metric"): m.get("value") for m in measures}


@mcp.tool()
def list_reports(repo: str, limit: int = 20) -> list[str]:
    """저장된 분석 리포트 파일 목록을 최신순으로 돌려준다."""
    out_dir = REPORT_ROOT / repo.replace("/", "__")
    if not out_dir.exists():
        return []
    files = sorted(out_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [str(p) for p in files[:limit]]


@mcp.tool()
def summarize_findings(report_path: str, group_by: str = "path", top_n: int = 25) -> dict[str, Any]:
    """리포트를 파일 또는 규칙 단위로 집계한다.

    LLM 에 넘길 입력을 줄이는 용도다. 전체 결과 대신 이 요약을 먼저 보고
    고칠 대상을 고른 다음 해당 파일만 읽는 흐름을 권장한다.
    """
    p = Path(report_path)
    if not p.is_file() or REPORT_ROOT.resolve() not in p.resolve().parents:
        raise ToolError(f"리포트를 찾을 수 없습니다: {report_path}")

    data = json.loads(p.read_text(encoding="utf-8"))
    rows = data.get("results", data) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ToolError("집계할 수 없는 리포트 형식입니다.")

    def key_of(row: dict[str, Any]) -> str:
        if group_by == "rule":
            return str(row.get("check_id") or row.get("code") or "unknown")
        return str(row.get("path") or row.get("filename") or "unknown")

    counter = Counter(key_of(r) for r in rows if isinstance(r, dict))
    return {
        "report": report_path,
        "total": len(rows),
        "group_by": group_by,
        "top": [{"key": k, "count": c} for k, c in counter.most_common(top_n)],
    }


@mcp.tool()
def server_info() -> dict[str, Any]:
    """현재 서버 설정과 분석기 버전을 확인한다."""
    def version(cmd: list[str]) -> str:
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.strip()[:80]
        except Exception:  # noqa: BLE001
            return "(미설치)"

    return {
        "repo_root": str(REPO_ROOT),
        "report_root": str(REPORT_ROOT),
        "sonar_base_url": SONAR_BASE_URL,
        "sonar_token_configured": bool(SONAR_TOKEN),
        "semgrep": version(["semgrep", "--version"]),
        "ruff": version(["ruff", "--version"]),
    }


if __name__ == "__main__":
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    mcp.settings.host = "0.0.0.0"
    mcp.settings.port = int(os.getenv("MCP_PORT", "8080"))
    mcp.run(transport="streamable-http")
