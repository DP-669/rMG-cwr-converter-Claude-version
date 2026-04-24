# ==============================================================================
# SWN MANAGER — Submitter Work Number Registry
#
# Google Drive is the master. Streamlit local file is the cache.
# Sync check runs before every generation.
# No human ever sets a SWN manually.
#
# Authentication: Google Service Account
# Credentials stored in Streamlit Secrets under [SWN]
# ==============================================================================

import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone


LOCAL_REGISTRY_PATH = "swn_registry.json"

BOOTSTRAP_REGISTRY = {
    "last_swn_used": 13616,
    "last_swn_source": "Chris — CW250011 (NO SURVIVORS VOCAL IMPACT 4)",
    "updated": "2025-01-01T00:00:00",
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
        }
    ]
}


class SWNError(Exception):
    pass


class SWNSyncMismatch(Exception):
    def __init__(self, local_val, drive_val):
        self.local_val = local_val
        self.drive_val = drive_val
        super().__init__(
            f"SWN registry mismatch: local={local_val}, Google Drive={drive_val}"
        )


def _get_access_token(secrets: dict) -> str:
    import base64
    swn_cfg = secrets.get("SWN", {})
    private_key_raw = swn_cfg.get("private_key", "")
    client_email    = swn_cfg.get("client_email", "")

    if not private_key_raw or not client_email:
        raise SWNError(
            "Service account credentials missing from Streamlit Secrets. "
            "Add [SWN] section with client_email and private_key."
        )

    now = int(time.time())
    header  = {"alg": "RS256", "typ": "JWT"}
    payload = {
        "iss":   client_email,
        "scope": "https://www.googleapis.com/auth/drive.file",
        "aud":   "https://oauth2.googleapis.com/token",
        "iat":   now,
        "exp":   now + 3600,
    }

    def b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")

    header_b64  = b64url(json.dumps(header).encode())
    payload_b64 = b64url(json.dumps(payload).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")

    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        pem = private_key_raw.replace("\\n", "\n").encode("utf-8")
        private_key = serialization.load_pem_private_key(pem, password=None)
        signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    except ImportError:
        raise SWNError(
            "The 'cryptography' package is required. Add it to requirements.txt."
        )

    jwt_token = f"{header_b64}.{payload_b64}.{b64url(signature)}"

    body = (
        f"grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer"
        f"&assertion={jwt_token}"
    ).encode("utf-8")

    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["access_token"]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        raise SWNError(f"Token exchange failed (HTTP {e.code}): {body}")
    except Exception as e:
        raise SWNError(f"Token exchange failed: {e}")


def _read_from_drive(token: str, file_id: str) -> dict:
    url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise SWNError(f"Google Drive read failed (HTTP {e.code}): {e.reason}")
    except Exception as e:
        raise SWNError(f"Google Drive read failed: {e}")


def _write_to_drive(token: str, file_id: str, registry: dict) -> None:
    content  = json.dumps(registry, indent=2).encode("utf-8")
    boundary = "rMG_SWN_BOUNDARY_XYZ"
    meta     = json.dumps({"mimeType": "application/json"}).encode("utf-8")
    body = (
        f"--{boundary}\r\n"
        f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
    ).encode() + meta + (
        f"\r\n--{boundary}\r\n"
        f"Content-Type: application/json\r\n\r\n"
    ).encode() + content + (
        f"\r\n--{boundary}--"
    ).encode()

    url = (
        f"https://www.googleapis.com/upload/drive/v3/files/{file_id}"
        f"?uploadType=multipart"
    )
    req = urllib.request.Request(
        url, data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  f"multipart/related; boundary={boundary}",
        },
        method="PATCH"
    )
    try:
        with urllib.request.urlopen(req) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        raise SWNError(f"Google Drive write failed (HTTP {e.code}): {e.reason}")
    except Exception as e:
        raise SWNError(f"Google Drive write failed: {e}")


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


def load_registry(secrets: dict) -> dict:
    swn_cfg  = secrets.get("SWN", {})
    file_id  = swn_cfg.get("gdrive_file_id", "")
    token        = None
    drive_error  = None
    drive_registry = None

    if file_id and swn_cfg.get("client_email"):
        try:
            token = _get_access_token(secrets)
        except SWNError as e:
            drive_error = str(e)

    if token and file_id:
        try:
            drive_registry = _read_from_drive(token, file_id)
        except SWNError as e:
            drive_error = str(e)

    local_registry = _read_local()

    if drive_registry is None and local_registry is None:
        registry = dict(BOOTSTRAP_REGISTRY)
        registry["updated"] = datetime.now(timezone.utc).isoformat()
        _write_local(registry)
        if token and file_id:
            try:
                _write_to_drive(token, file_id, registry)
            except SWNError:
                pass
        registry["_drive_available"] = bool(token and file_id and not drive_error)
        registry["_drive_error"]     = drive_error
        registry["_bootstrapped"]    = True
        return registry

    if drive_registry is None and local_registry is not None:
        local_registry["_drive_available"] = False
        local_registry["_drive_error"]     = drive_error
        local_registry["_sync_warning"] = (
            "⚠️ Google Drive unavailable. Using local cache. "
            "Generation allowed but Drive backup is offline."
        )
        return local_registry

    if drive_registry is not None and local_registry is None:
        _write_local(drive_registry)
        drive_registry["_drive_available"] = True
        drive_registry["_drive_error"]     = None
        return drive_registry

    d_swn = drive_registry.get("last_swn_used", -1)
    l_swn = local_registry.get("last_swn_used", -2)

    if d_swn != l_swn:
        raise SWNSyncMismatch(local_val=l_swn, drive_val=d_swn)

    _write_local(drive_registry)
    drive_registry["_drive_available"] = True
    drive_registry["_drive_error"]     = None
    drive_registry["_token"]           = token
    return drive_registry


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
        "file":        filename,
        "album":       album,
        "swn_start":   swn_start,
        "swn_end":     swn_end,
        "track_count": track_count,
        "generated_by": "rMG Converter",
        "date":        now
    })

    clean = {k: v for k, v in registry.items() if not k.startswith("_")}
    _write_local(clean)

    token   = registry.get("_token")
    file_id = secrets.get("SWN", {}).get("gdrive_file_id", "")

    if not token:
        try:
            token = _get_access_token(secrets)
        except SWNError as e:
            registry["_drive_write_error"] = str(e)
            return registry

    if token and file_id:
        try:
            _write_to_drive(token, file_id, clean)
        except SWNError as e:
            registry["_drive_write_error"] = str(e)

    return registry


def resolve_conflict(
    use_drive: bool,
    local_val: int,
    drive_val: int,
    secrets: dict
) -> dict:
    chosen = drive_val if use_drive else local_val
    source = "Google Drive (manual resolution)" if use_drive else "Local cache (manual resolution)"
    file_id = secrets.get("SWN", {}).get("gdrive_file_id", "")

    try:
        token = _get_access_token(secrets)
    except SWNError:
        token = None

    if use_drive and token and file_id:
        try:
            registry = _read_from_drive(token, file_id)
        except SWNError:
            registry = _read_local() or dict(BOOTSTRAP_REGISTRY)
    else:
        registry = _read_local() or dict(BOOTSTRAP_REGISTRY)

    registry["last_swn_used"]   = chosen
    registry["last_swn_source"] = source
    registry["updated"]         = datetime.now(timezone.utc).isoformat()

    clean = {k: v for k, v in registry.items() if not k.startswith("_")}
    _write_local(clean)
    if token and file_id:
        try:
            _write_to_drive(token, file_id, clean)
        except SWNError:
            pass

    clean["_drive_available"] = bool(token and file_id)
    return clean


def format_swn(n: int) -> str:
    return f"{n:07d}"
