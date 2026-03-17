# ==============================================================================
# INPUT PARSER
# Auto-detects SourceAudio or Harvest Media CSV format.
# Normalises both into a standard list of track dicts for the CWR engine.
#
# OUTPUT FORMAT (per track):
# {
#   'title':           str,
#   'track_code':      str,
#   'isrc':            str,
#   'iswc':            str,
#   'album_code':      str,
#   'album_title':     str,
#   'library_name':    str,
#   'duration':        int (seconds),
#   'publishers': [
#     {'name': str, 'ipi': str, 'pr_soc': str, 'mr_soc': str,
#      'pr_share': float, 'mr_share': float, 'sr_share': float}
#   ],
#   'writers': [
#     {'last_name': str, 'first_name': str, 'ipi': str,
#      'pr_soc': str, 'mr_soc': str, 'sr_soc': str,
#      'pr_share': float, 'original_publisher': str}
#   ]
# }
# ==============================================================================

import pandas as pd
import io
import re


class ParseError(Exception):
    pass


# --------------------------------------------------
# FORMAT DETECTION
# --------------------------------------------------

def detect_format(df: pd.DataFrame) -> str:
    """
    Returns 'sourceaudio', 'harvest', or raises ParseError.
    SourceAudio: single row per track, columns like 'TRACK: Title', 'WRITER 1: Last Name'
    Harvest:     multi-row per track, columns like 'Track Title', 'Surname'
    """
    cols_upper = [str(c).upper().strip() for c in df.columns]
    cols_str   = ' '.join(cols_upper)

    if 'TRACK: TITLE' in cols_upper or 'WRITER 1: LAST NAME' in cols_upper:
        return 'sourceaudio'

    if 'TRACK TITLE' in cols_upper or 'SURNAME' in cols_upper:
        return 'harvest'

    # Fallback checks
    if 'CODE: ISRC' in cols_upper:
        return 'sourceaudio'
    if 'ISRC CODE' in cols_upper:
        return 'harvest'

    raise ParseError(
        f"Cannot detect CSV format. Expected SourceAudio or Harvest Media column names. "
        f"Found columns: {', '.join(list(df.columns)[:10])}..."
    )


# --------------------------------------------------
# MAIN ENTRY POINT
# --------------------------------------------------

def parse_csv(file_content: bytes, filename: str = "") -> tuple:
    """
    Parse a CSV file into a list of normalised track dicts.

    Returns:
        (tracks: list, format_detected: str, warnings: list)
    """
    warnings = []

    # Try UTF-8-sig first (handles BOM), then latin-1
    for encoding in ('utf-8-sig', 'latin-1'):
        try:
            df = pd.read_csv(io.BytesIO(file_content), encoding=encoding)
            break
        except Exception:
            continue
    else:
        raise ParseError("Could not read CSV — try saving as UTF-8 or Latin-1.")

    # Normalise column names for detection
    df.columns = [str(c).strip() for c in df.columns]

    fmt = detect_format(df)

    if fmt == 'sourceaudio':
        tracks, w = _parse_sourceaudio(df)
    else:
        tracks, w = _parse_harvest(df)

    warnings.extend(w)
    return tracks, fmt, warnings


# --------------------------------------------------
# SOURCE AUDIO PARSER
# Single row per track. Writers/publishers in numbered columns.
# --------------------------------------------------

def _parse_sourceaudio(df: pd.DataFrame) -> tuple:
    """Parse SourceAudio export format."""
    warnings = []
    tracks = []

    # Normalise columns
    col_map = {str(c).strip().upper(): str(c).strip() for c in df.columns}
    df.columns = [str(c).strip().upper() for c in df.columns]

    def get(row, *keys, default=""):
        for k in keys:
            k_upper = k.upper()
            if k_upper in row.index:
                v = row[k_upper]
                if pd.notna(v) and str(v).strip().upper() not in ('NAN', 'NONE', ''):
                    return str(v).strip()
        return default

    for idx, row in df.iterrows():
        title = get(row, 'TRACK: TITLE', 'TRACK: DISPLAY TITLE')
        if not title:
            warnings.append(f"Row {idx+2}: no title found, skipping.")
            continue

        # Duration: SourceAudio stores seconds as integer
        dur_raw = get(row, 'TRACK: DURATION')
        duration = _parse_duration(dur_raw)

        track = {
            'title':        title,
            'track_code':   get(row, 'TRACK: NUMBER'),  # sequential number only, never Identity hash
            'isrc':         get(row, 'CODE: ISRC').replace('-',''),
            'iswc':         get(row, 'CODE: ISWC'),
            'album_code':   get(row, 'ALBUM: CODE'),
            'album_title':  get(row, 'ALBUM: TITLE', 'ALBUM: DISPLAY TITLE'),
            'library_name': get(row, 'LIBRARY: NAME'),
            'duration':     duration,
            'publishers':   [],
            'writers':      [],
        }

        # Publishers — up to 5 slots
        # Handles both SourceAudio export variants:
        #   'PUBLISHER 1: NAME'  (space before colon)
        #   'PUBLISHER:1: NAME'  (colon after number)
        for n in range(1, 6):
            name = get(row,
                f'PUBLISHER {n}: NAME', f'PUBLISHER:{n}: NAME',
                f'PUBLISHER {n}:NAME',  f'PUBLISHER:{n}:NAME')
            if not name:
                break
            track['publishers'].append({
                'name':     name,
                'ipi':      get(row, f'PUBLISHER {n}: IPI',    f'PUBLISHER:{n}: IPI'),
                'pr_soc':   _soc_code(get(row,
                                f'PUBLISHER {n}: SOCIETY',     f'PUBLISHER:{n}: SOCIETY')),
                'mr_soc':   _soc_code(get(row,
                                f'PUBLISHER {n}: SOCIETY',     f'PUBLISHER:{n}: SOCIETY')),
                'pr_share': _safe_float(get(row,
                                f'PUBLISHER {n}: OWNER PERFORMANCE SHARE %',
                                f'PUBLISHER:{n}: OWNER PERFORMANCE SHARE %',
                                f'PUBLISHER {n}: COLLECTION PERFORMANCE SHARE %',
                                f'PUBLISHER:{n}: COLLECTION PERFORMANCE SHARE %')),
                'mr_share': _safe_float(get(row,
                                f'PUBLISHER {n}: OWNER MECHANICAL SHARE %',
                                f'PUBLISHER:{n}: OWNER MECHANICAL SHARE %',
                                f'PUBLISHER {n}: COLLECTION MECHANICAL SHARE %',
                                f'PUBLISHER:{n}: COLLECTION MECHANICAL SHARE %')),
                'sr_share': 0.0,
            })

        # Writers — up to 5 slots
        # Handles both variants: 'WRITER 1: LAST NAME' and 'WRITER:1: LAST NAME'
        for n in range(1, 6):
            last = get(row, f'WRITER {n}: LAST NAME', f'WRITER:{n}: LAST NAME')
            if not last:
                break
            track['writers'].append({
                'last_name':          last,
                'first_name':         get(row, f'WRITER {n}: FIRST NAME',  f'WRITER:{n}: FIRST NAME'),
                'ipi':                get(row, f'WRITER {n}: IPI',          f'WRITER:{n}: IPI'),
                'pr_soc':             _soc_code(get(row,
                                        f'WRITER {n}: SOCIETY',             f'WRITER:{n}: SOCIETY')),
                'mr_soc':             '099',
                'sr_soc':             '099',
                'pr_share':           _safe_float(get(row,
                                        f'WRITER {n}: OWNER PERFORMANCE SHARE %',
                                        f'WRITER:{n}: OWNER PERFORMANCE SHARE %',
                                        f'WRITER {n}: COLLECTION PERFORMANCE SHARE %',
                                        f'WRITER:{n}: COLLECTION PERFORMANCE SHARE %')),
                'original_publisher': get(row,
                                        f'WRITER {n}: ORIGINAL PUBLISHER',
                                        f'WRITER:{n}: ORIGINAL PUBLISHER'),
            })

        _validate_track(track, warnings)
        tracks.append(track)

    return tracks, warnings


# --------------------------------------------------
# HARVEST MEDIA PARSER
# Multi-row per track. One row per writer/publisher combo.
# Groups rows by track code or ISRC.
# --------------------------------------------------

def _parse_harvest(df: pd.DataFrame) -> tuple:
    """Parse Harvest Media export format."""
    warnings = []
    tracks = []

    df.columns = [str(c).strip().upper() for c in df.columns]

    def get(row, *keys, default=""):
        for k in keys:
            k_upper = k.upper()
            if k_upper in row.index:
                v = row[k_upper]
                if pd.notna(v) and str(v).strip().upper() not in ('NAN', 'NONE', ''):
                    return str(v).strip()
        return default

    # Group by Track Code (primary) or Track Title + ISRC (fallback)
    if 'TRACK CODE' in df.columns:
        group_key = 'TRACK CODE'
    elif 'ISRC CODE' in df.columns:
        group_key = 'ISRC CODE'
    else:
        group_key = 'TRACK TITLE'

    seen_tracks = []
    current_group = []
    current_key = None

    def flush_group(group_rows, warnings):
        if not group_rows:
            return None
        first = group_rows[0]

        title = get(first, 'TRACK TITLE', 'TITLE')
        if not title:
            warnings.append(f"Group with no title, skipping.")
            return None

        track = {
            'title':        title,
            'track_code':   get(first, 'TRACK CODE', 'DOWNLOAD ID'),
            'isrc':         get(first, 'ISRC CODE').replace('-',''),
            'iswc':         get(first, 'ISWC CODE'),
            'album_code':   get(first, 'LIBRARY CODE', 'ALBUM CODE'),
            'album_title':  get(first, 'ALBUM TITLE', 'LIBRARY NAME'),
            'library_name': get(first, 'LIBRARY NAME', 'ARTIST NAME'),
            'duration':     0,
            'publishers':   [],
            'writers':      [],
        }

        # Each row = one publisher deal. Writers may appear on multiple rows
        # (one per publisher). Deduplicate writers by last+first name.
        seen_pub_names  = []
        seen_writer_keys = []

        for row in group_rows:
            last = get(row, 'SURNAME', 'LAST NAME')
            if not last:
                continue

            # Writer — add only once per unique name
            writer_key = (last.upper(), get(row, 'FIRST NAME').upper())
            if writer_key not in seen_writer_keys:
                pr_share = _safe_float(get(row, 'WRITER SHARE'))
                track['writers'].append({
                    'last_name':          last,
                    'first_name':         get(row, 'FIRST NAME'),
                    'ipi':                get(row, 'IPI/CAE NUMBER', 'IPI NUMBER'),
                    'pr_soc':             _soc_code(get(row, 'WRITER AGREEMENT', 'PRO')),
                    'mr_soc':             '099',
                    'sr_soc':             '099',
                    'pr_share':           pr_share,
                    'original_publisher': get(row, 'PUBLISHER NAME', 'AGREEMENT DESCRIPTION'),
                })
                seen_writer_keys.append(writer_key)

            # Publisher — add each unique publisher
            pub_name = get(row, 'PUBLISHER NAME', 'AGREEMENT DESCRIPTION')
            if pub_name and pub_name.upper() not in [p.upper() for p in seen_pub_names]:
                pr_share_w = _safe_float(get(row, 'WRITER SHARE'))
                pub_ipi    = get(row, 'PUBLISHER IPI')
                pub_share  = _safe_float(get(row, 'PUBLISHER SHARE', default='0'))
                track['publishers'].append({
                    'name':     pub_name,
                    'ipi':      pub_ipi,
                    'pr_soc':   '021',
                    'mr_soc':   '021',
                    'pr_share': pr_share_w,
                    'mr_share': pub_share,
                    'sr_share': 0.0,
                })
                seen_pub_names.append(pub_name)

        return track

    # Walk rows, group by key
    for idx, row in df.iterrows():
        key = get(row, group_key)
        if key != current_key:
            if current_group:
                t = flush_group(current_group, warnings)
                if t:
                    _validate_track(t, warnings)
                    tracks.append(t)
            current_group = [row]
            current_key = key
        else:
            current_group.append(row)

    # Flush last group
    if current_group:
        t = flush_group(current_group, warnings)
        if t:
            _validate_track(t, warnings)
            tracks.append(t)

    return tracks, warnings


# --------------------------------------------------
# HELPERS
# --------------------------------------------------

SOCIETY_CODES = {
    'BMI':   '021',
    'ASCAP': '021',
    'PRS':   '052',
    'MCPS':  '033',
    'GEMA':  '035',
    'SESAC': '055',
    'SOCAN': '022',
    'APRA':  '065',
    'SACEM': '058',
    # Harvest uses agreement short codes like 'EPP', 'RC', 'SSC'
    # These are not society codes — they'll default to BMI (021)
}

def _soc_code(value: str) -> str:
    """Convert society name or code to 3-digit numeric code."""
    if not value:
        return '021'
    v = value.strip().upper()
    # Already numeric
    if re.match(r'^\d{1,3}$', v):
        return v.zfill(3)
    # Known name
    return SOCIETY_CODES.get(v, '021')


def _safe_float(value, default=0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _parse_duration(value) -> int:
    """Parse duration to seconds. Accepts seconds int, MM:SS, or HH:MM:SS."""
    if not value or str(value).strip() in ('', 'nan', 'NaN'):
        return 0
    s = str(value).strip()
    if ':' in s:
        parts = s.split(':')
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


def _validate_track(track: dict, warnings: list):
    """Non-fatal validation — appends warnings, does not raise."""
    title = track.get('title', 'Unknown')

    if not track.get('isrc'):
        warnings.append(f"Track '{title}': no ISRC found.")
    elif len(track['isrc']) != 12:
        warnings.append(f"Track '{title}': ISRC '{track['isrc']}' is not 12 characters.")

    if not track.get('publishers'):
        warnings.append(f"Track '{title}': no publishers found.")

    if not track.get('writers'):
        warnings.append(f"Track '{title}': no writers found.")

    total_pr = sum(w.get('pr_share', 0) for w in track.get('writers', []))
    if track.get('writers') and abs(total_pr - 100.0) > 0.5:
        warnings.append(f"Track '{title}': writer PR shares sum to {total_pr:.2f}% (expected 100%).")
