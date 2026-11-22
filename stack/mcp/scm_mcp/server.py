"""scm-mcp — 형상관리(GitHub/GitLab) 접근을 MCP 도구로 노출한다.

1차 범위
  읽기 : 저장소 목록, 동기화, 파일 읽기, 코드 검색, 커밋 로그, diff
  쓰기 : ALLOW_WRITE=true 일 때만 브랜치 생성 + PR 생성
         main 직접 푸시 도구는 의도적으로 만들지 않는다.

LLM 연결(2차)은 이 서버를 그대로 둔 채 클라이언트 쪽만 붙이면 된다.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import FastMCP

SCM_PROVIDER = os.getenv("SCM_PROVIDER", "github").lower()
SCM_API_BASE = os.getenv("SCM_API_BASE", "https://api.github.com").rstrip("/")
SCM_TOKEN = os.getenv("SCM_TOKEN", "")
SCM_REPOS = [r.strip() for r in os.getenv("SCM_REPOS", "").split(",") if r.strip()]
ALLOW_WRITE = os.getenv("ALLOW_WRITE", "false").lower() == "true"
REPO_ROOT = Path(os.getenv("REPO_ROOT", "/data/repos"))
MAX_BYTES = int(os.getenv("MAX_FILE_BYTES", "200000"))

mcp = FastMCP("scm-mcp")


# ---------------------------------------------------------------- 공통 유틸


class ToolError(RuntimeError):
    pass


def _repo_dir(repo: str) -> Path:
    """repo 인자를 검증하고 로컬 클론 경로를 돌려준다."""
    if repo not in SCM_REPOS:
        raise ToolError(
            f"허용되지 않은 저장소입니다: {repo}. 허용 목록: {', '.join(SCM_REPOS) or '(비어 있음)'}"
        )
    return REPO_ROOT / repo.replace("/", "__")


def _safe_path(base: Path, rel: str) -> Path:
    """경로 탈출(../)을 막는다."""
    target = (base / rel).resolve()
    if base.resolve() not in target.parents and target != base.resolve():
        raise ToolError(f"저장소 밖 경로입니다: {rel}")
    return target


def _git(repo_dir: Path, *args: str, timeout: int = 300) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise ToolError(f"git {' '.join(args)} 실패: {proc.stderr.strip()[:2000]}")
    return proc.stdout


def _clone_url(repo: str) -> str:
    if not SCM_TOKEN:
        raise ToolError("SCM_TOKEN 이 비어 있습니다. Secret Manager 에 토큰을 추가하세요.")
    if SCM_PROVIDER == "github":
        host = "github.com"
        if "api.github.com" not in SCM_API_BASE:  # GitHub Enterprise
            host = SCM_API_BASE.split("//", 1)[-1].split("/", 1)[0]
        return f"https://x-access-token:{SCM_TOKEN}@{host}/{repo}.git"
    host = SCM_API_BASE.split("//", 1)[-1].split("/", 1)[0]
    return f"https://oauth2:{SCM_TOKEN}@{host}/{repo}.git"


def _api_headers() -> dict[str, str]:
    if SCM_PROVIDER == "github":
        return {
            "Authorization": f"Bearer {SCM_TOKEN}",
            "Accept": "application/vnd.github+json",
        }
    return {"PRIVATE-TOKEN": SCM_TOKEN}


def _default_branch(repo_dir: Path) -> str:
    try:
        ref = _git(repo_dir, "symbolic-ref", "refs/remotes/origin/HEAD").strip()
        return ref.rsplit("/", 1)[-1]
    except ToolError:
        return "main"


# ---------------------------------------------------------------- 읽기 도구


@mcp.tool()
def list_repos() -> list[dict[str, Any]]:
    """이 서버가 다루도록 허용된 저장소 목록과 로컬 동기화 상태를 돌려준다."""
    out = []
    for repo in SCM_REPOS:
        d = REPO_ROOT / repo.replace("/", "__")
        cloned = (d / ".git").exists()
        head = ""
        if cloned:
            try:
                head = _git(d, "rev-parse", "--short", "HEAD").strip()
            except ToolError:
                head = "(unknown)"
        out.append({"repo": repo, "cloned": cloned, "head": head, "path": str(d)})
    return out


@mcp.tool()
def sync_repo(repo: str) -> str:
    """저장소를 최초 클론하거나 최신 상태로 fetch 한다. 다른 도구를 쓰기 전에 한 번 호출한다."""
    d = _repo_dir(repo)
    if (d / ".git").exists():
        _git(d, "fetch", "--prune", "origin")
        branch = _default_branch(d)
        _git(d, "checkout", branch)
        _git(d, "reset", "--hard", f"origin/{branch}")
        return f"{repo}: {branch} 최신화 완료 ({_git(d, 'rev-parse', '--short', 'HEAD').strip()})"

    d.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["git", "clone", _clone_url(repo), str(d)],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if proc.returncode != 0:
        raise ToolError(f"클론 실패: {proc.stderr.strip()[:2000].replace(SCM_TOKEN, '***')}")
    return f"{repo}: 클론 완료 ({_git(d, 'rev-parse', '--short', 'HEAD').strip()})"


@mcp.tool()
def list_files(repo: str, subdir: str = "", pattern: str = "") -> list[str]:
    """저장소의 추적 중인 파일 목록. subdir 로 범위를 좁히고 pattern 으로 걸러낸다."""
    d = _repo_dir(repo)
    args = ["ls-files"]
    if subdir:
        _safe_path(d, subdir)
        args.append(subdir)
    files = _git(d, *args).splitlines()
    if pattern:
        files = [f for f in files if pattern in f]
    return files[:2000]


@mcp.tool()
def read_file(repo: str, path: str, start_line: int = 1, max_lines: int = 400) -> str:
    """파일 내용을 줄 번호와 함께 읽는다. 큰 파일은 start_line 으로 나눠 읽는다."""
    d = _repo_dir(repo)
    target = _safe_path(d, path)
    if not target.is_file():
        raise ToolError(f"파일이 없습니다: {path}")
    if target.stat().st_size > MAX_BYTES:
        raise ToolError(f"파일이 너무 큽니다({target.stat().st_size} bytes). 범위를 나눠 읽으세요.")

    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, start_line)
    chunk = lines[start - 1 : start - 1 + max_lines]
    body = "\n".join(f"{start + i:6d}\t{line}" for i, line in enumerate(chunk))
    return f"# {repo}/{path} ({start}-{start + len(chunk) - 1} / {len(lines)} lines)\n{body}"


@mcp.tool()
def search_code(repo: str, query: str, glob: str = "", max_results: int = 80) -> str:
    """ripgrep 으로 코드를 검색한다. 저장소 전체를 읽는 대신 이 도구로 필요한 부분만 찾는다."""
    d = _repo_dir(repo)
    cmd = ["rg", "--line-number", "--no-heading", "--color", "never", "--max-count", "5"]
    if glob:
        cmd += ["--glob", glob]
    cmd += ["--", query, str(d)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode not in (0, 1):
        raise ToolError(f"검색 실패: {proc.stderr.strip()[:1000]}")
    lines = proc.stdout.replace(f"{d}/", "").splitlines()[:max_results]
    return "\n".join(lines) or "(일치하는 내용 없음)"


@mcp.tool()
def git_log(repo: str, path: str = "", limit: int = 20) -> str:
    """최근 커밋 이력. path 를 주면 해당 파일의 이력만 본다."""
    d = _repo_dir(repo)
    args = ["log", f"-{limit}", "--date=short", "--pretty=format:%h %ad %an %s"]
    if path:
        _safe_path(d, path)
        args += ["--", path]
    return _git(d, *args) or "(이력 없음)"


@mcp.tool()
def git_diff(repo: str, base: str, head: str = "HEAD", path: str = "") -> str:
    """두 리비전 사이의 변경 내용을 본다."""
    d = _repo_dir(repo)
    args = ["diff", "--stat=200", f"{base}...{head}"]
    stat = _git(d, *args)
    args = ["diff", f"{base}...{head}"]
    if path:
        _safe_path(d, path)
        args += ["--", path]
    body = _git(d, *args)
    if len(body) > MAX_BYTES:
        body = body[:MAX_BYTES] + "\n... (생략됨. path 로 범위를 좁히세요)"
    return f"{stat}\n\n{body}"


# ---------------------------------------------------------------- 쓰기 도구


def _require_write() -> None:
    if not ALLOW_WRITE:
        raise ToolError(
            "쓰기 도구가 비활성 상태입니다(ALLOW_WRITE=false). "
            "1차 운영 기간에는 읽기 전용으로 사용하세요."
        )


@mcp.tool()
def create_branch(repo: str, branch: str, from_ref: str = "") -> str:
    """기본 브랜치에서 새 작업 브랜치를 만들고 원격에 올린다. ALLOW_WRITE=true 필요."""
    _require_write()
    d = _repo_dir(repo)
    base = from_ref or _default_branch(d)
    _git(d, "fetch", "origin", base)
    _git(d, "checkout", "-B", branch, f"origin/{base}")
    _git(d, "push", "-u", "origin", branch)
    return f"{repo}: 브랜치 {branch} 생성 (기준 {base})"


@mcp.tool()
def apply_patch(repo: str, branch: str, patch: str, message: str) -> str:
    """통합 diff 형식 패치를 작업 브랜치에 적용하고 커밋·푸시한다.

    main 등 기본 브랜치에는 적용할 수 없다. ALLOW_WRITE=true 필요.
    """
    _require_write()
    d = _repo_dir(repo)
    if branch == _default_branch(d):
        raise ToolError("기본 브랜치에는 직접 적용할 수 없습니다. 작업 브랜치를 쓰세요.")

    _git(d, "checkout", branch)
    patch_file = d / ".codeops.patch"
    patch_file.write_text(patch, encoding="utf-8")
    try:
        _git(d, "apply", "--index", str(patch_file))
    finally:
        patch_file.unlink(missing_ok=True)

    _git(d, "-c", "user.name=codeops-bot", "-c", "user.email=codeops-bot@local",
         "commit", "-m", message)
    _git(d, "push", "origin", branch)
    return f"{repo}: {branch} 에 커밋 완료 ({_git(d, 'rev-parse', '--short', 'HEAD').strip()})"


@mcp.tool()
def open_pull_request(repo: str, branch: str, title: str, body: str, base: str = "") -> str:
    """작업 브랜치로 PR(또는 Merge Request)을 연다. ALLOW_WRITE=true 필요."""
    _require_write()
    d = _repo_dir(repo)
    target = base or _default_branch(d)

    with httpx.Client(timeout=60) as client:
        if SCM_PROVIDER == "github":
            url = f"{SCM_API_BASE}/repos/{repo}/pulls"
            payload = {"title": title, "head": branch, "base": target, "body": body}
        else:
            url = f"{SCM_API_BASE}/projects/{quote(repo, safe='')}/merge_requests"
            payload = {
                "title": title,
                "source_branch": branch,
                "target_branch": target,
                "description": body,
            }
        resp = client.post(url, json=payload, headers=_api_headers())

    if resp.status_code >= 300:
        raise ToolError(f"PR 생성 실패 ({resp.status_code}): {resp.text[:800]}")
    data = resp.json()
    return data.get("html_url") or data.get("web_url") or str(data.get("number", ""))


@mcp.tool()
def server_info() -> dict[str, Any]:
    """현재 서버 설정을 확인한다. 토큰 값은 노출하지 않는다."""
    return {
        "provider": SCM_PROVIDER,
        "api_base": SCM_API_BASE,
        "repos": SCM_REPOS,
        "allow_write": ALLOW_WRITE,
        "token_configured": bool(SCM_TOKEN),
        "repo_root": str(REPO_ROOT),
    }


if __name__ == "__main__":
    REPO_ROOT.mkdir(parents=True, exist_ok=True)
    mcp.settings.host = "0.0.0.0"
    mcp.settings.port = int(os.getenv("MCP_PORT", "8080"))
    mcp.run(transport="streamable-http")
