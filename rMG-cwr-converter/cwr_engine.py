# ==============================================================================
# CWR ENGINE — CANVAS STAMPER
# Every record is a fixed-length canvas of spaces.
# Fields are stamped into exact byte positions. Nothing shifts.
# If a value is too long: HALT. Never truncate silently.
# If a value is too short: pad (spaces for alpha, zeros for numeric).
# ==============================================================================

from datetime import datetime
from cwr_schema import SCHEMA


class CWREngineError(Exception):
    pass


class Canvas:
    """Fixed-length character canvas. Stamp fields into exact positions."""

    def __init__(self, length: int):
        self._buf = [' '] * length
        self._length = length

    def stamp(self, start: int, length: int, value: str, fmt: str, field_name: str, context: str = ""):
        """Stamp a value into the canvas at exact 1-based position."""
        idx = start - 1  # convert to 0-based

        # Normalise
        val = str(value).strip().upper() if value is not None else ""
        if val in ('NAN', 'NONE', 'NAT'):
            val = ""

        # Validate length — hard stop, never silently truncate
        if len(val) > length:
            raise CWREngineError(
                f"FIELD TOO LONG: {field_name}{' (' + context + ')' if context else ''} — "
                f"value '{val}' is {len(val)} chars, max is {length}."
            )

        # Pad
        if fmt == 'N':
            padded = val.zfill(length)
        else:
            padded = val.ljust(length)

        # Write into canvas
        for i, ch in enumerate(padded):
            self._buf[idx + i] = ch

    def render(self) -> str:
        result = ''.join(self._buf)
        if len(result) != self._length:
            raise CWREngineError(f"Canvas length mismatch: expected {self._length}, got {len(result)}")
        return result


def build_record(record_type: str, data: dict, context: str = "") -> str:
    """Build one CWR record from a data dict. Returns fixed-length string."""
    rec_def = SCHEMA.get(record_type)
    if not rec_def:
        raise CWREngineError(f"Unknown record type: {record_type}")

    canvas = Canvas(rec_def.total_length)

    for fld in rec_def.fields:
        value = fld.constant if fld.constant is not None else data.get(fld.name, "")
        canvas.stamp(fld.start, fld.length, value, fld.fmt, fld.name, context)

    line = canvas.render()

    # Final length assertion — belt and braces
    if len(line) != rec_def.total_length:
        raise CWREngineError(
            f"RECORD LENGTH FAIL: {record_type} rendered to {len(line)}, expected {rec_def.total_length}"
        )

    return line


# ==============================================================================
# SHARE FORMATTING
# Shares in CWR are stored as implied 2-decimal integers: 50.00% = 05000
# Writers own PR only. Publishers collect both PR and MR.
# ==============================================================================

def fmt_share(value, context="") -> str:
    """Convert a percentage float/string to 5-digit CWR share format."""
    try:
        f = float(value)
        return f"{int(round(f * 100)):05d}"
    except (TypeError, ValueError):
        return "00000"


def pad_ipi(value) -> str:
    """Normalise IPI to 11-digit zero-padded string."""
    import re
    s = re.sub(r'\D', '', str(value)) if value and str(value).upper() not in ('NAN', 'NONE', '') else ''
    return s.zfill(11) if s else '00000000000'


def fmt_duration(value) -> str:
    """Convert duration in seconds to HHMMSS, or return 000000."""
    try:
        secs = int(float(value))
        h = secs // 3600
        m = (secs % 3600) // 60
        s = secs % 60
        return f"{h:02d}{m:02d}{s:02d}"
    except (TypeError, ValueError):
        return "000000"


# ==============================================================================
# MAIN GENERATOR
# ==============================================================================

def generate_cwr(tracks: list, catalog_config: dict, agreement_map: dict,
                 sequence_number: int = 1) -> tuple:
    """
    Generate a complete CWR V2.2 file.

    Args:
        tracks: List of normalised track dicts (output of input_parser.py)
        catalog_config: Dict with publisher/IPI/territory for this catalog
        agreement_map: Dict mapping original publisher name -> agreement number
        sequence_number: CWR file sequence number (NNNN in filename)

    Returns:
        (cwr_content: str, warnings: list, filename: str)
    """
    if not tracks:
        raise CWREngineError("No tracks provided.")

    warnings = []
    lines = []
    now = datetime.utcnow()

    lumina_name = catalog_config['lumina_name']
    lumina_ipi  = catalog_config['lumina_ipi']        # full 11-digit
    lumina_id   = catalog_config['lumina_pub_id']     # 9-char internal ID e.g. '000000012'
    lumina_pr_soc = catalog_config.get('lumina_pr_soc', '052')  # PRS
    lumina_mr_soc = catalog_config.get('lumina_mr_soc', '033')  # MCPS
    territory   = catalog_config.get('territory', '0826')        # UK
    software_tag = catalog_config.get('software_tag', 'rMG-CWR')
    submitter_code = catalog_config.get('submitter_code', 'LUM_319')

    # ---- HDR ----
    lines.append(build_record("HDR", {
        "sender_id":         lumina_ipi[-9:],   # 9-digit IPI
        "sender_name":       lumina_name,
        "creation_date":     now.strftime("%Y%m%d"),
        "creation_time":     now.strftime("%H%M%S"),
        "transmission_date": now.strftime("%Y%m%d"),
        "character_set":     "",
        "software_package":  software_tag,
    }))

    # ---- GRH ----
    lines.append(build_record("GRH", {}))

    total_records_in_group = 0  # counts everything between GRH and GRT

    # ---- WORK TRANSACTIONS ----
    for t_idx, track in enumerate(tracks):
        t_seq = f"{t_idx:08d}"
        context = track.get('title', f'Track {t_idx+1}')
        rec_seq = 1   # record sequence within this transaction

        # Validate required fields
        for req in ('title', 'isrc', 'album_code', 'library_name'):
            if not track.get(req):
                raise CWREngineError(f"Track {t_idx+1}: missing required field '{req}'")

        title = track['title']
        if len(title) > 60:
            raise CWREngineError(f"Track '{title}': title exceeds 60 characters ({len(title)}). Must be shortened.")

        isrc = track['isrc'].replace('-', '').strip()
        if len(isrc) != 12:
            raise CWREngineError(f"Track '{title}': ISRC '{isrc}' must be exactly 12 characters.")

        # ---- NWR ----
        # submitter_work_id: simple sequential integer zero-padded to 7 digits.
        # Matches Chris's approved format (0000001, 0000002, etc.)
        # Do NOT use TRACK: Identity — that's a SourceAudio hex hash, not a CWR work number.
        submitter_work_id = f"{t_idx+1:07d}"
        lines.append(build_record("NWR", {
            "t_seq":             t_seq,
            "title":             title,
            "submitter_work_id": submitter_work_id,
            "iswc":              track.get('iswc', ''),
            "copyright_date":    "00000000",
            "duration":          fmt_duration(track.get('duration', 0)),
        }, context=context))
        total_records_in_group += 1

        # ---- PUBLISHER CHAIN ----
        publishers = track.get('publishers', [])
        if not publishers:
            raise CWREngineError(f"Track '{title}': no publishers found.")

        for p_idx, pub in enumerate(publishers, start=1):
            pub_name = pub['name'].strip().upper()
            if not pub_name:
                continue

            # Look up agreement number
            agr_num = _lookup_agreement(pub_name, agreement_map)
            if not agr_num:
                raise CWREngineError(
                    f"Track '{title}': no agreement number found for publisher '{pub_name}'. "
                    f"Add it to the agreement map."
                )

            pub_ipi  = pad_ipi(pub.get('ipi', ''))
            pub_pr_soc = _fmt_soc(pub.get('pr_soc', '021'))
            pub_mr_soc = _fmt_soc(pub.get('mr_soc', '021'))
            pr_share = fmt_share(pub.get('pr_share', 0))
            mr_share = fmt_share(pub.get('mr_share', 0))
            sr_share = fmt_share(pub.get('sr_share', 0))

            chain_id = f"{p_idx:02d}"
            pub_internal_id = f"0000000{p_idx:02d}"[:9]

            # SPU — Original Publisher (E)
            # SR share = 10000 for Original Publisher (confirmed from Chris's approved files)
            lines.append(build_record("SPU", {
                "t_seq":       t_seq,
                "rec_seq":     f"{rec_seq:08d}",
                "chain_id":    chain_id,
                "pub_id":      pub_internal_id,
                "pub_name":    pub_name[:45],
                "pub_type":    "E ",
                "ipi_name":    pub_ipi,
                "pr_soc":      pub_pr_soc,
                "pr_share":    pr_share,
                "mr_soc":      pub_mr_soc,
                "mr_share":    mr_share,
                "sr_soc":      "",
                "sr_share":    "10000",
                "soc_agr_num": str(agr_num)[:14],
            }, context=context))
            rec_seq += 1
            total_records_in_group += 1

            # SPU — Lumina as Sub-Publisher (SE)
            # SR society = 033 (MCPS), SR share = 00000 (confirmed from Chris's approved files)
            lines.append(build_record("SPU", {
                "t_seq":       t_seq,
                "rec_seq":     f"{rec_seq:08d}",
                "chain_id":    chain_id,
                "pub_id":      lumina_id,
                "pub_name":    lumina_name[:45],
                "pub_type":    "SE",
                "ipi_name":    lumina_ipi,
                "pr_soc":      lumina_pr_soc,
                "pr_share":    "00000",
                "mr_soc":      lumina_mr_soc,
                "mr_share":    "00000",
                "sr_soc":      lumina_mr_soc,
                "sr_share":    "00000",
                "soc_agr_num": str(agr_num)[:14],
            }, context=context))
            rec_seq += 1
            total_records_in_group += 1

            # SPT — Publisher Territory (Lumina collecting)
            lines.append(build_record("SPT", {
                "t_seq":    t_seq,
                "rec_seq":  f"{rec_seq:08d}",
                "pub_id":   lumina_id,
                "constant": "      ",
                "pr_coll":  pr_share,
                "mr_coll":  mr_share,
                "sr_coll":  sr_share,
                "tis_code": territory,
            }, context=context))
            rec_seq += 1
            total_records_in_group += 1

        # ---- WRITER CHAIN ----
        _writer_pub_links = []   # collects (writer_id, original_publisher) tuples
        writers = track.get('writers', [])
        if not writers:
            raise CWREngineError(f"Track '{title}': no writers found.")

        # Validate total writer PR share — approved files show 50% (collection share)
        # or 100% (ownership share). Both are valid. Warn on anything else.
        total_pr = sum(float(w.get('pr_share', 0)) for w in writers)
        if abs(total_pr - 50.0) > 0.5 and abs(total_pr - 100.0) > 0.5:
            warnings.append(
                f"Track '{title}': writer PR shares sum to {total_pr:.2f}%. "
                f"Expected 50.00% (collection) or 100.00% (ownership). Verify."
            )

        for w_idx, writer in enumerate(writers, start=1):
            last_name = str(writer.get('last_name', '')).strip().upper()
            if not last_name:
                continue

            first_name = str(writer.get('first_name', '')).strip().upper()
            w_ipi      = pad_ipi(writer.get('ipi', ''))
            w_pr_soc   = _fmt_soc(writer.get('pr_soc', '021'))
            w_mr_soc   = _fmt_soc(writer.get('mr_soc', '099'))
            w_sr_soc   = _fmt_soc(writer.get('sr_soc', '099'))
            w_pr_share = fmt_share(writer.get('pr_share', 0))
            writer_id  = f"0000000{w_idx:02d}"[:9]

            # SWR
            lines.append(build_record("SWR", {
                "t_seq":       t_seq,
                "rec_seq":     f"{rec_seq:08d}",
                "writer_id":   writer_id,
                "last_name":   last_name[:45],
                "first_name":  first_name[:30],
                "designation": "C ",
                "ipi_name":    w_ipi,
                "pr_soc":      w_pr_soc,
                "pr_share":    w_pr_share,
                "mr_soc":      w_mr_soc,
                "mr_share":    "00000",
                "sr_soc":      w_sr_soc,
                "sr_share":    "00000",
            }, context=context))
            rec_seq += 1
            total_records_in_group += 1

            # SWT
            lines.append(build_record("SWT", {
                "t_seq":      t_seq,
                "rec_seq":    f"{rec_seq:08d}",
                "writer_id":  writer_id,
                "pr_coll":    w_pr_share,
                "mr_coll":    "00000",
                "sr_coll":    "00000",
            }, context=context))
            rec_seq += 1
            total_records_in_group += 1

            # Track writer→publisher linkage for PWR block below
            _writer_pub_links.append((writer_id, str(writer.get('original_publisher', '')).strip().upper()))

        # ---- PWR — one per publisher per linked writer ----
        # Rule confirmed from Chris's approved files:
        #   1 pub  + 1 writer  = 1 PWR
        #   2 pubs + 1 writer  = 2 PWR (one per pub, same writer)
        #   1 pub  + 2 writers = 2 PWR (same pub, one per writer)
        # For each publisher, find all writers linked to it.
        # If no writer links to a pub, use the first writer as fallback.
        _primary_wid = f"0000000{1:02d}"[:9]
        for p_idx2, pub in enumerate(publishers, start=1):
            pub_upper = pub['name'].strip().upper()
            agr_num   = _lookup_agreement(pub_upper, agreement_map)
            linked_wids = [
                wid for wid, opub in _writer_pub_links
                if opub == pub_upper or not opub
            ]
            if not linked_wids:
                linked_wids = [_primary_wid]
            for wid in linked_wids:
                lines.append(build_record("PWR", {
                    "t_seq":        t_seq,
                    "rec_seq":      f"{rec_seq:08d}",
                    "pub_id":       f"0000000{p_idx2:02d}"[:9],
                    "pub_name":     pub_upper[:45],
                    "subm_agr_num": "",
                    "soc_agr_num":  str(agr_num)[:14] if agr_num else "",
                    "writer_id":    wid,
                    "chain_id":     f"{p_idx2:02d}",
                }, context=context))
                rec_seq += 1
                total_records_in_group += 1

        # ---- REC × 2 ----
        album_code   = str(track.get('album_code', ''))[:15]
        library_name = str(track.get('library_name', ''))[:60]

        # REC 1 — physical release (media_type = 'CD ')
        lines.append(build_record("REC", {
            "t_seq":           t_seq,
            "rec_seq":         f"{rec_seq:08d}",
            "release_catalog": album_code,
            "isrc":            isrc,
            "media_type":      "CD ",
            "recording_title": "",
            "record_label":    library_name,
        }, context=context))
        rec_seq += 1
        total_records_in_group += 1

        # REC 2 — digital (media_type = 'DW ', includes track title)
        lines.append(build_record("REC", {
            "t_seq":           t_seq,
            "rec_seq":         f"{rec_seq:08d}",
            "release_catalog": "",
            "isrc":            isrc,
            "media_type":      "DW ",
            "recording_title": title[:60],
            "record_label":    "",
        }, context=context))
        rec_seq += 1
        total_records_in_group += 1

        # ---- ORN ----
        # album_title: prefer explicit field, fall back to album_code
        album_title = str(track.get('album_title', '') or album_code).strip()[:60]
        if not album_title:
            album_title = album_code

        # cut_number: use track's own number from CSV if present, else batch position
        raw_cut = track.get('track_number', '') or track.get('cut_number', '')
        try:
            cut_number = f"{int(str(raw_cut).strip()):04d}"
        except (ValueError, TypeError):
            cut_number = f"{t_idx+1:04d}"

        # ORN library field: full name, no truncation.
        # Record length is dynamic: 101 fixed chars + exact library name length.
        # Chris's approved files confirm no trailing padding after library name.
        orn_library = library_name[:60].rstrip()
        orn_length  = 101 + len(orn_library)
        orn_canvas  = Canvas(orn_length)
        _orn_data = {
            "t_seq":         t_seq,
            "rec_seq":       f"{rec_seq:08d}",
            "prod_title":    album_title,
            "cd_identifier": album_code,
            "cut_number":    cut_number,
            "library":       orn_library,
        }
        for fld in SCHEMA["ORN"].fields:
            val = fld.constant if fld.constant is not None else _orn_data.get(fld.name, "")
            if fld.name == "library":
                orn_canvas.stamp(fld.start, len(orn_library), val, fld.fmt, fld.name, context)
            else:
                orn_canvas.stamp(fld.start, fld.length, val, fld.fmt, fld.name, context)
        lines.append(orn_canvas.render())
        total_records_in_group += 1

    # ---- GRT ----
    # record_count = all records between GRH and GRT inclusive (GRH + work records + GRT)
    nwr_count = len(tracks)
    grt_record_count = total_records_in_group + 2  # +1 GRH, +1 GRT itself
    lines.append(build_record("GRT", {
        "transaction_count": f"{nwr_count:08d}",
        "record_count":      f"{grt_record_count:08d}",
    }))

    # ---- TRL ----
    # record_count = every record in the file including HDR and TRL
    total_all_records = total_records_in_group + 4  # HDR + GRH + GRT + TRL
    lines.append(build_record("TRL", {
        "transaction_count": f"{nwr_count:08d}",
        "record_count":      f"{total_all_records:08d}",
    }))

    # Join with CRLF, add final CRLF
    cwr_content = "\r\n".join(lines) + "\r\n"

    # Generate filename
    yr = now.strftime("%y")
    filename = f"CW{yr}{sequence_number:04d}{submitter_code}.V22"

    return cwr_content, warnings, filename


# ==============================================================================
# HELPERS
# ==============================================================================

def _normalize(s: str) -> str:
    """Uppercase and remove all spaces for fuzzy comparison."""
    return ''.join(str(s).upper().split())


def _lookup_agreement(pub_name: str, agreement_map: dict) -> str:
    """
    Match publisher name to agreement number.
    Pass 1: exact normalized match (spaces removed, uppercased).
    Pass 2: normalized substring match (either direction).
    Pass 3: strip common suffixes and retry.
    """
    needle = _normalize(pub_name)

    for key, val in agreement_map.items():
        if _normalize(key) == needle:
            return str(val)

    for key, val in agreement_map.items():
        k = _normalize(key)
        if k in needle or needle in k:
            return str(val)

    suffixes = ['MUSIC', 'PUBLISHING', 'SONGS', 'ENTERTAINMENT', 'RECORDS', 'PRODUCTIONS']
    needle_s = needle
    for sfx in suffixes:
        if needle_s.endswith(sfx):
            needle_s = needle_s[:-len(sfx)]
            break
    if needle_s != needle:
        for key, val in agreement_map.items():
            k = _normalize(key)
            for sfx in suffixes:
                if k.endswith(sfx):
                    k = k[:-len(sfx)]
                    break
            if k == needle_s or k in needle_s or needle_s in k:
                return str(val)

    return ""


def _fmt_soc(value) -> str:
    """Format society code to 3-digit zero-padded string."""
    import re
    s = re.sub(r'\D', '', str(value).split('.')[0]) if value else ''
    return s.zfill(3)[:3] if s else '000'
