# ==============================================================================
# rMG CWR CONVERTER — STREAMLIT APP
# Claude Version | DP-669/rMG-cwr-converter-Claude-version
#
# Tab 1: Generate — upload CSV or fetch from SourceAudio, generate .V22, ZIP
# Tab 2: Validate — upload .V22 (+ optional source CSV), run geometry audit
# Tab 3: Ledger   — log accepted files, track sequence numbers
# ==============================================================================

import streamlit as st
import pandas as pd
import io
import json
import zipfile
import os
import time
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
from sourceaudio_fetcher import (
    load_config as sa_load_config,
    check_config as sa_check_config,
    fetch_albums, fetch_tracks_for_album, fetch_all_tracks, fetch_swn_status,
    SourceAudioError, SourceAudioConfigError,
)

# ── PAGE CONFIG ────────────────────────────────────────────────────────────────
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
    .main-title { text-align: center; font-size: 2.2rem; font-weight: 700; color: #1A1A1A; margin-bottom: 0; }
    .sub-title  { text-align: center; font-size: 1rem; color: #888; margin-top: 4px; margin-bottom: 24px; }
    div[data-testid="metric-container"] { text-align: center; }
    .swn-box { background: #F0F4FF; border: 1px solid #C8D8FF; border-radius: 8px;
               padding: 14px 18px; margin-bottom: 16px; font-size: 0.92rem; }
    .swn-error { background: #FFF0F0; border: 1px solid #FFB3B3; border-radius: 8px;
                 padding: 14px 18px; margin-bottom: 16px; }
    .swn-warn  { background: #FFFBF0; border: 1px solid #FFE0A0; border-radius: 8px;
                 padding: 14px 18px; margin-bottom: 16px; }
    .sa-box    { background: #F0FFF4; border: 1px solid #B3FFD1; border-radius: 8px;
                 padding: 14px 18px; margin-bottom: 16px; font-size: 0.92rem; }
    .swn-flag  { color: #CC6600; font-weight: 600; }
    .swn-ok    { color: #007700; font-weight: 600; }
</style>
""", unsafe_allow_html=True)

import os
if os.path.exists("assets/lumina_logo.png"):
    _, col_logo, _ = st.columns([2, 1, 2])
    with col_logo:
        st.image("assets/lumina_logo.png", use_container_width=True)

st.markdown("<h1 class='main-title'>rMG CWR Converter</h1>", unsafe_allow_html=True)
st.markdown("<p class='sub-title'>CWR 2.2 · ICE · PRS · Claude Version</p>", unsafe_allow_html=True)

# ── LOAD CONFIG ────────────────────────────────────────────────────────────────
if "LUMINA" in st.secrets:
    lumina_cfg = dict(st.secrets["LUMINA"])
else:
    lumina_cfg = config.LUMINA

if "AGREEMENT_MAP" in st.secrets:
    agreement_map = dict(st.secrets["AGREEMENT_MAP"])
else:
    agreement_map = config.AGREEMENT_MAP

catalogs = config.CATALOGS
for k in catalogs:
    catalogs[k]["lumina_name"]   = lumina_cfg.get("name",   catalogs[k]["lumina_name"])
    catalogs[k]["lumina_ipi"]    = lumina_cfg.get("ipi",    catalogs[k]["lumina_ipi"])
    catalogs[k]["lumina_pub_id"] = lumina_cfg.get("pub_id", catalogs[k]["lumina_pub_id"])

# ── SEQUENCE LEDGER ────────────────────────────────────────────────────────────
SEQ_FILE    = "cwr_sequence_log.json"
current_year = datetime.now().year

if not os.path.exists(SEQ_FILE):
    with open(SEQ_FILE, 'w') as f:
        json.dump({"year": current_year, "history": []}, f)

with open(SEQ_FILE, 'r') as f:
    seq_data = json.load(f)

if current_year > seq_data.get("year", 0):
    seq_data["year"]    = current_year
    seq_data["history"] = []
    with open(SEQ_FILE, 'w') as f:
        json.dump(seq_data, f)

history  = seq_data.get("history", [])
next_seq = max([item["sequence"] for item in history] + [0]) + 1

# ── LOAD SWN REGISTRY ─────────────────────────────────────────────────────────
if 'swn_registry' not in st.session_state:
    try:
        st.session_state['swn_registry'] = load_registry(st.secrets)
        st.session_state['swn_conflict'] = None
    except SWNSyncMismatch as e:
        st.session_state['swn_registry'] = None
        st.session_state['swn_conflict'] = e
    except Exception as e:
        st.session_state['swn_registry'] = None
        st.session_state['swn_conflict'] = None
        st.session_state['swn_load_error'] = str(e)

# ── TABS ───────────────────────────────────────────────────────────────────────
tab_gen, tab_val, tab_ledger = st.tabs(["⚡  Generate", "🛡️  Validate", "📋  Ledger"])


# ==============================================================================
# TAB 1 — GENERATOR
# ==============================================================================
with tab_gen:
    st.markdown("### CWR 2.2 Generator")

    # ── SWN REGISTRY PANEL ────────────────────────────────────────────────────
    registry    = st.session_state.get('swn_registry')
    conflict    = st.session_state.get('swn_conflict')
    load_error  = st.session_state.get('swn_load_error')
    swn_blocked = False

    if conflict:
        swn_blocked = True
        st.markdown(f"""
        <div class='swn-error'>
        🔴 <strong>SWN REGISTRY MISMATCH — GENERATION BLOCKED</strong><br>
        Local cache says last SWN = <code>{format_swn(conflict.local_val)}</code><br>
        Google Drive says last SWN = <code>{format_swn(conflict.drive_val)}</code><br><br>
        Choose which value is correct to resume generation.
        </div>
        """, unsafe_allow_html=True)

        col_r1, col_r2 = st.columns(2)
        with col_r1:
            if st.button(f"✅ Use Google Drive value ({format_swn(conflict.drive_val)})",
                         use_container_width=True):
                resolved = resolve_conflict(
                    use_drive=True, local_val=conflict.local_val,
                    drive_val=conflict.drive_val, secrets=st.secrets
                )
                st.session_state['swn_registry'] = resolved
                st.session_state['swn_conflict']  = None
                st.rerun()
        with col_r2:
            if st.button(f"Use Local cache value ({format_swn(conflict.local_val)})",
                         use_container_width=True):
                resolved = resolve_conflict(
                    use_drive=False, local_val=conflict.local_val,
                    drive_val=conflict.drive_val, secrets=st.secrets
                )
                st.session_state['swn_registry'] = resolved
                st.session_state['swn_conflict']  = None
                st.rerun()

    elif load_error:
        st.markdown(f"""
        <div class='swn-warn'>
        ⚠️ <strong>SWN Registry load error</strong> — {load_error}<br>
        Generation will proceed using bootstrap values. Verify after generation.
        </div>
        """, unsafe_allow_html=True)

    elif registry:
        next_swn_val = get_next_swn(registry)
        last_swn     = registry.get('last_swn_used', 0)
        last_src     = registry.get('last_swn_source', '—')
        drive_ok     = registry.get('_drive_available', False)
        sync_warn    = registry.get('_sync_warning', '')
        bootstrapped = registry.get('_bootstrapped', False)

        if sync_warn:
            status_icon  = "🟡"
            status_label = "Google Drive offline — using local cache"
            box_class    = "swn-warn"
        elif bootstrapped:
            status_icon  = "🟡"
            status_label = "Registry bootstrapped from defaults — no prior files found"
            box_class    = "swn-warn"
        else:
            status_icon  = "✅"
            status_label = "Registry in sync" + (" (Drive + Local)" if drive_ok else " (Local only)")
            box_class    = "swn-box"

        st.markdown(f"""
        <div class='{box_class}'>
        {status_icon} <strong>SWN Registry — {status_label}</strong><br>
        Last registered SWN: <code>{format_swn(last_swn)}</code> &nbsp;·&nbsp; {last_src}<br>
        Next file starts at: <code>{format_swn(next_swn_val)}</code>
        {('<br>⚠️ ' + sync_warn) if sync_warn else ''}
        </div>
        """, unsafe_allow_html=True)

    # ── DATA SOURCE TOGGLE ────────────────────────────────────────────────────
    st.markdown("#### Data Source")
    data_source = st.radio(
        "How do you want to load track data?",
        options=["upload_csv", "sourceaudio_api"],
        format_func=lambda x: {
            "upload_csv":      "📄 Upload CSV (SourceAudio export or Harvest Media)",
            "sourceaudio_api": "🔗 Fetch from SourceAudio API",
        }[x],
        horizontal=True,
        key="data_source_radio",
    )

    # ── OPTIONS ───────────────────────────────────────────────────────────────
    col_options_left, col_options_right = st.columns(2)
    with col_options_left:
        catalog_key = st.selectbox(
            "Catalog",
            options=list(catalogs.keys()),
            format_func=lambda k: f"{k} — {catalogs[k]['label']}"
        )
    with col_options_right:
        seq_override = st.number_input(
            "Sequence number",
            min_value=1, max_value=9999,
            value=int(next_seq), step=1
        )

    # ── PATH A: CSV UPLOAD ────────────────────────────────────────────────────
    if data_source == "upload_csv":
        uploaded_csv = st.file_uploader(
            "Upload source CSV (SourceAudio export or Harvest Media)",
            type=["csv"],
            key="gen_csv"
        )

        if uploaded_csv and not swn_blocked:
            if registry:
                next_swn_val = get_next_swn(registry)
                st.info(
                    f"📋 **SWN preview** — this file will use "
                    f"`{format_swn(next_swn_val)}` onward "
                    f"(exact end depends on track count)"
                )

            if st.button("Generate CWR File", type="primary", use_container_width=True,
                         key="gen_btn_csv"):
                _run_generation(
                    file_bytes=uploaded_csv.getvalue(),
                    filename=uploaded_csv.name,
                    catalog_key=catalog_key,
                    seq_override=seq_override,
                    registry=registry,
                    agreement_map=agreement_map,
                    catalogs=catalogs,
                )

        elif swn_blocked:
            st.warning("⛔ Resolve the SWN registry conflict above before generating.")

    # ── PATH B: SOURCEAUDIO API ───────────────────────────────────────────────
    else:
        sa_status = sa_check_config(st.secrets)

        if not sa_status["configured"]:
            st.markdown(f"""
            <div class='swn-warn'>
            ⚠️ <strong>SourceAudio API not configured</strong><br>
            {sa_status['error']}<br><br>
            Add the following to your Streamlit Secrets to enable API fetch mode:<br>
            <pre>[SOURCEAUDIO]
api_base_url = "https://[your-library].sourceaudio.net/api"
api_token    = "your-api-token-here"
library_name = "redCola"</pre>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown(f"""
            <div class='sa-box'>
            ✅ <strong>SourceAudio API configured</strong><br>
            Base URL: <code>{sa_status['base_url']}</code>
            {(' &nbsp;·&nbsp; Library: <code>' + sa_status['library'] + '</code>') if sa_status['library'] else ''}
            </div>
            """, unsafe_allow_html=True)

            # Album picker or all-tracks fetch
            fetch_mode = st.radio(
                "Fetch scope",
                options=["by_album", "all_tracks"],
                format_func=lambda x: {
                    "by_album":   "📀 Fetch a specific album",
                    "all_tracks": "📚 Fetch all tracks in library",
                }[x],
                horizontal=True,
                key="sa_fetch_mode",
            )

            sa_album_id    = None
            sa_album_label = ""

            if fetch_mode == "by_album":
                if st.button("🔄 Load album list from SourceAudio", key="sa_load_albums"):
                    with st.spinner("Loading albums..."):
                        try:
                            cfg    = sa_load_config(st.secrets)
                            albums = fetch_albums(cfg)
                            st.session_state['sa_albums'] = albums
                        except SourceAudioError as e:
                            st.error(f"❌ SourceAudio error: {e}")

                if 'sa_albums' in st.session_state:
                    albums = st.session_state['sa_albums']
                    if albums:
                        album_options = {
                            a['id']: f"{a['code']} — {a['title']} ({a['track_count']} tracks)"
                            for a in albums
                        }
                        sa_album_id = st.selectbox(
                            "Select album",
                            options=list(album_options.keys()),
                            format_func=lambda x: album_options[x],
                            key="sa_album_select",
                        )
                        sa_album_label = album_options.get(sa_album_id, "")
                    else:
                        st.warning("No albums found in your SourceAudio library.")

            # Fetch tracks + generate
            fetch_label = (
                f"Fetch tracks for: {sa_album_label}" if sa_album_id
                else "Fetch all tracks from SourceAudio"
                if fetch_mode == "all_tracks"
                else "Select an album above first"
            )

            can_fetch = (fetch_mode == "all_tracks") or (sa_album_id is not None)

            if can_fetch and not swn_blocked:
                if st.button(
                    f"⬇️ {fetch_label} and Generate CWR",
                    type="primary",
                    use_container_width=True,
                    key="sa_gen_btn",
                    disabled=not can_fetch,
                ):
                    with st.status("Fetching from SourceAudio...", expanded=True) as status:
                        try:
                            cfg = sa_load_config(st.secrets)

                            if fetch_mode == "by_album" and sa_album_id:
                                st.write(f"📀 Fetching tracks for album {sa_album_label}...")
                                tracks, sa_warnings = fetch_tracks_for_album(cfg, sa_album_id)
                            else:
                                st.write("📚 Fetching all tracks from SourceAudio library...")
                                tracks, sa_warnings = fetch_all_tracks(cfg)

                            if sa_warnings:
                                for w in sa_warnings:
                                    st.warning(w)

                            if not tracks:
                                status.update(label="No tracks returned from API", state="error")
                                st.error("No tracks found. Check album selection or API token.")
                                st.stop()

                            st.write(f"✅ Fetched **{len(tracks)} tracks** from SourceAudio.")

                            # SWN status — annotate tracks before generation
                            if registry:
                                tracks_annotated = fetch_swn_status(tracks, registry)
                                unassigned = [t for t in tracks_annotated if t.get("_swn_status") == "unassigned"]
                                assigned   = [t for t in tracks_annotated if t.get("_swn_status") == "assigned"]
                                unknown    = [t for t in tracks_annotated if t.get("_swn_status") == "unknown"]

                                swn_summary_parts = [f"🆕 {len(unassigned)} unassigned (will get new SWNs)"]
                                if assigned:
                                    swn_summary_parts.append(f"✅ {len(assigned)} already submitted")
                                if unknown:
                                    swn_summary_parts.append(f"❓ {len(unknown)} no ISRC")
                                st.info("**SWN status:** " + " · ".join(swn_summary_parts))

                                if assigned:
                                    st.warning(
                                        f"⚠️ {len(assigned)} track(s) already have SWNs in the registry. "
                                        "They will be re-registered with new SWNs if you proceed."
                                    )

                            status.update(label=f"✅ {len(tracks)} tracks ready. Running CWR generation...",
                                          state="running")

                            # Convert tracks to CSV bytes for compatibility with existing generation flow
                            # Use the normalised track list directly — pass to generate_cwr via in-memory path
                            _run_generation_from_tracks(
                                tracks=tracks,
                                source_label=sa_album_label or "SourceAudio library",
                                catalog_key=catalog_key,
                                seq_override=seq_override,
                                registry=registry,
                                agreement_map=agreement_map,
                                catalogs=catalogs,
                                status_widget=status,
                            )

                        except SourceAudioError as e:
                            st.error(f"❌ SourceAudio API error: {e}")
                        except Exception as e:
                            st.error(f"❌ Unexpected error: {e}")
                            raise

            elif swn_blocked:
                st.warning("⛔ Resolve the SWN registry conflict above before generating.")

    # ── DOWNLOAD SECTION (persists after rerun) ───────────────────────────────
    if 'cwr_content' in st.session_state and 'cwr_filename' in st.session_state:
        filename  = st.session_state['cwr_filename']
        stats     = st.session_state.get('cwr_stats', {})
        warns     = st.session_state.get('cwr_warnings', [])
        swn_start = st.session_state.get('cwr_swn_start')
        swn_end   = st.session_state.get('cwr_swn_end')
        swn_table = st.session_state.get('cwr_swn_table', [])

        st.divider()
        col_m1, col_m2, col_m3 = st.columns(3)
        col_m1.metric("Tracks (NWR)", stats.get('nwr_count', '—'))
        col_m2.metric("Filename", filename)
        col_m3.metric("Status", "✅ PASS")

        if swn_start and swn_end:
            st.markdown(f"""
            <div class='swn-box'>
            ✅ <strong>SWN range used:</strong>
            <code>{format_swn(swn_start)}</code> → <code>{format_swn(swn_end)}</code>
            &nbsp;·&nbsp; Next file starts at: <code>{format_swn(swn_end + 1)}</code>
            </div>
            """, unsafe_allow_html=True)

        # ── SWN per-track table ───────────────────────────────────────────────
        if swn_table:
            # Check for any tracks that ended up without SWN (safety flag)
            missing_swn = [row for row in swn_table if not row.get("swn")]
            if missing_swn:
                st.markdown(
                    f"<div class='swn-error'>⚠️ <strong>SWN MISSING</strong>: "
                    f"{len(missing_swn)} track(s) were not assigned an SWN. "
                    f"Do NOT submit this file to ICE/PRS until resolved.</div>",
                    unsafe_allow_html=True
                )
                for row in missing_swn:
                    st.error(f"❌ No SWN: {row.get('title', 'Unknown')} | ISRC: {row.get('isrc', '—')}")

            with st.expander(f"📋 SWN assignments — all {len(swn_table)} tracks"):
                df = pd.DataFrame(swn_table)
                if 'swn' in df.columns:
                    df['swn'] = df['swn'].apply(lambda x: format_swn(int(x)) if x else "⚠️ MISSING")
                cols = [c for c in ['swn', 'title', 'isrc', 'album_code'] if c in df.columns]
                st.dataframe(df[cols], use_container_width=True, hide_index=True)

        if warns:
            with st.expander(f"⚠️ {len(warns)} warning(s)"):
                for w in warns:
                    st.warning(f"Line {w.line} [{w.record_type}]: {w.message}")

        # Package as ZIP
        zip_buf = io.BytesIO()
        cwr_bytes = st.session_state['cwr_content'].encode('latin-1')
        with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(filename, cwr_bytes)

        st.download_button(
            label=f"⬇️  Download {filename}.zip",
            data=zip_buf.getvalue(),
            file_name=f"{filename}.zip",
            mime="application/zip",
            use_container_width=True,
            type="primary"
        )

        if st.button("Clear and start over", use_container_width=True):
            for k in ('cwr_content', 'cwr_filename', 'cwr_warnings', 'cwr_stats',
                      'cwr_swn_start', 'cwr_swn_end', 'cwr_swn_table'):
                st.session_state.pop(k, None)
            st.rerun()


# ==============================================================================
# GENERATION HELPERS
# ==============================================================================

def _run_generation(file_bytes, filename, catalog_key, seq_override,
                    registry, agreement_map, catalogs):
    """Full generation flow from raw CSV bytes."""
    try:
        with st.status("Processing...", expanded=True) as status:

            st.write("📂 Detecting and parsing CSV format...")
            tracks, fmt_detected, parse_warnings = parse_csv(file_bytes, filename)
            st.write(f"✅ Detected format: **{fmt_detected.upper()}** · {len(tracks)} tracks found")

            if parse_warnings:
                for w in parse_warnings:
                    st.warning(w)

            if not tracks:
                st.error("No tracks found in CSV. Check the file format.")
                st.stop()

            _generate_and_commit(
                tracks=tracks, source_csv_bytes=file_bytes,
                catalog_key=catalog_key, seq_override=seq_override,
                registry=registry, agreement_map=agreement_map,
                catalogs=catalogs, status=status,
            )

    except (ParseError, CWREngineError) as e:
        st.error(f"❌ {e}")
    except Exception as e:
        st.error(f"❌ Unexpected error: {e}")
        raise


def _run_generation_from_tracks(tracks, source_label, catalog_key, seq_override,
                                  registry, agreement_map, catalogs, status_widget):
    """Full generation flow from pre-fetched normalised track list."""
    try:
        _generate_and_commit(
            tracks=tracks, source_csv_bytes=None,
            catalog_key=catalog_key, seq_override=seq_override,
            registry=registry, agreement_map=agreement_map,
            catalogs=catalogs, status=status_widget,
        )
    except CWREngineError as e:
        st.error(f"❌ CWR Engine error: {e}")
    except Exception as e:
        st.error(f"❌ Unexpected error: {e}")
        raise


def _generate_and_commit(tracks, source_csv_bytes, catalog_key, seq_override,
                          registry, agreement_map, catalogs, status):
    """Shared generation, validation, and SWN commit logic."""

    if not agreement_map:
        st.error(
            "Agreement map is empty. Add publisher → agreement number mappings "
            "to Streamlit Secrets under [AGREEMENT_MAP]."
        )
        st.stop()

    starting_swn = get_next_swn(registry) if registry else 1
    st.write(f"🔢 SWN range: `{format_swn(starting_swn)}` onward · {len(tracks)} tracks")

    st.write("⚙️ Building CWR records (canvas stamper)...")
    catalog_config = catalogs[catalog_key]
    cwr_content, gen_warnings, filename, last_swn_used = generate_cwr(
        tracks=tracks,
        catalog_config=catalog_config,
        agreement_map=agreement_map,
        sequence_number=int(seq_override),
        starting_swn=starting_swn,
    )

    if gen_warnings:
        for w in gen_warnings:
            st.warning(w)

    # Build per-track SWN table for UI display + safety check
    swn_table = []
    for i, track in enumerate(tracks):
        swn_num = starting_swn + i
        swn_table.append({
            "swn":        swn_num,
            "title":      track.get("title", ""),
            "isrc":       track.get("isrc", ""),
            "album_code": track.get("album_code", ""),
        })

    # Flag any tracks missing SWN (should never happen — belt-and-braces)
    missing = [r for r in swn_table if not r.get("swn")]
    if missing:
        status.update(label=f"⚠️ {len(missing)} track(s) missing SWN — check before submitting",
                      state="error")
        st.error(
            f"❌ {len(missing)} track(s) were not assigned a Submitter Work Number. "
            "This file must NOT be submitted to ICE/PRS until resolved."
        )
        for row in missing:
            st.error(f"  Missing SWN: '{row.get('title')}' | ISRC: {row.get('isrc', '—')}")

    st.write("🛡️ Running geometry validation...")
    result = validate(cwr_content, source_csv_bytes=source_csv_bytes, filename=filename)

    if not result['passed']:
        status.update(label="Generation failed — validation errors found", state="error")
        st.error(f"❌ {len(result['errors'])} critical error(s) found. File NOT generated.")
        for err in result['errors']:
            st.error(f"Line {err.line} [{err.record_type}]: {err.message}")
            if err.excerpt:
                with st.expander("Show context"):
                    st.code(err.excerpt)
        st.stop()

    st.write("💾 Updating SWN registry...")
    album_label = tracks[0].get('album_code', 'unknown') if tracks else 'unknown'
    updated_registry = commit_swn_range(
        registry=registry,
        swn_start=starting_swn,
        swn_end=last_swn_used,
        track_count=len(tracks),
        filename=filename,
        album=album_label,
        secrets=st.secrets,
    )
    st.session_state['swn_registry'] = updated_registry

    drive_write_err = updated_registry.get('_drive_write_error')
    if drive_write_err:
        st.warning(f"⚠️ SWN saved locally but Google Drive write failed: {drive_write_err}")
    else:
        st.write("✅ SWN registry updated (local + Google Drive)")

    status.update(label=f"✅ {filename} ready", state="complete")

    st.session_state['cwr_content']   = cwr_content
    st.session_state['cwr_filename']  = filename
    st.session_state['cwr_warnings']  = result['warnings']
    st.session_state['cwr_stats']     = result['stats']
    st.session_state['cwr_swn_start'] = starting_swn
    st.session_state['cwr_swn_end']   = last_swn_used
    st.session_state['cwr_swn_table'] = swn_table

    st.rerun()


# ==============================================================================
# TAB 2 — VALIDATOR
# ==============================================================================
with tab_val:
    st.markdown("### CWR Geometry Validator")
    st.caption("Upload a .V22 file to check geometry, field positions, and share totals. "
               "Optionally upload the source CSV to enable the mirror audit.")

    col_v1, col_v2 = st.columns(2)
    with col_v1:
        v22_file = st.file_uploader("1. Upload .V22 file", type=["V22", "v22", "txt"], key="val_v22")
    with col_v2:
        csv_mirror = st.file_uploader("2. Source CSV (optional — enables mirror audit)",
                                       type=["csv"], key="val_csv")

    if v22_file:
        if st.button("Run Validation", type="primary", use_container_width=True):
            cwr_content = v22_file.getvalue().decode('latin-1')
            csv_bytes   = csv_mirror.getvalue() if csv_mirror else None

            with st.spinner("Validating..."):
                result = validate(cwr_content, source_csv_bytes=csv_bytes, filename=v22_file.name)

            st.divider()

            col_m1, col_m2, col_m3, col_m4 = st.columns(4)
            col_m1.metric("NWR Records",   result['stats']['nwr_count'])
            col_m2.metric("SPU Records",   result['stats']['spu_count'])
            col_m3.metric("SWR Records",   result['stats']['swr_count'])
            col_m4.metric("REC Records",   result['stats']['rec_count'])

            if result['passed']:
                st.success("✅ All checks passed — file is geometrically valid.")
            else:
                st.error(f"❌ {len(result['errors'])} critical error(s) found.")

            if result['errors']:
                st.markdown("#### 🔴 Critical Errors")
                for err in result['errors']:
                    st.error(f"**Line {err.line}** `[{err.record_type}]` {err.message}")
                    if err.excerpt:
                        with st.expander("Show context"):
                            st.code(err.excerpt)

            if result['warnings']:
                st.markdown("#### 🟡 Warnings")
                for w in result['warnings']:
                    st.warning(f"**Line {w.line}** `[{w.record_type}]` {w.message}")
                    if w.excerpt:
                        with st.expander("Show context"):
                            st.code(w.excerpt)

            if result['passed'] and not result['warnings']:
                st.balloons()


# ==============================================================================
# TAB 3 — LEDGER
# ==============================================================================
with tab_ledger:
    st.markdown("### Accepted File Ledger")
    st.caption("Log files accepted by ICE or PRS to track sequence numbers.")

    with st.expander("➕ Log a new accepted file"):
        uploaded_v22 = st.file_uploader("Upload accepted .V22",
                                         type=["V22", "v22", "txt"], key="ledger_upload")

        if uploaded_v22:
            fname = uploaded_v22.name
            try:
                seq_str       = fname[4:8]
                extracted_seq = int(seq_str)
            except (ValueError, IndexError):
                extracted_seq = 0

            content = uploaded_v22.getvalue().decode('latin-1')
            lines   = [l for l in content.replace('\r\n', '\n').split('\n') if l.strip()]

            library_name = "UNKNOWN"
            album_code   = "UNKNOWN"
            nwr_count    = sum(1 for l in lines if l[:3] in ('NWR', 'REV'))

            for line in lines:
                if line[:3] == 'ORN' and len(line) >= 102:
                    album_code   = line[82:97].strip()
                    library_name = line[101:].strip()[:40]
                    break

            label = f"Seq {extracted_seq:04d} · {album_code} · {library_name} · {nwr_count} tracks"
            st.info(f"Detected: **{label}**")

            col_soc1, col_soc2 = st.columns(2)
            accepted_by   = col_soc1.selectbox("Accepted by",
                                               ["ICE (Berlin)", "PRS (London)", "Both", "Other"])
            accepted_date = col_soc2.date_input("Date accepted", value=datetime.today())

            if st.button("✅ Mark as Accepted"):
                if not any(item["sequence"] == extracted_seq for item in history):
                    seq_data["history"].append({
                        "sequence":    extracted_seq,
                        "label":       label,
                        "accepted_by": accepted_by,
                        "date":        str(accepted_date),
                        "filename":    fname,
                    })
                    with open(SEQ_FILE, 'w') as f:
                        json.dump(seq_data, f)
                    st.success("Logged.")
                    time.sleep(0.8)
                    st.rerun()
                else:
                    st.warning(f"Sequence {extracted_seq:04d} already in ledger.")

    st.divider()
    st.markdown(f"**Next available sequence number: `{next_seq:04d}`**")
    st.markdown(f"Year: `{current_year}`")

    # SWN summary in Ledger tab
    reg = st.session_state.get('swn_registry')
    if reg:
        st.divider()
        st.markdown("### SWN Registry")
        st.markdown(f"**Last SWN used:** `{format_swn(reg.get('last_swn_used', 0))}`")
        st.markdown(f"**Source:** {reg.get('last_swn_source', '—')}")
        st.markdown(f"**Next available:** `{format_swn(get_next_swn(reg))}`")
        st.markdown(f"**Last updated:** {reg.get('updated', '—')}")

        swn_history = reg.get('history', [])
        if swn_history:
            with st.expander(f"SWN history ({len(swn_history)} entries)"):
                df = pd.DataFrame(reversed(swn_history))
                if 'swn_start' in df.columns:
                    df['swn_start'] = df['swn_start'].apply(lambda x: format_swn(int(x)))
                if 'swn_end' in df.columns:
                    df['swn_end']   = df['swn_end'].apply(lambda x: format_swn(int(x)))
                cols = [c for c in ['date', 'file', 'album', 'swn_start', 'swn_end',
                                    'track_count', 'generated_by'] if c in df.columns]
                st.dataframe(df[cols], use_container_width=True, hide_index=True)

    if history:
        st.divider()
        st.markdown("### File Sequence Ledger")
        ledger_df = pd.DataFrame(reversed(history))
        ledger_df['sequence'] = ledger_df['sequence'].apply(lambda x: f"{int(x):04d}")
        st.dataframe(
            ledger_df[['sequence', 'filename', 'accepted_by', 'date']],
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("No accepted files logged yet.")
