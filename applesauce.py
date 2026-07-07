# --- libraries ---
from tabulate import tabulate
from tkinter import Tk, filedialog
import re
import time
import os
import pandas as pd
from get_data import _unblock_csv_file, download_and_rename_ti_specs, download_and_rename_ti_zener_specs, download_and_rename_aos_specs, download_diodes_specs, download_nexperia_specs, download_littelfuse_specs, download_jiangsu_specs, download_semtech_specs
from find_package_matches import find_package_matches
from parsing import safe_strip, normalize_package, normalize_structured_package, to_numeric_val, normalize_aos_package, normalize_amazing_package, normalize_diodes_package, normalize_nexperia_package, normalize_littelfuse_package, normalize_jiangsu_package, normalize_semtech_package
from ti_scrape import load_excel_data, fetch_ti_specs_from_excel
from ti_zener_scrape import fetch_ti_zener_specs_from_excel
from aos_scrape import fetch_aos_specs_from_excel
from amazing_scrape import fetch_amazing_specs_from_excel
from diodes_scrape import fetch_diodes_specs_from_excel
from nexperia_scrape import fetch_nexperia_specs_from_excel
from littelfuse_scrape import fetch_littelfuse_specs_from_excel
from jiangsu_scrape import fetch_jiangsu_specs_from_excel
from vishay_scrape import fetch_vishay_specs_from_excel
from semtech_scrape import fetch_semtech_specs_from_excel
from stm_scrape import fetch_stm_specs_from_excel
from panjit_scrape import fetch_panjit_specs_from_excel
from onsemi_scrape import fetch_onsemi_specs_from_excel
import sys

CACHE_DIR = ".applesauce_cache"

# ============================================================================
# Canonical package classification (DigiKey <-> TI)
#
# Each package+pin combo this program understands maps to a canonical code
# like "DFN0603_2", "X2SON_4", "SOT886_6". DigiKey strings are read primarily
# from "Supplier Device Package", falling back to "Package / Case" for pin
# disambiguation when needed. TI strings are read from the Package name +
# Pin count columns. Anything that doesn't match a known rule returns None
# and falls back to the legacy normalize_package() matching.
# ============================================================================

def _has_pin_marker(text, pin_n):
    """True if `text` has pin_n indicated via -N suffix, N- prefix, or a
    trailing token containing pin_n (e.g. 'P2', '-2L')."""
    t = text.upper()
    n = str(pin_n)
    if re.search(rf'(^|[^0-9]){n}-', t):
        return True
    if re.search(rf'-{n}([^0-9]|$)', t):
        return True
    if re.search(rf'-{n}[A-Z]', t):
        return True
    if re.search(rf'[A-Z]{n}(\b|$)', t):
        return True
    return False


def _has_pin_count_anywhere(text, pin_n):
    """Looser check: pin_n appears as a standalone-ish number in the string."""
    return bool(re.search(rf'(?<![0-9]){pin_n}(?![0-9])', text.upper()))


def _has_dim(text, *dim_variants):
    """True if any of the given dimension strings (e.g. '0.6x0.3', '0603')
    appear in text, accommodating separators like 'x', '.', '-', ' '."""
    t = text.upper().replace(' ', '')
    for variant in dim_variants:
        v = variant.upper().replace(' ', '')
        # Build a loose regex: digits/dots literally, x/X as separator, allow optional . and -
        pattern = re.escape(v).replace('X', '[Xx]').replace(r'\.', r'\.?')
        if re.search(pattern, t):
            return True
    return False


def _classify_digikey_package(supplier_pkg, case_pkg):
    """
    Returns a canonical code (e.g. 'DFN0603_2') for a DigiKey part based on
    Supplier Device Package (primary) and Package / Case (backup, for pin info).
    Returns None if no rule matches.
    """
    sup = str(supplier_pkg or "").strip().upper()
    case = str(case_pkg or "").strip().upper()
    combined = f"{sup} {case}"

    if not sup and not case:
        return None

    # --- SOD523 (must be checked before DFN0603, since some DigiKey entries
    # mislabel it like "0603/SOD-523") ---
    if _has_dim(combined, "SOD523", "SOD-523"):
        return "SOD523_2"

    # --- SOD323 ---
    if _has_dim(combined, "SOD323", "SOD-323"):
        return "SOD323_2"

    # --- DFN0603 (2-pin) / X2SON (4-pin) ---
    # 0603-style dims: 0.6x0.3, 0.62x0.32, or literal '0603' with pin markers.
    is_0603_dim = _has_dim(sup, "0.6x0.3", ".6x0.3", "0.6x.3", "0.62x0.32", "0808")
    is_0603_token = bool(re.search(r'(^|[^0-9])0603([^0-9]|$)', sup)) or \
                    bool(re.search(r'^0?603$', sup.replace(' ', '')))
    is_0808_dim = _has_dim(sup, "0.8x0.8", "0808")

    if is_0808_dim and _has_pin_marker(combined, 4):
        return "X2SON_4"
    if "X2SON" in sup:
        return "X2SON_4"

    if (is_0603_dim or is_0603_token) and not _has_dim(sup, "0201"):
        # Exclude if Package/Case explicitly indicates a different pin count (e.g. 3-pin)
        if _has_pin_marker(case, 3) or _has_pin_count_anywhere(case, 3):
            return None  # not our 2-pin DFN0603; some other pin count
        return "DFN0603_2"

    if _has_dim(sup, "0201"):
        if _has_pin_marker(case, 3) or _has_pin_count_anywhere(case, 3):
            return None
        return "DFN0603_2"

    # --- SOD882 -> DFN1006 (same footprint) ---
    if _has_dim(sup, "SOD882", "SOD-882", "SOD 882"):
        return "DFN1006_2"

    # --- DFN1006 (1x0.6, 2-pin or 3-pin) ---
    if _has_dim(sup, "1x0.6", "1.0x0.6", "1006"):
        if _has_pin_marker(combined, 3):
            return "DFN1006_3"
        return "DFN1006_2"

    # --- DFN1616 (1.6x1.6, 6-pin, suffix VEBR) ---
    if _has_dim(sup, "1.6x1.6", "1616") and _has_pin_marker(combined, 6):
        return "DFN1616_6"

    # --- DFN1110 (3-pin) ---
    if _has_dim(sup, "1110", "1.1x1.0", "1.1x1"):
        return "DFN1110_3"

    # --- DFN2020 (2x2, 6-pin) ---
    if _has_dim(sup, "2x2", "2020"):
        return "DFN2020_6"

    # --- DFN2510 (10-pin) ---
    if _has_dim(sup, "2510", "2.5x1.0", "2.5x1"):
        return "DFN2510_10"

    # --- SOT-886 (1.45x1.0, 6-pin, suffix DRYR) ---
    if _has_dim(sup, "1.45x1.0", "1.45x1", "886") and _has_pin_marker(combined, 6):
        return "SOT886_6"
    if "SOT886" in sup.replace('-', '') or "SOT-886" in sup:
        return "SOT886_6"
    if "XSON" in sup and _has_pin_marker(combined, 6):
        return "SOT886_6"

    # --- USON (6-pin, 1.6x1.6, suffix DPKR) ---
    if _has_dim(sup, "1.6x1.6") and _has_pin_marker(combined, 6) and "USON" in combined:
        return "USON_6"

    # --- DFN3030 (3x3, 8-pin) / 8-MSOP ---
    if _has_dim(sup, "3x3", "3030") or "MSOP" in sup:
        return "DFN3030_8"

    # --- DSBGA (4-pin) ---
    if "DSBGA" in sup:
        return "DSBGA_4"

    # --- SC70-3 / SOT323 ---
    if re.search(r'SC-?70-?3', sup) or "SOT323" in sup.replace('-', '') or (re.search(r'SC-?70', sup) and not re.search(r'SC-?70-?6', sup) and "SC88" not in sup.replace('-', '')):
        return "SC703_3"

    # --- SC70-6 / SOT363 / SC-88 ---
    if re.search(r'SC-?70-?6', sup) or "SOT363" in sup.replace('-', '') or "SC88" in sup.replace('-', '').replace(' ', ''):
        return "SC706_6"

    # --- SOT-23 family ---
    if "SOT23" in sup.replace('-', '').replace(' ', '') or "SOT143" in sup.replace('-', '').replace(' ', ''):
        sup_clean = sup.replace('-', '').replace(' ', '')
        if "SOT143" in sup_clean:
            return "SOT234_4"
        if _has_pin_marker(combined, 5):
            return "SOT235_5"
        if _has_pin_marker(combined, 6):
            return "SOT236_6"
        if _has_pin_marker(combined, 4):
            return "SOT234_4"
        return "SOT233_3"

    # --- SOT-5X3 (2-pin: SOT523, 5-pin: SOT553) ---
    if "SOT523" in sup.replace('-', '').replace(' ', ''):
        return "SOT523_2"
    if "SOT553" in sup.replace('-', '').replace(' ', ''):
        return "SOT553_5"

    # --- SOT-9X3 (1x1, 3-pin) ---
    if _has_dim(sup, "1x1") and _has_pin_marker(combined, 3):
        return "SOT9X3_3"

    # --- UQFN family ---
    if "UQFN" in sup or "U-QFN" in sup:
        if _has_dim(sup, "3.5x1.35", "3.5x1.4"):
            return "UQFN_14"
        if _has_dim(sup, "2.0x1.5", "2x1.5"):
            return "UQFN_10"
        if "10-UQFN" in case.replace(' ', '') or _has_pin_marker(combined, 10):
            return "UQFN_10"
        if _has_pin_marker(combined, 14):
            return "UQFN_14"

    # --- WQFN (4x4, 12-pin) ---
    if "WQFN" in sup:
        if _has_dim(sup, "4x4"):
            return "WQFN_12"
        if _has_pin_marker(combined, 12):
            return "WQFN_12"

    # --- WSON (6-pin DRSR, 15-pin) ---
    if "WSON" in sup:
        if _has_dim(sup, "3x3") and _has_pin_marker(combined, 6):
            return "WSON_6"
        if _has_pin_marker(combined, 15) or "15-SON" in case.replace(' ', ''):
            return "WSON_15"
        if _has_pin_marker(combined, 6):
            return "WSON_6"

    if "15-SON" in sup.replace(' ', '') or "15SON" in sup.replace(' ', '').replace('-', ''):
        return "WSON_15"

    # --- Fallback: try classifying using Package / Case as the primary string ---
    if case and case != sup:
        return _classify_digikey_package(case, "")

    return None


def _classify_ti_package(ti_pkg_token, pin_val):
    """
    Returns a canonical code for a TI package name + pin count, matching
    the same code space as _classify_digikey_package.
    """
    norm = normalize_package(ti_pkg_token)
    norm_u = norm.upper()

    if norm_u.startswith("SOD523"):
        return "SOD523_2"
    if norm_u.startswith("SOD323"):
        return "SOD323_2"
    if norm_u.startswith("DFN0603"):
        return "DFN0603_2"
    if norm_u.startswith("X2SON"):
        return f"X2SON_{pin_val}" if pin_val else "X2SON_4"
    if norm_u.startswith("DFN1006"):
        return f"DFN1006_{pin_val}" if pin_val else "DFN1006_2"
    if norm_u.startswith("DFN1616"):
        return "DFN1616_6"
    if norm_u.startswith("DFN1110"):
        return "DFN1110_3"
    if norm_u.startswith("DFN2020"):
        return "DFN2020_6"
    if norm_u.startswith("DFN2510"):
        return "DFN2510_10"
    if "SOT886" in norm_u or "SOT-886" in ti_pkg_token.upper():
        return "SOT886_6"
    if norm_u.startswith("USON"):
        return "USON_6"
    if norm_u.startswith("DFN3030"):
        return "DFN3030_8"
    if norm_u.startswith("DSBGA"):
        return "DSBGA_4"
    if norm_u.startswith("SC703"):
        return "SC703_3"
    if norm_u.startswith("SC706"):
        return "SC706_6"
    if norm_u.startswith("SOT233"):
        return "SOT233_3"
    if norm_u.startswith("SOT234"):
        return "SOT234_4"
    if norm_u.startswith("SOT235"):
        return "SOT235_5"
    if norm_u.startswith("SOT236"):
        return "SOT236_6"
    if re.match(r"SOT5\d3", norm_u):
        return "SOT523_2" if pin_val == 2 else "SOT553_5"
    if re.match(r"SOT9\d3", norm_u):
        return "SOT9X3_3"
    if norm_u.startswith("UQFN"):
        if pin_val == 14: return "UQFN_14"
        if pin_val == 10: return "UQFN_10"
        return None
    if norm_u.startswith("WQFN"):
        return "WQFN_12"
    if norm_u.startswith("WSON"):
        if pin_val == 6: return "WSON_6"
        if pin_val == 15: return "WSON_15"
        return None

    return None


# Canonical code -> TI OPN suffix
CANONICAL_SUFFIX_MAP = {
    "SOD523_2": "DYAR",
    "SOD323_2": "DYFR",
    "DFN0603_2": "DPLR",
    "X2SON_4": "DPWR",
    "DFN1006_2": "DPYR",
    "DFN1006_3": "DMXR",   # ESD122 exception
    "DFN1616_6": "VEBR",
    "DFN1110_3": "DXAR",
    "DFN2020_6": "DRVR",
    "DFN2510_10": "DQAR",
    "SOT886_6": "DRYR",
    "USON_6": "DPKR",
    "DFN3030_8": "DRBR",
    "DSBGA_4": "YZFR",
    "SC703_3": "DCKR",
    "SC706_6": "DCKR",
    "SOT233_3": "DBZR",
    "SOT234_4": "DZDR",
    "SOT235_5": "DBVR",
    "SOT236_6": "DBVR",
    "SOT523_2": "DRLR",
    "SOT553_5": "DRLR",
    "SOT9X3_3": "DRTR",
    "UQFN_14": "RVZR",
    "UQFN_10": "RSER",
    "WQFN_12": "RSFR",
    "WSON_6": "DRSR",
    "WSON_15": "DSMR",
}

def load_and_cache(cache_filename, source_path, force_reload, loader_func):
    if not os.path.exists(source_path):
        return pd.DataFrame()
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)
    cache_path = os.path.join(CACHE_DIR, cache_filename)
    use_cache = False
    if os.path.exists(cache_path) and not force_reload:
        try:
            if os.path.getmtime(source_path) < os.path.getmtime(cache_path):
                use_cache = True
        except FileNotFoundError:
            use_cache = False
    if use_cache:
        return pd.read_pickle(cache_path)
    else:
        print(f"Processing and caching '{os.path.basename(source_path)}'...")
        df = loader_func()
        if df is not None and not df.empty:
            df.to_pickle(cache_path)
        return df

PACKAGE_DISPLAY_MAP = {
    "SOT236": "SOT-23-6",
    "SOT235": "SOT-23-5",
    "SOT233": "SOT-23-3",
    "SC76": "SC-76",
    "SC79": "SC-79",
}

TI_SPECS_DATABASE_FILE = "ti_specs.xlsx"
TI_ZENER_SPECS_DATABASE_FILE = "ti_zener_specs.xlsx"
AOS_SPECS_DATABASE_FILE = "aos_specs.xlsx"
AMAZING_SPECS_DATABASE_FILE = "amazing_specs.xlsx"
DIODES_DL_DATABASE_FILE = "diodes_specs_dl.xlsx"
DIODES_PL_DATABASE_FILE = "diodes_specs_pl.xlsx"
DIODES_TS_DATABASE_FILE = "diodes_specs_ts.xlsx"
DIODES_USBC_DATABASE_FILE = "diodes_specs_usbc.xlsx"
NEXPERIA_ZENER_DATABASE_FILE = "nexperia_specs_zener.xls"
NEXPERIA_AUTO_ZENER_DATABASE_FILE = "nexperia_specs_auto_zener.xls"
NEXPERIA_ESD_DATABASE_FILE = "nexperia_specs_esd.xls"
NEXPERIA_EMI_DATABASE_FILE = "nexperia_specs_emi.xls"
NEXPERIA_TVS_DATABASE_FILE = "nexperia_specs_tvs.xls"
NEXPERIA_MORE_CSV = "nexperia_specs_more.csv"
NEXPERIA_MORE = "nexperia_specs_more.xlsx"
NEXPERIA_AUTO_PROTECTION_DATABASE_FILE = "nexperia_specs_auto_protection.xls"
LITTELFUSE_AUTO_TVS_ARRAY_FILE = "littelfuse_specs_auto_tvs_array.xlsx"
LITTELFUSE_TVS_ARRAY_FILE = "littelfuse_specs_tvs_array.xlsx"
LITTELFUSE_AUTO_TVS_FILE = "littelfuse_specs_auto_tvs.xlsx"
LITTELFUSE_TVS_FILE = "littelfuse_specs_tvs.xlsx"
LITTELFUSE_PESD_FILE = "littelfuse_specs_pesd.xlsx"
JIANGSU_ESD_FILE = "jiangsu_specs_esd.xlsx"
JIANGSU_TVS_FILE = "jiangsu_specs_tvs.xlsx"
JIANGSU_ZENER_FILE = "jiangsu_specs_zener.xlsx"
VISHAY_SPECS_CSV_FILE = "vishay_specs_protection.csv"
VISHAY_SPECS_DATABASE_FILE = "vishay_specs_protection.xlsx"
SEMTECH_SPECS_FILE = "semtech_specs.xlsx"
STM_SPECS_CSV_FILE = "stmicroelectronics_specs.csv"
STM_SPECS_DATABASE_FILE = "stmicroelectronics_specs.xlsx"
PANJIT_SPECS_CSV_FILE = "panjit_specs.csv"
PANJIT_SPECS_DATABASE_FILE = "panjit_specs.xlsx"
ONSEMI_SPECS_CSV_TVS = "onsemi_specs_tvs.csv"
ONSEMI_SPECS_TVS = "onsemi_specs_tvs.xlsx"
DIGIKEY_SPECS_CSV = "digikey_specs.csv"


def is_similar_voltage(v1_str, v2_str, tolerance_percent=20):
    v1 = to_numeric_val(v1_str, default_if_error=None)
    v2 = to_numeric_val(v2_str, default_if_error=None)
    if v1 is None or v2 is None:
        return False
    if v1 == 0 and v2 == 0: return True
    if v1 == 0 or v2 == 0: return False
    return abs(v1 - v2) / v1 * 100 <= tolerance_percent

def _find_part_number_column(df, keywords):
    if df is None or df.empty:
        return None
    for col in df.columns:
        for keyword in keywords:
            if keyword.lower() in str(col).lower():
                return col
    return None

def _find_exact_match(norm_input, df, part_col):
    df_normalized = df[part_col].astype(str).str.upper().str.replace(r'[\W_]+', '', regex=True)
    match_mask = (df_normalized == norm_input)
    if match_mask.any():
        return df.loc[match_mask.idxmax()][part_col]
    return None

def _find_partial_match(norm_input, df, part_col):
    df_normalized = df[part_col].astype(str).str.upper().str.replace(r'[\W_]+', '', regex=True)
    match_mask = df_normalized.str.contains(norm_input, na=False)
    if match_mask.any():
        return df.loc[match_mask.idxmax()][part_col]
    return None

def _find_reverse_partial_match(norm_input, df, part_col):
    df_normalized = df[part_col].astype(str).str.upper().str.replace(r'[\W_]+', '', regex=True)
    non_empty_mask = (df_normalized != '') & (df_normalized.notna())
    reverse_partial_mask = df_normalized[non_empty_mask].apply(lambda db_part: norm_input.startswith(db_part))
    if reverse_partial_mask.any():
        return df.loc[reverse_partial_mask.idxmax()][part_col]
    return None

def _find_fuzzy_match(norm_input, df, part_col, min_len):
    if len(norm_input) <= min_len:
        return None
    df_normalized = df[part_col].astype(str).str.upper().str.replace(r'[\W_]+', '', regex=True)
    non_empty_mask = (df_normalized != '') & (df_normalized.notna())
    for i in range(len(norm_input) - 1, min_len - 1, -1):
        sliced_input = norm_input[:i]
        fuzzy_match_mask = df_normalized[non_empty_mask].str.startswith(sliced_input, na=False)
        if fuzzy_match_mask.any():
            matched_part = df.loc[fuzzy_match_mask.idxmax()][part_col]
            print(f"--> Fuzzy match (min_len={min_len}) found. Using closest match: '{matched_part}'")
            return matched_part
    return None


PART_COL = "Mfr Part #"
SUPPLIER_COL = "Supplier"


def _normalize_digikey_package(raw_pkg):
    """
    Handles DigiKey package strings like 'SC-79', 'SOD-523', '0402 (1005 Metric)',
    and comma-separated lists as seen in TI parts. Strips imperial/metric size
    suffixes and passes the result through the standard normalize_package.
    """
    if not raw_pkg or raw_pkg == "-":
        return raw_pkg

    # If there are multiple packages separated by commas, normalize each one.
    parts = [p.strip() for p in str(raw_pkg).split(',') if p.strip()]
    normalized = []
    for p in parts:
        # Strip trailing imperial/metric size annotations like '0402 (1005 Metric)'
        # keeping only the first token when it looks like a passive size code.
        p_clean = re.sub(r'\s*\(.*?\)', '', p).strip()
        normalized.append(normalize_package(p_clean))

    return ', '.join(normalized) if normalized else raw_pkg


def _clean_digikey_value(val):
    """
    Keeps number + unit, strips trailing qualifiers like '(Max)', '@ 1MHz'.
    Adds a space between number and unit: '12V (Max)' -> '12 V', '55pF @ 1MHz' -> '55 pF'.
    """
    if not val or val == "-":
        return val
    m = re.match(r'^\s*([±]?\s*[\d.]+)\s*([a-zA-ZµΩ]+)?', str(val))
    if not m:
        return val
    number = m.group(1).strip()
    unit = m.group(2) or ""
    return f"{number} {unit}".strip() if unit else number



def fetch_digikey_specs_from_csv(part_input, supplier_input, digikey_df):
    """
    Looks up a competitor part in digikey_specs.csv filtered by supplier name,
    then falls back to an unfiltered search if no match is found.
    """
    if digikey_df is None or digikey_df.empty:
        print("DigiKey specs CSV is not loaded.")
        return None

    if PART_COL not in digikey_df.columns:
        print(f"Column '{PART_COL}' not found in digikey_specs.csv.")
        return None

    norm_input = re.sub(r'[\W_]+', '', str(part_input).upper())
    norm_supplier = str(supplier_input).strip().lower()

    # Filter to this supplier's rows first to avoid cross-competitor part name collisions.
    supplier_df = pd.DataFrame()
    if SUPPLIER_COL in digikey_df.columns and norm_supplier:
        supplier_df = digikey_df[digikey_df[SUPPLIER_COL].astype(str).str.lower().str.contains(norm_supplier, na=False)]
        print(f"Filtered to {len(supplier_df)} rows for supplier '{supplier_input}'.")

    search_df = supplier_df if not supplier_df.empty else digikey_df
    if supplier_df.empty and norm_supplier:
        print(f"No supplier match for '{supplier_input}', searching all rows.")

    exact_part_name = (
        _find_exact_match(norm_input, search_df, PART_COL) or
        _find_partial_match(norm_input, search_df, PART_COL) or
        _find_reverse_partial_match(norm_input, search_df, PART_COL) or
        _find_fuzzy_match(norm_input, search_df, PART_COL, min_len=6) or
        _find_fuzzy_match(norm_input, search_df, PART_COL, min_len=4)
    )

    if not exact_part_name:
        print(f"Could not find '{part_input}' in digikey_specs.csv.")
        return None

    print(f"Found match '{exact_part_name}'. Fetching specs...")
    row = search_df[search_df[PART_COL].astype(str) == str(exact_part_name)].iloc[0]

    def get(col_name):
        val = str(row.get(col_name, "-") if col_name in row.index else "-").strip()
        return "-" if val.lower() in ("", "nan", "none") else val

    # Directionality: one of the two channel columns will be populated.
    uni_ch = get("Unidirectional Channels")
    bi_ch = get("Bidirectional Channels")
    if bi_ch != "-":
        direction = "Bidirectional"
        channels = bi_ch
    elif uni_ch != "-":
        direction = "Unidirectional"
        channels = uni_ch
    else:
        direction = "-"
        channels = "1"

    # Zener detection: no clamping voltage and has a breakdown voltage column filled.
    clamping = get("Voltage - Clamping (Max) @ Ipp")
    breakdown = get("Voltage - Breakdown (Min)")
    vrwm = get("Voltage - Reverse Standoff (Typ)")
    is_zener = (clamping == "-") and (breakdown != "-" or (vrwm != "-" and clamping == "-"))
    source_label = "DigiKey Zener" if is_zener else "DigiKey"

    raw_pkg = get("Package / Case")
    supplier_device_pkg = get("Supplier Device Package")
    canonical_pkg = _classify_digikey_package(supplier_device_pkg, raw_pkg)
    # Primary display package: Supplier Device Package, fall back to Package / Case
    display_pkg = supplier_device_pkg if supplier_device_pkg != "-" else raw_pkg

    grade_raw = get("Grade")
    grade = "Automotive" if "automotive" in grade_raw.lower() else "Commercial"

    specs = {
        "Device Name": exact_part_name,
        "Package": display_pkg,
        "Canonical Package": canonical_pkg,
        "Voltage - Reverse Standoff (Typ)": _clean_digikey_value(vrwm),
        "Voltage - Breakdown (Min)": _clean_digikey_value(breakdown),
        "Voltage - Clamping (Max) @ Ipp": _clean_digikey_value(clamping),
        "Capacitance": _clean_digikey_value(get("Capacitance @ Frequency")),
        "Channels": channels,
        "Direction": direction,
        "IEC 61000-4-5": _clean_digikey_value(get("IEC 61000-4-5")),
        "IEC 61000-4-2": _clean_digikey_value(get("IEC 61000-4-2")),
        "Tolerance": get("Tolerance"),
        "Power Dissipation (Pd)": (lambda v: (v + " W") if v != "-" else "-")(_clean_digikey_value(next((str(row[c]).strip() for c in digikey_df.columns if "peak pulse" in c.lower() and str(row.get(c, "")).strip().lower() not in ("", "nan", "none", "-")), "-"))),
        "Price ($/ku)": get("Price ($/ku)"),
        "Grade": grade,
        "Source File": source_label,
    }
    return specs


def get_competitor_specs_leniently(part_input, competitor_name, all_dfs, digikey_df):
    """
    Central dispatcher. Checks competitor-specific local databases first,
    falls back to DigiKey CSV if not found.
    """
    print(f"\nPerforming lenient search for '{part_input}' from competitor '{competitor_name}'...")

    aos_specs_df, amazing_specs_df, diodes_df_map, nexperia_df_map, \
    littelfuse_df_map, jiangsu_df_map, vishay_df, semtech_specs_df, \
    stm_specs_df, panjit_specs_df, onsemi_tvs_df, ti_specs_df, ti_zener_specs_df = all_dfs

    norm_input = re.sub(r'[\W_]+', '', str(part_input).upper())
    if not norm_input:
        return None

    exact_part_name = None
    comp_specs = None

    is_diodes_part = any(k in competitor_name for k in ["diodes", "diodes inc"])
    is_nexperia_part = "nexperia" in competitor_name
    is_littelfuse_part = any(k in competitor_name for k in ["lf", "littelfuse"])
    is_jiangsu_part = any(k in competitor_name for k in ["jiangsu", "jsu"])

    if is_diodes_part or is_nexperia_part or is_littelfuse_part or is_jiangsu_part:
        df_map = {}
        part_col_keywords = []
        fetch_func = None

        if is_diodes_part:
            df_map, part_col_keywords, fetch_func = (diodes_df_map, ["part number", "mfr part"], fetch_diodes_specs_from_excel)
        elif is_nexperia_part:
            df_map, part_col_keywords, fetch_func = (nexperia_df_map, ["type number", "part number", "mfr part"], fetch_nexperia_specs_from_excel)
        elif is_littelfuse_part:
            df_map, part_col_keywords, fetch_func = (littelfuse_df_map, ["part number", "mfr part"], fetch_littelfuse_specs_from_excel)
        elif is_jiangsu_part:
            df_map, part_col_keywords, fetch_func = (jiangsu_df_map, ["part number", "mfr part"], fetch_jiangsu_specs_from_excel)

        non_fuzzy_methods = [
            _find_exact_match,
            _find_partial_match,
            _find_reverse_partial_match,
        ]
        fuzzy_methods = [
            lambda ni, df, pc: _find_fuzzy_match(ni, df, pc, min_len=6),
            lambda ni, df, pc: _find_fuzzy_match(ni, df, pc, min_len=4)
        ]

        for method in non_fuzzy_methods:
            for df_name, df in df_map.items():
                part_col = _find_part_number_column(df, part_col_keywords)
                if part_col:
                    exact_part_name = method(norm_input, df, part_col)
                    if exact_part_name:
                        comp_specs = fetch_func(exact_part_name, df_map)
                        break
            if comp_specs:
                break

        if not comp_specs:
            # Before fuzzy, try DigiKey
            dk_specs = fetch_digikey_specs_from_csv(part_input, competitor_name, digikey_df)
            if dk_specs:
                return dk_specs
            # Fall back to fuzzy
            for method in fuzzy_methods:
                for df_name, df in df_map.items():
                    part_col = _find_part_number_column(df, part_col_keywords)
                    if part_col:
                        exact_part_name = method(norm_input, df, part_col)
                        if exact_part_name:
                            comp_specs = fetch_func(exact_part_name, df_map)
                            break
                if comp_specs:
                    break

    else:
        single_file_checks = [
            ("aos", aos_specs_df, ["product", "part number", "mfr part"], fetch_aos_specs_from_excel),
            ("amazing", amazing_specs_df, ["part number", "mfr part"], fetch_amazing_specs_from_excel),
            ("onsemi", onsemi_tvs_df, ["manufacturer part number", "part number", "mfr part"], fetch_onsemi_specs_from_excel),
            ("vishay", vishay_df, ["manufacturer part number", "part number", "mfr part"], fetch_vishay_specs_from_excel),
            ("semtech", semtech_specs_df, ["parts", "mfr part"], fetch_semtech_specs_from_excel),
            ("stm", stm_specs_df, ["manufacturer part number", "part number", "mfr part"], fetch_stm_specs_from_excel),
            ("panjit", panjit_specs_df, ["manufacturer part number", "part number", "mfr part"], fetch_panjit_specs_from_excel)
        ]

        found_competitor = False
        for comp_key, df, keywords, fetch_func in single_file_checks:
            if comp_key in competitor_name:
                found_competitor = True
                part_col = _find_part_number_column(df, keywords)
                if part_col:
                    # Try exact/partial/reverse before fuzzy
                    exact_part_name = (
                        _find_exact_match(norm_input, df, part_col) or
                        _find_partial_match(norm_input, df, part_col) or
                        _find_reverse_partial_match(norm_input, df, part_col)
                    )
                    if exact_part_name:
                        comp_specs = fetch_func(exact_part_name, df)
                    else:
                        # Before fuzzy, try DigiKey — it may have the exact part
                        dk_specs = fetch_digikey_specs_from_csv(part_input, competitor_name, digikey_df)
                        if dk_specs:
                            return dk_specs
                        # DigiKey also didn't find it — fall back to fuzzy
                        exact_part_name = (
                            _find_fuzzy_match(norm_input, df, part_col, min_len=6) or
                            _find_fuzzy_match(norm_input, df, part_col, min_len=4)
                        )
                        if exact_part_name:
                            comp_specs = fetch_func(exact_part_name, df)
                break

        if not found_competitor:
            print("Competitor not in local database, falling back to DigiKey CSV...")
            return fetch_digikey_specs_from_csv(part_input, competitor_name, digikey_df)

    if comp_specs:
        print(f"Found match. The exact part name is '{exact_part_name}'. Fetching specs...")
        ppp_val = str(comp_specs.get("Power Dissipation (Pd)", "-")).strip()
        if ppp_val not in ("-", "", "None") and 'W' in ppp_val.upper() and 'A' not in ppp_val.upper():
            # Normalize '200W' -> '200 W'
            ppp_val = re.sub(r'(\d)(W)$', r'\1 \2', ppp_val, flags=re.IGNORECASE)
            comp_specs["Power Dissipation (Pd)"] = ppp_val
        elif ppp_val in ("-", "", "None"):
            dk_specs = fetch_digikey_specs_from_csv(part_input, competitor_name, digikey_df)
            if dk_specs and dk_specs.get("Power Dissipation (Pd)", "-") not in ("-", "", None):
                comp_specs["Power Dissipation (Pd)"] = dk_specs["Power Dissipation (Pd)"]
    else:
        print(f"Could not find '{part_input}' in local database. Falling back to DigiKey CSV...")
        comp_specs = fetch_digikey_specs_from_csv(part_input, competitor_name, digikey_df)

    return comp_specs


def find_ti_zener_alternatives(competitor_specs, ti_zener_specs_df):
    if ti_zener_specs_df is None or ti_zener_specs_df.empty:
        return []

    print("\n--- Starting TI Zener Diode Search ---")

    SCORE_PKG_MATCH = 4000
    SCORE_VZ_EXACT = 2000
    SCORE_VZ_CLOSE = 1000
    SCORE_TOLERANCE_MATCH = 500
    SCORE_POWER_MATCH = 300

    comp_pkg_alias = competitor_specs.get("Package", "")
    is_structured_pkg = isinstance(comp_pkg_alias, dict)
    valid_comp_pkgs_for_match = []
    if not is_structured_pkg:
        valid_comp_pkgs_for_match = [normalize_package(p) for p in str(comp_pkg_alias).split(',') if p.strip()]

    comp_vz_str = competitor_specs.get("Voltage - Reverse Standoff (Typ)", None)
    comp_vz = to_numeric_val(comp_vz_str, default_if_error=None)
    comp_tol_str = competitor_specs.get("Tolerance", "")
    comp_tol = float('inf')
    comp_tol_num_match = re.search(r'([\d.]+)', comp_tol_str)
    if comp_tol_num_match:
        matched_str = comp_tol_num_match.group(1)
        if matched_str != '.':
            try:
                comp_tol = float(matched_str)
            except ValueError:
                pass
    comp_pd_str = competitor_specs.get("Power Dissipation (Pd)", "")
    comp_pd = to_numeric_val(comp_pd_str, default_if_error=None)

    if comp_vz is None:
        print("Competitor Zener Voltage (Vz) is not available. Cannot find alternatives.")
        return []

    part_num_col = next((c for c in ti_zener_specs_df.columns if 'product or part number' in c.lower()), None)
    pkg_col = next((c for c in ti_zener_specs_df.columns if 'package name' in c.lower()), None)
    vz_col = next((c for c in ti_zener_specs_df.columns if 'vz (nom) (v)' in c.lower()), None)
    tol_col = next((c for c in ti_zener_specs_df.columns if 'tolerance' in c.lower()), None)
    pd_col = next((c for c in ti_zener_specs_df.columns if 'pd (max) (w)' in c.lower()), None)

    if not all([part_num_col, pkg_col, vz_col, tol_col, pd_col]):
        print("! Warning: One or more required columns not found in the TI Zener database.")
        return []

    _, package_matches_df, _ = find_package_matches(comp_pkg_alias, ti_zener_specs_df)
    if package_matches_df.empty:
        print("Warning: No TI Zener parts with a matching package found. Scoring on parameters only.")

    potential_alternatives = []
    target_df = ti_zener_specs_df.copy()
    target_df[vz_col] = pd.to_numeric(target_df[vz_col], errors='coerce')

    for index, ti_row in target_df.iterrows():
        score = 0
        ti_vz = ti_row.get(vz_col)
        if pd.isna(ti_vz) or not (comp_vz * 0.95 <= ti_vz <= comp_vz * 1.05):
            continue

        is_pkg_match = index in package_matches_df.index
        if is_pkg_match:
            score += SCORE_PKG_MATCH

        vz_diff_percent = abs(ti_vz - comp_vz) / comp_vz * 100
        if vz_diff_percent <= 1:
            score += SCORE_VZ_EXACT
        else:
            score += SCORE_VZ_CLOSE

        ti_tol_str = str(ti_row.get(tol_col, ''))
        ti_tol_num_match = re.search(r'([\d.]+)', ti_tol_str)
        if ti_tol_num_match:
            ti_tol = float(ti_tol_num_match.group(1))
            if ti_tol <= comp_tol:
                score += SCORE_TOLERANCE_MATCH

        if comp_pd is not None:
            ti_pd = pd.to_numeric(ti_row.get(pd_col), errors='coerce')
            if pd.notna(ti_pd) and (comp_pd * 0.90 <= ti_pd <= comp_pd * 1.10):
                score += SCORE_POWER_MATCH

        if score > 0:
            potential_alternatives.append({
                "part_number": ti_row[part_num_col],
                "score": score,
                "is_package_match": is_pkg_match,
                "vz": ti_vz,
                "tolerance": ti_row.get(tol_col, '-'),
                "pd": ti_row.get(pd_col, '-')
            })

    if not potential_alternatives:
        return []

    sorted_alternatives = sorted(potential_alternatives, key=lambda x: x["score"], reverse=True)
    print("Top potential Zener alternatives:")
    for i, alt in enumerate(sorted_alternatives[:5]):
        print(f"{i+1}. {alt['part_number']} - Score: {alt['score']}, PkgMatch: {alt['is_package_match']}, Vz: {alt['vz']}, Tol: {alt['tolerance']}, Pd: {alt['pd']}")
    return sorted_alternatives[:3]


def find_ti_alternatives(competitor_specs, ti_specs_df):
    if ti_specs_df is None or ti_specs_df.empty:
        return []
    ti_specs_df.columns = ti_specs_df.columns.str.strip()

    comp_grade = competitor_specs.get("Grade", "-").lower()
    comp_is_automotive = "automotive" in comp_grade or "aec-q" in comp_grade
    comp_pkg_alias = competitor_specs.get("Package", "")
    is_structured_pkg = isinstance(comp_pkg_alias, dict)
    valid_comp_pkgs_for_match = []
    if not is_structured_pkg:
        valid_comp_pkgs_for_match = [p.strip() for p in str(comp_pkg_alias).split(',') if p.strip()]
    comp_v_clamp_str = competitor_specs.get("Voltage - Clamping (Max) @ Ipp", "-")
    comp_v_clamp = to_numeric_val(comp_v_clamp_str if comp_v_clamp_str and comp_v_clamp_str != "-" else "inf")
    comp_cap_str = competitor_specs.get("Capacitance", "-")
    comp_cap = to_numeric_val(comp_cap_str if comp_cap_str and comp_cap_str != "-" else "inf")
    comp_vrw_str = competitor_specs.get("Voltage - Reverse Standoff (Typ)", "-")
    comp_vrw_numeric = to_numeric_val(comp_vrw_str, default_if_error=None)
    comp_direction = competitor_specs.get("Direction", "-").lower()
    comp_channels_str = competitor_specs.get("Channels", "1")
    comp_surge_str = competitor_specs.get("IEC 61000-4-5", "-")
    comp_surge = to_numeric_val(comp_surge_str, default_if_error=0)
    comp_esd_str = competitor_specs.get("IEC 61000-4-2", "-")
    comp_esd = to_numeric_val(comp_esd_str, default_if_error=0)
    comp_ppp_str = competitor_specs.get("Power Dissipation (Pd)", "-")
    comp_ppp = to_numeric_val(comp_ppp_str, default_if_error=0)

    pkg_display_for_log = comp_pkg_alias if is_structured_pkg else valid_comp_pkgs_for_match
    print(f"Grade: {'Automotive' if comp_is_automotive else 'Non-Automotive'}, Normalized Competitor Pkgs: {pkg_display_for_log}")
    base_ti_df_for_search = ti_specs_df.copy()

    package_matches_df = pd.DataFrame()
    vrwm_matches_df = pd.DataFrame()

    stage1_package_matches_df, final_package_matches_df, pin_matched_df = find_package_matches(comp_pkg_alias, base_ti_df_for_search)
    pin_match_indices = set(pin_matched_df.index)
    final_package_match_indices = set(final_package_matches_df.index)

    if comp_vrw_numeric is not None and 'Vrwm (V)' in base_ti_df_for_search.columns:
        vrwm_matched_indices = base_ti_df_for_search['Vrwm (V)'].apply(
            lambda ti_vrwm: is_similar_voltage(comp_vrw_str, str(ti_vrwm), tolerance_percent=20)
        )
        vrwm_matches_df = base_ti_df_for_search[vrwm_matched_indices].copy()
        print(f"Found {len(vrwm_matches_df)} TI parts with Vrwm within 30% tolerance.")

    all_candidates_df = pd.concat([stage1_package_matches_df, vrwm_matches_df]).drop_duplicates()
    target_df_for_params = all_candidates_df if not all_candidates_df.empty else base_ti_df_for_search
    if all_candidates_df.empty:
        print(f"FindTI: Stage 2 - No package or Vrwm matches. Checking all {len(target_df_for_params)} parts.")
    else:
        print(f"FindTI: Stage 2 - Parametrically checking {len(target_df_for_params)} package/Vrwm-matched parts.")

    potential_alternatives = []
    VCLAMP_TOLERANCE = 3.1
    CAP_TOLERANCE = 5.0

    for index, ti_row in target_df_for_params.iterrows():
        ti_part_num = safe_strip(ti_row.get("Product or Part number"))
        if ti_part_num.upper().startswith(('UC', 'SN')):
            continue
        ti_pkg_excel_original = safe_strip(ti_row.get("Package name"))
        ti_v_clamp_str = str(ti_row.get("Clamping voltage (V)", "inf"))
        ti_v_clamp = to_numeric_val(ti_v_clamp_str)
        vclamp_ok = ti_v_clamp <= (comp_v_clamp * VCLAMP_TOLERANCE) if comp_v_clamp != float('inf') else True

        ti_cap_str = str(ti_row.get("IO capacitance (typ) (pF)", "inf"))
        ti_cap = to_numeric_val(ti_cap_str)
        cap_ok = False
        if comp_cap == float('inf'):
            cap_ok = True
        elif comp_cap <= 0.5:
            cap_ok = (ti_cap <= 1.0)
        elif comp_cap < 1.0:
            cap_ok = (ti_cap <= 2.0)
        elif comp_cap < 10.0:
            cap_ok = (ti_cap <= 15.0)
        else:
            cap_ok = (ti_cap <= (comp_cap * CAP_TOLERANCE))

        ti_vrw_str_excel = str(ti_row.get("Vrwm (V)", "-"))
        vrw_filter_passed = False
        if comp_vrw_numeric is None:
            vrw_filter_passed = True
        else:
            vrw_filter_passed = is_similar_voltage(comp_vrw_str, ti_vrw_str_excel, tolerance_percent=20)

        ti_direction_raw = safe_strip(str(ti_row.get("Bi-/uni-directional", ""))).lower()
        ti_direction = "unidirectional" if "uni-directional" in ti_direction_raw else \
                       "bidirectional" if "bi-directional" in ti_direction_raw else "unknown"
        direction_match = (comp_direction in ["-", "unknown"] or ti_direction == "unknown" or comp_direction == ti_direction)

        if comp_direction == 'bidirectional':
            passes_direction_filter = (ti_direction == 'bidirectional')
        else:
            passes_direction_filter = True
        direction_match = (comp_direction in ["-", "unknown"] or ti_direction == "unknown" or comp_direction == ti_direction)

        num_channels_ti_raw = ti_row.get("Number of channels")
        channels_match = False
        try:
            ti_channels_val = int(float(safe_strip(str(num_channels_ti_raw))))
            comp_channels_val = int(float(comp_channels_str))
            channels_match = (ti_channels_val == comp_channels_val)
        except (ValueError, TypeError):
            channels_match = False

        ti_is_automotive = ti_part_num.endswith("-Q1")
        grade_match = (comp_is_automotive == ti_is_automotive)

        score = 0
        is_pkg_and_pin_match = index in pin_match_indices
        is_pkg_match_for_score = index in final_package_match_indices
        if is_pkg_and_pin_match:
            score += 4500   # package + pin count match
        elif is_pkg_match_for_score:
            score += 1500   # package match, wrong/unknown pin count
        if grade_match: score += 500
        if channels_match: score += 3000
        if direction_match: score += 100

        ti_surge_str = str(ti_row.get("IEC 61000-4-5 (A)", "0"))
        ti_surge = to_numeric_val(ti_surge_str, default_if_error=0)
        ti_esd_str = str(ti_row.get("IEC 61000-4-2 contact (k±V)", "0"))
        ti_esd = to_numeric_val(ti_esd_str, default_if_error=0)
        if ti_surge >= comp_surge and comp_surge > 0: score += 500
        if ti_esd >= comp_esd and comp_esd > 0: score += 250
        ti_ppp = to_numeric_val(str(ti_row.get("Peak pulse power (8/20 μs) (max) (W)", "0")), default_if_error=0)
        if ti_ppp >= comp_ppp and comp_ppp > 0: score += 500

        ti_vrw_numeric_current = to_numeric_val(ti_vrw_str_excel, default_if_error=None)
        exact_vrw_match = False
        if vrw_filter_passed and comp_vrw_numeric is not None and ti_vrw_numeric_current is not None:
            vrwm_diff_percent = abs(comp_vrw_numeric - ti_vrw_numeric_current) / comp_vrw_numeric * 100
            if vrwm_diff_percent <= 5:
                exact_vrw_match = True
                score += 2000
            elif vrwm_diff_percent <= 15:
                score += 1500
            else:
                score += 300

        if comp_cap != float('inf'):
            if ti_cap < comp_cap:
                max_cap_score = 2000 if comp_cap < 10 else 1000
                improvement_ratio = (comp_cap - ti_cap) / comp_cap
                score += improvement_ratio * max_cap_score
            elif cap_ok:
                score += 100

        meets_min_criteria_relaxed = True
        if not vclamp_ok: meets_min_criteria_relaxed = False
        if not cap_ok: meets_min_criteria_relaxed = False
        if not passes_direction_filter: meets_min_criteria_relaxed = False
        if comp_vrw_numeric is not None and not vrw_filter_passed: meets_min_criteria_relaxed = False
        if meets_min_criteria_relaxed and score > 0:
            potential_alternatives.append({
                "part_number": ti_part_num, "score": score, "v_clamp": ti_v_clamp,
                "capacitance": ti_cap, "vrw": ti_vrw_str_excel,
                "direction": ti_direction.capitalize(), "package": ti_pkg_excel_original,
                "is_package_match": is_pkg_and_pin_match, "pkg_only_match": is_pkg_match_for_score, "exact_vrw_match": exact_vrw_match,
                "is_automotive": ti_is_automotive
            })

    if potential_alternatives:
        def get_base_part_number(pn):
            return pn.replace("-Q1", "") if pn.upper().endswith("-Q1") else pn
        final_alternatives = []
        processed_base_names = set()
        sorted_for_dedup = sorted(potential_alternatives, key=lambda x: x['score'], reverse=True)
        for alt in sorted_for_dedup:
            base_name = get_base_part_number(alt["part_number"])
            if base_name in processed_base_names:
                continue
            is_wrong_grade = alt["is_automotive"] != comp_is_automotive
            if is_wrong_grade and not comp_is_automotive:
                commercial_part_name = base_name
                commercial_part_row = base_ti_df_for_search[base_ti_df_for_search['Product or Part number'].astype(str).str.strip().str.lower() == commercial_part_name.lower()]
                if not commercial_part_row.empty:
                    print(f"Grade Correction - Swapping automotive '{alt['part_number']}' for commercial '{commercial_part_name}'.")
                    correct_alt = {
                        "part_number": commercial_part_name,
                        "score": alt['score'] + 1,
                        "is_automotive": False,
                        "is_package_match": alt["is_package_match"],
                        "exact_vrw_match": alt.get("exact_vrw_match", False),
                        "v_clamp": to_numeric_val(str(commercial_part_row.iloc[0].get("Clamping voltage (V)", "inf"))),
                        "capacitance": to_numeric_val(str(commercial_part_row.iloc[0].get("IO capacitance (typ) (pF)", "inf"))),
                        "vrw": alt.get("vrw", "-"),
                        "direction": alt.get("direction", "-"),
                        "package": alt.get("package", "-")
                    }
                    final_alternatives.append(correct_alt)
                else:
                    final_alternatives.append(alt)
            else:
                final_alternatives.append(alt)
            processed_base_names.add(base_name)

        print(f"Deduplicated and grade-corrected {len(potential_alternatives)} candidates down to {len(final_alternatives)}.")
        potential_alternatives = final_alternatives

    if not potential_alternatives:
        return []

    sorted_alternatives = sorted(
        potential_alternatives,
        key=lambda alt: (
            alt["score"],
            alt["is_package_match"],
            alt["exact_vrw_match"],
            alt["capacitance"],
            -alt["v_clamp"],
        ),
        reverse=True
    )

    print(f"Found {len(sorted_alternatives)} potential TI alternatives after filtering and scoring.")
    print("Top potential alternatives before final selection:")
    for i, alt in enumerate(sorted_alternatives[:5]):
        print(f"{i+1}. {alt['part_number']} - Score: {alt['score']}, PkgMatch: {alt['is_package_match']}, Auto: {alt['is_automotive']}, Vcl: {alt['v_clamp']}, Cap: {alt['capacitance']}")

    return sorted_alternatives[:3]


def manage_data_files(force_reload=False):
    ti_file = "ti_specs.xlsx"
    ti_zener_file = "ti_zener_specs.xlsx"

    if force_reload:
        print("\n--- Force reloading TI data files ---")
        download_and_rename_ti_specs()
        download_and_rename_ti_zener_specs()
        print("\n--- TI data files reloaded. ---")
        return

    if not os.path.exists(ti_file):
        print(f"'{ti_file}' not found. Downloading...")
        download_and_rename_ti_specs()

    if not os.path.exists(ti_zener_file):
        print(f"'{ti_zener_file}' not found. Downloading...")
        download_and_rename_ti_zener_specs()


def _known_pin_count(norm_pkg):
    if norm_pkg.startswith("DFN1006"):   return 2
    if norm_pkg.startswith("DFN11103"):  return 3
    if norm_pkg.startswith("DFN2020"):   return 6
    if norm_pkg.startswith("DFN3030"):   return 8
    if norm_pkg.startswith("DSBGA"):     return 4
    if norm_pkg.startswith("SC703"):     return 3
    if norm_pkg.startswith("SC706"):     return 6
    if norm_pkg.startswith("SOD323"):    return 2
    if norm_pkg.startswith("SOD523"):    return 2
    if norm_pkg.startswith("SOT233"):    return 3
    if norm_pkg.startswith("SOT234"):    return 4
    if norm_pkg.startswith("SOT235"):    return 5
    if norm_pkg.startswith("SOT236"):    return 6
    if norm_pkg.startswith("WQFN"):      return 12
    if norm_pkg.startswith("USON"):      return 6
    return None


def _resolve_pin_count(target_norm_pkg, all_packages_str, all_pins_str, ti_gpn):
    packages = [normalize_package(p.strip()) for p in str(all_packages_str).split(",") if p.strip()]
    try:
        pins_sorted = sorted([int(p.strip()) for p in str(all_pins_str).split(",") if p.strip()])
    except (ValueError, TypeError):
        pins_sorted = []

    if not pins_sorted:
        return _known_pin_count(target_norm_pkg)

    # Single pin value means all packages share it
    if len(pins_sorted) == 1:
        return pins_sorted[0]

    if len(packages) == 1:
        return pins_sorted[0]

    if "ESD122" in ti_gpn.upper() and target_norm_pkg.startswith("DFN1006"):
        return 3

    # Try elimination: remove known pin counts for other packages from the sorted list
    known = {}
    for pkg in packages:
        if pkg == target_norm_pkg:
            continue
        pc = _known_pin_count(pkg)
        if pc is not None:
            known[pkg] = pc

    remaining_pins = list(pins_sorted)
    for pkg, pc in known.items():
        if pc in remaining_pins:
            remaining_pins.remove(pc)

    unknowns = [pkg for pkg in packages if pkg not in known]

    if len(unknowns) == 1 and unknowns[0] == target_norm_pkg and len(remaining_pins) == 1:
        return remaining_pins[0]

    # Fall back to fixed known count for target
    fixed = _known_pin_count(target_norm_pkg)
    if fixed is not None:
        return fixed

    if len(remaining_pins) == 1:
        return remaining_pins[0]

    return None


def _get_suffix(norm_pkg, pin_val, ti_gpn):
    if norm_pkg.startswith("DFN0603"):
        if pin_val == 2:   return "DPLR"
        if pin_val == 4:   return "DPWR"
    elif norm_pkg.startswith("DFN1006"):
        if "ESD122" in ti_gpn.upper(): return "DMXR"
        return "DPYR"
    elif norm_pkg.startswith("DFN11103"):
        return "DXAR"
    elif norm_pkg.startswith("DFN2020"):
        return "DRVR"
    elif norm_pkg.startswith("DFN2510"):
        if pin_val == 10:  return "DQAR"
        if pin_val == 6:   return "DRYR"
    elif norm_pkg.startswith("DFN3030"):
        return "DRBR"
    elif norm_pkg.startswith("DSBGA"):
        return "YZFR"
    elif norm_pkg.startswith("SC703") or norm_pkg.startswith("SC706"):
        return "DCKR"
    elif norm_pkg.startswith("SOD323"):
        return "DYFR"
    elif norm_pkg.startswith("SOD523"):
        return "DYAR"
    elif norm_pkg.startswith("SOT233"):
        return "DBZR"
    elif norm_pkg.startswith("SOT234"):
        return "DZDR"
    elif norm_pkg.startswith("SOT235") or norm_pkg.startswith("SOT236"):
        return "DBVR"
    elif re.match(r"SOT5\d3", norm_pkg):
        return "DRLR"
    elif re.match(r"SOT9\d3", norm_pkg):
        return "DRTR"
    elif norm_pkg.startswith("UQFN"):
        if pin_val == 10:  return "RSER"
        if pin_val == 14:  return "RVZR"
    elif norm_pkg.startswith("USON"):
        return "DRYR"
    elif norm_pkg.startswith("WQFN"):
        return "RSFR"
    elif norm_pkg.startswith("WSON"):
        if pin_val == 6:   return "VEBR"
        if pin_val == 15:  return "DSMR"
    return None


def _generate_ti_opn(ti_gpn, ti_package_str, ti_pin_str, competitor_package_alias, competitor_canonical_pkg=None):
    if not ti_gpn or ti_gpn == "-" or not ti_package_str or ti_package_str == "-":
        return ti_gpn

    # If no canonical pkg passed, try to derive one from the raw package string
    if not competitor_canonical_pkg and competitor_package_alias:
        pkg_str = competitor_package_alias if isinstance(competitor_package_alias, str) else ""
        for p in pkg_str.split(','):
            derived = _classify_digikey_package(normalize_package(p.strip()), "")
            if derived:
                competitor_canonical_pkg = derived
                break

    gpn_base = ti_gpn
    is_automotive = False
    if gpn_base.upper().endswith("-Q1"):
        gpn_base = gpn_base[:-3]
        is_automotive = True

    ti_packages_list = [p.strip() for p in str(ti_package_str).split(",") if p.strip()]
    try:
        ti_pins_list = [int(p.strip()) for p in str(ti_pin_str).split(",") if p.strip()]
    except (ValueError, TypeError):
        ti_pins_list = []

    # --- Canonical-code matching path (preferred) ---
    if competitor_canonical_pkg:
        for i, ti_pkg in enumerate(ti_packages_list):
            pin_for_pkg = None
            if len(ti_pins_list) == 1:
                pin_for_pkg = ti_pins_list[0]
            elif i < len(ti_pins_list):
                pin_for_pkg = ti_pins_list[i]
            else:
                pin_for_pkg = _resolve_pin_count(normalize_package(ti_pkg), ti_package_str, ti_pin_str, ti_gpn)

            ti_canonical = _classify_ti_package(ti_pkg, pin_for_pkg)
            if ti_canonical and ti_canonical == competitor_canonical_pkg:
                suffix = CANONICAL_SUFFIX_MAP.get(ti_canonical)
                if suffix:
                    final_opn = gpn_base + suffix
                    if is_automotive:
                        final_opn += "Q1"
                    return final_opn

    # --- Legacy fallback path ---
    comp_pkg_set = set()
    if isinstance(competitor_package_alias, dict):
        comp_pkg_set = normalize_structured_package(competitor_package_alias)
    elif isinstance(competitor_package_alias, str) and competitor_package_alias:
        comp_pkg_set = {normalize_package(p) for p in competitor_package_alias.split(",") if p.strip()}

    matched_pkg = None
    for ti_pkg in ti_packages_list:
        if normalize_package(ti_pkg) in comp_pkg_set:
            matched_pkg = ti_pkg
            break

    if not matched_pkg:
        matched_pkg = ti_packages_list[0] if ti_packages_list else None

    if not matched_pkg:
        return ti_gpn

    norm_pkg = normalize_package(matched_pkg)
    pin_val = _resolve_pin_count(norm_pkg, ti_package_str, ti_pin_str, ti_gpn)
    suffix = _get_suffix(norm_pkg, pin_val, ti_gpn)

    if suffix:
        final_opn = gpn_base + suffix
        if is_automotive:
            final_opn += "Q1"
        return final_opn

    return ti_gpn

def _canonical_to_ti_pkg(canonical_code):
    """
    Derives a TI-compatible package string from a canonical code for use in
    find_package_matches. Strips the pin count suffix.
    e.g. 'DFN2510_10' -> 'DFN2510', 'SOT886_6' -> 'SOT886', 'SOT233_3' -> 'SOT-23-3'
    """
    if not canonical_code:
        return None
    family = canonical_code.rsplit('_', 1)[0]
    # Map back to TI's exact package name conventions where they differ
    mapping = {
        "SOT233": "SOT-23-3",
        "SOT234": "SOT-23-4",
        "SOT235": "SOT-23-5",
        "SOT236": "SOT-23-6",
        "SC703":  "SC70-3",
        "SC706":  "SC70-6",
        "SOD323": "SOD323",
        "SOD523": "SOD523",
        "SOT886": "SOT886",
        "SOT523": "SOT-5X3",
        "SOT553": "SOT-5X3",
        "SOT9X3": "SOT-9X3",
    }
    return mapping.get(family, family)




def _canonical_to_ti_pkg_with_pins(canonical_code):
    """
    Like _canonical_to_ti_pkg but appends '-{pins}' so find_package_matches
    can extract the pin count for unstructured stage 2 filtering.
    e.g. 'DFN2510_10' -> 'DFN2510-10'
    (no suffix for packages where pin count is fixed/unambiguous)
    """
    if not canonical_code:
        return None
    parts = canonical_code.rsplit('_', 1)
    base = _canonical_to_ti_pkg(canonical_code)
    if len(parts) == 2:
        pin_count = parts[1]
        variable_pin_families = {"DFN2510", "DFN0603", "DFN1006", "UQFN", "WSON", "X2SON"}
        family = parts[0]
        if family in variable_pin_families:
            return f"{base}-{pin_count}"
    return base


def _check_capacitance_rules(comp_cap, ti_cap, rule_set):
    if comp_cap is None or ti_cap is None or comp_cap == float('inf') or ti_cap == float('inf'):
        return True
    if rule_set == 'S':
        if comp_cap <= 0.5: return ti_cap <= 0.5
        if comp_cap <= 1.0: return ti_cap <= 2.0
        if comp_cap <= 2.0: return ti_cap <= 5.0
        if comp_cap <= 10.0: return ti_cap <= 15.0
        return True
    elif rule_set == 'Q':
        if comp_cap <= 0.5: return ti_cap <= 1.0
        if comp_cap <= 1.0: return ti_cap <= 5.0
        if comp_cap <= 2.0: return ti_cap <= 10.0
        if comp_cap <= 10.0: return ti_cap <= 20.0
        return True
    return False


def _get_replacement_type(comp_specs, ti_alt_specs, is_zener, is_package_match):
    if not is_package_match:
        return 'P'

    if is_zener:
        comp_vz_str = comp_specs.get("Voltage - Reverse Standoff (Typ)")
        ti_vz_str = ti_alt_specs.get("Voltage - Reverse Standoff (Typ)")
        comp_tol_str = comp_specs.get("Tolerance", "inf")
        ti_tol_str = ti_alt_specs.get("Tolerance", "inf")
        comp_tol = to_numeric_val(comp_tol_str, default_if_error=float('inf'))
        ti_tol = to_numeric_val(ti_tol_str, default_if_error=float('inf'))

        if is_similar_voltage(comp_vz_str, ti_vz_str, 1) and (ti_tol <= comp_tol):
            return 'S'
        if is_similar_voltage(comp_vz_str, ti_vz_str, 5):
            return 'Q'
        return 'P'

    else:
        comp_vrw = comp_specs.get("Voltage - Reverse Standoff (Typ)")
        ti_vrw = ti_alt_specs.get("Voltage - Reverse Standoff (Typ)")
        comp_dir = comp_specs.get("Direction", "").lower()
        ti_dir = ti_alt_specs.get("Direction", "").lower()
        comp_cap = to_numeric_val(comp_specs.get("Capacitance"), float('inf'))
        ti_cap = to_numeric_val(ti_alt_specs.get("Capacitance"), float('inf'))
        comp_esd = to_numeric_val(comp_specs.get("IEC 61000-4-2"), 0)
        ti_esd = to_numeric_val(ti_alt_specs.get("IEC 61000-4-2"), 0)
        comp_surge = to_numeric_val(comp_specs.get("IEC 61000-4-5"), 0)
        ti_surge = to_numeric_val(ti_alt_specs.get("IEC 61000-4-5"), 0)

        s_vrw_ok = is_similar_voltage(comp_vrw, ti_vrw, 10)
        s_dir_ok = (comp_dir == ti_dir)
        s_cap_ok = _check_capacitance_rules(comp_cap, ti_cap, 'S')
        s_esd_ok = is_similar_voltage(comp_esd, ti_esd, 10) if comp_esd > 0 else True
        s_surge_ok = (ti_surge >= (comp_surge * 0.20)) if comp_surge > 0 else True
        if s_vrw_ok and s_dir_ok and s_cap_ok and s_esd_ok and s_surge_ok:
            return 'S'

        q_vrw_ok = is_similar_voltage(comp_vrw, ti_vrw, 10)
        q_cap_ok = _check_capacitance_rules(comp_cap, ti_cap, 'Q')
        if q_vrw_ok and q_cap_ok:
            return 'Q'

        return 'P'


def main():
    start_time = time.time()

    reload_input = input("Type 'reload' to refresh TI data files, or press Enter to continue: ").strip().lower()
    force_reload = (reload_input == 'reload')
    manage_data_files(force_reload=force_reload)

    print("Loading databases (from cache if available)...")

    ti_specs_df = load_and_cache("ti_specs.pkl", TI_SPECS_DATABASE_FILE, force_reload,
        lambda: load_excel_data(TI_SPECS_DATABASE_FILE, header_row=10))

    ti_zener_specs_df = load_and_cache("ti_zener_specs.pkl", TI_ZENER_SPECS_DATABASE_FILE, force_reload,
        lambda: load_excel_data(TI_ZENER_SPECS_DATABASE_FILE, header_row=10))

    aos_specs_df = load_and_cache("aos_specs.pkl", AOS_SPECS_DATABASE_FILE, force_reload,
        lambda: load_excel_data(AOS_SPECS_DATABASE_FILE, header_row=6))

    diodes_dl_df = load_and_cache("diodes_dl.pkl", DIODES_DL_DATABASE_FILE, force_reload,
        lambda: load_excel_data(DIODES_DL_DATABASE_FILE, header_row=0))
    diodes_pl_df = load_and_cache("diodes_pl.pkl", DIODES_PL_DATABASE_FILE, force_reload,
        lambda: load_excel_data(DIODES_PL_DATABASE_FILE, header_row=0))
    diodes_ts_df = load_and_cache("diodes_ts.pkl", DIODES_TS_DATABASE_FILE, force_reload,
        lambda: load_excel_data(DIODES_TS_DATABASE_FILE, header_row=0))
    diodes_usbc_df = load_and_cache("diodes_usbc.pkl", DIODES_USBC_DATABASE_FILE, force_reload,
        lambda: load_excel_data(DIODES_USBC_DATABASE_FILE, header_row=0))

    nexperia_zener_df = load_and_cache("nexperia_zener.pkl", NEXPERIA_ZENER_DATABASE_FILE, force_reload,
        lambda: load_excel_data(NEXPERIA_ZENER_DATABASE_FILE, header_row=9))
    nexperia_auto_zener_df = load_and_cache("nexperia_auto_zener.pkl", NEXPERIA_AUTO_ZENER_DATABASE_FILE, force_reload,
        lambda: load_excel_data(NEXPERIA_AUTO_ZENER_DATABASE_FILE, header_row=9))
    nexperia_esd_df = load_and_cache("nexperia_esd.pkl", NEXPERIA_ESD_DATABASE_FILE, force_reload,
        lambda: load_excel_data(NEXPERIA_ESD_DATABASE_FILE, header_row=9))
    nexperia_emi_df = load_and_cache("nexperia_emi.pkl", NEXPERIA_EMI_DATABASE_FILE, force_reload,
        lambda: load_excel_data(NEXPERIA_EMI_DATABASE_FILE, header_row=9))
    nexperia_tvs_df = load_and_cache("nexperia_tvs.pkl", NEXPERIA_TVS_DATABASE_FILE, force_reload,
        lambda: load_excel_data(NEXPERIA_TVS_DATABASE_FILE, header_row=9))
    nexperia_auto_protection_df = load_and_cache("nexperia_auto_protection.pkl", NEXPERIA_AUTO_PROTECTION_DATABASE_FILE, force_reload,
        lambda: load_excel_data(NEXPERIA_AUTO_PROTECTION_DATABASE_FILE, header_row=9))

    nexperia_more_df = load_and_cache("nexperia_more.pkl", NEXPERIA_MORE, force_reload,
        lambda: pd.read_excel(NEXPERIA_MORE))
    if nexperia_more_df.empty and os.path.exists(NEXPERIA_MORE_CSV):
        print(f"First-time setup: Found '{NEXPERIA_MORE_CSV}'. Converting...")
        if _unblock_csv_file(NEXPERIA_MORE_CSV):
            temp_df = pd.read_csv(NEXPERIA_MORE_CSV, on_bad_lines='skip', encoding='latin-1')
            temp_df.to_excel(NEXPERIA_MORE, index=False)
            os.remove(NEXPERIA_MORE_CSV)
            nexperia_more_df = load_and_cache("nexperia_more.pkl", NEXPERIA_MORE, True,
                lambda: pd.read_excel(NEXPERIA_MORE))

    littelfuse_auto_tvs_array_df = load_and_cache("littelfuse_auto_tvs_array.pkl", LITTELFUSE_AUTO_TVS_ARRAY_FILE, force_reload,
        lambda: load_excel_data(LITTELFUSE_AUTO_TVS_ARRAY_FILE))
    littelfuse_tvs_array_df = load_and_cache("littelfuse_tvs_array.pkl", LITTELFUSE_TVS_ARRAY_FILE, force_reload,
        lambda: load_excel_data(LITTELFUSE_TVS_ARRAY_FILE))
    littelfuse_auto_tvs_df = load_and_cache("littelfuse_auto_tvs.pkl", LITTELFUSE_AUTO_TVS_FILE, force_reload,
        lambda: load_excel_data(LITTELFUSE_AUTO_TVS_FILE))
    littelfuse_tvs_df = load_and_cache("littelfuse_tvs.pkl", LITTELFUSE_TVS_FILE, force_reload,
        lambda: load_excel_data(LITTELFUSE_TVS_FILE))
    littelfuse_pesd_df = load_and_cache("littelfuse_pesd.pkl", LITTELFUSE_PESD_FILE, force_reload,
        lambda: load_excel_data(LITTELFUSE_PESD_FILE))

    jiangsu_esd_df = load_and_cache("jiangsu_esd.pkl", JIANGSU_ESD_FILE, force_reload,
        lambda: load_excel_data(JIANGSU_ESD_FILE))
    jiangsu_tvs_df = load_and_cache("jiangsu_tvs.pkl", JIANGSU_TVS_FILE, force_reload,
        lambda: load_excel_data(JIANGSU_TVS_FILE))
    jiangsu_zener_df = load_and_cache("jiangsu_zener.pkl", JIANGSU_ZENER_FILE, force_reload,
        lambda: load_excel_data(JIANGSU_ZENER_FILE))

    semtech_specs_df = load_and_cache("semtech_specs.pkl", SEMTECH_SPECS_FILE, force_reload,
        lambda: load_excel_data(SEMTECH_SPECS_FILE))

    onsemi_tvs_df = load_and_cache("onsemi_specs_tvs.pkl", ONSEMI_SPECS_TVS, force_reload,
        lambda: pd.read_excel(ONSEMI_SPECS_TVS))
    if onsemi_tvs_df.empty and os.path.exists(ONSEMI_SPECS_CSV_TVS):
        print(f"First-time setup: Found '{ONSEMI_SPECS_CSV_TVS}'. Converting...")
        if _unblock_csv_file(ONSEMI_SPECS_CSV_TVS):
            temp_df = pd.read_csv(ONSEMI_SPECS_CSV_TVS, on_bad_lines='skip', encoding='latin-1')
            temp_df.to_excel(ONSEMI_SPECS_TVS, index=False)
            os.remove(ONSEMI_SPECS_CSV_TVS)
            onsemi_tvs_df = load_and_cache("onsemi_specs_tvs.pkl", ONSEMI_SPECS_TVS, True,
                lambda: pd.read_excel(ONSEMI_SPECS_TVS))

    vishay_df = load_and_cache("vishay_specs_protection.pkl", VISHAY_SPECS_DATABASE_FILE, force_reload,
        lambda: pd.read_excel(VISHAY_SPECS_DATABASE_FILE))
    if vishay_df.empty and os.path.exists(VISHAY_SPECS_CSV_FILE):
        print(f"First-time setup: Found '{VISHAY_SPECS_CSV_FILE}'. Converting...")
        if _unblock_csv_file(VISHAY_SPECS_CSV_FILE):
            temp_df = pd.read_csv(VISHAY_SPECS_CSV_FILE, on_bad_lines='skip', encoding='latin-1')
            temp_df.to_excel(VISHAY_SPECS_DATABASE_FILE, index=False)
            os.remove(VISHAY_SPECS_CSV_FILE)
            vishay_df = load_and_cache("vishay_specs_protection.pkl", VISHAY_SPECS_DATABASE_FILE, True,
                lambda: pd.read_excel(VISHAY_SPECS_DATABASE_FILE))

    stm_specs_df = load_and_cache("stm_specs.pkl", STM_SPECS_DATABASE_FILE, force_reload,
        lambda: pd.read_excel(STM_SPECS_DATABASE_FILE))
    if stm_specs_df.empty and os.path.exists(STM_SPECS_CSV_FILE):
        print(f"First-time setup: Found '{STM_SPECS_CSV_FILE}'. Converting...")
        if _unblock_csv_file(STM_SPECS_CSV_FILE):
            temp_df = pd.read_csv(STM_SPECS_CSV_FILE, on_bad_lines='skip', encoding='latin-1')
            temp_df.to_excel(STM_SPECS_DATABASE_FILE, index=False)
            os.remove(STM_SPECS_CSV_FILE)
            stm_specs_df = load_and_cache("stm_specs.pkl", STM_SPECS_DATABASE_FILE, True,
                lambda: pd.read_excel(STM_SPECS_DATABASE_FILE))

    panjit_specs_df = load_and_cache("panjit_specs.pkl", PANJIT_SPECS_DATABASE_FILE, force_reload,
        lambda: pd.read_excel(PANJIT_SPECS_DATABASE_FILE))
    if panjit_specs_df.empty and os.path.exists(PANJIT_SPECS_CSV_FILE):
        print(f"First-time setup: Found '{PANJIT_SPECS_CSV_FILE}'. Converting...")
        if _unblock_csv_file(PANJIT_SPECS_CSV_FILE):
            temp_df = pd.read_csv(PANJIT_SPECS_CSV_FILE, on_bad_lines='skip', encoding='latin-1')
            temp_df.to_excel(PANJIT_SPECS_DATABASE_FILE, index=False)
            os.remove(PANJIT_SPECS_CSV_FILE)
            panjit_specs_df = load_and_cache("panjit_specs.pkl", PANJIT_SPECS_DATABASE_FILE, True,
                lambda: pd.read_excel(PANJIT_SPECS_DATABASE_FILE))

    amazing_specs_df = load_and_cache("amazing_specs.pkl", AMAZING_SPECS_DATABASE_FILE, force_reload,
        lambda: pd.read_excel(AMAZING_SPECS_DATABASE_FILE, sheet_name='Amazing-Parametric', header=0, engine='openpyxl'))

    # Load DigiKey competitor specs CSV (fallback)
    digikey_df = pd.DataFrame()
    if os.path.exists(DIGIKEY_SPECS_CSV):
        try:
            digikey_df = pd.read_csv(DIGIKEY_SPECS_CSV, on_bad_lines='skip', encoding='latin-1')
            print(f"Loaded {len(digikey_df)} rows from '{DIGIKEY_SPECS_CSV}'.")
        except Exception as e:
            print(f"Warning: Could not load '{DIGIKEY_SPECS_CSV}'. Error: {e}")
    else:
        print(f"Warning: '{DIGIKEY_SPECS_CSV}' not found. DigiKey fallback unavailable.")

    # Pack competitor DFs for dispatcher
    all_dfs = (
        aos_specs_df, amazing_specs_df,
        {"Data Line": diodes_dl_df, "Power Line": diodes_pl_df, "TSPDs": diodes_ts_df, "USB-C": diodes_usbc_df},
        {"Zener": nexperia_zener_df, "Auto_Zener": nexperia_auto_zener_df, "ESD": nexperia_esd_df, "EMI": nexperia_emi_df, "TVS": nexperia_tvs_df, "Auto_PD": nexperia_auto_protection_df, "More TVS": nexperia_more_df},
        {"PESD": littelfuse_pesd_df, "TVS": littelfuse_tvs_df, "TVS_Array": littelfuse_tvs_array_df, "Auto_TVS": littelfuse_auto_tvs_df, "Auto_TVS_Array": littelfuse_auto_tvs_array_df},
        {"ESD": jiangsu_esd_df, "TVS": jiangsu_tvs_df, "Zener": jiangsu_zener_df},
        vishay_df, semtech_specs_df, stm_specs_df, panjit_specs_df, onsemi_tvs_df,
        ti_specs_df, ti_zener_specs_df
    )

    print("All databases loaded and ready.")

    mode = input("Select mode: (1) Single Part Cross, (2) Batch File Cross, (3) Specs: ").strip()

    if mode == '3':
        comp_part_input = input("Enter the competitor's diode part number: ").strip()
        if not comp_part_input:
            print("No competitor part number entered.")
            return

        competitor_name = input("Enter the competitor's name: ").strip().lower()
        comp_specs = get_competitor_specs_leniently(comp_part_input, competitor_name, all_dfs, digikey_df)

        if not comp_specs:
            print(f"\nCould not find specifications for '{comp_part_input}'.")
            return

        is_zener_part = "zener" in comp_specs.get("Source File", "").lower()

        param_keys_tvs = [
            ("Device Name", "Device Name"), ("Direction", "Direction"), ("Package", "Package"),
            ("Voltage - Reverse Standoff (Typ)", "Vrw Standoff (Typ)"),
            ("Voltage - Clamping (Max) @ Ipp", "Vcl @ Ipp (Max)"),
            ("Capacitance", "Capacitance"), ("Channels", "Channels"),
            ("IEC 61000-4-5", "IEC 61000-4-5 (Surge)"), ("IEC 61000-4-2", "IEC 61000-4-2 (ESD)"),
            ("Power Dissipation (Pd)", "Peak Pulse Power"),
            ("Price ($/ku)", "Price ($/ku)")
        ]
        param_keys_zener = [
            ("Device Name", "Device Name"), ("Package", "Package"),
            ("Voltage - Reverse Standoff (Typ)", "Zener Voltage (Vz)"),
            ("Tolerance", "Tolerance"), ("Power Dissipation (Pd)", "Power (Pd)"),
            ("Price ($/ku)", "Price ($/ku)")
        ]

        display_keys = param_keys_zener if is_zener_part else param_keys_tvs
        table_data = [[display_name, comp_specs.get(key, "-")] for key, display_name in display_keys]

        print(f"\n--- Specifications for {comp_specs.get('Device Name', comp_part_input).upper()} ---")
        print(tabulate(table_data, headers=["Parameter", "Value"], tablefmt="grid"))

        end_time = time.time()
        print(f"\nTotal execution time: {end_time - start_time:.2f} seconds")
        return

    if mode == '2':
        is_gui_mode = "RUNNING_IN_GUI" in os.environ

        if is_gui_mode:
            print("Backend: GUI mode detected, reading file paths from input stream.")
            input_excel_path = input().strip()
            output_excel_path = input().strip()
        else:
            print("Backend: Command-line mode, opening file dialogs.")
            root = Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            input_excel_path = filedialog.askopenfilename(
                title="Select the Competitor Parts Excel File",
                filetypes=[("Excel Files", "*.xlsx *.xls")]
            )
            if not input_excel_path:
                print("No input file selected. Aborting.")
                return
            output_excel_path = filedialog.asksaveasfilename(
                title="Save Output As...",
                filetypes=[("Excel Files", "*.xlsx")],
                defaultextension=".xlsx",
                initialfile="ti_cross_results.xlsx"
            )
            if not output_excel_path:
                print("No output file location selected. Aborting.")
                return

        try:
            batch_df = pd.read_excel(input_excel_path)
            if "Competitor Parts" not in batch_df.columns or "Competitor Name" not in batch_df.columns:
                print(f"ERROR: Input file must have 'Competitor Parts' AND 'Competitor Name' columns.")
                return
        except FileNotFoundError:
            print(f"ERROR: Input file not found at '{input_excel_path}'")
            return
        except Exception as e:
            print(f"ERROR: Could not read the Excel file. {e}")
            return

        all_alternatives_padded = []

        try:
            from tqdm import tqdm
            iterator = tqdm(batch_df.iterrows(), desc="Processing parts", unit="part", file=sys.stdout, total=len(batch_df))
        except ImportError:
            print("Processing parts... (for a progress bar, run: pip install tqdm)")
            iterator = batch_df.iterrows()

        for index, row in iterator:
            part_str = str(row["Competitor Parts"]).strip()
            competitor_name_batch = str(row["Competitor Name"]).strip().lower()

            if not part_str or pd.isna(part_str) or part_str.lower() == 'nan' or not competitor_name_batch:
                all_alternatives_padded.append(["-", "-", "-"])
                continue

            comp_specs = get_competitor_specs_leniently(part_str, competitor_name_batch, all_dfs, digikey_df)

            if not comp_specs:
                all_alternatives_padded.append(["Specs Not Found", "-", "-"])
                continue

            is_zener_part_batch = "zener" in comp_specs.get("Source File", "").lower()
            original_display_package = comp_specs.get("Package", "-")

            if is_zener_part_batch:
                comp_specs["Package"] = _canonical_to_ti_pkg_with_pins(comp_specs.get("Canonical Package") or _classify_digikey_package(original_display_package, "")) or normalize_package(original_display_package)
                ti_alternatives_list = find_ti_zener_alternatives(comp_specs, ti_zener_specs_df)
            else:
                comp_specs["Package"] = _canonical_to_ti_pkg_with_pins(comp_specs.get("Canonical Package") or _classify_digikey_package(original_display_package, "")) or normalize_package(original_display_package)
                ti_alternatives_list = find_ti_alternatives(comp_specs, ti_specs_df)

            generated_opns = []
            if ti_alternatives_list:
                for alt_dict in ti_alternatives_list:
                    gpn = alt_dict['part_number']
                    if is_zener_part_batch:
                        ti_alt_specs = fetch_ti_zener_specs_from_excel(gpn, ti_zener_specs_df, [])
                    else:
                        ti_alt_specs = fetch_ti_specs_from_excel(gpn, ti_specs_df, [])

                    if ti_alt_specs:
                        ti_package = ti_alt_specs.get("Package", "-")
                        competitor_pkg_alias = comp_specs.get("Package", "")
                        src_df = ti_zener_specs_df if is_zener_part_batch else ti_specs_df
                        ti_pin_str = "-"
                        if src_df is not None and not src_df.empty:
                            pn_col = next((c for c in src_df.columns if "product or part number" in c.lower()), None)
                            pin_col = next((c for c in src_df.columns if "pin count" in c.lower()), None)
                            if pn_col and pin_col:
                                match = src_df[src_df[pn_col].astype(str).str.strip() == gpn.strip()]
                                if not match.empty:
                                    ti_pin_str = str(match.iloc[0][pin_col])
                        opn = _generate_ti_opn(gpn, ti_package, ti_pin_str, competitor_pkg_alias, comp_specs.get("Canonical Package"))
                        generated_opns.append(opn)
                    else:
                        generated_opns.append(gpn)

            padded_list = (generated_opns + ["-", "-", "-"])[:3]
            all_alternatives_padded.append(padded_list)

        batch_df["TI Alternate 1"] = [alt[0] for alt in all_alternatives_padded]
        batch_df["TI Alternate 2"] = [alt[1] for alt in all_alternatives_padded]
        batch_df["TI Alternate 3"] = [alt[2] for alt in all_alternatives_padded]

        try:
            with pd.ExcelWriter(output_excel_path, engine='openpyxl') as writer:
                batch_df.to_excel(writer, index=False, sheet_name='Results')
                worksheet = writer.sheets['Results']
                for column_cells in worksheet.columns:
                    length = max(len(str(cell.value)) for cell in column_cells)
                    column_letter = column_cells[0].column_letter
                    worksheet.column_dimensions[column_letter].width = length + 2
            print(f"\nSuccessfully processed batch file. Output saved to '{output_excel_path}'")
        except Exception as e:
            print(f"\nERROR: Could not save the output Excel file. {e}")

        end_time = time.time()
        print(f"\nTotal execution time: {end_time - start_time:.2f} seconds")
        return

    elif mode == '4':
        is_gui_mode = "RUNNING_IN_GUI" in os.environ

        if is_gui_mode:
            print("Backend: GUI mode detected, reading file paths from input stream.")
            input_excel_path = input().strip()
            output_excel_path = input().strip()
        else:
            print("Backend: Command-line mode, opening file dialogs.")
            root = Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            input_excel_path = filedialog.askopenfilename(
                title="Select the Competitor Parts Excel File",
                filetypes=[("Excel Files", "*.xlsx *.xls")]
            )
            if not input_excel_path:
                print("No input file selected. Aborting.")
                return
            output_excel_path = filedialog.asksaveasfilename(
                title="Save CrossRef Output As...",
                filetypes=[("Excel Files", "*.xlsx")],
                defaultextension=".xlsx",
                initialfile="ti_crossref_results.xlsx"
            )
            if not output_excel_path:
                print("No output file location selected. Aborting.")
                return

        try:
            batch_df = pd.read_excel(input_excel_path)
            if "Competitor Parts" not in batch_df.columns or "Competitor Name" not in batch_df.columns:
                print(f"ERROR: Input file must have 'Competitor Parts' AND 'Competitor Name' columns.")
                return
        except FileNotFoundError:
            print(f"ERROR: Input file not found at '{input_excel_path}'")
            return
        except Exception as e:
            print(f"ERROR: Could not read the Excel file. {e}")
            return

        crossref_results = []

        try:
            from tqdm import tqdm
            iterator = tqdm(batch_df.iterrows(), desc="Processing parts for CrossRef", unit="part", file=sys.stdout, total=len(batch_df))
        except ImportError:
            print("Processing parts for CrossRef... (for a progress bar, run: pip install tqdm)")
            iterator = batch_df.iterrows()

        for index, row in iterator:
            part_str = str(row["Competitor Parts"]).strip()
            competitor_name = str(row["Competitor Name"]).strip().lower()

            ti_opn = "-"
            top_ti_alt_gpn = "-"
            replacement_type = "P"
            is_competitor_zener = False

            if not part_str or pd.isna(part_str) or part_str.lower() == 'nan' or not competitor_name:
                pass
            else:
                comp_specs = get_competitor_specs_leniently(part_str, competitor_name, all_dfs, digikey_df)

                if comp_specs:
                    is_competitor_zener = "zener" in comp_specs.get("Source File", "").lower()
                    original_display_package = comp_specs.get("Package", "-")

                    ti_alternatives_list = []
                    if is_competitor_zener:
                        comp_specs["Package"] = _canonical_to_ti_pkg_with_pins(comp_specs.get("Canonical Package") or _classify_digikey_package(original_display_package, "")) or normalize_package(original_display_package)
                        ti_alternatives_list = find_ti_zener_alternatives(comp_specs, ti_zener_specs_df)
                    else:
                        comp_specs["Package"] = _canonical_to_ti_pkg_with_pins(comp_specs.get("Canonical Package") or _classify_digikey_package(original_display_package, "")) or normalize_package(original_display_package)
                        ti_alternatives_list = find_ti_alternatives(comp_specs, ti_specs_df)

                    if ti_alternatives_list:
                        top_alt_dict = ti_alternatives_list[0]
                        top_ti_alt_gpn = top_alt_dict['part_number']
                        is_pkg_match = top_alt_dict.get('is_package_match', False)

                        if is_competitor_zener:
                            ti_alt_full_specs = fetch_ti_zener_specs_from_excel(top_ti_alt_gpn, ti_zener_specs_df, [])
                        else:
                            ti_alt_full_specs = fetch_ti_specs_from_excel(top_ti_alt_gpn, ti_specs_df, [])

                        if ti_alt_full_specs:
                            replacement_type = _get_replacement_type(comp_specs, ti_alt_full_specs, is_competitor_zener, is_pkg_match)
                            ti_package = ti_alt_full_specs.get("Package", "-")
                            competitor_pkg_alias = comp_specs.get("Package", "")
                            src_df = ti_zener_specs_df if is_competitor_zener else ti_specs_df
                            ti_pin_str = "-"
                            if src_df is not None and not src_df.empty:
                                pn_col = next((c for c in src_df.columns if "product or part number" in c.lower()), None)
                                pin_col = next((c for c in src_df.columns if "pin count" in c.lower()), None)
                                if pn_col and pin_col:
                                    match = src_df[src_df[pn_col].astype(str).str.strip() == top_ti_alt_gpn.strip()]
                                    if not match.empty:
                                        ti_pin_str = str(match.iloc[0][pin_col])
                            ti_opn = _generate_ti_opn(top_ti_alt_gpn, ti_package, ti_pin_str, competitor_pkg_alias, comp_specs.get("Canonical Package"))
                        else:
                            ti_opn = top_ti_alt_gpn

            crossref_results.append({
                "COMPETITOR_NAME": competitor_name.upper(),
                "COMP_GENERIC_PART_NUMBER": part_str,
                "COMP_ORDERABLE_PART_NUMBER": part_str,
                "TI_GPN": top_ti_alt_gpn,
                "GPN_REPLACEMENT_TYPE": replacement_type,
                "TI_OPN": ti_opn,
                "OPN_REPLACEMENT_TYPE": "P"
            })

        output_df = pd.DataFrame(crossref_results)

        try:
            with pd.ExcelWriter(output_excel_path, engine='openpyxl') as writer:
                output_df.to_excel(writer, index=False, sheet_name='CrossRef_Results')
                worksheet = writer.sheets['CrossRef_Results']
                for column_cells in worksheet.columns:
                    length = max(len(str(cell.value)) for cell in column_cells)
                    column_letter = column_cells[0].column_letter
                    worksheet.column_dimensions[column_letter].width = length + 2
            print(f"\nSuccessfully processed batch file. Output saved to '{output_excel_path}'")
        except Exception as e:
            print(f"\nERROR: Could not save the output Excel file. {e}")

        end_time = time.time()
        print(f"\nTotal execution time: {end_time - start_time:.2f} seconds")
        return

    if mode == '1':
        comp_part_input = input("Enter the competitor's diode part number: ").strip()
    else:
        print("Invalid mode selected. Please enter '1', '2', '3', or '4'.")
        return

    if not comp_part_input:
        print("No competitor part number entered.")
        return

    competitor_name = input("Enter the competitor's name (for display only): ").strip().lower()
    comp_specs = get_competitor_specs_leniently(comp_part_input, competitor_name, all_dfs, digikey_df)

    if not comp_specs:
        print(f"\nCould not find specifications for '{comp_part_input}'. Aborting.")
        return

    if "automotive" in comp_specs.get("Grade", "").lower():
        print("--> Competitor part identified as Automotive Grade.")
    else:
        print("--> Competitor part identified as Commercial/Standard Grade.")

    is_zener_part = "zener" in comp_specs.get("Source File", "").lower()

    if is_zener_part:
        print("\nCompetitor identified as a Zener Diode. Starting Zener cross-reference...")
        param_keys_display_order = [
            ("Device Name", "Device Name"),
            ("Package", "Package"),
            ("Voltage - Reverse Standoff (Typ)", "Zener Voltage (Vz)"),
            ("Tolerance", "Tolerance"),
            ("Power Dissipation (Pd)", "Power (Pd)"),
            ("Price ($/ku)", "Price ($/ku)"),
        ]
        original_display_package = comp_specs.get("Package", "-")
        comp_specs["Package"] = _canonical_to_ti_pkg_with_pins(comp_specs.get("Canonical Package") or _classify_digikey_package(original_display_package, "")) or normalize_package(original_display_package)
        print(f"\nOriginal Pkg: '{original_display_package}' -> Normalized: '{comp_specs['Package']}'")
        ti_alternative_opns = find_ti_zener_alternatives(comp_specs, ti_zener_specs_df)
        comp_specs["Package"] = _canonical_to_ti_pkg(comp_specs.get("Canonical Package")) or original_display_package
    else:
        print("\nCompetitor identified as a standard Diode/TVS. Starting standard cross-reference...")
        param_keys_display_order = [
            ("Device Name", "Device Name"),
            ("Direction", "Direction"),
            ("Package", "Package"),
            ("Voltage - Reverse Standoff (Typ)", "Vrw Standoff (Typ)"),
            ("Voltage - Clamping (Max) @ Ipp", "Vcl @ Ipp (Max)"),
            ("Capacitance", "Capacitance"),
            ("Channels", "Channels"),
            ("IEC 61000-4-5", "IEC 61000-4-5 (Surge/EFT)"),
            ("IEC 61000-4-2", "IEC 61000-4-2 (ESD)"),
            ("Power Dissipation (Pd)", "Peak Pulse Power"),
            ("Price ($/ku)", "Price ($/ku)")
        ]
        original_display_package = comp_specs.get("Package", "-")
        comp_specs["Package"] = _canonical_to_ti_pkg_with_pins(comp_specs.get("Canonical Package") or _classify_digikey_package(original_display_package, "")) or normalize_package(original_display_package)
        print(f"\nOriginal Pkg: '{original_display_package}' -> Normalized: '{comp_specs['Package']}'")
        ti_alternative_opns = find_ti_alternatives(comp_specs, ti_specs_df)
        comp_specs["Package"] = _canonical_to_ti_pkg(comp_specs.get("Canonical Package")) or original_display_package

    ti_alternatives_specs_list = []
    if ti_alternative_opns:
        print(f"\nFetching TI specs for top {len(ti_alternative_opns)} alternatives...")
        for alt_dict in ti_alternative_opns:
            part_number_str = alt_dict['part_number']
            if is_zener_part:
                ti_alt_specs = fetch_ti_zener_specs_from_excel(part_number_str, ti_zener_specs_df, param_keys_display_order)
            else:
                if ti_specs_df is not None:
                    ti_alt_specs = fetch_ti_specs_from_excel(part_number_str, ti_specs_df, param_keys_display_order)
                else:
                    ti_alt_specs = None
            if ti_alt_specs:
                src_df = ti_zener_specs_df if is_zener_part else ti_specs_df
                ti_pin_str = "-"
                if src_df is not None and not src_df.empty:
                    pn_col = next((c for c in src_df.columns if "product or part number" in c.lower()), None)
                    pin_col = next((c for c in src_df.columns if "pin count" in c.lower()), None)
                    ppp_col = next((c for c in src_df.columns if "peak pulse" in c.lower()), None)
                    if pn_col:
                        match = src_df[src_df[pn_col].astype(str).str.strip() == part_number_str.strip()]
                        if not match.empty:
                            if pin_col:
                                ti_pin_str = str(match.iloc[0][pin_col])
                            if ppp_col:
                                ppp_val = str(match.iloc[0][ppp_col])
                                ppp_clean = ppp_val.strip() if ppp_val not in ("-", "nan", "") else "-"
                                if ppp_clean != "-" and not ppp_clean.upper().endswith("W"):
                                    ppp_clean = ppp_clean + " W"
                                ti_alt_specs["Power Dissipation (Pd)"] = ppp_clean
                opn = _generate_ti_opn(part_number_str, ti_alt_specs.get("Package", "-"), ti_pin_str, comp_specs.get("Package", ""), comp_specs.get("Canonical Package"))
                ti_alt_specs["OPN"] = opn
                ti_alternatives_specs_list.append(ti_alt_specs)

    if "Direction" not in comp_specs:
        comp_specs["Direction"] = "-"

    print("\n--- Competitor Specs ---")
    for key, value in comp_specs.items():
        if key in [k[0] for k in param_keys_display_order]:
            print(f"  {key}: {value}")
    print("---------------------------------------------")

    if comp_specs.get("Package", "").endswith(", No"):
        comp_specs["Package"] = comp_specs["Package"][:-4]

    comp_canonical = comp_specs.get("Canonical Package")
    if not comp_canonical:
        for p in str(comp_specs.get("Package", "")).split(','):
            derived = _classify_digikey_package(normalize_package(p.strip()), "")
            if derived:
                comp_canonical = derived
                break
    comp_pkg_norm = normalize_package(comp_specs.get("Package", ""))

    for ti_spec in ti_alternatives_specs_list:
        original_ti_pkg_str = ti_spec.get("Package", "-")
        if original_ti_pkg_str == "-":
            continue
        package_list = [p.strip() for p in original_ti_pkg_str.split(',') if p.strip()]
        if not package_list:
            continue

        # Get pin counts for this TI part to classify packages properly
        ti_pin_str = ti_spec.get("Pin count", "-")
        try:
            ti_pins_list = [int(p.strip()) for p in str(ti_pin_str).split(',') if p.strip()]
        except (ValueError, TypeError):
            ti_pins_list = []

        chosen_pkg = None

        # Prefer package whose canonical code matches competitor's
        if comp_canonical:
            for i, pkg in enumerate(package_list):
                pin_for_pkg = ti_pins_list[0] if len(ti_pins_list) == 1 else (ti_pins_list[i] if i < len(ti_pins_list) else None)
                if _classify_ti_package(pkg, pin_for_pkg) == comp_canonical:
                    chosen_pkg = pkg
                    break

        # Fall back: match by normalized name
        if not chosen_pkg:
            for pkg in package_list:
                if normalize_package(pkg) == comp_pkg_norm:
                    chosen_pkg = pkg
                    break

        # No match — use first package
        if not chosen_pkg:
            chosen_pkg = package_list[0]

        normalized_chosen = normalize_package(chosen_pkg)
        ti_spec["Package"] = PACKAGE_DISPLAY_MAP.get(normalized_chosen, chosen_pkg)

    comp_name_header_val = comp_specs.get('Device Name', comp_part_input)
    headers = ["Parameter", f"Competitor ({comp_name_header_val.upper()})"]

    for i, ti_alt_specs in enumerate(ti_alternatives_specs_list):
        ti_alt_name = ti_alt_specs.get("OPN", ti_alt_specs.get("Device Name", f"Alt {i+1}"))
        headers.append(f"TI Alt {i+1} ({ti_alt_name})")

    for i in range(len(ti_alternatives_specs_list), 3):
        headers.append(f"TI Alt {i+1} (N/A)")

    table_data = []
    for key_name, display_name in param_keys_display_order:
        comp_value = comp_specs.get(key_name, "-")
        if key_name == "Device Name":
            comp_value = str(comp_value).upper()
        row_data = [display_name, comp_value]
        for ti_alt_specs in ti_alternatives_specs_list:
            if key_name == "Device Name":
                row_data.append(ti_alt_specs.get("OPN", ti_alt_specs.get(key_name, "-")))
            else:
                row_data.append(ti_alt_specs.get(key_name, "-"))
        for _ in range(len(ti_alternatives_specs_list), 3):
            row_data.append("-")
        table_data.append(row_data)

    if not ti_alternative_opns:
        print("\nNo suitable TI alternatives found based on the criteria.")
    else:
        print("\n--- Comparison Table ---")
        print(tabulate(table_data, headers=headers, tablefmt="grid"))

    end_time = time.time()
    print(f"\nTotal execution time: {end_time - start_time:.2f} seconds")

if __name__ == "__main__":
    main()