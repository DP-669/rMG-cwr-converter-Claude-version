# ==============================================================================
# rMG CWR CONVERTER - STREAMLIT APP
# Version: v1.7.0 - 2026-05-20
# ==============================================================================

import streamlit as st
import pandas as pd
import io
import json
import zipfile
import os
import re
import time
import urllib.request
import urllib.error
from datetime import datetime

import config
from input_parser import parse_csv, ParseError
from cwr_engine import generate_cwr, CWREngineError
from cwr_validator import validate
from swn_manager import (
    load_registry, get_next_swn, get_next_sequence, commit_swn_range,
    resolve_conflict, format_swn,
    SWNSyncMismatch, SWNError
)

APP_VERSION = "v1.7.2"
APP_DATE    = "2026-05-20"

st.set_page_config(
    page_title="rMG CWR Converter",
    page_icon="🎵",
    layout="centered",
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600&family=DM+Mono:wght@400;500&display=swap');

    html, body, [class*="css"] {
        font-family: 'DM Sans', -apple-system, BlinkMacSystemFont, sans-serif;
    }

    .stApp {
        background-color: #FAFAFA;
    }

    header { visibility: hidden; }

    .app-title {
        text-align: center;
        font-size: 1.9rem;
        font-weight: 600;
        color: #111;
        letter-spacing: -0.03em;
        margin-bottom: 2px;
    }

    .app-subtitle {
        text-align: center;
        font-size: 0.8rem;
        color: #999;
        letter-spacing: 0.05em;
        text-transform: uppercase;
        margin-bottom: 28px;
    }

    /* Status pill */
    .status-pill {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 4px 10px;
        border-radius: 20px;
        font-size: 0.78rem;
        font-weight: 500;
        letter-spacing: 0.01em;
    }

    .pill-ok    { background: #E8F5E9; color: #2E7D32; }
    .pill-warn  { background: #FFF8E1; color: #F57F17; }
    .pill-error { background: #FFEBEE; color: #C62828; }

    /* Registry card */
    .reg-card {
        background: #fff;
        border: 1px solid #E8E8E8;
        border-radius: 12px;
        padding: 16px 20px;
        margin-bottom: 12px;
    }

    .reg-label {
        font-size: 0.72rem;
        font-weight: 600;
        color: #999;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        margin-bottom: 4px;
    }

    .reg-value {
        font-family: 'DM Mono', monospace;
        font-size: 1.1rem;
        font-weight: 500;
        color: #111;
    }

    .reg-sub {
        font-size: 0.78rem;
        color: #888;
        margin-top: 2px;
    }

    /* Next file bar */
    .next-file-bar {
        background: #F0F4FF;
        border-radius: 8px;
        padding: 12px 16px;
        margin-bottom: 20px;
        display: flex;
        align-items: center;
        gap: 10px;
        font-size: 0.88rem;
    }

    .next-file-name {
        font-family: 'DM Mono', monospace;
        font-weight: 500;
        color: #1A237E;
        font-size: 0.92rem;
    }

    .next-file-meta {
        color: #666;
        font-size: 0.8rem;
    }

    /* Accepted bar */
    .accepted-bar {
        background: #F1F8E9;
        border: 1px solid #DCEDC8;
        border-radius: 8px;
        padding: 12px 16px;
        margin-bottom: 8px;
        font-size: 0.85rem;
        color: #33691E;
    }

    .manual-bar {
        background: #FFF3E0;
        border: 1px solid #FFE0B2;
        border-radius: 8px;
        padding: 12px 16px;
        margin-bottom: 8px;
        font-size: 0.85rem;
        color: #E65100;
    }

    /* Download card */
    .dl-card {
        background: #fff;
        border: 1px solid #E8E8E8;
        border-radius: 12px;
        padding: 20px;
        margin-top: 16px;
    }

    .dl-filename {
        font-family: 'DM Mono', monospace;
        font-size: 1rem;
        font-weight: 500;
        color: #111;
    }

    .dl-meta {
        font-size: 0.8rem;
        color: #888;
        margin-top: 4px;
    }

    /* Section label */
    .section-label {
        font-size: 0.72rem;
        font-weight: 600;
        color: #999;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        margin-bottom: 8px;
        margin-top: 20px;
    }

    /* Hide Streamlit chrome */
    div[data-testid="metric-container"] { text-align: center; }
    .stAlert > div { border-radius: 8px; }
    .stExpander { border: 1px solid #E8E8E8 !important; border-radius: 8px !important; }
</style>
""", unsafe_allow_html=True)

# ---- HEADER ----
if os.path.exists("assets/lumina_logo.png"):
    _, col_logo, _ = st.columns([3, 1, 3])
    with col_logo:
        st.image("assets/lumina_logo.png", use_container_width=True)

st.markdown("<h1 class='app-title'>rMG CWR Converter</h1>", unsafe_allow_html=True)
st.markdown(
    f"<p class='app-subtitle'>CWR 2.2 &nbsp;·&nbsp; ICE &nbsp;·&nbsp; PRS &nbsp;·&nbsp; {APP_VERSION}</p>",
    unsafe_allow_html=True
)

# ---- CONFIG ----
lumina_cfg    = dict(st.secrets["LUMINA"])        if "LUMINA"        in st.secrets else config.LUMINA
agreement_map = dict(st.secrets["AGREEMENT_MAP"]) if "AGREEMENT_MAP" in st.secrets else config.AGREEMENT_MAP
sa_token      = st.secrets.get("SOURCEAUDIO", {}).get("token", "")


def _get_dropbox_token():
    db = st.secrets.get("DROPBOX", {})
    refresh_token = db.get("refresh_token", "")
    app_key       = db.get("app_key", "")
    app_secret    = db.get("app_secret", "")
    if refresh_token and app_key and app_secret:
        try:
            import urllib.parse
            data = urllib.parse.urlencode({
                "grant_type":    "refresh_token",
                "refresh_token": refresh_token,
                "client_id":     app_key,
                "client_secret": app_secret,
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://api.dropboxapi.com/oauth2/token", data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())["access_token"]
        except Exception:
            pass
    return db.get("token", "")


dropbox_token = _get_dropbox_token()

catalogs = config.CATALOGS
for k in catalogs:
    catalogs[k]["lumina_name"]   = lumina_cfg.get("name",   catalogs[k]["lumina_name"])
    catalogs[k]["lumina_ipi"]    = lumina_cfg.get("ipi",    catalogs[k]["lumina_ipi"])
    catalogs[k]["lumina_pub_id"] = lumina_cfg.get("pub_id", catalogs[k]["lumina_pub_id"])


# ---- DROPBOX SPREADSHEET ----
DROPBOX_SEQ_PATH = "/01 rMG Admin/03 Metadata, Registrations & Data/00 CWR/2026 CWR registrations/CWR Sequencing by albums.xlsx"


def _dropbox_download(path, token):
    req = urllib.request.Request(
        "https://content.dropboxapi.com/2/files/download", data=b"",
        headers={"Authorization": f"Bearer {token}",
                 "Dropbox-API-Arg": json.dumps({"path": path}),
                 "Content-Type": "text/plain"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read()


def _dropbox_upload(path, content, token):
    req = urllib.request.Request(
        "https://content.dropboxapi.com/2/files/upload", data=content,
        headers={"Authorization": f"Bearer {token}",
                 "Dropbox-API-Arg": json.dumps({"path": path, "mode": "overwrite"}),
                 "Content-Type": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


@st.cache_data(ttl=60)
def load_sequencing_spreadsheet(token):
    if not token:
        return None
    try:
        raw = _dropbox_download(DROPBOX_SEQ_PATH, token)
        df  = pd.read_excel(io.BytesIO(raw), header=1)
        df.columns = [str(c).strip() for c in df.columns]
        cwr_col = None
        for c in df.columns:
            if "CWR" in str(c).upper() or df[c].astype(str).str.match(r"CW\d{6}").any():
                cwr_col = c
                break
        if cwr_col is None:
            cwr_col = df.columns[1] if len(df.columns) > 1 else df.columns[0]
        df = df.rename(columns={cwr_col: "cwr_file"})
        df = df[df["cwr_file"].astype(str).str.strip().ne("") &
                df["cwr_file"].astype(str).str.strip().ne("nan")]
        return df.reset_index(drop=True)
    except Exception:
        return None


def get_next_sequence_from_dropbox(token):
    df = load_sequencing_spreadsheet(token)
    if df is None or df.empty:
        return 6
    used = df[df["cwr_file"].astype(str).str.match(r"CW\d{6}")]
    if used.empty:
        return 6
    status_col = next((c for c in df.columns if "status" in str(c).lower()), None)
    if status_col:
        with_status = used[used[status_col].astype(str).str.strip().ne("nan") &
                           used[status_col].astype(str).str.strip().ne("")]
        if not with_status.empty:
            m = re.search(r"CW\d{2}(\d{4})", str(with_status.iloc[-1]["cwr_file"]))
            if m:
                return int(m.group(1)) + 1
    m = re.search(r"CW\d{2}(\d{4})", str(used.iloc[-1]["cwr_file"]))
    return int(m.group(1)) + 1 if m else 6


def write_new_row_to_dropbox(token, cwr_filename, album_name, album_code, date_str):
    if not token:
        return False
    try:
        raw = _dropbox_download(DROPBOX_SEQ_PATH, token)
        df  = pd.read_excel(io.BytesIO(raw), header=None)
        insert_row = len(df)
        for i, row in df.iterrows():
            if str(row.iloc[0]).strip() == cwr_filename:
                df.iloc[i, 1] = album_name
                df.iloc[i, 2] = album_code
                df.iloc[i, 3] = date_str
                df.iloc[i, 4] = "pending"
                insert_row = None
                break
            if all(str(v).strip() in ("", "nan") for v in row):
                insert_row = i
                break
        if insert_row is not None:
            new_row = ["", cwr_filename, album_name, album_code,
                       date_str, "pending", "auto-generated"]
            df.loc[insert_row] = (new_row + [""] * len(df.columns))[:len(df.columns)]
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, header=False)
        buf.seek(0)
        _dropbox_upload(DROPBOX_SEQ_PATH, buf.read(), token)
        return True
    except Exception:
        return False


# ---- SWN REGISTRY ----
if "swn_registry" not in st.session_state:
    try:
        st.session_state["swn_registry"] = load_registry(st.secrets)
        st.session_state["swn_conflict"]  = None
    except SWNSyncMismatch as e:
        st.session_state["swn_registry"] = None
        st.session_state["swn_conflict"]  = e
    except Exception as e:
        st.session_state["swn_registry"] = None
        st.session_state["swn_conflict"]  = None
        st.session_state["swn_load_error"] = str(e)

# Always read sequence fresh from registry — never cache stale value
_reg = st.session_state.get("swn_registry")
if _reg:
    next_seq = get_next_sequence(_reg)
else:
    next_seq = get_next_sequence_from_dropbox(dropbox_token)
st.session_state["next_seq"] = next_seq

# ---- TABS ----
tab_gen, tab_val, tab_ledger = st.tabs(["Generate", "Validate", "Ledger"])


# ---- SHARED GENERATION LOGIC ----
def run_generation(tracks, source_label, catalog_key, seq_num,
                   file_bytes_for_validation=None):
    registry     = st.session_state.get("swn_registry")
    accepted     = st.session_state.get("prev_accepted", False)

    if accepted:
        starting_swn = get_next_swn(registry) if registry else 1
    else:
        starting_swn = st.session_state.get("manual_swn_start",
                        get_next_swn(registry) if registry else 1)

    with st.status("Generating...", expanded=True) as status:
        if not tracks:
            st.error("No tracks found.")
            return False
        if not agreement_map:
            st.error("Agreement map empty. Add to Streamlit Secrets.")
            return False

        st.write(f"{len(tracks)} tracks · SWN {format_swn(starting_swn)} to "
                 f"{format_swn(starting_swn + len(tracks) - 1)}")

        try:
            cwr_content, gen_warnings, filename, last_swn_used = generate_cwr(
                tracks=tracks,
                catalog_config=catalogs[catalog_key],
                agreement_map=agreement_map,
                sequence_number=seq_num,
                starting_swn=starting_swn
            )
        except CWREngineError as e:
            st.error(f"{e}")
            return False

        for w in gen_warnings:
            st.warning(w)

        result = validate(cwr_content,
                          source_csv_bytes=file_bytes_for_validation,
                          filename=filename)

        if not result["passed"]:
            status.update(label="Validation failed", state="error")
            for err in result["errors"]:
                st.error(f"Line {err.line} [{err.record_type}]: {err.message}")
            return False

        album_label = tracks[0].get("album_code", "unknown") if tracks else "unknown"
        album_name  = tracks[0].get("album_title", album_label) if tracks else album_label
        updated = commit_swn_range(
            registry=registry,
            swn_start=starting_swn,
            swn_end=last_swn_used,
            track_count=len(tracks),
            filename=filename,
            album=album_label,
            secrets=st.secrets,
            sequence_number=seq_num
        )
        st.session_state["swn_registry"] = updated

        if dropbox_token:
            write_new_row_to_dropbox(
                token=dropbox_token,
                cwr_filename=filename,
                album_name=album_name,
                album_code=album_label,
                date_str=datetime.now().strftime("%d-%b-%y")
            )
            load_sequencing_spreadsheet.clear()
            st.session_state["next_seq"] = seq_num + 1

        status.update(label=f"{filename} ready", state="complete")

    st.session_state.update({
        "cwr_content":   cwr_content,
        "cwr_filename":  filename,
        "cwr_warnings":  result["warnings"],
        "cwr_stats":     result["stats"],
        "cwr_swn_start": starting_swn,
        "cwr_swn_end":   last_swn_used,
        "prev_accepted": False,
    })
    st.rerun()
    return True


# ==============================================================================
# TAB 1 - GENERATE
# ==============================================================================
with tab_gen:

    registry   = st.session_state.get("swn_registry")
    conflict   = st.session_state.get("swn_conflict")
    load_error = st.session_state.get("swn_load_error")

    # ---- SWN CONFLICT (hard block) ----
    if conflict:
        st.error("SWN registry mismatch — resolve before generating.")
        c1, c2 = st.columns(2)
        with c1:
            if st.button(f"Use Drive value ({format_swn(conflict.drive_val)})",
                         use_container_width=True):
                st.session_state["swn_registry"] = resolve_conflict(
                    True, conflict.local_val, conflict.drive_val, st.secrets)
                st.session_state["swn_conflict"] = None
                st.rerun()
        with c2:
            if st.button(f"Use Local value ({format_swn(conflict.local_val)})",
                         use_container_width=True):
                st.session_state["swn_registry"] = resolve_conflict(
                    False, conflict.local_val, conflict.drive_val, st.secrets)
                st.session_state["swn_conflict"] = None
                st.rerun()
        st.stop()

    # ---- REGISTRY STATUS (compact) ----
    if registry:
        last_swn     = int(registry.get("last_swn_used", 0))
        last_src     = registry.get("last_swn_source", "—")
        sync_warn    = registry.get("_sync_warning", "")
        bootstrapped = registry.get("_bootstrapped", False)
        github_ok    = registry.get("_github_available", False)

        if sync_warn or bootstrapped:
            status_html = "<span class='status-pill pill-warn'>⚡ Local cache</span>"
        elif github_ok:
            status_html = "<span class='status-pill pill-ok'>✓ GitHub</span>"
        else:
            status_html = "<span class='status-pill pill-warn'>⚡ Local</span>"

        st.markdown(f"""
        <div class='reg-card'>
            <div style='display:flex; justify-content:space-between; align-items:flex-start;'>
                <div>
                    <div class='reg-label'>SWN Registry</div>
                    <div class='reg-value'>{format_swn(last_swn)}</div>
                    <div class='reg-sub'>{last_src[:60]}</div>
                </div>
                <div style='text-align:right;'>
                    <div class='reg-label'>Next</div>
                    <div class='reg-value'>{format_swn(last_swn + 1)}</div>
                    <div style='margin-top:6px;'>{status_html}</div>
                </div>
            </div>
        </div>""", unsafe_allow_html=True)

    # ---- PREVIOUS FILE STATUS + ACCEPTED CHECKBOX ----
    last_history = registry.get("history", []) if registry else []
    last_entry   = last_history[-1] if last_history else None

    if last_entry and last_entry.get("file") != "MANUAL_RESET":
        file_name   = last_entry.get("file", "—")
        swn_start_e = format_swn(last_entry.get("swn_start", 0))
        swn_end_e   = format_swn(last_entry.get("swn_end", 0))
        tracks_e    = last_entry.get("track_count", "?")
        date_e      = str(last_entry.get("date", "—"))[:10]

        st.markdown(f"""
        <div class='reg-card' style='margin-bottom:8px;'>
            <div class='reg-label'>Previous file</div>
            <div style='display:flex; justify-content:space-between; align-items:center; margin-top:4px;'>
                <div>
                    <span class='reg-value' style='font-size:0.95rem;'>{file_name}</span>
                    <div class='reg-sub'>SWN {swn_start_e} — {swn_end_e} &nbsp;·&nbsp; {tracks_e} tracks &nbsp;·&nbsp; {date_e}</div>
                </div>
            </div>
        </div>""", unsafe_allow_html=True)

    accepted = st.checkbox(
        "Previous file was accepted by ICE",
        value=st.session_state.get("prev_accepted", False),
        key="prev_accepted_checkbox",
        help="When checked: SWN and sequence are locked and auto-incremented. "
             "When unchecked: enter any values manually."
    )
    st.session_state["prev_accepted"] = accepted

    if accepted:
        st.markdown(f"""
        <div class='accepted-bar'>
        Next file locked: <strong>CW26{next_seq:04d}LUM_319.V22</strong>
        &nbsp;·&nbsp; SWN starts at <strong>{format_swn(get_next_swn(registry) if registry else 1)}</strong>
        </div>""", unsafe_allow_html=True)
    else:
        st.markdown("""
        <div class='manual-bar'>
        Manual mode — enter sequence and starting SWN below.
        </div>""", unsafe_allow_html=True)

    # ---- OPTIONS ----
    col_cat, col_seq = st.columns(2)
    with col_cat:
        catalog_key = st.selectbox(
            "Catalog",
            options=list(catalogs.keys()),
            format_func=lambda k: f"{k} — {catalogs[k]['label']}"
        )
    with col_seq:
        if accepted:
            seq_override = next_seq
            st.number_input("Sequence", value=next_seq, disabled=True,
                            min_value=next_seq, max_value=next_seq)
        else:
            seq_override = st.number_input("Sequence", min_value=1,
                                           max_value=9999, value=int(next_seq))

    if not accepted:
        last_swn_val = int(registry.get("last_swn_used", 0)) if registry else 0
        manual_swn = st.number_input(
            "Starting SWN",
            min_value=1, max_value=999999,
            value=last_swn_val + 1,
            help="Enter the SWN this file should start from."
        )
        st.session_state["manual_swn_start"] = manual_swn

    st.markdown("<div style='margin-top:8px;'></div>", unsafe_allow_html=True)

    # ---- UPLOAD / FETCH ----
    mode_csv, mode_api = st.tabs(["Upload CSV", "Fetch from SourceAudio"])

    with mode_csv:
        uploaded_csv = st.file_uploader(
            "SourceAudio or Harvest Media CSV",
            type=["csv"], key="gen_csv", label_visibility="collapsed"
        )
        if uploaded_csv:
            if st.button("Generate", type="primary",
                         use_container_width=True, key="gen_csv_btn"):
                try:
                    file_bytes = uploaded_csv.getvalue()
                    tracks, fmt_detected, parse_warnings = parse_csv(
                        file_bytes, uploaded_csv.name)
                    for w in parse_warnings:
                        st.warning(w)
                    run_generation(tracks, fmt_detected.upper(),
                                   catalog_key, int(seq_override), file_bytes)
                except ParseError as e:
                    st.error(f"{e}")

    with mode_api:
        if not sa_token:
            st.caption("SourceAudio token not configured.")
        else:
            album_input = st.text_input("Album code", placeholder="e.g. RC055",
                                        label_visibility="collapsed")
            if album_input:
                if st.button("Fetch and Generate", type="primary",
                             use_container_width=True, key="gen_api_btn"):
                    try:
                        from sourceaudio_fetcher import fetch_album_tracks
                        with st.spinner(f"Fetching {album_input.strip().upper()}..."):
                            tracks, fetch_warnings = fetch_album_tracks(
                                token=sa_token,
                                album_code=album_input.strip().upper()
                            )
                        for w in fetch_warnings:
                            st.warning(w)
                        run_generation(tracks, album_input.strip().upper(),
                                       catalog_key, int(seq_override))
                    except Exception as e:
                        st.error(f"{e}")

    # ---- DOWNLOAD ----
    if "cwr_content" in st.session_state and "cwr_filename" in st.session_state:
        filename  = st.session_state["cwr_filename"]
        stats     = st.session_state.get("cwr_stats", {})
        warns     = st.session_state.get("cwr_warnings", [])
        swn_start = st.session_state.get("cwr_swn_start")
        swn_end   = st.session_state.get("cwr_swn_end")

        st.markdown(f"""
        <div class='dl-card'>
            <div class='dl-filename'>{filename}</div>
            <div class='dl-meta'>
                {stats.get("nwr_count", "—")} tracks
                &nbsp;·&nbsp; SWN {format_swn(swn_start)} — {format_swn(swn_end)}
                &nbsp;·&nbsp; ✓ Valid
            </div>
        </div>""", unsafe_allow_html=True)

        if warns:
            with st.expander(f"{len(warns)} warning(s)"):
                for w in warns:
                    st.caption(f"Line {w.line} [{w.record_type}]: {w.message}")

        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(filename,
                        st.session_state["cwr_content"].encode("latin-1"))

        st.download_button(
            label=f"Download {filename}",
            data=zip_buf.getvalue(),
            file_name=f"{filename}.zip",
            mime="application/zip",
            use_container_width=True,
            type="primary"
        )

        if st.button("Clear", use_container_width=False):
            for k in ("cwr_content", "cwr_filename", "cwr_warnings",
                      "cwr_stats", "cwr_swn_start", "cwr_swn_end"):
                st.session_state.pop(k, None)
            st.rerun()

    # ---- ADVANCED (hidden by default) ----
    with st.expander("Advanced — Reset SWN"):
        st.caption("Use only if you generated a file that was never submitted to ICE "
                   "and you need to reclaim those SWN numbers.")
        if registry:
            current_last = int(registry.get("last_swn_used", 0))
            reset_target = st.number_input(
                "Set last used SWN to:",
                min_value=1, max_value=current_last,
                value=current_last, step=1, key="swn_reset_input"
            )
            if reset_target < current_last:
                st.warning(
                    f"Will set last SWN to {format_swn(reset_target)}. "
                    f"Next file starts at {format_swn(reset_target + 1)}. "
                    f"Only do this if SWNs {format_swn(reset_target + 1)}-{format_swn(current_last)} "
                    f"were never submitted."
                )
                if st.button("Confirm Reset", key="swn_reset_btn"):
                    from datetime import timezone
                    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
                    updated_reg = dict(registry)
                    updated_reg["last_swn_used"]    = reset_target
                    updated_reg["last_swn_source"]  = f"Manual reset ({now})"
                    updated_reg["updated"]          = now
                    hist = list(registry.get("history", []))
                    hist.append({
                        "file": "MANUAL_RESET",
                        "album": f"Reset {current_last} to {reset_target}",
                        "swn_start": reset_target + 1,
                        "swn_end": current_last,
                        "track_count": current_last - reset_target,
                        "generated_by": "Manual reset",
                        "date": now,
                    })
                    updated_reg["history"] = hist
                    token = registry.get("_token", "")
                    repo  = registry.get("_repo", "DP-669/rMG-cwr-converter-Claude-version")
                    sha   = registry.get("_sha")
                    if token:
                        try:
                            import base64
                            import json as _json
                            url = (f"https://api.github.com/repos/{repo}"
                                   f"/contents/rMG-cwr-converter/swn_registry.json")
                            clean = {k: v for k, v in updated_reg.items()
                                     if not k.startswith("_")}
                            payload = _json.dumps({
                                "message": f"Manual SWN reset to {reset_target}",
                                "content": base64.b64encode(
                                    _json.dumps(clean, indent=2).encode()).decode(),
                                "sha": sha,
                            }).encode()
                            req = urllib.request.Request(
                                url, data=payload, method="PUT",
                                headers={
                                    "Authorization": f"token {token}",
                                    "Accept": "application/vnd.github.v3+json",
                                    "Content-Type": "application/json",
                                })
                            with urllib.request.urlopen(req, timeout=15) as resp:
                                resp.read()
                            updated_reg["_github_available"] = True
                            updated_reg["_sync_warning"] = ""
                            st.session_state["swn_registry"] = updated_reg
                            st.success(f"Reset to {format_swn(reset_target)}. "
                                       f"Next: {format_swn(reset_target + 1)}.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Reset failed: {e}")
                    else:
                        st.error("No GitHub token — cannot persist reset.")
        else:
            st.caption("Registry not loaded.")


# ==============================================================================
# TAB 2 - VALIDATE
# ==============================================================================
with tab_val:
    c1, c2 = st.columns(2)
    with c1:
        v22_file = st.file_uploader(".V22 file", type=["V22", "v22", "txt"],
                                    key="val_v22")
    with c2:
        csv_mirror = st.file_uploader("Source CSV (optional)",
                                      type=["csv"], key="val_csv")

    if v22_file:
        if st.button("Validate", type="primary", use_container_width=True):
            cwr_content = v22_file.getvalue().decode("latin-1")
            csv_bytes   = csv_mirror.getvalue() if csv_mirror else None
            with st.spinner("Checking..."):
                result = validate(cwr_content,
                                  source_csv_bytes=csv_bytes,
                                  filename=v22_file.name)
            st.divider()
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("NWR", result["stats"]["nwr_count"])
            c2.metric("SPU", result["stats"]["spu_count"])
            c3.metric("SWR", result["stats"]["swr_count"])
            c4.metric("REC", result["stats"]["rec_count"])
            if result["passed"]:
                st.success("All checks passed.")
            else:
                st.error(f"{len(result['errors'])} error(s).")
            for err in result.get("errors", []):
                st.error(f"Line {err.line} [{err.record_type}]: {err.message}")
                if err.excerpt:
                    with st.expander("Context"):
                        st.code(err.excerpt)
            for w in result.get("warnings", []):
                st.warning(f"Line {w.line} [{w.record_type}]: {w.message}")
            if result["passed"] and not result["warnings"]:
                st.balloons()


# ==============================================================================
# TAB 3 - LEDGER
# ==============================================================================
with tab_ledger:
    col_r, _ = st.columns([1, 5])
    with col_r:
        if st.button("Refresh"):
            load_sequencing_spreadsheet.clear()
            st.session_state["next_seq"] = get_next_sequence_from_dropbox(dropbox_token)
            st.rerun()

    if not dropbox_token:
        st.caption("No Dropbox token configured.")
    else:
        df = load_sequencing_spreadsheet(dropbox_token)
        if df is None:
            st.caption("Could not load spreadsheet.")
        elif df.empty:
            st.caption("No entries found.")
        else:
            def row_style(row):
                s = str(row.get("Status", row.get("status", ""))).lower()
                if s == "accepted":
                    return ["background-color:#F1F8E9"] * len(row)
                if s == "failed":
                    return ["background-color:#FFEBEE"] * len(row)
                if s in ("pending", "ongoing"):
                    return ["background-color:#FFF8E1"] * len(row)
                return [""] * len(row)

            st.dataframe(df.style.apply(row_style, axis=1),
                         use_container_width=True, hide_index=True)

    st.divider()

    # Next file
    seq_src = "Vesna's spreadsheet" if dropbox_token else "local fallback"
    st.markdown(
        f"Next: **`CW26{next_seq:04d}LUM_319.V22`** &nbsp;·&nbsp; "
        f"Sequence `{next_seq:04d}` &nbsp;·&nbsp; {seq_src}"
    )

    # SWN summary
    reg = st.session_state.get("swn_registry")
    if reg:
        st.markdown(
            f"Last SWN: `{format_swn(reg.get('last_swn_used', 0))}` &nbsp;·&nbsp; "
            f"Next: `{format_swn(get_next_swn(reg))}`"
        )
        swn_hist = reg.get("history", [])
        if swn_hist:
            with st.expander(f"SWN history ({len(swn_hist)} entries)"):
                hdf = pd.DataFrame(reversed(swn_hist))
                for col in ("swn_start", "swn_end"):
                    if col in hdf.columns:
                        hdf[col] = hdf[col].apply(lambda x: format_swn(int(x)))
                cols = [c for c in ["date", "file", "album", "swn_start",
                                    "swn_end", "track_count", "generated_by"]
                        if c in hdf.columns]
                st.dataframe(hdf[cols], use_container_width=True, hide_index=True)
