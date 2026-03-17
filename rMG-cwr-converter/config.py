# ==============================================================================
# rMG CWR CONVERTER — CONFIGURATION
# Claude Version | DP-669/rMG-cwr-converter-Claude-version
#
# Lumina Publishing UK is the sub-publisher for all three catalogs.
# Agreement numbers and IPI numbers are loaded from Streamlit Secrets
# in production. This file provides the structure only.
#
# DO NOT commit real agreement numbers or IPI numbers to GitHub.
# ==============================================================================

LUMINA = {
    "name":         "LUMINA PUBLISHING UK",
    "ipi":          "01254514077",
    "pub_id":       "000000012",
    "pr_soc":       "052",   # PRS
    "mr_soc":       "033",   # MCPS
}

TERRITORY_UK    = "0826"
TERRITORY_WORLD = "2136"

CATALOGS = {
    "rC": {
        "label":         "redCola",
        "lumina_name":   LUMINA["name"],
        "lumina_ipi":    LUMINA["ipi"],
        "lumina_pub_id": LUMINA["pub_id"],
        "lumina_pr_soc": LUMINA["pr_soc"],
        "lumina_mr_soc": LUMINA["mr_soc"],
        "territory":     TERRITORY_UK,
        "submitter_code":"LUM_319",
        "software_tag":  "rMG-CWR",
    },
    "EPP": {
        "label":         "Ekonomic Propaganda",
        "lumina_name":   LUMINA["name"],
        "lumina_ipi":    LUMINA["ipi"],
        "lumina_pub_id": LUMINA["pub_id"],
        "lumina_pr_soc": LUMINA["pr_soc"],
        "lumina_mr_soc": LUMINA["mr_soc"],
        "territory":     TERRITORY_UK,
        "submitter_code":"LUM_319",
        "software_tag":  "rMG-CWR",
    },
    "SSC": {
        "label":         "Short Story Collective",
        "lumina_name":   LUMINA["name"],
        "lumina_ipi":    LUMINA["ipi"],
        "lumina_pub_id": LUMINA["pub_id"],
        "lumina_pr_soc": LUMINA["pr_soc"],
        "lumina_mr_soc": LUMINA["mr_soc"],
        "territory":     TERRITORY_UK,
        "submitter_code":"LUM_319",
        "software_tag":  "rMG-CWR",
    },
}

# Populated via Streamlit Secrets in production. Keep empty here.
# Source: 00_Lumina_Publishing_UK_agreement_references.csv
AGREEMENT_MAP = {}
