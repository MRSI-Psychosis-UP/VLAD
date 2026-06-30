#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Create design / contrast / design.sub + subject/visit list for FSL SwE (modified mode)
from an Excel file, with within/between decomposition and interaction terms.

- Prompts for:
    - SUBJECT column
    - VISIT/TIME column
    - GROUP column (for modified SwE; optional)
    - Covariates to include in the design
    - Covariates to split into WITHIN/BETWEEN
    - Interaction terms (e.g. Time_W*Group)
    - Main effect column (for t-contrast + collinearity check)

- Within/Between:
    For each selected variable X:
        X_B = mean_subject(X) - grand_mean(mean_subject(X))
        X_W = X - mean_subject(X)
      Original X is dropped from the design.

- Outputs (all plain text for Text2Vest, except design.sub which is already SwE-style):
    MAIN_DIR/Results/ANALYSIS_NAME/
        MATRIX_NAME_mat.txt             -> Text2Vest -> design.mat
        MATRIX_NAME_con.txt             -> Text2Vest -> design.con
        MATRIX_NAME_design_sub.txt      (3-column modified SwE)
        MATRIX_NAME_correlation.png     (correlation heatmap)
        MATRIX_NAME_design_and_contrast.png (design heatmap + contrast table)

    MAIN_DIR/Listes/
        List_ANALYSIS_NAME.txt 

Usage:
    python create_SWE_design_from_excel.py \
        [ANALYSIS_NAME] [MATRIX_NAME] [MAIN_DIRECTORY] [MAKE_LIST,1/0]
"""

import pandas as pd
import tkinter as tk
from tkinter import filedialog
import sys
import os
import numpy as np
import seaborn as sns
import matplotlib.pyplot as plt
from scipy.stats import pearsonr
import shutil


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def select_excel_file_and_sheet():
    """Open a dialog to select an Excel file and then ask the user to select a sheet."""
    root = tk.Tk()
    root.withdraw()
    file_path = filedialog.askopenfilename(filetypes=[("Excel files", "*.xlsx")])

    if not file_path:
        return None, None

    xls = pd.ExcelFile(file_path)
    sheet_names = xls.sheet_names
    print("Available sheets:", sheet_names)
    selected_sheet = input("Enter the name of the sheet to use: ").strip()

    return file_path, selected_sheet


def is_binary(series: pd.Series) -> bool:
    """Check if a series is 0/1 or 1/2 style binary."""
    vals = pd.Series(series).dropna().unique()
    if len(vals) != 2:
        return False
    # allow {0,1}, {1,2}, {0,2}, etc., but treat as binary
    return True


def build_within_between(df: pd.DataFrame, subject_col: str, vars_to_split):
    """
    For each variable X in vars_to_split, create X_B (between) and X_W (within) and drop X.

    X_B = mean_subject(X) - grand_mean_subject_mean
    X_W = X - mean_subject(X)

    If a variable is non-numeric or binary, it is skipped.
    """
    df = df.copy()
    for var in vars_to_split:
        if var not in df.columns:
            print(f"[WARNING] within/between variable '{var}' not found in DataFrame, skipping.")
            continue

        if not pd.api.types.is_numeric_dtype(df[var]):
            print(f"[WARNING] variable '{var}' is not numeric, skipping within/between split.")
            continue

        if is_binary(df[var]):
            print(f"[INFO] variable '{var}' appears binary, skipping within/between split for it.")
            continue

        subj_mean = df.groupby(subject_col)[var].transform('mean')
        grand_mean = subj_mean.mean()

        df[f"{var}_B"] = subj_mean - grand_mean
        df[f"{var}_W"] = df[var] - subj_mean
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
            print(f"[WARNING] interaction spec '{spec}' has no '*', skipping.")
            continue

        a, b = [s.strip() for s in spec.split('*', 1)]
        if a not in df.columns or b not in df.columns:
            print(f"[WARNING] interaction {a}*{b} cannot be built (missing columns), skipping.")
            continue

        new_name = f"{a}_x_{b}"
        df[new_name] = df[a] * df[b]
        print(f" -> created interaction '{new_name}' = {a} * {b}")

    return df


def create_t_contrasts(design_cols, effect_vars):
    """
    Build a t-contrast matrix for a list of effect variables.

    For each variable in effect_vars, create two contrasts:
        1) var > 0
        2) var < 0

    This yields 2 * len(effect_vars) contrast rows.
    """
    num_cols = len(design_cols)
    contrasts = []

    for var in effect_vars:
        if var not in design_cols:
            raise ValueError(f"Contrast variable '{var}' not in design columns")

        idx = design_cols.index(var)
        row_pos = np.zeros(num_cols, dtype=float)
        row_neg = np.zeros(num_cols, dtype=float)

        row_pos[idx] = 1.0   # var > 0
        row_neg[idx] = -1.0  # var < 0

        contrasts.append(row_pos)
        contrasts.append(row_neg)

    return np.vstack(contrasts)


def write_plain_matrix(path, matrix_2d):
    """Write a 2D numpy array as a plain text matrix for Text2Vest."""
    np.savetxt(path, matrix_2d, fmt="%.6f")
    print(f" -> wrote matrix to {path}")


def write_design_sub_modified(path, subject_ids, visit_ids, group_ids):
    """
    Write SwE (modified) design.sub file: 3 columns (subject, visit, group),
    all mapped to integers.
    """
    n = len(subject_ids)
    with open(path, 'w') as f:
        #f.write("/NumWaves       3\n")
        #f.write(f"/NumPoints      {n}\n")
        #f.write("/Matrix\n")

        uniq_subs = {sub: i + 1 for i, sub in enumerate(sorted(set(subject_ids)))}
        uniq_vis = {v: i + 1 for i, v in enumerate(sorted(set(visit_ids)))}
        uniq_grp = {g: i + 1 for i, g in enumerate(sorted(set(group_ids)))}

        for s, v, g in zip(subject_ids, visit_ids, group_ids):
            f.write(f"{uniq_subs[s]:5d} {uniq_vis[v]:5d} {uniq_grp[g]:5d}\n")

    print(f" -> modified SwE design.sub written to {path}")


def check_collinearity_and_export(df_corr, main_variable, output_dir, matrix_name):
    """
    Compute correlation matrix and test collinearity of main_variable vs other columns.
    Save a correlation heatmap and return a flag (1 if any p<0.05).
    """
    corr_warning = 0

    # Ensure numeric
    df_numeric = df_corr.select_dtypes(include=[np.number]).copy()
    if main_variable not in df_numeric.columns:
        print(f"[WARNING] main variable '{main_variable}' not numeric or not in df_corr; "
              f"collinearity check will be limited.")
        cols_for_corr = [c for c in df_numeric.columns]
    else:
        cols_for_corr = [c for c in df_numeric.columns]

    # Full correlation matrix
    corr_matrix = df_numeric.corr()

    # Pairwise tests with main variable
    if main_variable in df_numeric.columns:
        for col in df_numeric.columns:
            if col == main_variable:
                continue
            try:
                r, p = pearsonr(df_numeric[main_variable], df_numeric[col])
            except Exception:
                continue

            if p < 0.05:
                print(f"Warning: {main_variable} is significantly collinear with {col} "
                      f"(p = {p:.4g}, r = {r:.3f})")
                corr_warning = 1

    # Save heatmap
    plt.figure(figsize=(max(8, 0.8 * len(cols_for_corr)), max(6, 0.5 * len(cols_for_corr))))
    sns.heatmap(corr_matrix, annot=True, fmt=".2f", vmin=-1, vmax=1, cmap="coolwarm")
    plt.title(f"Correlation Matrix ({matrix_name})")
    corr_path = os.path.join(output_dir, f"{matrix_name}_correlation_matrix.png")
    plt.tight_layout()
    plt.savefig(corr_path, dpi=300)
    plt.close()
    print(f" -> correlation heatmap saved to {corr_path}")
    try:
        shutil.copy(corr_path, os.path.join(output_dir, f"{matrix_name}_correlation.png"))
    except Exception:
        pass

    return corr_warning


def plot_design_and_contrast(design_df, subject_col, visit_col,
                             design_cols, contrast_matrix,
                             output_dir, matrix_name, analysis_name):
    """
    Create a combined figure with:
      - heatmap of the design matrix (rows = subject_visit, columns = design_cols)
      - contrast table with column names
    """
    # Build row labels: subject_visit
    subj = design_df[subject_col].astype(str).tolist()
    visit = design_df[visit_col].astype(str).tolist()
    row_labels = [f"{s}_{v}" for s, v in zip(subj, visit)]

    # Normalize design matrix per column for visualization
    X = design_df[design_cols].to_numpy(dtype=float)
    X_norm = np.zeros_like(X)
    for j in range(X.shape[1]):
        col = X[:, j]
        cmin = np.nanmin(col)
        cmax = np.nanmax(col)
        if cmax > cmin:
            X_norm[:, j] = (col - cmin) / (cmax - cmin)
        else:
            X_norm[:, j] = 0.0

    n_rows, n_cols = X_norm.shape
    fig_height = max(6, 0.25 * n_rows + 3)
    fig_width = max(8, 0.8 * n_cols)

    fig, (ax1, ax2) = plt.subplots(
        nrows=2,
        figsize=(fig_width, fig_height),
        gridspec_kw={"height_ratios": [3, 1]}
    )

    # Heatmap
    sns.heatmap(
        X_norm,
        ax=ax1,
        cbar=False,
        xticklabels=design_cols,
        yticklabels=row_labels
    )
    ax1.set_xlabel("Design columns")
    ax1.set_ylabel("Subject_Visit")
    ax1.set_title("Design matrix (normalized per column)")

    # Contrast table
    ax2.axis('off')
    table = ax2.table(
        cellText=contrast_matrix,
        colLabels=design_cols,
        loc='center'
    )
    table.auto_set_font_size(True)

    fig.suptitle(f"Analysis: {analysis_name} - Matrix: {matrix_name}", fontsize=14)
    plt.tight_layout(rect=[0, 0, 1, 0.95])

    out_path = os.path.join(output_dir, f"{matrix_name}_design_and_contrast.png")
    plt.savefig(out_path, dpi=300)
    plt.close(fig)
    print(f" -> design+contrast figure saved to {out_path}")
    return out_path


# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------

def main():
    if len(sys.argv) != 5:
        print("Usage: python create_SWE_design_from_excel.py "
              "[ANALYSIS_NAME] [MATRIX_NAME] [MAIN_DIRECTORY] [MAKE_LIST,1/0]")
        sys.exit(1)

    analysis_name = sys.argv[1]
    matrix_name = sys.argv[2]
    main_dir = sys.argv[3]

    try:
        make_list = int(sys.argv[4])
    except (ValueError, IndexError):
        print("Error: MAKE_LIST must be 0 or 1.")
        sys.exit(1)

    output_dir = os.path.join(main_dir, "Results", analysis_name)
    os.makedirs(output_dir, exist_ok=True)

    list_dir = os.path.join(main_dir, "Listes")
    os.makedirs(list_dir, exist_ok=True)

    print(f"Output directory: {output_dir}")
    print(f"List directory:   {list_dir}")
    print("SwE mode: MODIFIED (3-column design.sub)")

    # --- Excel import ---
    excel_file, selected_sheet = select_excel_file_and_sheet()
    if not excel_file or not selected_sheet:
        print("No file or sheet selected.")
        sys.exit(1)

    df = pd.read_excel(excel_file, sheet_name=selected_sheet)
    print("Available columns in the sheet:")
    print(list(df.columns))

    # --- Core columns for longitudinal structure ---
    subject_col = input("Name of SUBJECT column (e.g. SubjID): ").strip()
    visit_col = input("Name of VISIT/TIME column (e.g. Visit, Timepoint): ").strip()
    group_col = input("Name of GROUP column (for modified SwE; leave blank if none/all group 1): ").strip()

    # --- Design covariates ---
    design_cols_input = input(
        "Enter covariates to include in the design (comma-separated), "
        "e.g. Age,Sex,Hormone : "
    )
    design_covars = [c.strip() for c in design_cols_input.split(',') if c.strip()]

    # start from an ordered list
    needed = [subject_col, visit_col] + design_covars
    if group_col:
        needed.append(group_col)

    # remove duplicates while preserving order
    needed = list(dict.fromkeys(needed))

    missing = [c for c in needed if c not in df.columns]
    if missing:
        print(f"Error: the following requested columns are missing in the sheet: {missing}")
        sys.exit(1)

    df = df.loc[:, needed]

    # Drop rows with any NA in these columns
    df = df.dropna().copy()
    # Sort for reproducibility: subject, then visit
    df = df.sort_values([subject_col, visit_col]).reset_index(drop=True)

    print(f"Number of rows after dropping NA: {len(df)}")

    # --- Within/Between decomposition ---
    print("Current covariate columns:", design_covars)
    wb_input = input(
        "Which covariates should be decomposed into WITHIN/BETWEEN? "
        "(comma-separated names, or blank for none): "
    ).strip()
    wb_vars = [c.strip() for c in wb_input.split(',') if c.strip()]

    if wb_vars:
        print("Performing within/between split on:", wb_vars)
    df = build_within_between(df, subject_col, wb_vars)

    # Rebuild covariate list after WB split
    expanded_covars = []
    for c in design_covars:
        if c in wb_vars:
            expanded_covars.extend([f"{c}_B", f"{c}_W"])
        else:
            expanded_covars.append(c)

    # --- Interactions ---
    print("Available columns for interactions (design space so far):", expanded_covars)
    inter_input = input(
        "Specify interaction terms as Var1*Var2, separated by commas "
        "(or leave blank if none; e.g. Time_W*Group): "
    ).strip()
    inter_terms = [t.strip() for t in inter_input.split(',') if t.strip()]

    df = add_interactions(df, inter_terms)

    # Extend covariate list with created interactions
    for spec in inter_terms:
        if '*' in spec:
            a, b = [s.strip() for s in spec.split('*', 1)]
            new_name = f"{a}_x_{b}"
            if new_name in df.columns:
                expanded_covars.append(new_name)

    # --- Build design matrix dataframe (add intercept) ---
    design_matrix_cols = ["Intercept"] + expanded_covars
    design_df = df.copy()
    design_df.insert(0, "Intercept", 1.0)

    print("Final design columns:", design_matrix_cols)

    # --- Choose effects for contrasts and check collinearity ---
    print("Design columns available for contrasts:", design_matrix_cols)
    effects_input = input(
        "Enter columns to test (for t-contrasts), comma-separated "
        "(each will get + and - contrasts; use names from above): "
    ).strip()

    effect_vars = [c.strip() for c in effects_input.split(',') if c.strip()]

    if not effect_vars:
        print("Error: no contrast variables specified.")
        sys.exit(1)

    for v in effect_vars:
        if v not in design_matrix_cols:
            print(f"Error: contrast variable '{v}' is not in design columns.")
            sys.exit(1)

    # Use the first effect variable for collinearity diagnostics
    main_effect_for_corr = effect_vars[0]

    # Correlation diagnostics (exclude subject/visit/group and optional Intercept if desired)
    corr_cols = [c for c in design_matrix_cols if c != "Intercept"]
    df_corr = design_df[corr_cols]
    corr_flag = check_collinearity_and_export(df_corr, main_effect_for_corr, output_dir, matrix_name)

    if corr_flag == 1:
        go = input("Main effect is collinear with some covariates (p<0.05). Continue anyway? [Y/n]: ")
        if go.lower() == 'n':
            print("Aborting as requested due to collinearity.")
            sys.exit(1)

    # --- Write design matrix ---
    design_path_vest = os.path.join(output_dir, f"{matrix_name}_mat.txt")
    write_plain_matrix(design_path_vest, design_df[design_matrix_cols].to_numpy())

    # --- Contrasts ---
    contrast_mat = create_t_contrasts(design_matrix_cols, effect_vars)
    print("Generated t-contrasts (rows):")
    row_idx = 1
    for var in effect_vars:
        print(f"  C{row_idx}:   {var} > 0")
        print(f"  C{row_idx+1}: {var} < 0")
        row_idx += 2
    contrast_path_vest = os.path.join(output_dir, f"{matrix_name}_con.txt")
    write_plain_matrix(contrast_path_vest, contrast_mat)

    # --- design.sub (modified SwE) ---
    sub_ids = list(design_df[subject_col])
    visit_ids = list(design_df[visit_col])
    if group_col:
        group_vals = list(design_df[group_col])
    else:
        group_vals = [1] * len(design_df)

    design_sub_path = os.path.join(output_dir, f"{matrix_name}_design_sub.txt")
    write_design_sub_modified(design_sub_path, sub_ids, visit_ids, group_vals)

    # --- Subject/visit list (for 4D card) ---
    if make_list:
        list_path = os.path.join(list_dir, f"List_{analysis_name}.txt")
        with open(list_path, 'w') as f:
            for s, v in zip(sub_ids, visit_ids):
                # Convert visit to string and ensure it starts with 'V'
                v_str = str(v).strip()
                if not v_str.startswith('V'):
                    v_str = f"V{v_str}"
                # Concatenate subject and visit with underscore
                combined = f"{s}_{v_str}"
                f.write(f"{combined}\n")
        print(f"Subject/visit list saved to {list_path}")

    # --- Visualisation: design heatmap + contrast ---
    design_contrast_img = plot_design_and_contrast(
        design_df=design_df,
        subject_col=subject_col,
        visit_col=visit_col,
        design_cols=design_matrix_cols,
        contrast_matrix=contrast_mat,
        output_dir=output_dir,
        matrix_name=matrix_name,
        analysis_name=analysis_name
    )

    visualisation_copy = os.path.join(output_dir, f"{matrix_name}_visualisation.png")
    try:
        shutil.copy(design_contrast_img, visualisation_copy)
    except Exception as exc:
        print(f"[WARN] Could not copy design visualisation to {visualisation_copy}: {exc}")

    # --- Export a CSV for downstream R summary ---
    csv_cols = []
    for col in [subject_col, visit_col, group_col] + design_matrix_cols:
        if col and col not in csv_cols:
            csv_cols.append(col)
    csv_path = os.path.join(output_dir, f"{matrix_name}.csv")
    csv_df = design_df.loc[:, csv_cols].copy()
    rename_map = {}
    if subject_col:
        rename_map[subject_col] = "subject"
    if visit_col:
        rename_map[visit_col] = "visit"
    if group_col:
        rename_map[group_col] = "group"
    csv_df.rename(columns=rename_map, inplace=True)
    csv_df.to_csv(csv_path, sep="\t", index=False)
    print(f"Data for R exported to {csv_path}")

    print("All done.")


if __name__ == "__main__":
    main()
