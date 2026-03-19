# ==============================================================================
# CWR VALIDATOR
# Validates a generated .V22 file against the geometry and logic rules
# confirmed by ICE (Berlin) and PRS (London) acceptance.
#
# Two modes:
#   1. Geometry-only: check record lengths, field positions, share totals
#   2. Mirror audit: cross-check against source CSV (track count, titles)
# ==============================================================================

import re
import io
import pandas as pd
from cwr_schema import SCHEMA


class ValidationError:
    def __init__(self, level: str, line: int, record_type: str, message: str, excerpt: str = ""):
        self.level       = level        # CRITICAL | WARNING | INFO
        self.line        = line
        self.record_type = record_type
        self.message     = message
        self.excerpt     = excerpt

    def __repr__(self):
        return f"[{self.level}] Line {self.line} ({self.record_type}): {self.message}"


# Record types that must be exactly their schema length
STRICT_LENGTH_TYPES = {'NWR', 'SPU', 'SPT', 'SWR', 'SWT', 'PWR', 'REC', 'GRH', 'GRT', 'TRL'}
# Note: ORN is excluded from strict length check — its length is dynamic
#       (101 fixed chars + library name length, no trailing padding)


def validate(cwr_content: str, source_csv_bytes: bytes = None, filename: str = "") -> dict:
    """
    Validate a CWR V2.2 file string.

    Returns:
        {
          'errors':   list of ValidationError (CRITICAL)
          'warnings': list of ValidationError (WARNING)
          'info':     list of ValidationError (INFO)
          'stats':    dict of counts
          'passed':   bool
        }
    """
    errors   = []
    warnings = []
    info     = []

    lines = [l for l in cwr_content.replace('\r\n', '\n').split('\n') if l]

    stats = {
        'total_lines':  len(lines),
        'nwr_count':    0,
        'spu_count':    0,
        'swr_count':    0,
        'rec_count':    0,
        'orn_count':    0,
    }

    # ---- 0. FILENAME AUDIT ----
    if filename:
        pattern = r'^CW\d{2}\d{4}[A-Z0-9_]+\.V22$'
        if not re.match(pattern, filename, re.IGNORECASE):
            errors.append(ValidationError(
                "CRITICAL", 0, "HDR",
                f"Filename '{filename}' does not match CWR V2.2 pattern CW[YY][NNNN][SUBMITTER].V22"
            ))

    # ---- 1. FILE STRUCTURE ----
    if not lines:
        errors.append(ValidationError("CRITICAL", 0, "FILE", "File is empty."))
        return _result(errors, warnings, info, stats)

    if lines[0][:3] != 'HDR':
        errors.append(ValidationError("CRITICAL", 1, "HDR", "First record must be HDR."))

    if lines[-1][:3] != 'TRL':
        errors.append(ValidationError("CRITICAL", len(lines), "TRL", "Last record must be TRL."))

    hdr_count = sum(1 for l in lines if l[:3] == 'HDR')
    trl_count = sum(1 for l in lines if l[:3] == 'TRL')
    if hdr_count != 1:
        errors.append(ValidationError("CRITICAL", 0, "HDR", f"Expected exactly 1 HDR, found {hdr_count}."))
    if trl_count != 1:
        errors.append(ValidationError("CRITICAL", 0, "TRL", f"Expected exactly 1 TRL, found {trl_count}."))

    # ---- 2. GEOMETRY AUDIT ----
    nwr_t_seqs = []
    swr_shares = {}   # t_seq -> total PR share (for share validation)

    for line_num, line in enumerate(lines, start=1):
        if len(line) < 3:
            continue
        rtype = line[:3]

        # Length check — ORN excluded (dynamic length)
        expected = SCHEMA[rtype].total_length if rtype in SCHEMA else None
        if rtype in STRICT_LENGTH_TYPES and expected:
            if len(line) != expected:
                errors.append(ValidationError(
                    "CRITICAL", line_num, rtype,
                    f"Record length is {len(line)}, must be exactly {expected}.",
                    excerpt=line[:60] + ('...' if len(line) > 60 else '')
                ))

        # Per-type checks
        if rtype == 'NWR':
            stats['nwr_count'] += 1
            t_seq = line[3:11]
            nwr_t_seqs.append({'line': line_num, 't_seq': t_seq, 'content': line})
            swr_shares[t_seq] = 0

            # ORI verified at 0-based 142:145 (1-based pos 143-145)
            if len(line) >= 145:
                work_type = line[142:145]
                if work_type != 'ORI':
                    errors.append(ValidationError(
                        "CRITICAL", line_num, "NWR",
                        f"Version Type at [142:145] must be 'ORI', found '{work_type}'.",
                        excerpt=line[139:148]
                    ))

        elif rtype == 'SPU':
            stats['spu_count'] += 1
            # agr_type 'PG' at position 181 (0-based 180)
            if len(line) >= 182:
                agr_type = line[180:182]
                if agr_type not in ('PG', '  ', ''):
                    warnings.append(ValidationError(
                        "WARNING", line_num, "SPU",
                        f"Agreement Type at position 181 is '{agr_type}' — expected 'PG' for ICE/PRS.",
                        excerpt=line[175:182]
                    ))
            # Agreement number in soc_agr_num slot (pos 167, len 14, 0-based 166:180)
            if len(line) >= 182:
                subm_agr = line[98:112].strip()
                soc_agr  = line[166:180].strip()
                if subm_agr and not soc_agr:
                    warnings.append(ValidationError(
                        "WARNING", line_num, "SPU",
                        f"Agreement number appears in subm_agr_num (pos 99) instead of soc_agr_num (pos 167). "
                        f"ICE/PRS requires it at position 167.",
                        excerpt=line[95:182]
                    ))

        elif rtype == 'SWR':
            stats['swr_count'] += 1
            t_seq = line[3:11]
            if t_seq in swr_shares and len(line) >= 135:
                try:
                    pr_share = int(line[129:134].strip() or '0')
                    swr_shares[t_seq] += pr_share
                except ValueError:
                    pass

        elif rtype == 'REC':
            stats['rec_count'] += 1
            # media_type at position 264 (0-based 263), length 3
            if len(line) >= 267:
                media = line[263:266].strip()
                if media not in ('CD', 'DW', 'CDR'):
                    warnings.append(ValidationError(
                        "WARNING", line_num, "REC",
                        f"Media type at position 264 is '{media}' — expected 'CD' or 'DW'.",
                        excerpt=line[260:270]
                    ))

        elif rtype == 'ORN':
            stats['orn_count'] += 1
            # Purpose must be LIB at position 20 (0-based 19)
            if len(line) >= 22:
                purpose = line[19:22]
                if purpose != 'LIB':
                    errors.append(ValidationError(
                        "CRITICAL", line_num, "ORN",
                        f"Intended Purpose at position 20 must be 'LIB', found '{purpose}'.",
                        excerpt=line[19:25]
                    ))
            # ORN minimum length check (101 chars fixed prefix + at least 1 char library name)
            if len(line) < 102:
                errors.append(ValidationError(
                    "CRITICAL", line_num, "ORN",
                    f"ORN record is only {len(line)} chars — minimum is 102 (101 fixed + library name).",
                    excerpt=line
                ))

    # ---- 3. PR SHARE AUDIT ----
    for t_seq, total in swr_shares.items():
        # Approved files (ICE/PRS accepted) have writers summing to 5000 (50.00%).
        # SourceAudio exports collection share (half of ownership).
        # We accept 5000 or 10000 — warn on anything else.
        if total not in (5000, 10000) and total > 0:
            warnings.append(ValidationError(
                "WARNING", 0, "SWR",
                f"Work t_seq '{t_seq}': writer PR shares sum to {total} "
                f"(= {total/100:.2f}%). Approved files typically show 5000 (50.00%) "
                f"or 10000 (100.00%). Verify this is intentional."
            ))

    # ---- 4. REC SYMMETRY — each NWR must have 2 REC records (CD + DW) ----
    nwr_t_seq_set = {n['t_seq'] for n in nwr_t_seqs}
    rec_by_t_seq = {}
    for line_num, line in enumerate(lines, start=1):
        if line[:3] == 'REC':
            t_seq = line[3:11]
            rec_by_t_seq.setdefault(t_seq, []).append(line_num)

    for t_seq in nwr_t_seq_set:
        recs = rec_by_t_seq.get(t_seq, [])
        if len(recs) != 2:
            errors.append(ValidationError(
                "CRITICAL", 0, "REC",
                f"Work t_seq '{t_seq}': expected 2 REC records (CD + DW), found {len(recs)}."
            ))

    # ---- 5. MIRROR AUDIT (optional, requires source CSV) ----
    if source_csv_bytes:
        mirror_errors, mirror_warnings = _mirror_audit(source_csv_bytes, nwr_t_seqs, lines)
        errors.extend(mirror_errors)
        warnings.extend(mirror_warnings)

    return _result(errors, warnings, info, stats)


def _mirror_audit(source_csv_bytes: bytes, nwr_records: list, cwr_lines: list) -> tuple:
    """Cross-check generated CWR against source CSV."""
    errors   = []
    warnings = []

    try:
        for encoding in ('utf-8-sig', 'latin-1'):
            try:
                df = pd.read_csv(io.BytesIO(source_csv_bytes), encoding=encoding)
                break
            except Exception:
                continue
        else:
            errors.append(ValidationError("CRITICAL", 0, "MIRROR",
                "Could not parse source CSV for mirror audit."))
            return errors, warnings

        df.columns = [str(c).strip().upper() for c in df.columns]

        # Find title column
        title_col = None
        for candidate in ('TRACK: TITLE', 'TRACK TITLE', 'TITLE'):
            if candidate in df.columns:
                title_col = candidate
                break

        if not title_col:
            errors.append(ValidationError("CRITICAL", 0, "MIRROR",
                f"Source CSV has no title column. Found: {', '.join(df.columns[:8])}"))
            return errors, warnings

        # Determine unique track count from CSV.
        # SourceAudio: one row per track — use as-is.
        # Harvest: multi-row per track (one row per publisher deal).
        #   TRACK CODE is the reliable unique-per-track identifier.
        #   Do NOT use ISRC CODE — it can appear on multiple rows for the same track,
        #   causing drop_duplicates to undercount.
        if 'CODE: ISRC' in df.columns:
            # SourceAudio: single row per track already
            csv_df = df
        elif 'TRACK CODE' in df.columns:
            # Harvest: deduplicate by TRACK CODE — one unique code per track
            csv_df = df.drop_duplicates(subset=['TRACK CODE'])
        elif 'ISRC CODE' in df.columns:
            # Fallback only: ISRC may be shared across rows, use with caution
            csv_df = df.drop_duplicates(subset=['ISRC CODE'])
        else:
            csv_df = df.drop_duplicates(subset=[title_col])

        csv_titles = csv_df[title_col].astype(str).str.strip().tolist()
        csv_count  = len(csv_titles)
        nwr_count  = len(nwr_records)

        if csv_count != nwr_count:
            errors.append(ValidationError(
                "CRITICAL", 0, "MIRROR",
                f"Track count mismatch: source CSV has {csv_count} tracks, "
                f"CWR file has {nwr_count} NWR records."
            ))

        # Title length check
        for csv_title in csv_titles:
            if len(csv_title) > 60:
                errors.append(ValidationError(
                    "CRITICAL", 0, "MIRROR",
                    f"CSV title '{csv_title}' is {len(csv_title)} characters — exceeds 60-char CWR limit. "
                    f"Shorten before converting."
                ))

    except Exception as e:
        warnings.append(ValidationError("WARNING", 0, "MIRROR",
            f"Mirror audit failed: {str(e)}"))

    return errors, warnings


def _result(errors, warnings, info, stats) -> dict:
    return {
        'errors':   errors,
        'warnings': warnings,
        'info':     info,
        'stats':    stats,
        'passed':   len(errors) == 0,
    }
