# ==============================================================================
# SWN MANAGER — Submitter Work Number Registry
#
# GitHub is the master. swn_registry.json lives in the repo root.
# Reads and writes via GitHub REST API using a Personal Access Token.
#
# Authentication: GitHub PAT stored in Streamlit Secrets under [GITHUB]
#   token = "ghp_xxx"
#   repo  = "DP-669/rMG-cwr-converter-Claude-version"
# ==============================================================================

import base64
import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone


LOCAL_REGISTRY_PATH = "swn_registry.json"
GITHUB_FILE_PATH    = "swn_registry.json"

BOOTSTRAP_REGISTRY = {
    "last_swn_used": 13621,
    "last_swn_source": "CW260006 (CATASTROPHE TROPHY)",
    "updated": "2026-05-13T00:00:00",
    "history": [
        {
            "file": "CW250010LUM_319.V22",
            "album": "redCola catalog",
            "swn_start": 1,
            "swn_end": 10011,
            "track_count": 10011,
            "generated_by": "Chris",
            "date": "2025-01-01T00:00:00"
        },
        {
            "file": "CW250011LUM_319.V22",
            "album": "EPP+SSC catalog",
            "swn_start": 10012,
            "swn_end": 13616,
            "track_count": 3605,
            "generated_by": "Chris",
            "date": "2025-01-01T00:00:00"
        },
        {
            "file": "CW260006LUM_319.V22",
            "album": "rC055",
            "swn_start": 13617,
            "swn_end": 13621,
            "track_count": 5,
            "generated_by": "rMG CWR Converter v1.5.1",
            "date": "2026-05-13T00:00:00"
        }
    ]
}


class SWNError(Exception):
    pass


class SWNSyncMismatch(Exception):
    def __init__(self, local_val, github_val):
        self.local_val  = local_val
        self.github_val = github_val
        super().__init__(
            f"SWN registry mismatch: local={local_val}, GitHub={github_val}"
        )


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------

def _github_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept":        "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent":    "rMG-CWR-Converter/1.5.2",
    }


def _read_from_github(token: str, repo: str) -> tuple[dict, str]:
    """Returns (registry_dict, sha) from GitHub."""
    url = f"https://api.github.com/repos/{repo}/contents/{GITHUB_FILE_PATH}"
    req = urllib.request.Request(url, headers=_github_headers(token))
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = base64.b64decode(data["content"]).decode("utf-8")
            return json.loads(content), data["sha"]
    except urllib.error.HTTPError as e:
        raise SWNError(f"GitHub read failed (HTTP {e.code}): {e.reason}")
    except Exception as e:
        raise SWNError(f"GitHub read failed: {e}")


def _write_to_github(token: str, repo: str, registry: dict, sha: str) -> None:
    """Commits registry_dict to GitHub, requires current SHA."""
    url     = f"https://api.github.com/repos/{repo}/contents/{GITHUB_FILE_PATH}"
    content = base64.b64encode(
        json.dumps(registry, indent=2).encode("utf-8")
    ).decode("utf-8")
    payload = json.dumps({
        "message": f"SWN update — last used {registry.get('last_swn_used')}",
        "content": content,
        "sha":     sha,
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={**_github_headers(token), "Content-Type": "application/json"},
        method="PUT"
    )
    try:
        with urllib.request.urlopen(req) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        raise SWNError(f"GitHub write failed (HTTP {e.code}): {body}")
    except Exception as e:
        raise SWNError(f"GitHub write failed: {e}")


# ---------------------------------------------------------------------------
# Local cache helpers
# ---------------------------------------------------------------------------

def _read_local() -> dict | None:
    if not os.path.exists(LOCAL_REGISTRY_PATH):
        return None
    try:
        with open(LOCAL_REGISTRY_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return None


def _write_local(registry: dict) -> None:
    with open(LOCAL_REGISTRY_PATH, "w") as f:
        json.dump(registry, f, indent=2)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_registry(secrets: dict) -> dict:
    gh_cfg = secrets.get("GITHUB", {})
    token  = gh_cfg.get("token", "")
    repo   = gh_cfg.get("repo", "")

    github_registry = None
    github_sha      = None
    github_error    = None

    if token and repo:
        try:
            github_registry, github_sha = _read_from_github(token, repo)
        except SWNError as e:
            github_error = str(e)

    local_registry = _read_local()

    # Bootstrap — nothing anywhere
    if github_registry is None and local_registry is None:
        registry = dict(BOOTSTRAP_REGISTRY)
        registry["updated"] = datetime.now(timezone.utc).isoformat()
        _write_local(registry)
        registry["_github_available"] = False
        registry["_github_error"]     = github_error or "No GitHub config"
        registry["_bootstrapped"]     = True
        return registry

    # GitHub unavailable — use local cache
    if github_registry is None and local_registry is not None:
        local_registry["_github_available"] = False
        local_registry["_github_error"]     = github_error
        local_registry["_sync_warning"] = (
            "⚠️ GitHub unavailable. Using local cache. "
            "Generation allowed but GitHub backup is offline."
        )
        return local_registry

    # Local missing — seed from GitHub
    if github_registry is not None and local_registry is None:
        _write_local(github_registry)
        github_registry["_github_available"] = True
        github_registry["_github_error"]     = None
        github_registry["_github_sha"]       = github_sha
        github_registry["_token"]            = token
        github_registry["_repo"]             = repo
        return github_registry

    # Both present — check for mismatch
    g_swn = github_registry.get("last_swn_used", -1)
    l_swn = local_registry.get("last_swn_used", -2)

    if g_swn != l_swn:
        raise SWNSyncMismatch(local_val=l_swn, github_val=g_swn)

    _write_local(github_registry)
    github_registry["_github_available"] = True
    github_registry["_github_error"]     = None
    github_registry["_github_sha"]       = github_sha
    github_registry["_token"]            = token
    github_registry["_repo"]             = repo
    return github_registry


def get_next_swn(registry: dict) -> int:
    return int(registry["last_swn_used"]) + 1


def commit_swn_range(
    registry: dict,
    swn_start: int,
    swn_end: int,
    track_count: int,
    filename: str,
    album: str,
    secrets: dict
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    registry["last_swn_used"]   = swn_end
    registry["last_swn_source"] = f"rMG Converter — {filename} ({album})"
    registry["updated"]         = now
    registry.setdefault("history", []).append({
        "file":         filename,
        "album":        album,
        "swn_start":    swn_start,
        "swn_end":      swn_end,
        "track_count":  track_count,
        "generated_by": "rMG Converter",
        "date":         now
    })

    clean = {k: v for k, v in registry.items() if not k.startswith("_")}
    _write_local(clean)

    token = registry.get("_token") or secrets.get("GITHUB", {}).get("token", "")
    repo  = registry.get("_repo")  or secrets.get("GITHUB", {}).get("repo", "")
    sha   = registry.get("_github_sha", "")

    if token and repo and sha:
        try:
            _write_to_github(token, repo, clean, sha)
            # Fetch new SHA for subsequent writes this session
            _, new_sha = _read_from_github(token, repo)
            registry["_github_sha"] = new_sha
        except SWNError as e:
            registry["_github_write_error"] = str(e)
    elif token and repo:
        # SHA missing — fetch it
        try:
            _, current_sha = _read_from_github(token, repo)
            _write_to_github(token, repo, clean, current_sha)
            _, new_sha = _read_from_github(token, repo)
            registry["_github_sha"] = new_sha
        except SWNError as e:
            registry["_github_write_error"] = str(e)

    return registry


def resolve_conflict(
    use_github: bool,
    local_val: int,
    github_val: int,
    secrets: dict
) -> dict:
    gh_cfg = secrets.get("GITHUB", {})
    token  = gh_cfg.get("token", "")
    repo   = gh_cfg.get("repo", "")

    chosen = github_val if use_github else local_val
    source = "GitHub (manual resolution)" if use_github else "Local cache (manual resolution)"

    if use_github and token and repo:
        try:
            registry, sha = _read_from_github(token, repo)
        except SWNError:
            registry = _read_local() or dict(BOOTSTRAP_REGISTRY)
            sha = ""
    else:
        registry = _read_local() or dict(BOOTSTRAP_REGISTRY)
        sha = ""

    registry["last_swn_used"]   = chosen
    registry["last_swn_source"] = source
    registry["updated"]         = datetime.now(timezone.utc).isoformat()

    clean = {k: v for k, v in registry.items() if not k.startswith("_")}
    _write_local(clean)
    if token and repo and sha:
        try:
            _write_to_github(token, repo, clean, sha)
        except SWNError:
            pass

    clean["_github_available"] = bool(token and repo)
    return clean


def format_swn(n: int) -> str:
    return f"{n:07d}"
