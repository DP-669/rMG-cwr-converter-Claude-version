# ==============================================================================
# SOURCEAUDIO API FETCHER
# rMG CWR Converter — Claude Version
#
# Fetches track and album metadata directly from the SourceAudio REST API.
# Returns data in the same normalised format as input_parser.py so the CWR
# engine can consume it without modification.
#
# ── CONFIGURATION (Streamlit Secrets) ─────────────────────────────────────────
#
#   [SOURCEAUDIO]
#   api_base_url  = "https://redcola.sourceaudio.net/api"   # your SA subdomain
#   api_token     = "your-api-token-here"
#   library_name  = "redCola"   # optional default library filter
#
# ── HOW TO GET YOUR API TOKEN ─────────────────────────────────────────────────
#   1. Log into your SourceAudio account
#   2. Go to Settings → Integrations → API
#   3. Generate or copy your token
#   4. Paste it into Streamlit Secrets under [SOURCEAUDIO] api_token
#
# ── BASE URL ──────────────────────────────────────────────────────────────────
#   SourceAudio deployments are subdomain-based.
#   Most common pattern: https://[library].sourceaudio.net/api
#   Confirm the exact URL with SourceAudio support if unsure.
#
# ── WHAT THIS FETCHER RETURNS ─────────────────────────────────────────────────
#   A list of track dicts identical to input_parser.parse_csv() output.
#
# ── SWN TRACKING ──────────────────────────────────────────────────────────────
#   fetch_swn_status() cross-references fetched tracks against the SWN
#   registry. Each track gets a '_swn_status' key:
#     'assigned'    — ISRC found in a prior CWR submission
#     'unassigned'  — not yet submitted
#     'unknown'     — no ISRC to match against
# ==============================================================================

import json
import urllib.request
import urllib.error
import urllib.parse
from typing import Optional


# ── EXCEPTIONS ────────────────────────────────────────────────────────────────

class SourceAudioError(Exception):
    """Raised on authentication failure, network error, or bad API response."""
    pass


class SourceAudioConfigError(SourceAudioError):
    """Raised when required configuration is missing from Streamlit Secrets."""
    pass


# ── PUBLIC API ─────────────────────────────────────────────────────────────────

def load_config(secrets: dict) -> dict:
    """
    Extract and validate SourceAudio config from Streamlit secrets.

    Returns:
        {'api_base_url': str, 'api_token': str, 'library_name': str}

    Raises:
        SourceAudioConfigError if api_base_url or api_token are missing.
    """
    sa_cfg   = dict(secrets.get("SOURCEAUDIO", {}))
    base_url = sa_cfg.get("api_base_url", "").rstrip("/")
    token    = sa_cfg.get("api_token", "")

    if not base_url:
        raise SourceAudioConfigError(
            "SOURCEAUDIO api_base_url is missing from Streamlit Secrets. "
            "Add [SOURCEAUDIO] api_base_url = \"https://[your-library].sourceaudio.net/api\" "
            "to your secrets.toml."
        )
    if not token:
        raise SourceAudioConfigError(
            "SOURCEAUDIO api_token is missing from Streamlit Secrets. "
            "Add [SOURCEAUDIO] api_token = \"your-token\" to your secrets.toml."
        )

    return {
        "api_base_url":  base_url,
        "api_token":     token,
        "library_name":  sa_cfg.get("library_name", ""),
    }


def check_config(secrets: dict) -> dict:
    """
    Non-throwing config check. Returns status dict for UI display.
    """
    try:
        cfg = load_config(secrets)
        return {
            "configured": True,
            "error":      None,
            "base_url":   cfg["api_base_url"],
            "library":    cfg["library_name"],
        }
    except SourceAudioConfigError as e:
        return {
            "configured": False,
            "error":      str(e),
            "base_url":   "",
            "library":    "",
        }


def fetch_albums(cfg: dict) -> list:
    """
    Fetch all albums from the SourceAudio library.

    Returns:
        List of album dicts: [{'id', 'code', 'title', 'track_count'}, ...]
    """
    raw = _paginated_get(cfg, "/albums")
    albums = []
    for item in raw:
        albums.append({
            "id":          str(item.get("id", "")),
            "code":        str(item.get("code", "") or item.get("album_code", "")),
            "title":       str(item.get("title", "") or item.get("display_title", "")),
            "track_count": int(item.get("track_count", 0)),
        })
    return sorted(albums, key=lambda a: a["code"])


def fetch_tracks_for_album(cfg: dict, album_id: str) -> tuple:
    """
    Fetch all tracks for a specific album.

    Returns:
        (tracks: list, warnings: list) — tracks in input_parser normalised format.
    """
    params = {"album_id": album_id}
    raw = _paginated_get(cfg, "/tracks", params=params)
    return _normalise_tracks(raw, cfg.get("library_name", ""))


def fetch_all_tracks(cfg: dict, library_filter: Optional[str] = None) -> tuple:
    """
    Fetch all tracks in the catalog (paginated).

    Returns:
        (tracks: list, warnings: list)
    """
    params = {}
    if library_filter:
        params["library"] = library_filter
    raw = _paginated_get(cfg, "/tracks", params=params)
    return _normalise_tracks(raw, cfg.get("library_name", library_filter or ""))


def fetch_tracks_by_isrc(cfg: dict, isrc_list: list) -> tuple:
    """
    Fetch specific tracks by ISRC.

    Returns:
        (tracks: list, warnings: list)
    """
    all_tracks   = []
    all_warnings = []
    for isrc in isrc_list:
        isrc_clean = isrc.replace("-", "").strip().upper()
        isrc_fmt   = _fmt_isrc_for_query(isrc_clean)
        try:
            raw = _paginated_get(cfg, "/tracks", params={"isrc": isrc_fmt})
            t, w = _normalise_tracks(raw, cfg.get("library_name", ""))
            all_tracks.extend(t)
            all_warnings.extend(w)
        except SourceAudioError as e:
            all_warnings.append(f"ISRC {isrc_clean}: {e}")
    return all_tracks, all_warnings


def fetch_swn_status(tracks: list, swn_registry: dict) -> list:
    """
    Cross-reference tracks against the SWN registry to flag which have already
    been CWR-submitted and which are new.

    Each track gets:
      '_swn_status'  'assigned' | 'unassigned' | 'unknown'
      '_swn_file'    filename where submitted (if assigned)
      '_swn_number'  SWN integer (if available in isrc_index)

    The registry's 'isrc_index' block (if present) maps ISRC -> SWN details.
    If not present, all tracks are returned as 'unassigned' — meaning they will
    receive new SWNs on the next generation run.
    """
    isrc_to_swn = {}
    for isrc, entry in swn_registry.get("isrc_index", {}).items():
        isrc_to_swn[isrc.replace("-", "").upper()] = entry

    result = []
    for track in tracks:
        t    = dict(track)
        isrc = str(t.get("isrc", "")).replace("-", "").upper()

        if not isrc or len(isrc) != 12:
            t["_swn_status"] = "unknown"
            t["_swn_file"]   = ""
        elif isrc in isrc_to_swn:
            entry = isrc_to_swn[isrc]
            t["_swn_status"] = "assigned"
            t["_swn_file"]   = entry.get("file", "")
            t["_swn_number"] = entry.get("swn", "")
        else:
            t["_swn_status"] = "unassigned"
            t["_swn_file"]   = ""
        result.append(t)

    return result


# ── HTTP LAYER ────────────────────────────────────────────────────────────────

def _make_request(cfg: dict, path: str, params: dict = None) -> dict:
    """Single HTTP GET. Returns parsed JSON response."""
    base_url = cfg["api_base_url"]
    token    = cfg["api_token"]

    url = f"{base_url}{path}"
    if params:
        url = url + "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept":        "application/json",
            "Content-Type":  "application/json",
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        if e.code == 401:
            raise SourceAudioError(
                "Authentication failed (HTTP 401). "
                "Check that api_token in Streamlit Secrets is correct and not expired."
            )
        if e.code == 403:
            raise SourceAudioError(
                "Access denied (HTTP 403). Your token may not have read permission."
            )
        if e.code == 404:
            raise SourceAudioError(
                f"Endpoint not found (HTTP 404): {url}. "
                "Check that api_base_url in Streamlit Secrets is correct."
            )
        if e.code == 429:
            raise SourceAudioError(
                "Rate limit exceeded (HTTP 429). Wait a moment and try again."
            )
        raise SourceAudioError(
            f"HTTP {e.code} from {url}. Response: {body[:300]}"
        )
    except urllib.error.URLError as e:
        raise SourceAudioError(
            f"Network error — {e.reason}. Check that api_base_url is reachable: {base_url}"
        )
    except json.JSONDecodeError as e:
        raise SourceAudioError(
            f"API response is not valid JSON. "
            f"Check api_base_url in Streamlit Secrets. Error: {e}"
        )


def _paginated_get(cfg: dict, path: str, params: dict = None, page_size: int = 100) -> list:
    """
    Fetch all pages from a paginated endpoint. Returns flat list of items.

    Handles two common response styles:
      1. Wrapped:  {'data': [...], 'meta': {'total_pages': N}}
      2. Bare list: [...]
    """
    if params is None:
        params = {}

    params          = dict(params)
    params["per_page"] = page_size
    params["page"]     = 1

    all_items = []
    max_pages = 200  # safety ceiling — 200 pages × 100/page = 20,000 tracks

    for page_num in range(1, max_pages + 1):
        params["page"] = page_num
        resp = _make_request(cfg, path, params)

        # Bare list response
        if isinstance(resp, list):
            all_items.extend(resp)
            break

        # Wrapped response
        if isinstance(resp, dict):
            items = (
                resp.get("data")
                or resp.get("tracks")
                or resp.get("albums")
                or resp.get("results")
                or []
            )
            if not isinstance(items, list):
                items = []
            all_items.extend(items)

            meta        = resp.get("meta", {})
            pagination  = resp.get("pagination", {})
            total_pages = (
                meta.get("total_pages")
                or meta.get("last_page")
                or pagination.get("total_pages")
            )

            if total_pages is not None:
                if page_num >= int(total_pages):
                    break
            else:
                if len(items) < page_size:
                    break
        else:
            break

    return all_items


# ── NORMALISATION ─────────────────────────────────────────────────────────────

def _normalise_tracks(raw_tracks: list, default_library: str = "") -> tuple:
    """Convert raw API track objects to input_parser normalised format."""
    tracks   = []
    warnings = []

    for idx, item in enumerate(raw_tracks):
        title = (
            item.get("title")
            or item.get("display_title")
            or item.get("track_title")
            or ""
        ).strip()

        if not title:
            warnings.append(f"Track at index {idx}: no title, skipping.")
            continue

        album_obj   = item.get("album") or {}
        album_code  = str(album_obj.get("code") or item.get("album_code") or "").strip()
        album_title = str(
            album_obj.get("title") or album_obj.get("display_title")
            or item.get("album_title") or album_code
        ).strip()

        lib_obj      = item.get("library") or {}
        library_name = str(
            lib_obj.get("name") or item.get("library_name")
            or item.get("library") or default_library
        ).strip()

        isrc = str(item.get("isrc") or item.get("code_isrc") or "").replace("-", "").upper()

        track = {
            "title":        title,
            "track_code":   str(
                item.get("number") or item.get("track_number") or item.get("id") or ""
            ),
            "isrc":         isrc,
            "iswc":         str(item.get("iswc") or item.get("code_iswc") or "").strip(),
            "album_code":   album_code,
            "album_title":  album_title,
            "library_name": library_name,
            "duration":     _parse_duration(item.get("duration") or item.get("length") or 0),
            "publishers":   _normalise_publishers(item),
            "writers":      _normalise_writers(item),
        }

        _validate_track(track, warnings)
        tracks.append(track)

    return tracks, warnings


def _normalise_publishers(item: dict) -> list:
    """Extract publisher data from a raw track object."""
    publishers = []

    raw_pubs = item.get("publishers") or item.get("publisher_list") or []
    if isinstance(raw_pubs, list):
        for p in raw_pubs:
            name = str(p.get("name") or p.get("publisher_name") or "").strip()
            if not name:
                continue
            publishers.append({
                "name":     name,
                "ipi":      str(p.get("ipi") or p.get("ipi_cae") or "").strip(),
                "pr_soc":   str(p.get("society") or p.get("pr_society") or "021").strip(),
                "mr_soc":   str(p.get("mr_society") or p.get("society") or "021").strip(),
                "pr_share": _safe_float(p.get("pr_share") or p.get("performance_share") or 0),
                "mr_share": _safe_float(p.get("mr_share") or p.get("mechanical_share") or 0),
                "sr_share": 0.0,
            })
    elif isinstance(raw_pubs, dict):
        name = str(raw_pubs.get("name") or raw_pubs.get("publisher_name") or "").strip()
        if name:
            publishers.append({
                "name": name, "ipi": "", "pr_soc": "021",
                "mr_soc": "021",
                "pr_share": _safe_float(raw_pubs.get("pr_share") or 0),
                "mr_share": _safe_float(raw_pubs.get("mr_share") or 0),
                "sr_share": 0.0,
            })

    # Fallback: numbered flat fields matching CSV convention
    if not publishers:
        for n in range(1, 6):
            name = str(
                item.get(f"publisher_{n}_name") or item.get(f"publisher{n}_name") or ""
            ).strip()
            if not name:
                break
            publishers.append({
                "name":     name,
                "ipi":      str(item.get(f"publisher_{n}_ipi") or "").strip(),
                "pr_soc":   str(item.get(f"publisher_{n}_society") or "021").strip(),
                "mr_soc":   "021",
                "pr_share": _safe_float(item.get(f"publisher_{n}_pr_share") or 0),
                "mr_share": _safe_float(item.get(f"publisher_{n}_mr_share") or 0),
                "sr_share": 0.0,
            })

    return publishers


def _normalise_writers(item: dict) -> list:
    """Extract writer data from a raw track object."""
    writers = []

    raw_writers = (
        item.get("writers") or item.get("writer_list")
        or item.get("composers") or []
    )

    if isinstance(raw_writers, list):
        for w in raw_writers:
            last = str(w.get("last_name") or w.get("surname") or w.get("name") or "").strip()
            if not last:
                continue
            writers.append({
                "last_name":          last,
                "first_name":         str(w.get("first_name") or w.get("given_name") or "").strip(),
                "ipi":                str(w.get("ipi") or w.get("ipi_cae") or "").strip(),
                "pr_soc":             str(w.get("society") or w.get("pr_society") or "021").strip(),
                "mr_soc":             "099",
                "sr_soc":             "099",
                "pr_share":           _safe_float(w.get("pr_share") or w.get("performance_share") or 0),
                "original_publisher": str(w.get("publisher") or w.get("original_publisher") or "").strip(),
            })
    elif isinstance(raw_writers, dict):
        last = str(raw_writers.get("last_name") or raw_writers.get("surname") or "").strip()
        if last:
            writers.append({
                "last_name": last, "first_name": "", "ipi": "",
                "pr_soc": "021", "mr_soc": "099", "sr_soc": "099",
                "pr_share": 0.0, "original_publisher": "",
            })

    # Fallback: numbered flat fields
    if not writers:
        for n in range(1, 6):
            last = str(
                item.get(f"writer_{n}_last_name") or item.get(f"writer{n}_last") or ""
            ).strip()
            if not last:
                break
            writers.append({
                "last_name":          last,
                "first_name":         str(item.get(f"writer_{n}_first_name") or "").strip(),
                "ipi":                str(item.get(f"writer_{n}_ipi") or "").strip(),
                "pr_soc":             str(item.get(f"writer_{n}_society") or "021").strip(),
                "mr_soc":             "099",
                "sr_soc":             "099",
                "pr_share":           _safe_float(item.get(f"writer_{n}_pr_share") or 0),
                "original_publisher": str(item.get(f"writer_{n}_publisher") or "").strip(),
            })

    return writers


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _parse_duration(value) -> int:
    """Convert duration to integer seconds. Accepts int, float, or MM:SS/HH:MM:SS string."""
    if not value:
        return 0
    s = str(value).strip()
    if ":" in s:
        parts = s.split(":")
        try:
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            elif len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        except ValueError:
            return 0
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return 0


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _fmt_isrc_for_query(isrc: str) -> str:
    """Add dashes to a bare 12-char ISRC for API query."""
    isrc = isrc.replace("-", "").upper()
    if len(isrc) == 12:
        return f"{isrc[:2]}-{isrc[2:5]}-{isrc[5:7]}-{isrc[7:]}"
    return isrc


def _validate_track(track: dict, warnings: list):
    """Non-fatal validation — appends warnings, does not raise."""
    title = track.get("title", "Unknown")
    if not track.get("isrc"):
        warnings.append(f"Track '{title}': no ISRC in API response.")
    elif len(track["isrc"]) != 12:
        warnings.append(f"Track '{title}': ISRC '{track['isrc']}' is not 12 chars.")
    if not track.get("album_code"):
        warnings.append(f"Track '{title}': no album code in API response.")
    if not track.get("publishers"):
        warnings.append(f"Track '{title}': no publishers in API response.")
    if not track.get("writers"):
        warnings.append(f"Track '{title}': no writers in API response.")
