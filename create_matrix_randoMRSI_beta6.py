#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Dec 26 20:02:45 2023

@author: edgar

Creation of Matrix files for RandoMRSI, directly from an Excel file. 
Beta 1 : first stable release, integrated in RandoMRSI
Beta 2 : Added the NA suppression in the dataframe, will also be displayed to be sure that everything is ok
Beta 3 : Added checking for correlations between main variable and the others, with warning etc.
Beta 4 : Little refinments to pass good csv to R
Beta 5 : Adding possibility to do ANOVAs : working
Beta 6 : Adding the renaming of columns for group. Corrections for binary analysis : any binary variable works now. ==> Working great. 

Important notes : 
    Finally, we are only creating .txt files that will be transformed via a specific function of the fsl package (see the bash code)
    Formating is a bit different, randomise is not working otherwise...
    No more intercept for quantitative data, this is useless

Matrix visualisation should work ! :-) 
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

def select_excel_file_and_sheet():
    """ Open a dialog to select an Excel file and then ask the user to select a sheet. """
    root = tk.Tk()
    root.withdraw()  # Hide the main window
    file_path = filedialog.askopenfilename(filetypes=[("Excel files", "*.xlsx")])

    if not file_path:  # Check if a file was selected
        return None, None

    # Load the workbook without any data to get the sheet names
    xls = pd.ExcelFile(file_path)
    sheet_names = xls.sheet_names

    # Display sheet names and ask the user to choose one
    print("Available sheets:", sheet_names)
    selected_sheet = input("Enter the name of the sheet to use: ").strip()

    return file_path, selected_sheet

def format_row(row):
    """ Format the row values as a space-separated string without brackets. """
    return ' '.join(map(str, row))

# def is_binary(series):
#     """ Check if a pandas series contains only 1s and 0s. """
#     return set(series.unique()).issubset({0, 1})

def is_binary(column):
    return column.nunique() == 2

def create_contrast_matrix_ttest(df_selected, main_variable, is_binary):
    """ Create the contrast matrix based on the type of the main variable. Only for t-tests """
    num_columns = len(df_selected.columns)
    main_var_index = df_selected.columns.get_loc(main_variable)

    if is_binary:
        # For binary variable: two rows with +1 and -1 in main and mirror variables
        matrix = np.zeros((2, num_columns))
        matrix[0, main_var_index] = 1  # +1 in main variable
        matrix[0, main_var_index + 1] = -1  # -1 in mirror variable
        matrix[1, main_var_index] = -1  # -1 in main variable
        matrix[1, main_var_index + 1] = 1  # +1 in mirror variable
    else:
        # For non-binary variable: three rows with 1 and -1 in main variable
        matrix = np.zeros((2, num_columns))
        matrix[0, main_var_index] = 1  # +1 in main variable
        matrix[1, main_var_index] = -1  # -1 in main variable

    return matrix

def create_contrast_matrix_anova(df_selected, names_main_matrix):
    """Create the contrast matrix for ANOVA matrix """
    num_columns = len(df_selected.columns)

    # Number of rows in the contrast matrix (same as the length of the legend)
    columns_anova = len(names_main_matrix)

    # Create the matrix with zeros: dimensions (columns_anova x num_columns)
    matrix = np.zeros((columns_anova, num_columns))

    # Fill the matrix with 1s in the appropriate locations
    for i, category in enumerate(names_main_matrix):
        # Find the index of the corresponding column in df_selected
        column_index = df_selected.columns.get_loc(category)
        matrix[i, column_index] = 1

    return matrix

def write_contrast_file(output_dir, matrix_name, contrast_matrix):
    """ Write the contrast matrix to a .con file. """
    output_file = os.path.join(output_dir, f"{matrix_name}_con.txt")

    with open(output_file, 'w') as file:
        num_columns, num_rows = contrast_matrix.shape
        # file.write(f"/Numwaves {num_columns}\n")
        # file.write(f"/NumContrasts {num_rows}\n")
        # file.write("/Matrix\n")

        for row in contrast_matrix:
            file.write(' '.join(map(str, row)) + '\n')

    print(f"Contrast data exported to {output_file}")

def write_anova_file(legend, file_name):
    """Write a file with as many '1's as there are elements in the legend, separated by spaces."""
    num_waves = len(legend)
    
    # Create a string with '1' repeated for each element in the legend, separated by spaces
    ones_line = ' '.join(['1'] * len(legend))
    
    # Write to the specified file
    with open(file_name, 'w') as file:
        file.write(f"/NumWaves {num_waves}\n")
        file.write(f"/NumContrasts 1\n")
        file.write(f"\n")
        file.write("/Matrix\n")
        file.write(ones_line + '\n')
    
    print(f"File for f-test exported to {file_name}")

def create_heatmap(df_selected, output_dir, matrix_name):
    num_rows = df_selected.shape[0]
    num_columns = df_selected.shape[1]
    
    # Create a new DataFrame for normalized data
    normalized_df = pd.DataFrame()

    # Normalize each column
    for col in df_selected.columns:
        col_data = df_selected[col].fillna(df_selected[col].min() - 1)
        col_min = col_data.min()
        col_max = col_data.max()
        if col_max > col_min:
            normalized_df[col] = (col_data - col_min) / (col_max - col_min)
        else:
            normalized_df[col] = pd.Series(0, index=col_data.index)
    
    cell_size_width = 1
    cell_size_height = 0.2
    fig_width = num_columns * cell_size_width
    fig_height = num_rows * cell_size_height
    
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))

    sns.heatmap(normalized_df, cmap="coolwarm", cbar=False, ax=ax,
                xticklabels=normalized_df.columns, yticklabels=True, annot=False)

    # Set the x-ticks to be the column names
    ax.set_xticks(np.arange(num_columns) + 0.5)
    ax.set_xticklabels(normalized_df.columns, rotation=45)

    # Ensure the ticks are evenly spaced and labels are readable
    ax.set_yticks(np.arange(num_rows) + 0.5)
    ax.set_yticklabels(df_selected.index, rotation=0)
    
    
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, f"{matrix_name}_heatmap.png"), bbox_inches='tight', dpi=300) # This intermediate figure isn't save anymore, only here for debuging 
    plt.close()

    return fig, ax


def create_contrast_table(contrast_matrix, column_labels, matrix_name):
    num_columns = contrast_matrix.shape[1]
    contrast_height = contrast_matrix.shape[0]
    
    cell_size_width = 1
    cell_size_height = 0.7
    fig_width = num_columns * cell_size_width
    fig_height = contrast_height * cell_size_height
    
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))

    # Display the contrast matrix as a table
    ax.axis('tight')
    ax.axis('off')
    table = ax.table(cellText=contrast_matrix, colLabels=column_labels, loc='center')
    table.auto_set_font_size(True)
    # table.set_fontsize(8)
    # table.scale(1, 1.5)  # Adjust table size
    
    
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, f"{matrix_name}_contrast.png"), bbox_inches='tight', dpi=300)
    plt.close()
    
    return fig, ax

def combine_heatmap_and_contrast(df_selected, contrast_matrix, output_dir, matrix_name, analysis_name):
    # Create heatmap and contrast table, but don't save or close the figures
    fig_heatmap, ax_heatmap = create_heatmap(df_selected, output_dir, matrix_name)
    fig_table, ax_table = create_contrast_table(contrast_matrix, df_selected.columns, matrix_name)
    
    # Create a new figure that will combine both the heatmap and the contrast table
    combined_height = fig_heatmap.get_figheight() + fig_table.get_figheight()
    combined_fig, combined_ax = plt.subplots(figsize=(fig_heatmap.get_figwidth(), combined_height))
    
    # Remove the axes for the combined figure
    combined_ax.axis('off')
    
    # Copy the heatmap to the combined figure
    combined_ax_heatmap = combined_fig.add_axes(ax_heatmap.get_position())
    combined_ax_heatmap.axis('off')
    combined_fig.figimage(fig_heatmap.canvas.buffer_rgba(), xo=0, yo=fig_table.get_figheight()*fig_table.dpi)

    # Copy the table to the combined figure
    combined_ax_table = combined_fig.add_axes(ax_table.get_position(), anchor='NE', zorder=-10)
    combined_ax_table.axis('off')
    combined_fig.figimage(fig_table.canvas.buffer_rgba(), xo=0, yo=0, zorder=-1)
    
    #Title
    title_y_position = 0  # Adjust this as needed  # Adjust this as needed
    combined_title = f"Analysis :{analysis_name} - Matrix :{matrix_name}"
    combined_fig.suptitle(combined_title, fontsize=16, y=title_y_position)
    
    # Save the combined figure
    # plt.tight_layout()
    combined_figure_path = os.path.join(output_dir, f"{matrix_name}_visualisation.png")
    plt.savefig(combined_figure_path, bbox_inches='tight', dpi=300, pad_inches=0.1)
    
    # Close all the figures to free memory
    
    plt.close(fig_heatmap)
    plt.close(fig_table)
    plt.close(combined_fig)
    

def check_collinearity_and_export(df_selected, main_variable, output_dir, matrix_name):
    # Step 4: Create a correlation matrix
    correlation_matrix = df_selected.corr()
    
    correlation_warning = 0

    # Create a dictionary to store p-values
    p_values = {}

    # Step 5: Check for collinearity and generate warnings
    for col in df_selected.columns:
        if col != main_variable:
            correlation_coefficient, p_value = pearsonr(df_selected[main_variable], df_selected[col])

            #Store p-value
            p_values[col] = p_value

            if p_value < 0.05:
                print(f"Warning: {main_variable} is significantly collinear with {col} (p-value = {p_value}, correlation coefficient = {correlation_coefficient})")
                correlation_warning = 1                                       
            
            #Old way of checking for correlation
            # if abs(correlation_coefficient) > 0.5: 
            #     print(f"Warning: {main_variable} is collinear with {col} (correlation coefficient = {correlation_coefficient})")
            #     correlation_warning = 1

    # Step 6: Export the correlation matrix as a PNG file
    
    combined_figure_path = os.path.join(output_dir, f"{matrix_name}_correlation_matrix.png")
    plt.figure(figsize=(10, 8))
    sns.heatmap(correlation_matrix, annot=True, cmap='coolwarm', fmt='.2f', vmin=-1, vmax=1)
    plt.title('Correlation Matrix')
    plt.savefig(combined_figure_path, bbox_inches='tight', dpi=300, pad_inches=0.1)
    #plt.show()
    
    return correlation_warning
    
def transform_column(df, column_name):
    # Get the column values
    column_values = df[column_name].unique()
    column_values.sort()

    # Test the range of these integers
    if len(column_values) < 3:
        raise ValueError("The range of integers is less than 3. Exiting...")

    # Store the index of the first new column (localisation)
    first_column_index = len(df.columns)

    # Create new columns based on the range of the integers
    for value in column_values:
        df[f'Category_{value}'] = (df[column_name] == value).astype(int)

    # Prompt the user for the legend (categories)
    legend = []
    for value in column_values:
        category = input(f"Please provide the category name for {column_name} {value}: ")
        legend.append(category)

    # Rename the new columns with the prompted names
    df.rename(columns={f'Category_{value}': legend[i] for i, value in enumerate(column_values)}, inplace=True)

    # Drop the original column
    df.drop(column_name, axis=1, inplace=True)

    # Return the localisation, range of integers, and legend
    return df, legend

def read_and_export_columns(file_path, selected_sheet, output_dir, matrix_name, analysis_name):
    """ Read user-specified columns from an Excel file and write them to a text file. """
    # Load the selected sheet from the Excel file
    df = pd.read_excel(file_path, sheet_name=selected_sheet)
    
    # Display column headers
    print("Available columns:", df.columns.tolist())
    
    # Ask user for columns to copy
    columns = input("Enter column names to copy, separated by commas, no spaces (e.g., Age,Sex,GAF): ").split(',')
    columns = [col.strip() for col in columns]  # Remove any extra whitespace

    # Select specified columns
    df_selected = df[columns]

    # Ask for the main variable
    main_variable = input("Enter the name of the main variable: ").strip()
    if main_variable not in df_selected.columns:
        print(f"Column {main_variable} not found in the selected columns.")
        sys.exit(1)

    main_contrast_place = df_selected.columns.get_loc(main_variable) + 1  # 1-indexed

    # Identify and display rows with NA values and store the indices to delete them in df
    rows_with_na = df_selected[df_selected.isna().any(axis=1)]
    
    na_indices = df_selected[df_selected.isna().any(axis=1)].index
    
    # Remove rows with NA values
    df_selected = df_selected.dropna()

    # Display the number of deleted rows
    num_deleted_rows = len(rows_with_na)
    print(f"\nNumber of rows deleted: {num_deleted_rows}")

    # Confirm the remaining dataframe
    print("\nDataFrame after deleting rows with NA values:")
    print(df_selected)

    #Reset the index of the rows since we delete the NAs : 
    df_selected = df_selected.reset_index(drop=True)
    df_selected.index = df_selected.index + 1
    
    
    # Checking for collinerarity
    correlation_warning = check_collinearity_and_export(df_selected, main_variable, output_dir, matrix_name)
    
    if correlation_warning == 1:
        go = input("So, your main variable seems to be collinear with another one, so there is an important probability that this doesn't give you any results. Continue ? Y/n")
        if go.lower() == 'y':
            print("Ok let's continue then")
        else:
            print("Let's start again then")
            sys.exit(1)

    names_main_matrix=[]
    if anova == 1:
        df_selected, names_main_matrix = transform_column(df_selected, main_variable)
    
    if anova == 0:
        # Check if the main variable is binary or not
        if is_binary(df_selected[main_variable]):
            # Assuming x and y are the two unique values in the column
            x, y = df_selected[main_variable].unique()

            # Create a mapping to swap x and y
            swap_mapping = {x: y, y: x}

            # Add mirror column using the mapping
            df_selected.insert(
                main_contrast_place,
                main_variable + '_mirror',
                df_selected[main_variable].map(swap_mapping)
            )
            # # Add mirror column -> old way
            # df_selected.insert(main_contrast_place, main_variable + '_mirror', 1 - df_selected[main_variable])
        else:
            # Add a column of 1s at the beginning and adjust main_contrast_place
            df_selected.insert(0, 'Intercept', 1)
            main_contrast_place += 1

    if make_list == 1:

        # Export the list of subjects
        print("You asked to create a list of subjects. Let's go")
        subject_list_name = f"List_{name}.txt"
        subject_column = "Nom_dossier"  # Default column name for subjects
        if subject_column not in df.columns:
            subject_column = input("Enter the name of the column where the subjects are: ").strip()
            if subject_column not in df.columns:
                print(f"Column {subject_column} not found in the DataFrame.")
                sys.exit(1)

        df_cleaned = df.drop(na_indices)
        subject_list_file = os.path.join(output_dir_list, subject_list_name)
        with open(subject_list_file, 'w') as file:
            file.write("\n".join(df_cleaned[subject_column].astype(str)))  # Write subject list to file

    elif make_list != 0:
        print("Unrecognized value for 'make_list'. It should be either 0 or 1.")

    # Create directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)

    # Output file path
    output_file = os.path.join(output_dir, f"{matrix_name}_mat.txt")

    # Write to a text file
    with open(output_file, 'w') as file:
       
        # Write data
        for index, row in df_selected.iterrows():
            file.write(format_row(row) + '\n')
        
    # Create contrast matrix
    if anova == 0:
        binary_check = is_binary(df_selected[main_variable])
        contrast_matrix = create_contrast_matrix_ttest(df_selected, main_variable, binary_check)
    else:
        contrast_matrix = create_contrast_matrix_anova(df_selected, names_main_matrix)

    # Write contrast file
    write_contrast_file(output_dir, matrix, contrast_matrix)

    #Write fts file for ANOVA if needed
    if anova == 1:
        output_file_anova = os.path.join(output_dir, f"{matrix_name}.fts")
        write_anova_file(names_main_matrix, output_file_anova)
    
    #Doing the heatmap
    combine_heatmap_and_contrast(df_selected, contrast_matrix, output_dir, matrix_name, analysis_name)
    
    
    print(f"Data exported to {output_file}")

    #New thing to make name directly the groups of a binary analysis
    if anova == 0:
        if is_binary(df_selected[main_variable]):
            # Get the unique values from the original column
            x, y = df_selected[main_variable].unique()

            # Ensure x is the greatest value
            if y > x:
                x, y = y, x

            # Prompt the user for new names
            new_name_x = input(f"Enter the group name of the value {x} in the column '{main_variable}': ")
            new_name_y = input(f"Enter the group name of the value {y} in the column '{main_variable}': ")

            # Rename the columns in the DataFrame
            df_selected.rename(columns={
                main_variable: new_name_x,
                main_variable + '_mirror': new_name_y
            }, inplace=True)

            names_main_matrix=[new_name_x, new_name_y]

            print("Columns renamed successfully.")


    # Also write data with column names to a CSV file for R
    csv_output_file = os.path.join(output_dir, f"{matrix_name}.csv")
    df_selected.to_csv(csv_output_file, index=False, sep='\t')
    print(f"Data for R exported to {csv_output_file}")

    return main_variable, names_main_matrix



# Main process
if __name__ == "__main__":
    if len(sys.argv) != 6:
        print("Usage: python script.py [ANALYSIS_NAME] [MATRIX_NAME] [MAIN DIRECTORY] [CREATE_LIST,1/0] [ANOVA,1/0]")
        sys.exit(1)

    name = sys.argv[1]
    matrix = sys.argv[2]
    main_dir = sys.argv[3]
    output_dir = f"{main_dir}/Results/{name}"
    try:
        make_list = int(sys.argv[4])
    except (IndexError, ValueError):
        print("Error: 'make_list' must be a valid integer.")
        sys.exit(1)
    try:
        anova = int(sys.argv[5])
    except (IndexError, ValueError):
        print("Error: 'make_list' must be a valid integer.")
        sys.exit(1)
    output_dir_list = f"{main_dir}/Listes"

    if anova:
        print("Careful, you've selected ANOVA. Make sure your Excel is formated with 1 column for the ANOVA, with each group corresponding to a number")
    excel_file, selected_sheet = select_excel_file_and_sheet()
    if excel_file and selected_sheet:
        main_variable, names_main_matrix = read_and_export_columns(excel_file, selected_sheet, output_dir, matrix, name)
        # Write the main variable to a text file in the same directory
        main_variable_file = os.path.join(output_dir, f"main_variable_{matrix}.txt")
        with open(main_variable_file, 'w') as temp_file:
            if len(names_main_matrix) == 0:
                temp_file.write(main_variable)
            else:
                for item in names_main_matrix:
                    temp_file.write(item + '\n')

        print(f"Main variable saved to {main_variable_file}")
    else:
        print("No file selected.")
       



