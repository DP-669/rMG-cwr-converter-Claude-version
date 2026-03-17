# ==============================================================================
# rMG CWR CONVERTER — STREAMLIT APP
# Claude Version | DP-669/rMG-cwr-converter-Claude-version
#
# Tab 1: Generate — upload CSV, select catalog, generate .V22, download ZIP
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
    .main-title { text-align: center; font-size: 2.2rem; font-weight: 700; color: #1A1A1A; margin-bottom: 0; }
    .sub-title  { text-align: center; font-size: 1rem; color: #888; margin-top: 4px; margin-bottom: 24px; }
    div[data-testid="metric-container"] { text-align: center; }
</style>
""", unsafe_allow_html=True)

import os
if os.path.exists("assets/lumina_logo.png"):
    _, col_logo, _ = st.columns([2, 1, 2])
    with col_logo:
        st.image("assets/lumina_logo.png", use_container_width=True)

st.markdown("<h1 class='main-title'>rMG CWR Converter</h1>", unsafe_allow_html=True)
st.markdown("<p class='sub-title'>CWR 2.2 · ICE · PRS · Claude Version</p>", unsafe_allow_html=True)

# ---- LOAD CONFIG FROM SECRETS OR LOCAL ----
if "LUMINA" in st.secrets:
    lumina_cfg = dict(st.secrets["LUMINA"])
else:
    lumina_cfg = config.LUMINA

if "AGREEMENT_MAP" in st.secrets:
    agreement_map = dict(st.secrets["AGREEMENT_MAP"])
else:
    agreement_map = config.AGREEMENT_MAP

catalogs = config.CATALOGS
# Override lumina values in catalogs with secrets if present
for k in catalogs:
    catalogs[k]["lumina_name"]   = lumina_cfg.get("name",   catalogs[k]["lumina_name"])
    catalogs[k]["lumina_ipi"]    = lumina_cfg.get("ipi",    catalogs[k]["lumina_ipi"])
    catalogs[k]["lumina_pub_id"] = lumina_cfg.get("pub_id", catalogs[k]["lumina_pub_id"])

# ---- SEQUENCE LEDGER ----
SEQ_FILE = "cwr_sequence_log.json"
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

history = seq_data.get("history", [])
next_seq = max([item["sequence"] for item in history] + [0]) + 1

# ---- TABS ----
tab_gen, tab_val, tab_ledger = st.tabs(["⚡  Generate", "🛡️  Validate", "📋  Ledger"])


# ==============================================================================
# TAB 1 — GENERATOR
# ==============================================================================
with tab_gen:
    st.markdown("### CWR 2.2 Generator")

    col_file, col_options = st.columns([2, 1])

    with col_file:
        uploaded_csv = st.file_uploader(
            "Upload source CSV (SourceAudio or Harvest Media)",
            type=["csv"],
            key="gen_csv"
        )

    with col_options:
        catalog_key = st.selectbox(
            "Catalog",
            options=list(catalogs.keys()),
            format_func=lambda k: f"{k} — {catalogs[k]['label']}"
        )
        seq_override = st.number_input(
            "Sequence number",
            min_value=1, max_value=9999,
            value=int(next_seq), step=1
        )

    if uploaded_csv:
        if st.button("Generate CWR File", type="primary", use_container_width=True):
            try:
                with st.status("Processing...", expanded=True) as status:

                    # 1. Parse CSV
                    st.write("📂 Detecting and parsing CSV format...")
                    file_bytes = uploaded_csv.getvalue()
                    tracks, fmt_detected, parse_warnings = parse_csv(file_bytes, uploaded_csv.name)

                    st.write(f"✅ Detected format: **{fmt_detected.upper()}** · {len(tracks)} tracks found")

                    if parse_warnings:
                        for w in parse_warnings:
                            st.warning(w)

                    if not tracks:
                        st.error("No tracks found in CSV. Check the file format.")
                        st.stop()

                    # 2. Check agreement map
                    st.write("🔑 Checking agreement map...")
                    if not agreement_map:
                        st.error(
                            "Agreement map is empty. Add publisher → agreement number mappings "
                            "to Streamlit Secrets under [AGREEMENT_MAP]."
                        )
                        st.stop()

                    # 3. Generate CWR
                    st.write("⚙️ Building CWR records (canvas stamper)...")
                    catalog_config = catalogs[catalog_key]
                    cwr_content, gen_warnings, filename = generate_cwr(
                        tracks=tracks,
                        catalog_config=catalog_config,
                        agreement_map=agreement_map,
                        sequence_number=int(seq_override)
                    )

                    if gen_warnings:
                        for w in gen_warnings:
                            st.warning(w)

                    # 4. Inline validation
                    st.write("🛡️ Running geometry validation...")
                    result = validate(cwr_content, source_csv_bytes=file_bytes, filename=filename)

                    if not result['passed']:
                        status.update(label="Generation failed — validation errors found", state="error")
                        st.error(f"❌ {len(result['errors'])} critical error(s) found. File NOT generated.")
                        for err in result['errors']:
                            st.error(f"Line {err.line} [{err.record_type}]: {err.message}")
                            if err.excerpt:
                                with st.expander("Show context"):
                                    st.code(err.excerpt)
                        st.stop()

                    status.update(label=f"✅ {filename} ready", state="complete")

                # Store in session state for download
                st.session_state['cwr_content']  = cwr_content
                st.session_state['cwr_filename']  = filename
                st.session_state['cwr_warnings']  = result['warnings']
                st.session_state['cwr_stats']     = result['stats']

                st.rerun()

            except (ParseError, CWREngineError) as e:
                st.error(f"❌ {str(e)}")
            except Exception as e:
                st.error(f"❌ Unexpected error: {str(e)}")
                raise

    # Download section (persists after rerun)
    if 'cwr_content' in st.session_state and 'cwr_filename' in st.session_state:
        filename = st.session_state['cwr_filename']
        stats    = st.session_state.get('cwr_stats', {})
        warns    = st.session_state.get('cwr_warnings', [])

        st.divider()
        col_m1, col_m2, col_m3 = st.columns(3)
        col_m1.metric("Tracks (NWR)", stats.get('nwr_count', '—'))
        col_m2.metric("Filename", filename)
        col_m3.metric("Status", "✅ PASS")

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
            for k in ('cwr_content', 'cwr_filename', 'cwr_warnings', 'cwr_stats'):
                st.session_state.pop(k, None)
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

            # Metrics
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
        uploaded_v22 = st.file_uploader("Upload accepted .V22", type=["V22", "v22", "txt"], key="ledger_upload")

        if uploaded_v22:
            fname = uploaded_v22.name
            try:
                seq_str = fname[4:8]
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
            accepted_by = col_soc1.selectbox("Accepted by", ["ICE (Berlin)", "PRS (London)", "Both", "Other"])
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

    if history:
        ledger_df = pd.DataFrame(reversed(history))
        ledger_df['sequence'] = ledger_df['sequence'].apply(lambda x: f"{int(x):04d}")
        st.dataframe(
            ledger_df[['sequence', 'filename', 'accepted_by', 'date']],
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("No accepted files logged yet.")
