import pandas as pd
import re
from parsing import safe_strip

def _get_value_by_keywords(row, keywords):
    """
    Searches a row's index (column headers) for the first match from a list of keywords.
    The search is case-insensitive. Returns the value from the matched column.
    """
    for col_header in row.index:
        for keyword in keywords:
            if keyword.lower() in str(col_header).lower():
                val = row[col_header]
                return val if pd.notna(val) else "-"
    return "-" # Return "-" if no matching column is found


def _parse_littelfuse_row(part_row, found_df_name, part_number):
    """
    Parses a row from a Littelfuse CSV datasheet to extract TVS/ESD parameters.
    """
    specs_result = {
        "Device Name": part_number,
        "Source File": found_df_name,
        "Grade": "Automotive" if "auto" in found_df_name.lower() else "Commercial",
        "Package": "-",
        "Direction": "-",
        "Voltage - Reverse Standoff (Typ)": "-",
        "IEC 61000-4-5": "-",
        "Voltage - Clamping (Max) @ Ipp": "-",
        "IEC 61000-4-2": "-",
        "Capacitance": "-",
        "Channels": "-",
    }

    # --- Map columns based on screenshot and expected values ---

    # Package (Simple, no version)
    specs_result["Package"] = safe_strip(_get_value_by_keywords(part_row, ["Package"]))

    # Standoff Voltage (VR)
    vr_val = _get_value_by_keywords(part_row, ["Vstandoff", "Operating"])
    if vr_val != "-":
        specs_result["Voltage - Reverse Standoff (Typ)"] = f"{vr_val} V"


    # Directionality
    direction_str = safe_strip(_get_value_by_keywords(part_row, ["Uni / Bi-Directional", "Polarity"])).lower()
    if "uni" in direction_str:
        specs_result["Direction"] = "Unidirectional"
    elif "bi" in direction_str:
        specs_result["Direction"] = "Bidirectional"
    
    # Surge (Ipp)
    surge_val = _get_value_by_keywords(part_row, ["I PP 8x20µs (A)"])
    if surge_val != "-":
        specs_result["IEC 61000-4-5"] = f"{surge_val} A (8/20µs)"

    # esd
    esd_val_raw = _get_value_by_keywords(part_row, ["ESD Contact"])

    # Check for valid, non-placeholder input.
    if pd.notna(esd_val_raw) and esd_val_raw != "-":
        # Use regex to find the first valid number (integer or decimal).
        numeric_match = re.search(r"([\d.]+)", str(esd_val_raw))
        
        if numeric_match:
            try:
                # Extract the number, convert it to a float.
                numeric_val = float(numeric_match.group(1))
                # Now, build the final string with the clean number.
                specs_result["IEC 61000-4-2"] = f"±{numeric_val:g} kV"
            except (ValueError, IndexError):
                # If conversion fails for any reason, use a placeholder.
                specs_result["IEC 61000-4-2"] = "-"
        else:
            # If no number was found, it's not a valid value.
            specs_result["IEC 61000-4-2"] = "-"
    else:
        # If the initial value is a placeholder, keep it that way.
        specs_result["IEC 61000-4-2"] = "-"

    # --- Handle other potential columns not in the screenshot ---
    # These use the keyword search to be flexible across different CSV files.
    
    clamping_val = _get_value_by_keywords(part_row, ["Vc (V)", "Clamping"])
    if clamping_val != "-":
        specs_result["Voltage - Clamping (Max) @ Ipp"] = f"{clamping_val} V"
    
    cap_val_raw = _get_value_by_keywords(part_row, ["Co Typ (pF)", "CI/O TYP (pF)"])

    # Check for valid, non-placeholder input before processing
    if pd.notna(cap_val_raw) and cap_val_raw != "-":
        # Convert raw value to string to handle both numbers and text formats
        s_val = str(cap_val_raw)
        
        # Regex to find the first valid number (integer or decimal) in the string
        numeric_match = re.search(r"([\d.]+)", s_val)
        
        if numeric_match:
            try:
                # Extract the number, convert to float, and format consistently
                numeric_val = float(numeric_match.group(1))
                specs_result["Capacitance"] = f"{numeric_val:.2f} pF"
            except (ValueError, IndexError):
                # If conversion fails for any reason, use a placeholder
                specs_result["Capacitance"] = "-"
        else:
            # If no number is found, it's not a valid capacitance value
            specs_result["Capacitance"] = "-"
    else:
        # If the initial value is a placeholder, keep it
        specs_result["Capacitance"] = "-"
        
    channels_val = _get_value_by_keywords(part_row, ["Channels"])
    if channels_val != "-":
        specs_result["Channels"] = str(int(float(channels_val)))

    return specs_result

def fetch_littelfuse_specs_from_excel(part_number, df_map):
    """
    Searches for a Littelfuse part number across multiple CSVs with a specific priority.
    """
    if part_number is None or not df_map:
        return None

    # Use the search priority requested by the user
    search_priority = [
        "Auto_TVS", "Auto_TVS_Array", "TVS",
        "PESD", "TVS_Array"
    ]

    all_df_keys = list(df_map.keys())
    ordered_search_keys = [key for key in search_priority if key in all_df_keys]
    ordered_search_keys.extend([key for key in all_df_keys if key not in ordered_search_keys])
    
    for df_name in ordered_search_keys:
        df = df_map.get(df_name)
        if df is None or df.empty:
            continue
        
        part_num_col = next((col for col in df.columns if 'part number' in str(col).lower()), None)
        if not part_num_col:
            continue
        
        match_row_series = df[df[part_num_col].astype(str).str.strip().str.lower().str.startswith(part_number.lower())]
        
        if not match_row_series.empty:
            part_row = match_row_series.iloc[0]
            print(f"Found specs for Part '{part_number}' in Littelfuse database '{df_name}'.")
            # There are no Zeners for Littelfuse, so we directly call the standard parser.
            return _parse_littelfuse_row(part_row, df_name, part_number)

    print(f"Part '{part_number}' not found in any local Littelfuse files.")
    return None