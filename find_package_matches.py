import pandas as pd
import re
from parsing import normalize_package

def find_package_matches(comp_pkg_alias, ti_df):
    part_num_col = next((c for c in ti_df.columns if 'product or part number' in c.lower()), None)

    pkg_col = next((c for c in ti_df.columns if 'package name' in c.lower()), None)
    if not pkg_col:
        print("! Warning: 'Package name' column not found in TI database for matching.")
        empty = pd.DataFrame()
        return empty, empty

    print(f"Stage 1 - Filtering by competitor packages on {len(ti_df)} TI parts...")

    is_structured_pkg = isinstance(comp_pkg_alias, dict)
    valid_comp_pkgs_for_match = []
    if not is_structured_pkg:
        valid_comp_pkgs_for_match = [normalize_package(p) for p in str(comp_pkg_alias).split(',') if p.strip()]

    def get_normalized_ti_pkg_list_from_cell(ti_pkg_cell_value):
        """
        For structured matching: checks only the first package in the cell (original behavior).
        For unstructured fallback: caller checks all packages via valid_comp_pkgs_for_match.
        """
        if pd.isna(ti_pkg_cell_value) or ti_pkg_cell_value == "-":
            return []
        first_pkg_str = str(ti_pkg_cell_value).split(',')[0].strip()
        pkgs_to_normalize = [p.strip() for p in first_pkg_str.split('/') if p.strip()]
        return [normalize_package(p) for p in pkgs_to_normalize if p]

    def get_all_normalized_ti_pkgs_from_cell(ti_pkg_cell_value):
        """Parses ALL comma-separated packages for unstructured fallback matching."""
        if pd.isna(ti_pkg_cell_value) or ti_pkg_cell_value == "-":
            return []
        result = []
        for pkg_token in str(ti_pkg_cell_value).split(','):
            for p in pkg_token.split('/'):
                p = p.strip()
                if p:
                    result.append(normalize_package(p))
        return result

    matched_indices = []
    comp_type = None
    if is_structured_pkg and comp_pkg_alias:
        comp_props = comp_pkg_alias
        comp_type = comp_props.get("type")
        

    for index, ti_row in ti_df.iterrows():
        ti_pkgs_cell = ti_row.get(pkg_col)
        ti_normalized_pkgs = get_normalized_ti_pkg_list_from_cell(ti_pkgs_cell)
        is_match = False
        
        # =================================================
            # --- DIODES INC. PACKAGE MATCHING RULES ---
        # =================================================
        if comp_type == "MSOP":
            expected_ti_pkg = f"VSSOP"
            if expected_ti_pkg in ti_normalized_pkgs:
                is_match = True
        
        elif comp_type == "SOD":
            for ti_pkg in ti_normalized_pkgs:
                if ti_pkg.startswith("SOD"):
                    ti_sod_num = re.search(r"SOD(\d+)", ti_pkg)
                    if ti_sod_num and ti_sod_num.group(1) == comp_props["body"]:
                        is_match = True; break
        
        elif comp_type == "SOT2X":
            if comp_props['pins'] == 3:
                if "SOT23" in ti_normalized_pkgs or "SOT233" in ti_normalized_pkgs:
                    is_match = True
            else:
                expected_ti_pkg = f"SOT23{comp_props['pins']}"
                if expected_ti_pkg in ti_normalized_pkgs:
                    is_match = True
        
        elif comp_type == "SOT3X3":
            ti_equivalent_pkg = f"SC70{comp_props['pins']}"
            if ti_equivalent_pkg in ti_normalized_pkgs:
                is_match = True

        elif comp_type == "SOTYXY":
            ti_raw_pkg_string = ti_row.get(pkg_col, "")
            
            for ti_pkg_part in str(ti_raw_pkg_string).split(','):
                ti_pkg_part_cleaned = ti_pkg_part.strip().upper()
                
                ti_sot_match = re.search(r"SOT-?(\d)[\dX](\d)", ti_pkg_part_cleaned)
                
                if ti_sot_match:
                    ti_body = f"{ti_sot_match.group(1)}{ti_sot_match.group(2)}"
                    
                    if ti_body == comp_props["body"]:
                        is_match = True
                        break  
        elif comp_type == "DFN_DIODES":
            for ti_pkg in ti_normalized_pkgs:
                ti_dfn_match = re.search(r"DFN(\d{4})", ti_pkg)
                if ti_dfn_match and ti_dfn_match.group(1) == comp_props["dims"]:
                    is_match = True; break
                
        # =================================================
            # --- AOS PACKAGE MATCHING RULES ---
        # =================================================

        elif comp_type == "SOT23":
                    if comp_props['pins'] == 3:
                        if "SOT23" in ti_normalized_pkgs or "SOT233" in ti_normalized_pkgs:
                            is_match = True
                    else:
                        expected_ti_pkg = f"SOT23{comp_props['pins']}"
                        if expected_ti_pkg in ti_normalized_pkgs:
                            is_match = True
        
        elif comp_type == "DFN":
            if "length" in comp_props:
                for ti_pkg in ti_normalized_pkgs:
                    if ti_pkg.startswith("DFN"):
                        ti_dims_match = re.search(r"DFN(\d{2})(\d{2})", ti_pkg)
                        if not ti_dims_match: continue
                        ti_len = float(ti_dims_match.group(1)) / 10.0
                        ti_wid = float(ti_dims_match.group(2)) / 10.0
                        len_ok = (comp_props["length"] * 0.9 <= ti_len <= comp_props["length"] * 1.1)
                        wid_ok = (comp_props["width"] * 0.9 <= ti_wid <= comp_props["width"] * 1.1)
                        if len_ok and wid_ok:
                            is_match = True
                            break
            elif "dims" in comp_props:
                if f"DFN{comp_props['dims']}" in ti_normalized_pkgs:
                    is_match = True

        # =================================================
            # --- NEXPERIA PACKAGE MATCHING RULES ---
        # =================================================
        elif comp_type == "NEXPERIA_SOT3X3":
            expected_ti_pkg = f"SC70{comp_props['pins']}"
            if expected_ti_pkg in ti_normalized_pkgs:
                is_match = True
                
        elif comp_type == "NEXPERIA_SOT66Y":
            ti_raw_pkg_string = ti_row.get(pkg_col, "")
            
            for ti_pkg_part in str(ti_raw_pkg_string).split(','):
                ti_pkg_part_cleaned = ti_pkg_part.strip().upper()
                
                ti_sot_match = re.search(r"SOT-?(\d)[\dX](\d)", ti_pkg_part_cleaned)
                
                if ti_sot_match:
                    ti_body = f"{ti_sot_match.group(1)}{ti_sot_match.group(2)}"
                    
                    if ti_body == "53":
                        is_match = True
                        break  
        
        elif comp_type == "NEXPERIA_SOD":
            for ti_pkg in ti_normalized_pkgs:
                if ti_pkg.startswith("SOD"):
                    ti_sod_num = re.search(r"SOD(\d+)", ti_pkg)
                    if ti_sod_num and ti_sod_num.group(1) == comp_props["body"]:
                        is_match = True
                        break
                        
        elif comp_type == "NEXPERIA_SOT23":
            if "SOT23" in ti_normalized_pkgs or "SOT233" in ti_normalized_pkgs:
                is_match = True
            

        elif comp_type == "NEXPERIA_SOT143":
            if "SOT234" in ti_normalized_pkgs:
                is_match = True

        elif comp_type == "NEXPERIA_SOT457":
            if "SOT236" in ti_normalized_pkgs:
                is_match = True

        # =================================================
            # --- AMAZING PACKAGE MATCHING RULES ---
        # =================================================

        elif comp_type == "SOD_AMAZING":
                for ti_pkg in ti_normalized_pkgs:
                    ti_sod_match = re.search(r"^SOD(\d)\d(\d)", ti_pkg)
                    if ti_sod_match:
                        ti_outer_digits = f"{ti_sod_match.group(1)}{ti_sod_match.group(2)}"
                        
                        if ti_outer_digits == comp_props["outer_digits"]:
                            is_match = True
                            break 
                        
        elif comp_type == "SOT23_AMAZING":
                    if comp_props['pins'] == 3:
                        if "SOT23" in ti_normalized_pkgs or "SOT233" in ti_normalized_pkgs:
                            is_match = True
                    else:
                        expected_ti_pkg = f"SOT23{comp_props['pins']}"
                        if expected_ti_pkg in ti_normalized_pkgs:
                            is_match = True
        
        elif comp_type == "SOT":
            comp_body_style = comp_props.get("body")
            if pd.notna(ti_pkgs_cell) and isinstance(ti_pkgs_cell, str):
                for ti_pkg_str in ti_pkgs_cell.split(','):
                    ti_pkg_str = ti_pkg_str.strip().upper()
                    ti_sot_match = re.search(r"SOT-(\d)X(\d)", ti_pkg_str)
                    if ti_sot_match:
                        ti_body_style = f"{ti_sot_match.group(1)}{ti_sot_match.group(2)}"
                        if ti_body_style == comp_body_style:
                            is_match = True
                            break
        
        elif comp_type == "SC70":
            if f"SC70{comp_props['pins']}" in ti_normalized_pkgs:
                is_match = True

        # =================================================
            # --- JIANGSU PACKAGE MATCHING RULES ---
        # =================================================
        elif comp_type == "DFN_JIANGSU":
            expected_ti_pkg = f"DFN{comp_props['dims']}"
            if expected_ti_pkg in ti_normalized_pkgs:
                is_match = True

        elif comp_type == "SOD_JIANGSU":
            for ti_pkg in ti_normalized_pkgs:
                if ti_pkg.startswith("SOD"):
                    ti_sod_num = re.search(r"SOD(\d+)", ti_pkg)
                    if ti_sod_num and ti_sod_num.group(1) == comp_props["body"]:
                        is_match = True
                        break
        
        elif comp_type == "SOT143_JIANGSU":
            if "SOT234" in ti_normalized_pkgs:
                is_match = True

        elif comp_type == "SOT3X3_JIANGSU":
            expected_ti_pkg = f"SC70{comp_props['pins']}"
            if expected_ti_pkg in ti_normalized_pkgs:
                is_match = True

        elif comp_type == "SOT23_JIANGSU":
            if comp_props['pins'] == 3:
                if "SOT23" in ti_normalized_pkgs or "SOT233" in ti_normalized_pkgs:
                    is_match = True
            else:
                expected_ti_pkg = f"SOT23{comp_props['pins']}"
                if expected_ti_pkg in ti_normalized_pkgs:
                    is_match = True
        
        elif comp_type == "SOTYXY_JIANGSU":
            ti_raw_pkg_string = ti_row.get(pkg_col, "")
            
            for ti_pkg_part in str(ti_raw_pkg_string).split(','):
                ti_pkg_part_cleaned = ti_pkg_part.strip().upper()

                ti_sot_match = re.search(r"SOT-?(\d)[\dX](\d)", ti_pkg_part_cleaned)
                
                if ti_sot_match:
                    ti_body = f"{ti_sot_match.group(1)}{ti_sot_match.group(2)}"
                    
                    if ti_body == comp_props["body"]:
                        is_match = True
                        break 

        
        # =================================================
         # --- LITTELFUSE PACKAGE MATCHING RULES ---
        # =================================================
        elif comp_type == "DFN_LITTEL" or comp_type == "WLCSP_LITTEL":
            expected_ti_pkg = f"DFN{comp_props['dims']}"
            if expected_ti_pkg in ti_normalized_pkgs:
                is_match = True

        elif comp_type == "MSOP_LITTEL":
            if "VSSOP" in ti_normalized_pkgs:
                is_match = True

        elif comp_type == "SC70_LITTEL":
            expected_ti_pkg = f"SC70{comp_props['pins']}"
            if expected_ti_pkg in ti_normalized_pkgs:
                is_match = True

        elif comp_type == "SOD88X_LITTEL":
            if "DFN1006" in ti_normalized_pkgs:
                is_match = True

        elif comp_type == "SOD_LITTEL":
            for ti_pkg in ti_normalized_pkgs:
                if ti_pkg.startswith("SOD"):
                    ti_sod_num = re.search(r"SOD(\d+)", ti_pkg)
                    if ti_sod_num and ti_sod_num.group(1) == comp_props["body"]:
                        is_match = True; break
        
        elif comp_type == "SOT143_LITTEL":
            if "SOT234" in ti_normalized_pkgs:
                is_match = True

        elif comp_type == "SOT23_LITTEL":
            if comp_props['pins'] == 3:
                if "SOT23" in ti_normalized_pkgs or "SOT233" in ti_normalized_pkgs:
                    is_match = True
            else:
                expected_ti_pkg = f"SOT23{comp_props['pins']}"
                if expected_ti_pkg in ti_normalized_pkgs:
                    is_match = True

        elif comp_type == "SOTZYZ_LITTEL":
            comp_body = comp_props.get("body")
            expected_ti_pkg = f"SOT{comp_body[0]}X{comp_body[1]}"
            if expected_ti_pkg in ti_normalized_pkgs:
                is_match = True

        # =================================================
            # --- SEMTECH PACKAGE MATCHING RULES ---
        # =================================================
        elif comp_type == "DFN_SEMTECH_SPECIAL":
            if comp_props.get("name") == "DFN0402" and "DFN1006" in ti_normalized_pkgs:
                is_match = True

        elif comp_type == "DFN_SEMTECH":
            expected_ti_pkg = f"DFN{comp_props['dims']}"
            if expected_ti_pkg in ti_normalized_pkgs:
                is_match = True

        elif comp_type == "MSOP_SEMTECH":
            if "VSSOP" in ti_normalized_pkgs:
                is_match = True
        
        elif comp_type == "SC70_SEMTECH":
            expected_ti_pkg = f"SC70{comp_props['pins']}"
            if expected_ti_pkg in ti_normalized_pkgs:
                is_match = True
        
        elif comp_type == "SOD_SEMTECH":
            for ti_pkg in ti_normalized_pkgs:
                if ti_pkg.startswith("SOD"):
                    ti_sod_num = re.search(r"SOD(\d+)", ti_pkg)
                    if ti_sod_num and ti_sod_num.group(1) == comp_props["body"]:
                        is_match = True
                        break
        
        elif comp_type == "SOT666_SEMTECH":
            if "SOT5X3" in ti_normalized_pkgs:
                is_match = True

        elif comp_type == "SOT143_SEMTECH":
            if "SOT234" in ti_normalized_pkgs:
                is_match = True

        elif comp_type == "SOT23_SEMTECH":
            if comp_props['pins'] == 3:
                if "SOT23" in ti_normalized_pkgs or "SOT233" in ti_normalized_pkgs:
                    is_match = True
            else:
                expected_ti_pkg = f"SOT23{comp_props['pins']}"
                if expected_ti_pkg in ti_normalized_pkgs:
                    is_match = True

        # =================================================
        # --- GENERIC/REUSABLE PACKAGE MATCHING RULES ---
        # =================================================
        elif comp_type == "DFN_GENERIC":
            # Generic DFNXXXX maps to TI's DFNXXXX
            expected_ti_pkg = f"DFN{comp_props['dims']}"
            if expected_ti_pkg in ti_normalized_pkgs:
                is_match = True

        elif comp_type == "SC70_GENERIC":
            expected_ti_pkg = f"SC70{comp_props['pins']}"
            if expected_ti_pkg in ti_normalized_pkgs:
                is_match = True

        elif comp_type == "SOD_GENERIC":
            for ti_pkg in ti_normalized_pkgs:
                if ti_pkg.startswith("SOD"):
                    ti_sod_num = re.search(r"SOD(\d+)", ti_pkg)
                    if ti_sod_num and ti_sod_num.group(1) == comp_props["body"]:
                        is_match = True
                        break 

        elif comp_type == "SOT23_GENERIC":
            if comp_props['pins'] == 3:
                if "SOT23" in ti_normalized_pkgs or "SOT233" in ti_normalized_pkgs:
                    is_match = True
            else:
                expected_ti_pkg = f"SOT23{comp_props['pins']}"
                if expected_ti_pkg in ti_normalized_pkgs:
                    is_match = True
        
        elif comp_type == "SOT5X3_GENERIC":
            if "SOT5X3" in ti_normalized_pkgs:
                is_match = True
        
        if not is_structured_pkg:
            all_ti_pkgs = get_all_normalized_ti_pkgs_from_cell(ti_pkgs_cell)

            # Expand competitor packages with known equivalences so rules apply
            # regardless of which competitor database or DigiKey the specs came from.
            expanded_comp_pkgs = set(valid_comp_pkgs_for_match)

            for cp in list(expanded_comp_pkgs):
                # SOT23 without pin count defaults to 3-pin
                if cp == "SOT23":
                    expanded_comp_pkgs.add("SOT233")

                # SOD882 variants -> DFN1006 (same footprint)
                if re.match(r"SOD.?882", cp):
                    expanded_comp_pkgs.add("DFN1006")

                # DFN0402 / DFN-0402 -> DFN1006
                if re.match(r"DFN.?0402", cp):
                    expanded_comp_pkgs.add("DFN1006")

                # SOT666 / SOT-666 -> SOT5X3
                if re.match(r"SOT.?666", cp):
                    expanded_comp_pkgs.add("SOT5X3")

                # SOT363 / SOT-363 -> SC706
                if re.match(r"SOT.?363", cp):
                    expanded_comp_pkgs.add("SC706")

                # SOT323 / SOT-323 -> SC703
                if re.match(r"SOT.?323", cp):
                    expanded_comp_pkgs.add("SC703")

                # SOT143 / SOT-143 -> SOT234
                if re.match(r"SOT.?143", cp):
                    expanded_comp_pkgs.add("SOT234")

                # SC88 / SC-88 -> SC706
                if re.match(r"SC.?88", cp):
                    expanded_comp_pkgs.add("SC706")

                # 0603 / 603 bare token -> DFN0603
                if re.match(r"^0?603$", cp):
                    expanded_comp_pkgs.add("DFN0603")

                # 0201 bare token -> DFN0603
                if re.match(r"^0?201$", cp):
                    expanded_comp_pkgs.add("DFN0603")

                # 1006 bare token -> DFN1006
                if re.match(r"^1006$", cp):
                    expanded_comp_pkgs.add("DFN1006")

                # X2SON -> DFN0603 (4-pin, but nearest TI family)
                if "X2SON" in cp:
                    expanded_comp_pkgs.add("DFN0603")

                # MSOP -> VSSOP
                if cp.startswith("MSOP"):
                    expanded_comp_pkgs.add("VSSOP")

                # WLCSP / DSBGA variants
                if "WLCSP" in cp or "DSBGA" in cp:
                    expanded_comp_pkgs.add("DSBGA")

                # SOT886 / SOT-886 -> SOT886 (already normalized but ensure)
                if re.match(r"SOT.?886", cp):
                    expanded_comp_pkgs.add("SOT886")

            if any(comp_pkg in all_ti_pkgs for comp_pkg in expanded_comp_pkgs):
                is_match = True
        if is_match:
            matched_indices.append(index)

        
    package_matches_df = ti_df.loc[list(set(matched_indices))].copy()
    print(f"Found {len(package_matches_df)} TI parts with matching packages (Stage 1).")

    if package_matches_df.empty:
        return package_matches_df, package_matches_df.copy(), package_matches_df.copy()

    if is_structured_pkg and comp_type in ["DFN", "DFN_JIANGSU", "DFN_LITTEL", "SOT", "SOTYXY", "DFN_DIODES", "NEXPERIA_SOT66Y", "SOTYXY_JIANGSU"]:
        if 'pins' not in comp_props:
            return package_matches_df, package_matches_df.copy(), package_matches_df.copy()

        pin_col = next((c for c in ti_df.columns if 'pin count' in c.lower()), None)
        if not pin_col or part_num_col is None:
            print("! Warning: 'Pin count' column not found in TI database. Skipping pin count filter.")
            return package_matches_df, package_matches_df.copy(), package_matches_df.copy()

        comp_pins = comp_props['pins']
        print(f"Stage 2 - Filtering by pin count ({comp_pins} pins)...")

        stage2_indices = []
        for index, ti_row in package_matches_df.iterrows():
            pin_str = str(ti_row.get(pin_col, ""))
            try:
                pin_vals = [int(p.strip()) for p in pin_str.split(',') if p.strip()]
            except ValueError:
                pin_vals = []
            if comp_pins in pin_vals:
                stage2_indices.append(index)

        final_matches_df = package_matches_df.loc[list(set(stage2_indices))].copy()
        print(f"Found {len(final_matches_df)} TI parts after pin count filter (Stage 2).")

        return package_matches_df, final_matches_df, final_matches_df

    elif not is_structured_pkg:
        # For unstructured matches, try to extract pin count from a trailing -N suffix
        # on the raw package string (before normalize_package strips hyphens).
        # e.g. "DFN2510-10" -> pin_count=10
        comp_pin_count = None
        for raw_cp in str(comp_pkg_alias).split(','):
            raw_cp = raw_cp.strip()
            pin_match = re.search(r'-(\d+)(?:[A-Za-z]*)$', raw_cp)
            if pin_match:
                comp_pin_count = int(pin_match.group(1))
                break

        if comp_pin_count is None:
            # No pin info in unstructured string — all pkg matches are equal
            print("Stage 2 - Pin count not determinable for unstructured package. Skipping.")
            return package_matches_df, package_matches_df.copy(), package_matches_df.copy()

        pin_col = next((c for c in ti_df.columns if 'pin count' in c.lower()), None)
        if not pin_col:
            return package_matches_df, package_matches_df.copy(), package_matches_df.copy()

        print(f"Stage 2 - Filtering unstructured package by pin count ({comp_pin_count} pins)...")
        stage2_indices = []
        for index, ti_row in package_matches_df.iterrows():
            pin_str = str(ti_row.get(pin_col, ""))
            try:
                pin_vals = [int(p.strip()) for p in pin_str.split(',') if p.strip()]
            except ValueError:
                pin_vals = []
            if comp_pin_count in pin_vals:
                stage2_indices.append(index)

        final_matches_df = package_matches_df.loc[list(set(stage2_indices))].copy()
        print(f"Found {len(final_matches_df)} TI parts after pin count filter (Stage 2).")
        return package_matches_df, final_matches_df, final_matches_df

    else:
        print("Stage 2 - Pin count filter not applicable for this package type. Skipping.")
        return package_matches_df, package_matches_df.copy(), package_matches_df.copy()