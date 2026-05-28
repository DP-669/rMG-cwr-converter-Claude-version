# ==============================================================================
# SWN MANAGER — GitHub as single source of truth
# Reads and writes swn_registry.json directly to/from GitHub repo.
# No Google Drive dependency. No session cache issues.
# Survives reboots, redeploys, and Drive being offline.
# ==============================================================================

import json
import urllib.request
import urllib.error
import base64
import streamlit as st
from datetime import datetime, timezone

REGISTRY_PATH = "rMG-cwr-converter/swn_registry.json"
BOOTSTRAP_LAST_SWN = 13734
BOOTSTRAP_SOURCE = "CW260006LUM_319.V22 - Vessel tracks 6-118"

BOOTSTRAP_REGISTRY = {
    "last_swn_used": BOOTSTRAP_LAST_SWN,
    "last_swn_source": BOOTSTRAP_SOURCE,
    "updated": "2026-05-18T00:00:00",
    "history": [
        {"file": "CW250010LUM_319.V22", "album": "redCola catalog",
         "swn_start": 1, "swn_end": 10011, "track_count": 10011,
         "generated_by": "Chris", "date": "2025-01-01T00:00:00"},
        {"file": "CW250011LUM_319.V22", "album": "EPP+SSC catalog",
         "swn_start": 10012, "swn_end": 13616, "track_count": 3605,
         "generated_by": "Chris", "date": "2025-01-01T00:00:00"},
        {"file": "CW260005LUM_319.V22", "album": "rC055 Vessel 5-track test",
         "swn_start": 13617, "swn_end": 13621, "track_count": 5,
         "generated_by": "rMG CWR Converter", "date": "2026-05-13T00:00:00"},
        {"file": "CW260006LUM_319.V22", "album": "rC055 Vessel tracks 6-118",
         "swn_start": 13622, "swn_end": 13734, "track_count": 113,
         "generated_by": "rMG CWR Converter", "date": "2026-05-18T00:00:00"}
    ]
}


class SWNError(Exception):
    pass


class SWNSyncMismatch(Exception):
    def __init__(self, local_val, drive_val):
        self.local_val = local_val
        self.drive_val = drive_val


def _github_headers(token):
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
    }


def _get_github_config(secrets):
    """Extract GitHub token and repo from Streamlit secrets."""
    gh = secrets.get("GITHUB", {}) if hasattr(secrets, 'get') else {}
    token = gh.get("token", "")
    repo  = gh.get("repo", "DP-669/rMG-cwr-converter-Claude-version")
    return token, repo


def _read_from_github(token, repo):
    """Read swn_registry.json from GitHub. Returns (content_dict, sha)."""
    url = f"https://api.github.com/repos/{repo}/contents/{REGISTRY_PATH}"
    req = urllib.request.Request(url, headers=_github_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            content = json.loads(base64.b64decode(data["content"]).decode("utf-8"))
            return content, data["sha"]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, None
        raise
    except Exception:
        return None, None


def _write_to_github(token, repo, registry, sha):
    """Write swn_registry.json to GitHub. sha required for updates."""
    url = f"https://api.github.com/repos/{repo}/contents/{REGISTRY_PATH}"
    content_b64 = base64.b64encode(
        json.dumps(registry, indent=2).encode("utf-8")
    ).decode("utf-8")
    payload = {
        "message": f"Update SWN registry - last used {registry['last_swn_used']}",
        "content": content_b64,
    }
    if sha:
        payload["sha"] = sha
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                  headers=_github_headers(token),
                                  method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as e:
        raise SWNError(f"GitHub write failed: {e}")


def load_registry(secrets):
    """
    Load SWN registry from GitHub.
    Falls back to bootstrap values if GitHub unavailable or token missing.
    """
    token, repo = _get_github_config(secrets)

    if not token:
        # No GitHub token - use bootstrap, warn clearly
        reg = dict(BOOTSTRAP_REGISTRY)
        reg["_github_available"] = False
        reg["_sync_warning"] = "No GitHub token in Secrets - using bootstrap values. Add [GITHUB] token."
        reg["_bootstrapped"] = True
        return reg

    content, sha = _read_from_github(token, repo)

    if content is None:
        # GitHub unreachable or file missing - use bootstrap
        reg = dict(BOOTSTRAP_REGISTRY)
        reg["_github_available"] = False
        reg["_sync_warning"] = "GitHub unreachable - using bootstrap values."
        reg["_bootstrapped"] = True
        reg["_sha"] = None
        return reg

    # Successfully read from GitHub
    content["_github_available"] = True
    content["_sync_warning"] = ""
    content["_bootstrapped"] = False
    content["_sha"] = sha
    content["_token"] = token
    content["_repo"] = repo
    return content


def get_next_swn(registry):
    """Return the next available SWN."""
    return int(registry.get("last_swn_used", BOOTSTRAP_LAST_SWN)) + 1


def get_next_sequence(registry):
    """Return the next suggested sequence number."""
    return int(registry.get("last_sequence_used", 7)) + 1


def format_swn(value):
    """Format SWN as 7-digit zero-padded string."""
    return f"{int(value):07d}"


def commit_swn_range(registry, swn_start, swn_end, track_count,
                     filename, album, secrets, sequence_number=None):
    """
    Update registry after successful generation.
    Writes back to GitHub immediately.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    updated = {
        "last_swn_used": swn_end,
        "last_swn_source": f"{filename} ({album})",
        "last_sequence_used": sequence_number if sequence_number else registry.get("last_sequence_used", 7),
        "updated": now,
        "history": list(registry.get("history", [])) + [{
            "file": filename,
            "album": album,
            "swn_start": swn_start,
            "swn_end": swn_end,
            "track_count": track_count,
            "generated_by": f"rMG CWR Converter",
            "date": now,
        }]
    }

    token = registry.get("_token", "")
    repo  = registry.get("_repo", "DP-669/rMG-cwr-converter-Claude-version")
    sha   = registry.get("_sha")

    if token:
        try:
            _write_to_github(token, repo, updated, sha)
            updated["_github_available"] = True
            updated["_sync_warning"] = ""
            # Refresh SHA after write
            _, new_sha = _read_from_github(token, repo)
            updated["_sha"] = new_sha
        except SWNError as e:
            updated["_github_available"] = False
            updated["_drive_write_error"] = str(e)
            updated["_sync_warning"] = f"GitHub write failed: {e}"
    else:
        updated["_github_available"] = False
        updated["_sync_warning"] = "No GitHub token - registry not persisted."

    updated["_token"] = token
    updated["_repo"] = repo
    updated["_bootstrapped"] = False
    return updated


def resolve_conflict(use_drive, local_val, drive_val, secrets):
    """Kept for API compatibility."""
    reg = dict(BOOTSTRAP_REGISTRY)
    reg["last_swn_used"] = drive_val if use_drive else local_val
    return load_registry(secrets)
