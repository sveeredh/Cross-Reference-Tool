
import pandas as pd
from parsing import safe_strip
import re

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

# --- ADD this entire function to diodes_scrape.py ---

def _parse_diodes_digikey_zener_row(part_row, source_df_name, part_number):
    """
    Parses a row from the Digi-Key formatted Diodes Inc. Zener datasheet.
    """
    specs_result = {
        "Device Name": str(part_number).upper(),
        "Source File": source_df_name,
        "Grade": "Non-Automotive",
        "Direction": "Unidirectional",
        "Channels": "1",
        "Voltage - Reverse Standoff (Typ)": "-", # Vz
        "Tolerance": "-",
        "Power Dissipation (Pd)": "-",
        "Package": "-",
    }

    # Package (Prioritize 'Supplier Device Package')
    pkg_supplier = safe_strip(_get_value_by_keywords(part_row, ["Supplier Device Package"]))
    specs_result["Package"] = pkg_supplier if pkg_supplier != "-" else safe_strip(_get_value_by_keywords(part_row, ["Package / Case"]))

    # Voltage - Zener (Vz)
    vz_raw = _get_value_by_keywords(part_row, ["Voltage - Zener"])
    if pd.notna(vz_raw) and vz_raw != "-":
        numeric_match = re.search(r"([\d.]+)", str(vz_raw))
        if numeric_match:
            specs_result["Voltage - Reverse Standoff (Typ)"] = f"{float(numeric_match.group(1)):g} V"

    # Tolerance
    tol_raw = _get_value_by_keywords(part_row, ["Tolerance"])
    if pd.notna(tol_raw) and tol_raw != "-":
        specs_result["Tolerance"] = safe_strip(tol_raw)

    # Power - Max
    pd_raw = _get_value_by_keywords(part_row, ["Power - Max"])
    if pd.notna(pd_raw) and pd_raw != "-":
        pd_str = str(pd_raw).lower()
        numeric_match = re.search(r"([\d.]+)", pd_str)
        if numeric_match:
            val = float(numeric_match.group(1))
            if "mw" in pd_str: # Convert mW to W
                val /= 1000
            specs_result["Power Dissipation (Pd)"] = f"{val:g} W"

    return specs_result

def _parse_diodes_zener_row(part_row, found_df_name, part_number):
    """
    Parses a row from a Diodes Inc. Zener datasheet to extract Zener-specific parameters.
    """
    specs_result = {
        "Device Name": str(part_number).upper(),
        "Source File": found_df_name,
        "Grade": "Non-Automotive", # Default
        "Voltage - Reverse Standoff (Typ)": "-", # This will hold Vz for comparison
        "Tolerance": "-",
        "Power Dissipation (Pd)": "-",
        "Package": "-",
    }

    # Grade
    if 'Yes' in safe_strip(_get_value_by_keywords(part_row, ["AEC Qualified"])):
        specs_result["Grade"] = "Automotive"
    elif 'automotive' in safe_strip(_get_value_by_keywords(part_row, ["Compliance"])).lower():
        specs_result["Grade"] = "Automotive"

    # Package
    specs_result["Package"] = safe_strip(_get_value_by_keywords(part_row, ["Packages"]))

    # Vz (mapped to the standard key for compatibility)
    vz_val = _get_value_by_keywords(part_row, ["Nom Vz"])
    if pd.notna(vz_val):
        specs_result["Voltage - Reverse Standoff (Typ)"] = f"{float(vz_val):g} V"

    # Tolerance
    tol_val = _get_value_by_keywords(part_row, ["Tol V"])
    if pd.notna(tol_val):
        specs_result["Tolerance"] = f"±{float(tol_val):g}%"

    # Power Dissipation (comes in mW, needs to be converted to W)
    pd_val_mw = _get_value_by_keywords(part_row, ["Power"])
    if pd.notna(pd_val_mw):
        pd_in_watts = float(pd_val_mw) / 1000.0
        specs_result["Power Dissipation (Pd)"] = f"{pd_in_watts:g} W"

    return specs_result

def _parse_diodes_tvs_row(part_row, found_df_name, part_number):
    """
    Parses a row from a standard Diodes Inc. TVS/ESD datasheet.
    """
    specs_result = { "Device Name": part_number, "Source File": found_df_name, "IEC 61000-4-5": "-", "IEC 61000-4-2": "-", "Capacitance": "-", "Channels": "-", "Package": "-", "Direction": "-", "Voltage - Reverse Standoff (Typ)": "-", "Voltage - Clamping (Max) @ Ipp": "-", "Price ($/ku)": "-", "Grade": "-" }

    specs_result["Package"] = safe_strip(_get_value_by_keywords(part_row, ["Packages"]))
    
    ch_val = safe_strip(_get_value_by_keywords(part_row, ["Channel"]))
    specs_result["Channels"] = ch_val if str(ch_val).isdigit() else "1"

    config_text = safe_strip(_get_value_by_keywords(part_row, ["Configuration"])).lower()
    if 'unidire' in config_text: specs_result["Direction"] = "Unidirectional"
    elif 'bidire' in config_text: specs_result["Direction"] = "Bidirectional"
    
    compliance_text = safe_strip(_get_value_by_keywords(part_row, ["Compliance"])).lower()
    if 'automotive' in compliance_text:
        specs_result["Grade"] = "Automotive"

    vrwm_val = _get_value_by_keywords(part_row, ["Reverse Standoff", "VRWM(V)"])
    if pd.notna(vrwm_val): specs_result["Voltage - Reverse Standoff (Typ)"] = f"{float(vrwm_val):g} V"

    vcl_val = _get_value_by_keywords(part_row, ["Clamping Voltage"])
    if pd.notna(vcl_val):
        try:
            match = re.search(r'([\d.]+)', str(vcl_val))
            if match:
                specs_result["Voltage - Clamping (Max) @ Ipp"] = f"{float(match.group(1)):g} V"
            else:
                specs_result["Voltage - Clamping (Max) @ Ipp"] = safe_strip(vcl_val)
        except (ValueError, TypeError):
            specs_result["Voltage - Clamping (Max) @ Ipp"] = safe_strip(vcl_val)
    
    surge_val = _get_value_by_keywords(part_row, ["Peak Pulse Current", "IPP @ 8x20"])
    if pd.notna(surge_val): specs_result["IEC 61000-4-5"] = f"{float(surge_val):g} A (8/20µs)"

    esd_val_raw = safe_strip(_get_value_by_keywords(part_row, ["Contact Discharge(k", "Contact Discharge (V)"]))
    if esd_val_raw != "-":
        cleaned_esd_str = str(esd_val_raw).replace('±', '').strip()
        try:
            esd_float = float(cleaned_esd_str)
            if "(v)" in str(_get_value_by_keywords(part_row, ["Contact Discharge"])).lower() and esd_float > 1000:
                 esd_float /= 1000
            specs_result["IEC 61000-4-2"] = f"±{esd_float:g} kV"
        except (ValueError, TypeError):
            specs_result["IEC 61000-4-2"] = esd_val_raw

    cap_val = _get_value_by_keywords(part_row, ["Input Capacitance", "CT Typ"])
    if pd.notna(cap_val): specs_result["Capacitance"] = f"{float(cap_val):.2f} pF"

    return specs_result

def fetch_diodes_specs_from_excel(part_number, diodes_dfs):
    """
    Searches across Diodes Inc. DataFrames, finds the part, and dispatches to the correct parser.
    """
    if part_number is None or not diodes_dfs:
        return None

    part_row = None
    found_df_name = ""

    search_priority = ["Data Line", "Power Line", "TSPDs", "USB-C", "Zener", "More Zeners", "More More Zeners"]
    ordered_keys = [key for key in search_priority if key in diodes_dfs] + [key for key in diodes_dfs if key not in search_priority]

    for df_name in ordered_keys:
        df = diodes_dfs[df_name]
        if df is None or df.empty: continue
        df.columns = df.columns.str.strip()
        
        # --- NEW ROBUST PART NUMBER COLUMN FINDER ---
        mfr_part_num_keywords = ["manufacturer part number", "part number", "mfr part"]
        part_num_col = None
        for col in df.columns:
            for keyword in mfr_part_num_keywords:
                if keyword in str(col).lower():
                    part_num_col = col
                    break
            if part_num_col:
                break
        # --- END OF NEW BLOCK ---

        if not part_num_col: continue

        part_row_series = df[df[part_num_col].astype(str).str.strip().str.lower() == part_number.lower()]
        
        if not part_row_series.empty:
            part_row = part_row_series.iloc[0]
            found_df_name = df_name
            break # Stop searching once the part is found

    if part_row is None:
        print(f"Part '{part_number}' not found in any Diodes Inc. database.")
        return None
    print(f"Found specs for Part '{part_number}' in Diodes Inc. database '{found_df_name}'.")

    # --- Dispatcher: Call the correct parser based on the source filename ---
    if "more more zeners" in found_df_name.lower():
        return _parse_diodes_digikey_zener_row(part_row, found_df_name, part_number)
    elif "zener" in found_df_name.lower():
        return _parse_diodes_zener_row(part_row, found_df_name, part_number)
    else:
        return _parse_diodes_tvs_row(part_row, found_df_name, part_number)
    