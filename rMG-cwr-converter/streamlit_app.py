# ==============================================================================
# rMG CWR CONVERTER — STREAMLIT APP
# Claude Version | DP-669/rMG-cwr-converter-Claude-version
# Version: v1.5.0 — 2026-05-12
#
# Tab 1: Generate — CSV upload or SourceAudio API fetch -> .V22 + download
# Tab 2: Validate — upload .V22, run geometry audit
# Tab 3: Ledger   — Vesna's Dropbox spreadsheet live view + sequence tracking
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
    load_registry, get_next_swn, commit_swn_range,
    resolve_conflict, format_swn,
    SWNSyncMismatch, SWNError
)

APP_VERSION = "v1.6.0"
APP_DATE    = "2026-05-19"

# ---- PAGE CONFIG ----
st.set_page_config(
    page_title="rMG CWR Converter",
    page_icon="🎵",
    layout="centered",
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
    .stApp { background-color: #F8F8F8; font-family: -apple-system, BlinkMacSystemFont, sans-serif; }
    header { visibility: hidden; }
    .main-title { text-align: center; font-size: 2.2rem; font-weight: 700;
                  color: #1A1A1A; margin-bottom: 0; }
    .sub-title  { text-align: center; font-size: 1rem; color: #888;
                  margin-top: 4px; margin-bottom: 24px; }
    div[data-testid="metric-container"] { text-align: center; }
    .swn-box  { background:#F0F4FF; border:1px solid #C8D8FF; border-radius:8px;
                padding:14px 18px; margin-bottom:16px; font-size:0.92rem; }
    .swn-warn { background:#FFFBF0; border:1px solid #FFE0A0; border-radius:8px;
                padding:14px 18px; margin-bottom:16px; font-size:0.92rem; }
    .swn-error{ background:#FFF0F0; border:1px solid #FFB3B3; border-radius:8px;
                padding:14px 18px; margin-bottom:16px; font-size:0.92rem; }
    .seq-box  { background:#F0FFF4; border:1px solid #A8E6C0; border-radius:8px;
                padding:12px 18px; margin-bottom:16px; font-size:0.92rem; }
</style>
""", unsafe_allow_html=True)

if os.path.exists("assets/lumina_logo.png"):
    _, col_logo, _ = st.columns([2, 1, 2])
    with col_logo:
        st.image("assets/lumina_logo.png", use_container_width=True)

st.markdown("<h1 class='main-title'>rMG CWR Converter</h1>", unsafe_allow_html=True)
st.markdown(
    f"<p class='sub-title'>CWR 2.2 · ICE · PRS · {APP_VERSION} · {APP_DATE}</p>",
    unsafe_allow_html=True
)

# ---- LOAD CONFIG ----
lumina_cfg    = dict(st.secrets["LUMINA"])    if "LUMINA"        in st.secrets else config.LUMINA
agreement_map = dict(st.secrets["AGREEMENT_MAP"]) if "AGREEMENT_MAP" in st.secrets else config.AGREEMENT_MAP
sa_token      = st.secrets.get("SOURCEAUDIO", {}).get("token", "")
def _get_dropbox_token():
    """Get a valid Dropbox access token. Uses refresh token if available."""
    db = st.secrets.get("DROPBOX", {})
    # Prefer refresh token flow (never expires)
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
    # Fall back to static token
    return db.get("token", "")

dropbox_token = _get_dropbox_token()

catalogs = config.CATALOGS
for k in catalogs:
    catalogs[k]["lumina_name"]   = lumina_cfg.get("name",   catalogs[k]["lumina_name"])
    catalogs[k]["lumina_ipi"]    = lumina_cfg.get("ipi",    catalogs[k]["lumina_ipi"])
    catalogs[k]["lumina_pub_id"] = lumina_cfg.get("pub_id", catalogs[k]["lumina_pub_id"])


# ==============================================================================
# DROPBOX SPREADSHEET INTEGRATION
# ==============================================================================

DROPBOX_SEQ_PATH = "/01 rMG Admin/03 Metadata, Registrations & Data/00 CWR/2026 CWR registrations/CWR Sequencing by albums.xlsx"


def _dropbox_download(path, token):
    url = "https://content.dropboxapi.com/2/files/download"
    arg = json.dumps({"path": path})
    req = urllib.request.Request(
        url, data=b"",
        headers={
            "Authorization":   f"Bearer {token}",
            "Dropbox-API-Arg": arg,
            "Content-Type":    "text/plain",
        }
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read()


def _dropbox_upload(path, content, token):
    url = "https://content.dropboxapi.com/2/files/upload"
    arg = json.dumps({"path": path, "mode": "overwrite", "autorename": False})
    req = urllib.request.Request(
        url, data=content,
        headers={
            "Authorization":   f"Bearer {token}",
            "Dropbox-API-Arg": arg,
            "Content-Type":    "application/octet-stream",
        }
    )
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
        return 5
    used = df[df["cwr_file"].astype(str).str.match(r"CW\d{6}")]
    if used.empty:
        return 5
    last_file = used.iloc[-1]["cwr_file"]
    m = re.search(r"CW\d{2}(\d{4})", str(last_file))
    if m:
        return int(m.group(1)) + 1
    return 5


def write_new_row_to_dropbox(token, cwr_filename, album_name, album_code, date_str):
    if not token:
        return False
    try:
        raw = _dropbox_download(DROPBOX_SEQ_PATH, token)
        df  = pd.read_excel(io.BytesIO(raw), header=None)
        insert_row = len(df)
        for i, row in df.iterrows():
            vals = [str(v).strip() for v in row]
            if all(v in ("", "nan") for v in vals):
                insert_row = i
                break
        new_row = ["", cwr_filename, album_name, album_code,
                   date_str, "pending", "auto-generated"]
        df.loc[insert_row] = (new_row + [""] * len(df.columns))[:len(df.columns)]
        buf = io.BytesIO()
        df.to_excel(buf, index=False, header=False)
        buf.seek(0)
        _dropbox_upload(DROPBOX_SEQ_PATH, buf.read(), token)
        return True
    except Exception:
        return False


# ---- LOAD SWN REGISTRY ----
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

# ---- SEQUENCE FROM DROPBOX ----
if "next_seq" not in st.session_state:
    st.session_state["next_seq"] = get_next_sequence_from_dropbox(dropbox_token)

next_seq = st.session_state["next_seq"]

# ---- TABS ----
tab_gen, tab_val, tab_ledger = st.tabs(["⚡  Generate", "🛡️  Validate", "📋  Ledger"])


# ==============================================================================
# SHARED GENERATION LOGIC
# ==============================================================================

def run_generation(tracks, source_label, catalog_key, seq_num,
                   file_bytes_for_validation=None):
    registry     = st.session_state.get("swn_registry")
    starting_swn = get_next_swn(registry) if registry else 1

    with st.status("Processing...", expanded=True) as status:
        st.write(f"📂 Source: **{source_label}** · {len(tracks)} tracks")

        if not tracks:
            st.error("No tracks found.")
            return False

        if not agreement_map:
            st.error("Agreement map empty. Add to Streamlit Secrets under [AGREEMENT_MAP].")
            return False

        st.write(f"🔢 SWN: `{format_swn(starting_swn)}` to "
                 f"`{format_swn(starting_swn + len(tracks) - 1)}`")
        st.write(f"📄 File: `CW26{seq_num:04d}LUM_319.V22`")

        st.write("⚙️ Building CWR records...")
        try:
            cwr_content, gen_warnings, filename, last_swn_used = generate_cwr(
                tracks=tracks,
                catalog_config=catalogs[catalog_key],
                agreement_map=agreement_map,
                sequence_number=seq_num,
                starting_swn=starting_swn
            )
        except CWREngineError as e:
            st.error(f"❌ {e}")
            return False

        for w in gen_warnings:
            st.warning(w)

        st.write("🛡️ Validating...")
        result = validate(cwr_content,
                          source_csv_bytes=file_bytes_for_validation,
                          filename=filename)

        if not result["passed"]:
            status.update(label="Validation failed", state="error")
            for err in result["errors"]:
                st.error(f"Line {err.line} [{err.record_type}]: {err.message}")
            return False

        st.write("💾 Updating SWN registry...")
        album_label = tracks[0].get("album_code", "unknown") if tracks else "unknown"
        album_name  = tracks[0].get("album_title", album_label) if tracks else album_label
        updated = commit_swn_range(
            registry=registry,
            swn_start=starting_swn,
            swn_end=last_swn_used,
            track_count=len(tracks),
            filename=filename,
            album=album_label,
            secrets=st.secrets
        )
        st.session_state["swn_registry"] = updated
        if updated.get("_drive_write_error"):
            st.warning(f"SWN saved locally. Drive error: {updated['_drive_write_error']}")
        else:
            st.write("✅ SWN registry updated")

        if dropbox_token:
            st.write("📊 Updating CWR Sequencing spreadsheet...")
            ok = write_new_row_to_dropbox(
                token=dropbox_token,
                cwr_filename=filename,
                album_name=album_name,
                album_code=album_label,
                date_str=datetime.now().strftime("%d-%b-%y")
            )
            if ok:
                load_sequencing_spreadsheet.clear()
                st.session_state["next_seq"] = seq_num + 1
                st.write("✅ Spreadsheet updated")
            else:
                st.warning("Could not update Dropbox spreadsheet — update manually.")

        status.update(label=f"✅ {filename} ready", state="complete")

    st.session_state.update({
        "cwr_content":   cwr_content,
        "cwr_filename":  filename,
        "cwr_warnings":  result["warnings"],
        "cwr_stats":     result["stats"],
        "cwr_swn_start": starting_swn,
        "cwr_swn_end":   last_swn_used,
    })
    st.rerun()
    return True


# ==============================================================================
# TAB 1 — GENERATOR
# ==============================================================================
with tab_gen:
    st.markdown("### CWR 2.2 Generator")

    registry   = st.session_state.get("swn_registry")
    conflict   = st.session_state.get("swn_conflict")
    load_error = st.session_state.get("swn_load_error")
    swn_blocked = False

    if conflict:
        swn_blocked = True
        st.markdown(f"""
        <div class='swn-error'>
        🔴 <strong>SWN REGISTRY MISMATCH — GENERATION BLOCKED</strong><br>
        Local: <code>{format_swn(conflict.local_val)}</code>
        Drive: <code>{format_swn(conflict.drive_val)}</code><br>
        Choose the correct value to resume.
        </div>""", unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        with c1:
            if st.button(f"Use Drive ({format_swn(conflict.drive_val)})",
                         use_container_width=True):
                st.session_state["swn_registry"] = resolve_conflict(
                    True, conflict.local_val, conflict.drive_val, st.secrets)
                st.session_state["swn_conflict"] = None
                st.rerun()
        with c2:
            if st.button(f"Use Local ({format_swn(conflict.local_val)})",
                         use_container_width=True):
                st.session_state["swn_registry"] = resolve_conflict(
                    False, conflict.local_val, conflict.drive_val, st.secrets)
                st.session_state["swn_conflict"] = None
                st.rerun()

    elif load_error:
        st.markdown(f"""
        <div class='swn-warn'>
        ⚠️ SWN Registry load error — {load_error}
        </div>""", unsafe_allow_html=True)

    elif registry:
        last_swn     = registry.get("last_swn_used", 0)
        last_src     = registry.get("last_swn_source", "—")
        drive_ok     = registry.get("_drive_available", False)
        sync_warn    = registry.get("_sync_warning", "")
        bootstrapped = registry.get("_bootstrapped", False)
        box_class = "swn-warn" if (sync_warn or bootstrapped) else "swn-box"
        icon      = "🟡" if (sync_warn or bootstrapped) else "✅"
        if sync_warn:
            label = "Google Drive offline — using local cache"
        elif bootstrapped:
            label = "Registry bootstrapped from defaults"
        else:
            label = "Registry in sync" + (" (Drive + Local)" if drive_ok else " (Local only)")
        st.markdown(f"""
        <div class='{box_class}'>
        {icon} <strong>SWN Registry — {label}</strong><br>
        Last SWN: <code>{format_swn(last_swn)}</code> · {last_src}<br>
        Next file starts at: <code>{format_swn(get_next_swn(registry))}</code>
        </div>""", unsafe_allow_html=True)

    seq_source = "Vesna's CWR Sequencing spreadsheet" if dropbox_token else "local fallback"
    st.markdown(f"""
    <div class='seq-box'>
    📄 <strong>Next file:</strong> <code>CW26{next_seq:04d}LUM_319.V22</code>
    · Sequence <code>{next_seq:04d}</code> · Source: {seq_source}
    </div>""", unsafe_allow_html=True)

    # ---- SWN RESET (for Vesna — advanced use only) ----
    with st.expander("Reset SWN — use only if a generated file was NOT submitted to ICE"):
        if registry:
            current_last = int(registry.get("last_swn_used", 0))
            st.caption(
                f"Use this only if you generated a CWR file but did NOT submit it to ICE. "
                f"Enter the last SWN that WAS successfully submitted and accepted."
            )
            reset_target = st.number_input(
                "Reset last used SWN to:",
                min_value=1,
                max_value=current_last,
                value=current_last,
                step=1,
                key="swn_reset_input"
            )
            if reset_target >= current_last:
                st.info("Enter a value lower than the current last SWN to enable reset.")
            else:
                st.warning(
                    f"This will set last SWN to **{format_swn(reset_target)}**. "
                    f"Next file will start at **{format_swn(reset_target + 1)}**. "
                    f"Only do this if the file using SWNs "
                    f"{format_swn(reset_target + 1)}-{format_swn(current_last)} "
                    f"was never submitted to ICE."
                )
                if st.button("Confirm Reset", type="primary", key="swn_reset_btn"):
                    from datetime import datetime, timezone
                    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
                    updated_reg = dict(registry)
                    updated_reg["last_swn_used"] = reset_target
                    updated_reg["last_swn_source"] = f"Manual reset by Vesna ({now})"
                    updated_reg["updated"] = now
                    hist = list(registry.get("history", []))
                    hist.append({
                        "file": "MANUAL_RESET",
                        "album": f"Reset from {current_last} to {reset_target}",
                        "swn_start": reset_target + 1,
                        "swn_end": current_last,
                        "track_count": current_last - reset_target,
                        "generated_by": "Manual reset - files not submitted",
                        "date": now,
                    })
                    updated_reg["history"] = hist
                    try:
                        from swn_manager import commit_swn_range
                        token = registry.get("_token", "")
                        repo  = registry.get("_repo", "DP-669/rMG-cwr-converter-Claude-version")
                        sha   = registry.get("_sha")
                        if token:
                            import base64, urllib.request, urllib.error
                            import json as _json
                            url = f"https://api.github.com/repos/{repo}/contents/rMG-cwr-converter/swn_registry.json"
                            clean = {k: v for k, v in updated_reg.items() if not k.startswith("_")}
                            content_b64 = base64.b64encode(
                                _json.dumps(clean, indent=2).encode("utf-8")
                            ).decode("utf-8")
                            payload = _json.dumps({
                                "message": f"Manual SWN reset to {reset_target}",
                                "content": content_b64,
                                "sha": sha,
                            }).encode("utf-8")
                            req = urllib.request.Request(
                                url, data=payload, method="PUT",
                                headers={
                                    "Authorization": f"token {token}",
                                    "Accept": "application/vnd.github.v3+json",
                                    "Content-Type": "application/json",
                                }
                            )
                            with urllib.request.urlopen(req, timeout=15) as resp:
                                resp.read()
                            updated_reg["_github_available"] = True
                            updated_reg["_sync_warning"] = ""
                            st.session_state["swn_registry"] = updated_reg
                            st.success(
                                f"Reset complete. Last SWN is now {format_swn(reset_target)}. "
                                f"Next file starts at {format_swn(reset_target + 1)}."
                            )
                            st.rerun()
                        else:
                            st.error("No GitHub token configured. Cannot persist reset.")
                    except Exception as e:
                        st.error(f"Reset failed: {e}")
        else:
            st.info("Registry not loaded — cannot reset.")

    col_opt1, col_opt2 = st.columns(2)
    with col_opt1:
        catalog_key = st.selectbox(
            "Catalog",
            options=list(catalogs.keys()),
            format_func=lambda k: f"{k} — {catalogs[k]['label']}"
        )
    with col_opt2:
        seq_override = st.number_input(
            "Sequence (auto — override if needed)",
            min_value=1, max_value=9999,
            value=int(next_seq), step=1
        )

    if swn_blocked:
        st.warning("⛔ Resolve SWN conflict above before generating.")
    else:
        mode_csv, mode_api = st.tabs(["📄  Upload CSV", "🔗  Fetch from SourceAudio"])

        with mode_csv:
            uploaded_csv = st.file_uploader(
                "Upload source CSV (SourceAudio or Harvest Media)",
                type=["csv"], key="gen_csv"
            )
            if uploaded_csv:
                if st.button("Generate CWR File", type="primary",
                             use_container_width=True, key="gen_csv_btn"):
                    try:
                        file_bytes = uploaded_csv.getvalue()
                        tracks, fmt_detected, parse_warnings = parse_csv(
                            file_bytes, uploaded_csv.name)
                        for w in parse_warnings:
                            st.warning(w)
                        run_generation(tracks, f"CSV ({fmt_detected.upper()})",
                                       catalog_key, int(seq_override), file_bytes)
                    except ParseError as e:
                        st.error(f"❌ {e}")

        with mode_api:
            if not sa_token:
                st.warning("SourceAudio token not configured. "
                           "Add [SOURCEAUDIO] token to Streamlit Secrets.")
            else:
                album_input = st.text_input("Album code", placeholder="e.g. RC055")
                if album_input:
                    if st.button("Fetch from SourceAudio", type="primary",
                                 use_container_width=True, key="gen_api_btn"):
                        try:
                            from sourceaudio_fetcher import (fetch_album_tracks,
                                                             SourceAudioError)
                            with st.spinner(f"Fetching {album_input.strip().upper()}..."):
                                tracks, fetch_warnings = fetch_album_tracks(
                                    token=sa_token,
                                    album_code=album_input.strip().upper()
                                )
                            for w in fetch_warnings:
                                st.warning(w)
                            st.success(f"✅ {len(tracks)} tracks fetched.")
                            run_generation(tracks,
                                           f"SourceAudio ({album_input.strip().upper()})",
                                           catalog_key, int(seq_override))
                        except Exception as e:
                            st.error(f"❌ {e}")

    if "cwr_content" in st.session_state and "cwr_filename" in st.session_state:
        filename  = st.session_state["cwr_filename"]
        stats     = st.session_state.get("cwr_stats", {})
        warns     = st.session_state.get("cwr_warnings", [])
        swn_start = st.session_state.get("cwr_swn_start")
        swn_end   = st.session_state.get("cwr_swn_end")

        st.divider()
        c1, c2, c3 = st.columns(3)
        c1.metric("Tracks", stats.get("nwr_count", "—"))
        c2.metric("File", filename)
        c3.metric("Status", "✅ PASS")

        if swn_start and swn_end:
            st.markdown(f"""
            <div class='swn-box'>
            ✅ SWN <code>{format_swn(swn_start)}</code> to <code>{format_swn(swn_end)}</code>
            · Next: <code>{format_swn(swn_end + 1)}</code>
            </div>""", unsafe_allow_html=True)

        if warns:
            with st.expander(f"⚠️ {len(warns)} warning(s)"):
                for w in warns:
                    st.warning(f"Line {w.line} [{w.record_type}]: {w.message}")

        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(filename,
                        st.session_state["cwr_content"].encode("latin-1"))

        st.download_button(
            label=f"⬇️  Download {filename}.zip",
            data=zip_buf.getvalue(),
            file_name=f"{filename}.zip",
            mime="application/zip",
            use_container_width=True,
            type="primary"
        )

        if st.button("Clear and start over", use_container_width=True):
            for k in ("cwr_content", "cwr_filename", "cwr_warnings",
                      "cwr_stats", "cwr_swn_start", "cwr_swn_end"):
                st.session_state.pop(k, None)
            st.rerun()


# ==============================================================================
# TAB 2 — VALIDATOR
# ==============================================================================
with tab_val:
    st.markdown("### CWR Geometry Validator")
    st.caption("Upload a .V22 file to check geometry, field positions, and share totals.")

    c1, c2 = st.columns(2)
    with c1:
        v22_file = st.file_uploader("1. Upload .V22 file",
                                    type=["V22", "v22", "txt"], key="val_v22")
    with c2:
        csv_mirror = st.file_uploader("2. Source CSV (optional)",
                                      type=["csv"], key="val_csv")

    if v22_file:
        if st.button("Run Validation", type="primary", use_container_width=True):
            cwr_content = v22_file.getvalue().decode("latin-1")
            csv_bytes   = csv_mirror.getvalue() if csv_mirror else None
            with st.spinner("Validating..."):
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
                st.success("✅ All checks passed.")
            else:
                st.error(f"❌ {len(result['errors'])} error(s).")
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
# TAB 3 — LEDGER
# ==============================================================================
with tab_ledger:
    st.markdown("### CWR Registration Ledger")

    if not dropbox_token:
        st.warning("No Dropbox token. Add [DROPBOX] token to Streamlit Secrets.")
    else:
        if st.button("🔄 Refresh"):
            load_sequencing_spreadsheet.clear()
            st.session_state["next_seq"] = get_next_sequence_from_dropbox(dropbox_token)
            st.rerun()

        df = load_sequencing_spreadsheet(dropbox_token)

        if df is None:
            st.error("Could not load spreadsheet from Dropbox. Check token and file path.")
        elif df.empty:
            st.info("Spreadsheet loaded — no entries found.")
        else:
            st.caption("Live view of Vesna's CWR Sequencing spreadsheet. "
                       "Edit directly in Dropbox.")

            def row_style(row):
                s = str(row.get("Status", row.get("status", ""))).lower()
                if s == "accepted":
                    return ["background-color:#F0FFF4"] * len(row)
                if s == "failed":
                    return ["background-color:#FFF0F0"] * len(row)
                if s in ("pending", "ongoing"):
                    return ["background-color:#FFFBF0"] * len(row)
                return [""] * len(row)

            st.dataframe(
                df.style.apply(row_style, axis=1),
                use_container_width=True,
                hide_index=True
            )

    st.divider()
    st.markdown(f"**Next sequence: `{next_seq:04d}`** "
                f"· Next file: **`CW26{next_seq:04d}LUM_319.V22`**")

    reg = st.session_state.get("swn_registry")
    if reg:
        st.divider()
        st.markdown("### SWN Registry")
        c1, c2, c3 = st.columns(3)
        c1.metric("Last SWN", format_swn(reg.get("last_swn_used", 0)))
        c2.metric("Next available", format_swn(get_next_swn(reg)))
        c3.metric("Updated", reg.get("updated", "—")[:10])
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
