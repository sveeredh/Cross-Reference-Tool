# --- START OF FILE aos_scrape.py ---

import pandas as pd
from parsing import safe_strip

def _get_value_by_keywords(row, keywords):
    """
    Searches a row's index (column headers) for the first match from a list of keywords.
    The search is case-insensitive. Returns the value from the matched column.
    """
    for col_header in row.index:
        for keyword in keywords:
            if keyword.lower() in str(col_header).lower():
                return row[col_header]
    return None # Return None if no matching column is found

def fetch_aos_specs_from_excel(aos_opn, aos_specs_df):
    """
    Fetches specifications for an AOS part from a pre-loaded pandas DataFrame
    using flexible, keyword-based column searching.
    """
    specs_result = { "Device Name": aos_opn, "IEC 61000-4-5": "-", "IEC 61000-4-2": "-", "Capacitance": "-", "Channels": "-", "Package": "-", "Direction": "-", "Voltage - Reverse Standoff (Typ)": "-", "Voltage - Clamping (Max) @ Ipp": "-", "Price ($/ku)": "-", "Grade": "-" }

    if aos_opn is None or aos_specs_df is None or aos_specs_df.empty:
        return None

    aos_specs_df.columns = aos_specs_df.columns.str.strip()
    part_num_col = next((col for col in aos_specs_df.columns if 'product' in col.lower()), None)
    
    if not part_num_col:
        print("AOS Scraper: Could not find a 'Product' column in the Excel file.")
        return None

    part_row_series = aos_specs_df[aos_specs_df[part_num_col].astype(str).str.strip().str.lower() == aos_opn.lower()]
    
    if part_row_series.empty:
        print(f"AOS Part '{aos_opn}' not found in the AOS Excel database.")
        return None
    
    part_row = part_row_series.iloc[0]
    print(f"Found specs for AOS Part '{aos_opn}' in Excel.")

    # --- Map and format specs using the keyword helper function ---

    specs_result["Package"] = safe_strip(_get_value_by_keywords(part_row, ["Package"]))

    ch_val = _get_value_by_keywords(part_row, ["Protected Lines"])
    if pd.notna(ch_val): specs_result["Channels"] = str(int(float(ch_val)))

    dir_text = safe_strip(_get_value_by_keywords(part_row, ["Directional"])).lower()
    if 'uni' in dir_text: specs_result["Direction"] = "Unidirectional"
    elif 'bi' in dir_text: specs_result["Direction"] = "Bidirectional"

    vrwm_val = _get_value_by_keywords(part_row, ["VRWM max"])
    if pd.notna(vrwm_val): specs_result["Voltage - Reverse Standoff (Typ)"] = f"{float(vrwm_val):g} V"

    vcl_val = _get_value_by_keywords(part_row, ["VCL max"])
    if pd.notna(vcl_val): specs_result["Voltage - Clamping (Max) @ Ipp"] = f"{float(vcl_val):g} V"
    
    ippm_val = _get_value_by_keywords(part_row, ["IEC61000-4-5", "Lightning"])
    if pd.notna(ippm_val):
        try:
            specs_result["IEC 61000-4-5"] = f"{float(ippm_val):g} A (8/20µs)"
        except (ValueError, TypeError):
            specs_result["IEC 61000-4-5"] = safe_strip(ippm_val)

    esd_val_raw = safe_strip(_get_value_by_keywords(part_row, ["(ESD) Contact"]))
    if esd_val_raw != "-":
        cleaned_val = esd_val_raw.lower().replace('±', '').replace('kv', '').strip()
        if cleaned_val:
            specs_result["IEC 61000-4-2"] = f"±{cleaned_val} kV"
        else:
            specs_result["IEC 61000-4-2"] = safe_strip(esd_val_raw)
    
    cap_val = _get_value_by_keywords(part_row, ["Cj typ", "I/O-GND"])
    if pd.notna(cap_val):
        try:
            specs_result["Capacitance"] = f"{float(cap_val):.2f} pF"
        except (ValueError, TypeError):
            specs_result["Capacitance"] = safe_strip(cap_val)

    return specs_result