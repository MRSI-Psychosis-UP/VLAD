#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Create AFNI 3dLME data table + model from an Excel file,
with within/between decomposition, optional interaction terms,
and automatic generation of:
  - AFNI 3dLME -dataTable file
  - 3dLME command (printed)
  - expected GLT sub-brick indices for 3dClusterize
  - design & correlation heatmaps with model text.

Usage (minimal arguments):
    python create_matrix_VLADlov_afni_alpha1.py PREFIX MASK IMG_DIR IMG_SUFFIX

Where:
  PREFIX     = AFNI prefix for 3dLME outputs (e.g. LME-Ins-DHEA_S_test2)
  MASK       = mask dataset (e.g. HarvardOxford-white-grey-matter.nii.gz)
  IMG_DIR    = directory where sub-XXX_ses-YYY*.nii.gz live
  IMG_SUFFIX = suffix that comes *after* "sub-XXX_ses-YYY",
               e.g. "_space-MNI_res-1p5_desc-Ins.nii.gz"

The script will open a dialog to select an Excel file, then prompt for:
  - subject column
  - visit column
  - covariates to include
  - covariates to split into WITHIN/BETWEEN
  - interaction terms (Var1*Var2)
  - main continuous variable (for Within/Between GLTs)

It will then run 3dLME (requires AFNI in your PATH).
"""

import os
import sys
import textwrap
import subprocess

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

import tkinter as tk
from tkinter import filedialog


# -------------------------------------------------------------------
# Small helpers
# -------------------------------------------------------------------

def select_excel_file_and_sheet():
    """Open a dialog to select an Excel file and then ask the user to select a sheet."""
    root = tk.Tk()
    root.withdraw()
    file_path = filedialog.askopenfilename(filetypes=[("Excel files", "*.xlsx")])

    if not file_path:
        print("No file selected. Aborting.")
        return None, None

    xls = pd.ExcelFile(file_path)
    sheet_names = xls.sheet_names
    print("Available sheets:", sheet_names)
    selected_sheet = input("Enter the name of the sheet to use: ").strip()

    if selected_sheet not in sheet_names:
        print(f"Sheet '{selected_sheet}' not found. Aborting.")
        return None, None

    return file_path, selected_sheet


def build_within_between(df: pd.DataFrame, subject_col: str, vars_to_split):
    """
    For each selected variable X:
        X_B = mean_subject(X) - grand_mean(mean_subject(X))
        X_W = X - mean_subject(X)
      Original X is dropped from the design.
    """
    df = df.copy()
    if not vars_to_split:
        return df

    if subject_col not in df.columns:
        raise ValueError(f"Subject column '{subject_col}' not in dataframe.")

    for var in vars_to_split:
        if var not in df.columns:
            print(f"[WARN] Cannot split '{var}' (not in dataframe); skipping.")
            continue

        subj_means = df.groupby(subject_col)[var].transform("mean")
        grand_mean = subj_means.groupby(df[subject_col]).transform("mean").mean()

        df[f"{var}_B"] = subj_means - grand_mean
        df[f"{var}_W"] = df[var] - subj_means

        # Drop original
        df.drop(columns=[var], inplace=True)
        print(f" -> created '{var}_B' and '{var}_W', dropped '{var}'")

    return df


def add_interactions(df: pd.DataFrame, interaction_specs):
    """
    For each spec like 'Var1*Var2', create new column 'Var1_x_Var2' = Var1 * Var2.
    """
    df = df.copy()
    for spec in interaction_specs:
        spec = spec.strip()
        if not spec:
            continue
        if '*' not in spec:
            print(f"[WARN] Interaction spec '{spec}' does not contain '*'; skipping.")
            continue
        a, b = [s.strip() for s in spec.split('*', 1)]
        if a not in df.columns or b not in df.columns:
            print(f"[WARN] Interaction '{spec}': '{a}' or '{b}' not in dataframe; skipping.")
            continue
        new_name = f"{a}_x_{b}"
        df[new_name] = df[a] * df[b]
        print(f" -> created interaction '{new_name}' = {a} * {b}")
    return df


def make_design_plots(df: pd.DataFrame,
                      subject_col: str,
                      visit_col: str,
                      covar_cols,
                      model_str: str,
                      prefix: str,
                      out_dir: str):
    """
    Create:
      - design matrix heatmap (standardized columns)
      - correlation matrix heatmap
    and save to out_dir with prefix.
    """
    if not covar_cols:
        print("[INFO] No covariates for diagnostics; skipping plots.")
        return

    # Keep only numeric columns among covariates
    numeric_cols = [c for c in covar_cols if pd.api.types.is_numeric_dtype(df[c])]
    if not numeric_cols:
        print("[INFO] No numeric covariates for diagnostics; skipping plots.")
        return

    X = df[numeric_cols].copy()
    # Standardize per column (avoid division by zero)
    X_std = X.copy()
    for col in numeric_cols:
        col_std = X[col].std(ddof=0)
        if col_std == 0 or np.isnan(col_std):
            X_std[col] = 0.0
        else:
            X_std[col] = (X[col] - X[col].mean()) / col_std

    row_labels = df[subject_col].astype(str) + "_" + df[visit_col].astype(str)

    os.makedirs(out_dir, exist_ok=True)

    # Design matrix heatmap
    fig, ax = plt.subplots(figsize=(max(8, len(numeric_cols) * 0.6), 8))
    im = ax.imshow(X_std.values, aspect="auto", interpolation="nearest")
    ax.set_xticks(range(len(numeric_cols)))
    ax.set_xticklabels(numeric_cols, rotation=90)
    ax.set_yticks(range(len(row_labels)))
    # If too many rows, don't label all of them
    if len(row_labels) <= 40:
        ax.set_yticklabels(row_labels)
    else:
        ax.set_yticklabels([])
    ax.set_xlabel("Covariates")
    ax.set_ylabel("Subject_Visit")
    ax.set_title("AFNI 3dLME design (standardized)")

    plt.colorbar(im, ax=ax, fraction=0.02, pad=0.02)

    wrapped_model = "\n".join(textwrap.wrap(f"MODEL: {model_str}", width=80))
    fig.text(0.01, 0.01, wrapped_model,
             ha="left", va="bottom", fontsize=8)

    fig.tight_layout(rect=(0, 0.05, 1, 1))
    out_design = os.path.join(out_dir, f"{prefix}_afni_design_matrix.png")
    fig.savefig(out_design, dpi=200)
    plt.close(fig)

    # Correlation matrix
    corr = X.corr()
    fig, ax = plt.subplots(figsize=(max(8, len(numeric_cols) * 0.6), 8))
    im = ax.imshow(corr.values, interpolation="nearest")
    ax.set_xticks(range(len(numeric_cols)))
    ax.set_yticks(range(len(numeric_cols)))
    ax.set_xticklabels(numeric_cols, rotation=90)
    ax.set_yticklabels(numeric_cols)
    ax.set_title("AFNI 3dLME: correlation matrix")
    plt.colorbar(im, ax=ax, fraction=0.02, pad=0.02)
    fig.tight_layout()
    out_corr = os.path.join(out_dir, f"{prefix}_afni_design_correlation.png")
    fig.savefig(out_corr, dpi=200)
    plt.close(fig)

    print("[INFO] Saved design diagnostics:")
    print("       ", out_design)
    print("       ", out_corr)


# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------

def main():
    # ------------------------------------------------------------------
    # Parse simple command-line arguments
    # ------------------------------------------------------------------
    if len(sys.argv) != 5:
        print("Usage: python create_matrix_VLADlov_afni_alpha1.py "
              "PREFIX MASK IMG_DIR IMG_SUFFIX")
        sys.exit(1)

    prefix = sys.argv[1]
    mask = sys.argv[2]
    img_dir = sys.argv[3]
    img_suffix = sys.argv[4]

    if not os.path.isdir(img_dir):
        print(f"[ERROR] IMG_DIR does not exist or is not a directory: {img_dir}")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Choose Excel file + sheet, load data
    # ------------------------------------------------------------------
    file_path, sheet_name = select_excel_file_and_sheet()
    if file_path is None:
        sys.exit(1)

    print(f"[INFO] Loading Excel file: {file_path}")
    df = pd.read_excel(file_path, sheet_name=sheet_name)

    print("[INFO] Columns in the selected sheet:")
    print(list(df.columns))

    # ------------------------------------------------------------------
    # Ask for subject / visit columns
    # ------------------------------------------------------------------
    subject_col = input("Name of SUBJECT column (e.g. SubjID): ").strip()
    visit_col = input("Name of VISIT/TIME column (e.g. Visit, Timepoint): ").strip()

    if subject_col not in df.columns or visit_col not in df.columns:
        print("[ERROR] Subject or visit column not found in the dataframe.")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Design covariates
    # ------------------------------------------------------------------
    design_cols_input = input(
        "Enter covariates to include in the AFNI 3dLME model "
        "(comma-separated, e.g. Age,Sex,Hormone): "
    ).strip()
    design_covars = [c.strip() for c in design_cols_input.split(",") if c.strip()]

    needed = [subject_col, visit_col] + design_covars
    missing = [c for c in needed if c not in df.columns]
    if missing:
        print("[ERROR] These requested columns are not in the sheet:", missing)
        sys.exit(1)

    df = df.loc[:, needed]

    # Drop rows with NA in these columns
    df = df.dropna().copy()

    # Rename subject/visit columns to AFNI-required names
    rename_map = {}
    if subject_col != "Subj":
        rename_map[subject_col] = "Subj"
    if visit_col != "Visit":
        rename_map[visit_col] = "Visit"

    if rename_map:
        df.rename(columns=rename_map, inplace=True)
        if subject_col in rename_map:
            subject_col = "Subj"
        if visit_col in rename_map:
            visit_col = "Visit"
        print(f"[INFO] Renamed columns for AFNI compatibility: {rename_map}")

    df = df.sort_values([subject_col, visit_col]).reset_index(drop=True)

    print(f"[INFO] Number of rows after dropping NA: {len(df)}")

    # ------------------------------------------------------------------
    # Within/Between decomposition
    # ------------------------------------------------------------------
    print("Current covariate columns:", design_covars)
    wb_input = input(
        "Which covariates should be decomposed into WITHIN/BETWEEN? "
        "(comma-separated names, or blank for none): "
    ).strip()
    wb_vars = [c.strip() for c in wb_input.split(",") if c.strip()]

    if wb_vars:
        print("[INFO] Performing within/between split on:", wb_vars)
    df = build_within_between(df, subject_col, wb_vars)

    # Rebuild covariate list after WB split
    expanded_covars = []
    for c in design_covars:
        if c in wb_vars:
            expanded_covars.extend([f"{c}_B", f"{c}_W"])
        else:
            expanded_covars.append(c)

    # ------------------------------------------------------------------
    # Interactions (for 3dLME model formula only)
    # ------------------------------------------------------------------
    print("Available columns for interactions (design space so far):")
    print(expanded_covars)
    inter_input = input(
        "Specify interaction terms to add to the 3dLME model (e.g. DHEA_S_W*Sex), "
        "separated by commas, or leave blank for none: "
    ).strip()

    interaction_terms = []
    if inter_input:
        for spec in inter_input.split(","):
            spec = spec.strip()
            if not spec:
                continue
            # Accept either A*B or A:B syntax; just pass through to AFNI/R
            if ("*" not in spec) and (":" not in spec):
                print(f"[WARN] Interaction '{spec}' has no '*' or ':'; skipping.")
                continue
            interaction_terms.append(spec)
        print("[INFO] Interaction terms to be added to model:", interaction_terms)
    else:
        print("[INFO] No interaction terms specified.")

    # ------------------------------------------------------------------
    # Decide which covariates are categorical vs quantitative (qVars)
    # ------------------------------------------------------------------
    print("Current design covariates after WB split:")
    print(expanded_covars)

    numeric_candidates = [c for c in expanded_covars
                          if pd.api.types.is_numeric_dtype(df[c])]
    print("Numeric covariates detected:", numeric_candidates)

    cat_input = input(
        "Among these, which should be treated as CATEGORICAL factors in 3dLME? "
        "(comma-separated names, e.g. Sex; leave blank for none): "
    ).strip()
    categorical_covars = [c.strip() for c in cat_input.split(",") if c.strip()]

    qvars = [c for c in numeric_candidates if c not in categorical_covars]
    print("[INFO] Quantitative variables (qVars):", qvars)
    print("[INFO] Categorical factors:", categorical_covars)

    # ------------------------------------------------------------------
    # Build AFNI dataTable: add InputFile column
    # ------------------------------------------------------------------
    def build_input_path(row):
        subj_val = str(row[subject_col])
        visit_raw = str(row[visit_col]).strip()

        # If visit already starts with V/v, keep as-is; if it is only digits, prefix with 'V'; otherwise, leave unchanged
        if visit_raw.upper().startswith("V"):
            visit_token = visit_raw
        elif visit_raw.isdigit():
            visit_token = f"V{visit_raw}"
        else:
            visit_token = visit_raw

        fname = f"sub-{subj_val}_ses-{visit_token}{img_suffix}"
        return os.path.join(img_dir, fname)

    df["InputFile"] = df.apply(build_input_path, axis=1)

    # Check files exist (warn only)
    missing_files = [p for p in df["InputFile"].unique() if not os.path.exists(p)]
    if missing_files:
        print("[WARN] Some image files do NOT exist (check paths):")
        for p in missing_files[:10]:
            print("   ", p)
        if len(missing_files) > 10:
            print(f"   ... and {len(missing_files) - 10} more.")
    else:
        print("[INFO] All image files found.")

    # Order columns: Subject, Visit, covariates, InputFile
    out_cols = [subject_col, visit_col] + expanded_covars + ["InputFile"]
    df_out = df[out_cols].copy()

    # Center quantitative covariates that are not already WB components
    for v in qvars:
        if v in df_out.columns and not (v.endswith("_W") or v.endswith("_B")):
            mean_v = df_out[v].mean()
            df_out[v] = df_out[v] - mean_v
            print(f"[INFO] Centered qVar '{v}' at mean {mean_v:.5f}")

    # Write dataTable
    datatable_name = f"{prefix}_3dLME_dataTable.txt"
    datatable_path = os.path.abspath(datatable_name)
    df_out.to_csv(datatable_path, sep="\t", index=False, na_rep="NA")
    print(f"[INFO] Wrote AFNI 3dLME dataTable to: {datatable_path}")

    # ------------------------------------------------------------------
    # Build model string and qVars
    # ------------------------------------------------------------------
    model_terms = expanded_covars.copy()
    # Add interaction terms as R-style/AFNI-style formula terms
    if 'interaction_terms' in locals() and interaction_terms:
        model_terms.extend(interaction_terms)

    model_str = "+".join(model_terms) if model_terms else "1"

    # qVars string and centers (all qVars already centered in df_out)
    qvars_str = ",".join(qvars)
    qcenters_str = ",".join(["0"] * len(qvars))

    # ------------------------------------------------------------------
    # Ask user for main continuous variable -> GLTs
    # ------------------------------------------------------------------
    if wb_vars:
        print("Variables with WITHIN/BETWEEN split:", wb_vars)
        default_main = wb_vars[0]
    else:
        default_main = None

    main_cont = input(
        f"Main continuous variable for GLTs (must be one of {wb_vars}; "
        f"leave blank to use '{default_main}'): "
    ).strip()

    if not main_cont:
        main_cont = default_main

    if main_cont and main_cont not in wb_vars:
        print(f"[ERROR] main_cont '{main_cont}' is not in WB vars {wb_vars}.")
        sys.exit(1)

    if not main_cont:
        print("[WARN] No main continuous variable chosen. "
              "GLTs will not be defined; exiting.")
        sys.exit(1)

    main_within = f"{main_cont}_W"
    main_between = f"{main_cont}_B"

    glt_label_within = f"{main_cont}_Within_effect"
    glt_label_between = f"{main_cont}_Between_effect"
    glt_code_within = f"{main_within} :"
    glt_code_between = f"{main_between} :"

    # ------------------------------------------------------------------
    # Design diagnostics (visual)
    # ------------------------------------------------------------------
    out_dir = os.getcwd()
    make_design_plots(df_out, subject_col, visit_col,
                      covar_cols=expanded_covars,
                      model_str=model_str,
                      prefix=prefix,
                      out_dir=out_dir)

    # ------------------------------------------------------------------
    # Build and run 3dLME command
    # ------------------------------------------------------------------
    jobs = input("Number of CPU threads for 3dLME [default 8]: ").strip()
    try:
        n_jobs = int(jobs) if jobs else 8
    except ValueError:
        n_jobs = 8

    ran_eff_str = f"~1|{subject_col}"

    cmd = [
        "3dLME",
        "-prefix", prefix,
        "-jobs", str(n_jobs),
        "-mask", mask,
        "-model", model_str,
        "-SS_type", "3",
        "-ranEff", ran_eff_str,
        "-num_glt", "2",
        "-gltLabel", "1", glt_label_within,
        "-gltCode", "1", glt_code_within,
        "-gltLabel", "2", glt_label_between,
        "-gltCode", "2", glt_code_between,
        "-dataTable", f"@{datatable_path}",
    ]

    if qvars:
        cmd.extend(["-qVars", qvars_str, "-qVarCenters", qcenters_str])

    print("\n================ 3dLME command =================\n")
    print(" \\\n  ".join(cmd))
    print("\n================================================\n")

    run_now = input("Run 3dLME now? [Y/n]: ").strip().lower()
    if run_now in ("", "y", "yes"):
        try:
            subprocess.run(cmd, check=True)
            print("[INFO] 3dLME finished successfully.")
        except subprocess.CalledProcessError as e:
            print("[ERROR] 3dLME failed with return code", e.returncode)
    else:
        print("[INFO] Skipping 3dLME run (command printed above).")

    # ------------------------------------------------------------------
    # Print expected GLT sub-brick indices for 3dClusterize
    # ------------------------------------------------------------------
    n_terms = len(model_terms)
    n_F = 1 + n_terms  # intercept + each model term

    beta_within_idx = n_F          # GLT1 Coef
    z_within_idx = n_F + 1         # GLT1 Z/T
    beta_between_idx = n_F + 2     # GLT2 Coef
    z_between_idx = n_F + 3        # GLT2 Z/T

    print("=== Expected sub-brick indices for GLTs (for 3dClusterize) ===")
    print(f"# Number of fixed-effect F-bricks (including intercept): {n_F}")
    print("")
    print(f"# {glt_label_within}")
    print(f"BETA_WITHIN_BRICK={beta_within_idx}")
    print(f"TSTAT_WITHIN_BRICK={z_within_idx}")
    print("")
    print(f"# {glt_label_between}")
    print(f"BETA_BETWEEN_BRICK={beta_between_idx}")
    print(f"TSTAT_BETWEEN_BRICK={z_between_idx}")
    print("=============================================================\n")
    print("Check with:")
    print(f"  3dinfo -verb {prefix}+tlrc.HEAD | egrep 'Sub-brick|{glt_label_within}|{glt_label_between}'")


if __name__ == "__main__":
    main()