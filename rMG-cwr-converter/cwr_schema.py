# ==============================================================================
# CWR SCHEMA — FIXED FIELD DEFINITIONS
# Ground truth: approved files CW250010LUM_319_V22 and CW250011LUM_319_V22
# accepted by ICE (Berlin) and PRS (London).
#
# RULE: Every record is a fixed-length canvas. Fields are stamped into exact
# byte positions. Nothing shifts. Empty fields are padded — never omitted.
# Spec document is reference only. Approved file geometry is law.
# ==============================================================================

from dataclasses import dataclass, field
from typing import List

@dataclass
class FieldDef:
    name: str
    start: int      # 1-based position (matches spec notation)
    length: int
    fmt: str        # 'A' = alpha (left-pad spaces), 'N' = numeric (right-pad zeros)
    constant: str = None  # If set, always write this literal value

@dataclass
class RecordDef:
    record_type: str
    total_length: int
    fields: List[FieldDef]


# ------------------------------------------------------------------------------
# HDR — Transmission Header
# Approved length: 115
# Notes: Ends after cwr_revision + software tag. Optional software_name /
#        software_version fields (spec pos 108–167) are omitted entirely.
#        sender_type field contains '01' not 'PB' — Chris's format, ICE accepts.
# ------------------------------------------------------------------------------
HDR = RecordDef("HDR", 115, [
    FieldDef("record_type",       1,   3,  'A', constant="HDR"),
    FieldDef("sender_type",       4,   2,  'A', constant="01"),
    FieldDef("sender_id",         6,   9,  'N'),   # IPI number (9 digits)
    FieldDef("sender_name",       15,  45, 'A'),
    FieldDef("edi_version",       60,  5,  'A', constant="01.10"),
    FieldDef("creation_date",     65,  8,  'A'),   # YYYYMMDD
    FieldDef("creation_time",     73,  6,  'A'),   # HHMMSS
    FieldDef("transmission_date", 79,  8,  'A'),   # YYYYMMDD
    FieldDef("character_set",     87,  15, 'A'),   # blank = ASCII
    FieldDef("cwr_version",       102, 3,  'A', constant="2.2"),
    FieldDef("cwr_revision",      105, 3,  'N', constant="001"),
    FieldDef("software_package",  108, 8,  'A'),   # e.g. 'BACKBEAT'
])

# ------------------------------------------------------------------------------
# GRH — Group Header
# Approved length: 26
# Notes: transaction_type is NWR (not REV) per Damir's instruction.
# ------------------------------------------------------------------------------
GRH = RecordDef("GRH", 26, [
    FieldDef("record_type",        1,  3,  'A', constant="GRH"),
    FieldDef("transaction_type",   4,  3,  'A', constant="NWR"),
    FieldDef("group_id",           7,  5,  'N', constant="00001"),
    FieldDef("transaction_version",12, 5,  'A', constant="02.20"),
    FieldDef("batch_request_id",   17, 10, 'N', constant="0000000001"),
])

# ------------------------------------------------------------------------------
# NWR — New Work Registration
# Approved length: 260 (same geometry as REV in approved files)
# Key fields verified against approved file byte positions.
# ------------------------------------------------------------------------------
NWR = RecordDef("NWR", 260, [
    # Positions verified character-by-character against approved files
    # CW250010 and CW250011 accepted by ICE (Berlin) and PRS (London).
    FieldDef("record_type",       1,   3,  'A', constant="NWR"),
    FieldDef("t_seq",             4,   8,  'N'),   # pos 4-11
    FieldDef("rec_seq",           12,  8,  'N', constant="00000000"),  # pos 12-19
    FieldDef("title",             20,  60, 'A'),   # pos 20-79
    FieldDef("language_code",     80,  2,  'A'),   # pos 80-81, blank
    FieldDef("submitter_work_id", 82,  14, 'A'),   # pos 82-95
    FieldDef("iswc",              96,  11, 'A'),   # pos 96-106
    FieldDef("copyright_date",    107, 8,  'A'),   # pos 107-114, 00000000
    FieldDef("copyright_number",  115, 12, 'A'),   # pos 115-126, blank
    FieldDef("musical_work_dist", 127, 3,  'A', constant="UNC"),  # pos 127-129
    FieldDef("duration",          130, 6,  'N'),   # pos 130-135, HHMMSS
    FieldDef("recorded_ind",      136, 1,  'A', constant="Y"),    # pos 136
    FieldDef("text_music_rel",    137, 1,  'A'),   # pos 137, blank
    FieldDef("composite_type",    138, 4,  'A'),   # pos 138-141, blank (4 chars gap to ORI)
    FieldDef("version_type",      143, 3,  'A', constant="ORI"),  # pos 143-145, verified ✓
    FieldDef("excerpt_type",      146, 3,  'A'),   # pos 146-148, blank
    FieldDef("music_arrangement", 149, 3,  'A'),   # pos 149-151, blank
    FieldDef("lyric_adaptation",  152, 3,  'A'),   # pos 152-154, blank
    FieldDef("contact_name",      155, 30, 'A'),   # pos 155-184, blank
    FieldDef("contact_id",        185, 14, 'A'),   # pos 185-198, blank
    FieldDef("cwr_work_type",     199, 2,  'A'),   # pos 199-200, blank
    FieldDef("grand_rights_ind",  201, 1,  'A'),   # pos 201, blank
    FieldDef("composite_count",   202, 3,  'A'),   # pos 202-204, blank (approved file uses spaces)
    FieldDef("date_publ_printed", 205, 8,  'A'),   # pos 205-212, blank
    FieldDef("exceptional_clause",213, 1,  'A'),   # pos 213, blank
    FieldDef("opus_number",       214, 25, 'A'),   # pos 214-238, blank
    FieldDef("catalogue_number",  239, 14, 'A'),   # pos 239-252, blank
    FieldDef("priority_flag",     253, 6,  'A'),   # pos 253-258, blank
    FieldDef("trailing_flag",     260, 1,  'A', constant="Y"),    # pos 260, verified ✓
])

# ------------------------------------------------------------------------------
# SPU — Publisher Controlled by Submitter
# Approved length: 182
# Key finding: agreement number goes in soc_agr_num (pos 167, len 14).
#              subm_agr_num (pos 99, len 14) stays blank.
#              agr_type at pos 181, len 2 = 'PG' — ICE required marker.
#              sr_soc and sr_share present but blank for non-SR publishers.
# ------------------------------------------------------------------------------
SPU = RecordDef("SPU", 182, [
    FieldDef("record_type",     1,   3,  'A', constant="SPU"),
    FieldDef("t_seq",           4,   8,  'N'),
    FieldDef("rec_seq",         12,  8,  'N'),
    FieldDef("chain_id",        20,  2,  'N'),   # publisher chain number
    FieldDef("pub_id",          22,  9,  'A'),   # interested party ID
    FieldDef("pub_name",        31,  45, 'A'),
    FieldDef("unknown_ind",     76,  1,  'A'),   # blank
    FieldDef("pub_type",        77,  2,  'A'),   # 'E ' = orig pub, 'SE' = sub-pub
    FieldDef("tax_id",          79,  9,  'A'),   # blank
    FieldDef("ipi_name",        88,  11, 'N'),   # IPI number zero-padded
    FieldDef("subm_agr_num",    99,  14, 'A'),   # BLANK — agreement goes in soc_agr
    FieldDef("pr_soc",          113, 3,  'N'),   # society code
    FieldDef("pr_share",        116, 5,  'N'),   # 00000–05000
    FieldDef("mr_soc",          121, 3,  'N'),   # society code
    FieldDef("mr_share",        124, 5,  'N'),   # 00000–10000
    FieldDef("sr_soc",          129, 3,  'A'),   # blank in approved files
    FieldDef("sr_share",        132, 5,  'N'),   # 00000 or actual
    FieldDef("special_agr",     137, 1,  'A'),   # blank
    FieldDef("first_rec_ref",   138, 1,  'A', constant="N"),
    FieldDef("filler",          139, 1,  'A'),   # blank
    FieldDef("ipi_base",        140, 13, 'A'),   # blank
    FieldDef("isac",            153, 14, 'A'),   # blank
    FieldDef("soc_agr_num",     167, 14, 'A'),   # AGREEMENT NUMBER goes here
    FieldDef("agr_type",        181, 2,  'A', constant="PG"),  # ICE required
])

# ------------------------------------------------------------------------------
# SPT — Publisher Territory of Control
# Approved length: 58
# Notes: pub_id here is Lumina's ID (the sub-publisher collecting).
#        TIS code 0826 = United Kingdom.
# ------------------------------------------------------------------------------
SPT = RecordDef("SPT", 58, [
    FieldDef("record_type",  1,  3,  'A', constant="SPT"),
    FieldDef("t_seq",        4,  8,  'N'),
    FieldDef("rec_seq",      12, 8,  'N'),
    FieldDef("pub_id",       20, 9,  'A'),   # Lumina's pub_id
    FieldDef("constant",     29, 6,  'A'),   # 6 spaces — mandatory per spec
    FieldDef("pr_coll",      35, 5,  'N'),   # collection share PR
    FieldDef("mr_coll",      40, 5,  'N'),   # collection share MR
    FieldDef("sr_coll",      45, 5,  'N'),   # collection share SR
    FieldDef("incl_excl",    50, 1,  'A', constant="I"),
    FieldDef("tis_code",     51, 4,  'A'),   # territory code e.g. 0826
    FieldDef("shares_change",55, 1,  'A'),   # blank
    FieldDef("sequence",     56, 3,  'N', constant="001"),
])

# ------------------------------------------------------------------------------
# SWR — Writer Controlled by Submitter
# Approved length: 152 (NOT 182 as in Gemini schema — verified from file)
# Notes: record ends at reversionary_ind (pos 151) + 1 trailing char = 152.
#        Fields ipi_base, personal_number, usa_license_ind omitted — ICE accepts.
# ------------------------------------------------------------------------------
SWR = RecordDef("SWR", 152, [
    FieldDef("record_type",  1,   3,  'A', constant="SWR"),
    FieldDef("t_seq",        4,   8,  'N'),
    FieldDef("rec_seq",      12,  8,  'N'),
    FieldDef("writer_id",    20,  9,  'A'),   # interested party ID
    FieldDef("last_name",    29,  45, 'A'),
    FieldDef("first_name",   74,  30, 'A'),
    FieldDef("unknown_ind",  104, 1,  'A'),   # blank
    FieldDef("designation",  105, 2,  'A'),   # 'C ' = composer
    FieldDef("tax_id",       107, 9,  'A'),   # blank
    FieldDef("ipi_name",     116, 11, 'N'),   # IPI zero-padded
    FieldDef("pr_soc",       127, 3,  'N'),   # society code
    FieldDef("pr_share",     130, 5,  'N'),   # performance share
    FieldDef("mr_soc",       135, 3,  'N'),   # mechanical society
    FieldDef("mr_share",     138, 5,  'N'),   # 00000 for writers
    FieldDef("sr_soc",       143, 3,  'N'),   # sync society
    FieldDef("sr_share",     146, 5,  'N'),   # 00000 for writers
    FieldDef("reversionary", 151, 1,  'A'),   # blank
    FieldDef("trailing",     152, 1,  'A', constant="N"),  # trailing N — ICE format
])

# ------------------------------------------------------------------------------
# SWT — Writer Territory
# Approved length: 52
# Notes: TIS code 2136 = World (not 0826). SWT uses worldwide territory.
# ------------------------------------------------------------------------------
SWT = RecordDef("SWT", 52, [
    FieldDef("record_type",  1,  3,  'A', constant="SWT"),
    FieldDef("t_seq",        4,  8,  'N'),
    FieldDef("rec_seq",      12, 8,  'N'),
    FieldDef("writer_id",    20, 9,  'A'),
    FieldDef("pr_coll",      29, 5,  'N'),   # matches writer PR share
    FieldDef("mr_coll",      34, 5,  'N'),   # 00000
    FieldDef("sr_coll",      39, 5,  'N'),   # 00000
    FieldDef("incl_excl",    44, 1,  'A', constant="I"),
    FieldDef("tis_code",     45, 4,  'A', constant="2136"),  # World
    FieldDef("shares_change",49, 1,  'A'),   # blank
    FieldDef("sequence",     50, 3,  'N', constant="001"),
])

# ------------------------------------------------------------------------------
# PWR — Publisher for Writer
# Approved length: 112
# Notes: agreement number goes in soc_agr_num (pos 88, len 14) — same rule as SPU.
#        subm_agr_num (pos 74, len 14) stays blank.
# ------------------------------------------------------------------------------
PWR = RecordDef("PWR", 112, [
    FieldDef("record_type",  1,  3,  'A', constant="PWR"),
    FieldDef("t_seq",        4,  8,  'N'),
    FieldDef("rec_seq",      12, 8,  'N'),
    FieldDef("pub_id",       20, 9,  'A'),   # publisher interested party ID
    FieldDef("pub_name",     29, 45, 'A'),
    FieldDef("subm_agr_num", 74, 14, 'A'),   # BLANK
    FieldDef("soc_agr_num",  88, 14, 'A'),   # AGREEMENT NUMBER goes here
    FieldDef("writer_id",    102, 9, 'A'),
    FieldDef("chain_id",     111, 2, 'N'),
])

# ------------------------------------------------------------------------------
# REC — Recording Detail
# Approved length: 507
# Two REC records per work: REC1 = physical (CD), REC2 = digital (DW)
# Both end at position 507 with the isrc_validity flag 'Y'.
# ------------------------------------------------------------------------------
REC = RecordDef("REC", 507, [
    FieldDef("record_type",       1,   3,  'A', constant="REC"),
    FieldDef("t_seq",             4,   8,  'N'),
    FieldDef("rec_seq",           12,  8,  'N'),
    FieldDef("release_date",      20,  8,  'N', constant="00000000"),   # YYYYMMDD, always 00000000
    FieldDef("release_duration",  28,  6,  'A'),   # spaces when unknown (per approved file)
    FieldDef("album_title",       99,  60, 'A'),   # blank
    FieldDef("album_label",       159, 60, 'A'),   # blank
    FieldDef("release_catalog",   219, 15, 'A'),   # album code e.g. RC001
    FieldDef("ean",               237, 13, 'A'),   # blank
    FieldDef("isrc",              250, 12, 'A'),   # ISRC code
    FieldDef("recording_format",  262, 1,  'A'),   # blank
    FieldDef("recording_tech",    263, 1,  'A'),   # blank
    FieldDef("media_type",        264, 3,  'A'),   # 'CD ' or 'DW ' — source type
    FieldDef("recording_title",   267, 60, 'A'),   # blank (REC1) or track title (REC2)
    FieldDef("version_title",     327, 60, 'A'),   # blank
    FieldDef("display_artist",    387, 60, 'A'),   # blank
    FieldDef("record_label",      447, 60, 'A'),   # library name e.g. RED COLA
    FieldDef("isrc_validity",     507, 1,  'A', constant="Y"),
])

# ------------------------------------------------------------------------------
# ORN — Work Origin
# Approved length: 109
# Notes: record ends after library name (pos 102, len 60 = 162 per spec,
#        but approved file ends at 109 — library name is short, no padding).
#        We pad library to fill to 109 total.
# ------------------------------------------------------------------------------
# ORN total length is dynamic: 101 fixed chars + library name length (up to 60).
# Chris's approved files confirm no trailing padding — record ends after library name.
# We set total_length=162 (spec max) here but the engine overrides it per-record.
ORN = RecordDef("ORN", 162, [
    FieldDef("record_type",   1,  3,  'A', constant="ORN"),
    FieldDef("t_seq",         4,  8,  'N'),
    FieldDef("rec_seq",       12, 8,  'N'),
    FieldDef("purpose",       20, 3,  'A', constant="LIB"),
    FieldDef("prod_title",    23, 60, 'A'),   # album title
    FieldDef("cd_identifier", 83, 15, 'A'),   # album code
    FieldDef("cut_number",    98, 4,  'N'),   # track number zero-padded
    FieldDef("library",       102, 60, 'A'),  # library name — full name, no truncation
])

# ------------------------------------------------------------------------------
# GRT — Group Trailer
# Approved length: 24
# Note: GRT/TRL do NOT use the standard t_seq/rec_seq prefix.
# Layout: record_type(3) + group_id(5) + transaction_count(8) + record_count(8)
# Verified from approved: GRT000010001001100117018
# ------------------------------------------------------------------------------
GRT = RecordDef("GRT", 24, [
    FieldDef("record_type",      1,  3,  'A', constant="GRT"),
    FieldDef("group_id",         4,  5,  'N', constant="00001"),
    FieldDef("transaction_count",9,  8,  'N'),   # number of NWR transactions
    FieldDef("record_count",     17, 8,  'N'),   # total records in group incl GRH+GRT
])

# ------------------------------------------------------------------------------
# TRL — Transmission Trailer
# Approved length: 24
# Layout: record_type(3) + group_count(5) + transaction_count(8) + record_count(8)
# Verified from approved: TRL000010001001100117020
# ------------------------------------------------------------------------------
TRL = RecordDef("TRL", 24, [
    FieldDef("record_type",      1,  3,  'A', constant="TRL"),
    FieldDef("group_count",      4,  5,  'N', constant="00001"),
    FieldDef("transaction_count",9,  8,  'N'),   # number of NWR transactions
    FieldDef("record_count",     17, 8,  'N'),   # total all records incl HDR+TRL
])

# Master registry
SCHEMA = {
    "HDR": HDR,
    "GRH": GRH,
    "NWR": NWR,
    "SPU": SPU,
    "SPT": SPT,
    "SWR": SWR,
    "SWT": SWT,
    "PWR": PWR,
    "REC": REC,
    "ORN": ORN,
    "GRT": GRT,
    "TRL": TRL,
}
