"""
api.py — Flask backend for the Cross Reference Tool frontend.
Run with: python api.py
Listens on http://localhost:5000
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import pandas as pd
import os
import io
import uuid
import threading

# Import everything from applesauce that the API needs
from applesauce import (
    get_competitor_specs_leniently,
    find_ti_alternatives,
    find_ti_zener_alternatives,
    _generate_ti_opn,
    _get_replacement_type,
    load_and_cache,
    manage_data_files,
    CACHE_DIR,
    TI_SPECS_DATABASE_FILE,
    TI_ZENER_SPECS_DATABASE_FILE,
    AOS_SPECS_DATABASE_FILE,
    AMAZING_SPECS_DATABASE_FILE,
    DIODES_DL_DATABASE_FILE, DIODES_PL_DATABASE_FILE, DIODES_TS_DATABASE_FILE,
    DIODES_USBC_DATABASE_FILE,
    NEXPERIA_ZENER_DATABASE_FILE, NEXPERIA_AUTO_ZENER_DATABASE_FILE,
    NEXPERIA_ESD_DATABASE_FILE, NEXPERIA_EMI_DATABASE_FILE, NEXPERIA_TVS_DATABASE_FILE,
    NEXPERIA_AUTO_PROTECTION_DATABASE_FILE, NEXPERIA_MORE, NEXPERIA_MORE_CSV,
    LITTELFUSE_AUTO_TVS_ARRAY_FILE, LITTELFUSE_TVS_ARRAY_FILE, LITTELFUSE_AUTO_TVS_FILE,
    LITTELFUSE_TVS_FILE, LITTELFUSE_PESD_FILE,
    JIANGSU_ESD_FILE, JIANGSU_TVS_FILE, JIANGSU_ZENER_FILE,
    SEMTECH_SPECS_FILE, VISHAY_SPECS_DATABASE_FILE, VISHAY_SPECS_CSV_FILE,
    STM_SPECS_DATABASE_FILE, STM_SPECS_CSV_FILE,
    PANJIT_SPECS_DATABASE_FILE, PANJIT_SPECS_CSV_FILE,
    ONSEMI_SPECS_TVS, ONSEMI_SPECS_CSV_TVS,
    DIGIKEY_SPECS_CSV,
    _unblock_csv_file,
)
from ti_scrape import load_excel_data, fetch_ti_specs_from_excel
from ti_zener_scrape import fetch_ti_zener_specs_from_excel
from parsing import normalize_package

app = Flask(__name__)
CORS(app)

# ── In-memory storage for batch jobs ─────────────────────────────────────────
jobs = {}

# ── Load all databases once at startup ──────────────────────────────────────

print("Loading databases...")
manage_data_files(force_reload=False)

ti_specs_df = load_and_cache("ti_specs.pkl", TI_SPECS_DATABASE_FILE, False,
    lambda: load_excel_data(TI_SPECS_DATABASE_FILE, header_row=10))
ti_zener_specs_df = load_and_cache("ti_zener_specs.pkl", TI_ZENER_SPECS_DATABASE_FILE, False,
    lambda: load_excel_data(TI_ZENER_SPECS_DATABASE_FILE, header_row=10))
aos_specs_df = load_and_cache("aos_specs.pkl", AOS_SPECS_DATABASE_FILE, False,
    lambda: load_excel_data(AOS_SPECS_DATABASE_FILE, header_row=6))
diodes_dl_df  = load_and_cache("diodes_dl.pkl",  DIODES_DL_DATABASE_FILE,  False, lambda: load_excel_data(DIODES_DL_DATABASE_FILE))
diodes_pl_df  = load_and_cache("diodes_pl.pkl",  DIODES_PL_DATABASE_FILE,  False, lambda: load_excel_data(DIODES_PL_DATABASE_FILE))
diodes_ts_df  = load_and_cache("diodes_ts.pkl",  DIODES_TS_DATABASE_FILE,  False, lambda: load_excel_data(DIODES_TS_DATABASE_FILE))
nexperia_zener_df          = load_and_cache("nexperia_zener.pkl",           NEXPERIA_ZENER_DATABASE_FILE,           False, lambda: load_excel_data(NEXPERIA_ZENER_DATABASE_FILE, header_row=9))
nexperia_auto_zener_df     = load_and_cache("nexperia_auto_zener.pkl",      NEXPERIA_AUTO_ZENER_DATABASE_FILE,      False, lambda: load_excel_data(NEXPERIA_AUTO_ZENER_DATABASE_FILE, header_row=9))
nexperia_esd_df            = load_and_cache("nexperia_esd.pkl",             NEXPERIA_ESD_DATABASE_FILE,             False, lambda: load_excel_data(NEXPERIA_ESD_DATABASE_FILE, header_row=9))
nexperia_emi_df            = load_and_cache("nexperia_emi.pkl",             NEXPERIA_EMI_DATABASE_FILE,             False, lambda: load_excel_data(NEXPERIA_EMI_DATABASE_FILE, header_row=9))
nexperia_tvs_df            = load_and_cache("nexperia_tvs.pkl",             NEXPERIA_TVS_DATABASE_FILE,             False, lambda: load_excel_data(NEXPERIA_TVS_DATABASE_FILE, header_row=9))
nexperia_auto_protection_df= load_and_cache("nexperia_auto_protection.pkl", NEXPERIA_AUTO_PROTECTION_DATABASE_FILE, False, lambda: load_excel_data(NEXPERIA_AUTO_PROTECTION_DATABASE_FILE, header_row=9))
nexperia_more_df           = load_and_cache("nexperia_more.pkl",            NEXPERIA_MORE,                          False, lambda: pd.read_excel(NEXPERIA_MORE))
littelfuse_auto_tvs_array_df = load_and_cache("littelfuse_auto_tvs_array.pkl", LITTELFUSE_AUTO_TVS_ARRAY_FILE, False, lambda: load_excel_data(LITTELFUSE_AUTO_TVS_ARRAY_FILE))
littelfuse_tvs_array_df      = load_and_cache("littelfuse_tvs_array.pkl",      LITTELFUSE_TVS_ARRAY_FILE,      False, lambda: load_excel_data(LITTELFUSE_TVS_ARRAY_FILE))
littelfuse_auto_tvs_df       = load_and_cache("littelfuse_auto_tvs.pkl",       LITTELFUSE_AUTO_TVS_FILE,       False, lambda: load_excel_data(LITTELFUSE_AUTO_TVS_FILE))
littelfuse_tvs_df            = load_and_cache("littelfuse_tvs.pkl",            LITTELFUSE_TVS_FILE,            False, lambda: load_excel_data(LITTELFUSE_TVS_FILE))
littelfuse_pesd_df           = load_and_cache("littelfuse_pesd.pkl",           LITTELFUSE_PESD_FILE,           False, lambda: load_excel_data(LITTELFUSE_PESD_FILE))
jiangsu_esd_df   = load_and_cache("jiangsu_esd.pkl",   JIANGSU_ESD_FILE,   False, lambda: load_excel_data(JIANGSU_ESD_FILE))
jiangsu_tvs_df   = load_and_cache("jiangsu_tvs.pkl",   JIANGSU_TVS_FILE,   False, lambda: load_excel_data(JIANGSU_TVS_FILE))
jiangsu_zener_df = load_and_cache("jiangsu_zener.pkl", JIANGSU_ZENER_FILE, False, lambda: load_excel_data(JIANGSU_ZENER_FILE))
semtech_specs_df = load_and_cache("semtech_specs.pkl", SEMTECH_SPECS_FILE, False, lambda: load_excel_data(SEMTECH_SPECS_FILE))
vishay_df     = load_and_cache("vishay_specs_protection.pkl", VISHAY_SPECS_DATABASE_FILE, False, lambda: pd.read_excel(VISHAY_SPECS_DATABASE_FILE))
stm_specs_df  = load_and_cache("stm_specs.pkl",   STM_SPECS_DATABASE_FILE,   False, lambda: pd.read_excel(STM_SPECS_DATABASE_FILE))
panjit_specs_df=load_and_cache("panjit_specs.pkl",PANJIT_SPECS_DATABASE_FILE,False, lambda: pd.read_excel(PANJIT_SPECS_DATABASE_FILE))
onsemi_tvs_df = load_and_cache("onsemi_specs_tvs.pkl", ONSEMI_SPECS_TVS, False, lambda: pd.read_excel(ONSEMI_SPECS_TVS))
amazing_specs_df = load_and_cache("amazing_specs.pkl", AMAZING_SPECS_DATABASE_FILE, False,
    lambda: pd.read_excel(AMAZING_SPECS_DATABASE_FILE, sheet_name='Amazing-Parametric', header=0, engine='openpyxl'))

digikey_df = pd.DataFrame()
if os.path.exists(DIGIKEY_SPECS_CSV):
    try:
        digikey_df = pd.read_csv(DIGIKEY_SPECS_CSV, on_bad_lines='skip', encoding='latin-1')
    except Exception as e:
        print(f"Warning: Could not load DigiKey CSV: {e}")

all_dfs = (
    aos_specs_df, amazing_specs_df,
    {"Data Line": diodes_dl_df, "Power Line": diodes_pl_df, "TSPDs": diodes_ts_df},
    {"Zener": nexperia_zener_df, "Auto_Zener": nexperia_auto_zener_df, "ESD": nexperia_esd_df,
     "EMI": nexperia_emi_df, "TVS": nexperia_tvs_df, "Auto_PD": nexperia_auto_protection_df,
     "More TVS": nexperia_more_df},
    {"PESD": littelfuse_pesd_df, "TVS": littelfuse_tvs_df, "TVS_Array": littelfuse_tvs_array_df,
     "Auto_TVS": littelfuse_auto_tvs_df, "Auto_TVS_Array": littelfuse_auto_tvs_array_df},
    {"ESD": jiangsu_esd_df, "TVS": jiangsu_tvs_df, "Zener": jiangsu_zener_df},
    vishay_df, semtech_specs_df, stm_specs_df, panjit_specs_df, onsemi_tvs_df,
    ti_specs_df, ti_zener_specs_df,
)

print("All databases loaded. API ready.")

# ── Helpers ─────────────────────────────────────────────────────────────────

PARAM_KEYS_TVS = [
    ("Device Name",                    "Device Name"),
    ("Direction",                      "Direction"),
    ("Package",                        "Package"),
    ("Voltage - Reverse Standoff (Typ)","Vrwm (V)"),
    ("Voltage - Clamping (Max) @ Ipp", "Vcl @ Ipp (Max)"),
    ("Capacitance",                    "Capacitance"),
    ("Channels",                       "Channels"),
    ("IEC 61000-4-5",                  "IEC 61000-4-5"),
    ("IEC 61000-4-2",                  "IEC 61000-4-2"),
    ("Price ($/ku)",                   "Price ($/ku)"),
]

PARAM_KEYS_ZENER = [
    ("Device Name",                    "Device Name"),
    ("Package",                        "Package"),
    ("Voltage - Reverse Standoff (Typ)","Vz (V)"),
    ("Tolerance",                      "Tolerance"),
    ("Power Dissipation (Pd)",         "Power (Pd)"),
    ("Price ($/ku)",                   "Price ($/ku)"),
]

def _run_cross(part, competitor):
    comp_specs = get_competitor_specs_leniently(part, competitor, all_dfs, digikey_df)
    if not comp_specs:
        return None, None, []

    is_zener = "zener" in comp_specs.get("Source File", "").lower()
    original_pkg = comp_specs.get("Package", "-")
    from applesauce import _canonical_to_ti_pkg
    comp_specs["Package"] = _canonical_to_ti_pkg(comp_specs.get("Canonical Package")) or normalize_package(original_pkg)

    if is_zener:
        alts = find_ti_zener_alternatives(comp_specs, ti_zener_specs_df)
    else:
        alts = find_ti_alternatives(comp_specs, ti_specs_df)

    param_keys = PARAM_KEYS_ZENER if is_zener else PARAM_KEYS_TVS

    alt_specs_list = []
    src_df = ti_zener_specs_df if is_zener else ti_specs_df
    pn_col  = next((c for c in src_df.columns if "product or part number" in c.lower()), None)
    pin_col = next((c for c in src_df.columns if "pin count" in c.lower()), None)

    for alt in alts[:3]:
        gpn = alt["part_number"]
        if is_zener:
            specs = fetch_ti_zener_specs_from_excel(gpn, ti_zener_specs_df, param_keys)
        else:
            specs = fetch_ti_specs_from_excel(gpn, ti_specs_df, param_keys)
        if not specs:
            continue

        ti_pin_str = "-"
        if pn_col and pin_col:
            row = src_df[src_df[pn_col].astype(str).str.strip() == gpn.strip()]
            if not row.empty:
                ti_pin_str = str(row.iloc[0][pin_col])

        opn = _generate_ti_opn(gpn, specs.get("Package", "-"), ti_pin_str,
                                comp_specs.get("Package", ""), comp_specs.get("Canonical Package"))
        specs["OPN"] = opn
        alt_specs_list.append(specs)

    comp_specs["Package"] = _canonical_to_ti_pkg(comp_specs.get("Canonical Package")) or original_pkg
    return comp_specs, param_keys, alt_specs_list

# ── Endpoints ────────────────────────────────────────────────────────────────

@app.route("/cross", methods=["POST"])
def cross():
    data = request.json or {}
    part       = (data.get("part") or "").strip()
    competitor = (data.get("competitor") or "").strip().lower()
    if not part or not competitor:
        return jsonify({"error": "part and competitor are required"}), 400

    comp_specs, _, alt_specs_list = _run_cross(part, competitor)
    if comp_specs is None:
        return jsonify({"error": f"Could not find specs for '{part}'"}), 404

    return jsonify({
        "competitor_part": part,
        "alternatives": [s.get("OPN", s.get("Device Name", "-")) for s in alt_specs_list],
    })

@app.route("/specs", methods=["POST"])
def specs():
    data = request.json or {}
    part       = (data.get("part") or "").strip()
    competitor = (data.get("competitor") or "").strip().lower()
    if not part or not competitor:
        return jsonify({"error": "part and competitor are required"}), 400

    comp_specs, param_keys, alt_specs_list = _run_cross(part, competitor)
    if comp_specs is None:
        return jsonify({"error": f"Could not find specs for '{part}'"}), 404

    rows = []
    for key, label in param_keys:
        comp_val = comp_specs.get(key, "-")
        if key == "Device Name": comp_val = str(comp_val).upper()
        row = {"label": label, "comp": comp_val}
        for i, alt in enumerate(alt_specs_list):
            val = alt.get("OPN", alt.get("Device Name", "-")) if key == "Device Name" else alt.get(key, "-")
            row[f"alt{i+1}"] = val
        for i in range(len(alt_specs_list), 3):
            row[f"alt{i+1}"] = "-"
        rows.append(row)

    return jsonify({
        "competitor_part": part,
        "rows": rows,
        "alt_names": [s.get("OPN", s.get("Device Name", "-")) for s in alt_specs_list],
    })

# ── Batch Processing (New Endpoints) ────────────────────────────────────────

def process_batch_job(job_id, df):
    """The actual work of processing a batch file, run in a background thread."""
    total = len(df)
    for index, row in df.iterrows():
        part = str(row["Part Names"]).strip()
        competitor = str(row["Competitor Name"]).strip().lower()
        result = {"part": part, "competitor": competitor, "alt1": "-", "alt2": "-", "alt3": "-"}
        
        if part and part.lower() != "nan":
            _, _, alt_specs_list = _run_cross(part, competitor)
            opns = [s.get("OPN", s.get("Device Name", "-")) for s in alt_specs_list]
            opns += ["-"] * (3 - len(opns))
            result.update({"alt1": opns[0], "alt2": opns[1], "alt3": opns[2]})
        
        # Update job progress in the shared dictionary
        with app.app_context():
            jobs[job_id]['results'].append(result)
            jobs[job_id]['progress'] = index + 1
            if jobs[job_id]['progress'] == total:
                jobs[job_id]['status'] = 'complete'

@app.route("/batch_start", methods=["POST"])
def batch_start():
    """Starts a batch job and returns a job ID."""
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    
    f = request.files["file"]
    try:
        batch_df = pd.read_excel(io.BytesIO(f.read()))
    except Exception as e:
        return jsonify({"error": f"Could not read Excel file: {e}"}), 400

    if "Part Names" not in batch_df.columns or "Competitor Name" not in batch_df.columns:
        return jsonify({"error": "Excel file must have 'Part Names' and 'Competitor Name' columns"}), 400

    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        'progress': 0,
        'total': len(batch_df),
        'status': 'processing',
        'results': []
    }

    # Start the processing in a background thread
    thread = threading.Thread(target=process_batch_job, args=(job_id, batch_df))
    thread.start()

    return jsonify({"job_id": job_id})

@app.route("/batch_status/<job_id>", methods=["GET"])
def batch_status(job_id):
    """Returns the status of a running batch job."""
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


if __name__ == "__main__":
    app.run(debug=False, port=5000)