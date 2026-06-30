#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sat Jan  6 00:26:07 2024

@author: edgar and mr GPT
"""


import numpy as np
import nibabel as nib
from nilearn import plotting
import matplotlib.pyplot as plt
import matplotlib as mpl
import sys
import os
from pathlib import Path

# Check if required arguments are passed (1: statistical_map_img, 2: image name, 3: colormap, 4: anatomical image)
if len(sys.argv) != 5:
    print("Usage: python script_name.py <path_to_statistical_map> <output_image_name> <colormap> <path_anatomical_image>")
    sys.exit(1)

# Load the statistical map
statistical_map_img = sys.argv[1]
anatomical_img = sys.argv[4]
stat_map_img = nib.load(statistical_map_img)
stat_map_data = stat_map_img.get_fdata()

output_image_name = sys.argv[2]
output_image_path_obj = Path(output_image_name)
if output_image_path_obj.suffix == "":
    output_image_name = f"{output_image_name}.png"
colormap = sys.argv[3]
# Flip the X-axis data to match the radiological orientation
stat_map_data = np.flip(stat_map_data, axis=0)

# Create a new NIfTI image with the flipped data
flipped_stat_map_img = nib.Nifti1Image(stat_map_data, stat_map_img.affine)


# Define the threshold
threshold = 0.95

# Create a figure to hold all subplots (12 slices * 3 orientations = 36 plots)
fig, axes = plt.subplots(3, 9, figsize=(24, 8))  # Adjust figsize as necessary
# axes = axes.flatten()  # Flatten the axes array for easy iteration

def generate_coordinates(index):
    # Calculate which set of 12 the index belongs to (0-based)
    set_index = (index - 1) // 9
    # Calculate the position within the set of 12
    position_within_set = (index - 1) % 9
    # Calculate row and column within the 4x3 grid
    row = position_within_set // 3
    column = position_within_set % 3
    # Adjust the column based on the set index
    column += set_index * 3
    return row, column

# Helper function to plot slices in a given orientation and indices
def plot_slices(orientation, axes, start_index):
    affine = flipped_stat_map_img.affine
    data = flipped_stat_map_img.get_fdata()

    # Determine the axis index for the given orientation
    if orientation == 'x':
        axis_index = 0
    elif orientation == 'y':
        axis_index = 1
    else:  # 'z'
        axis_index = 2

    # Get the indices where the data exceeds the threshold
    significant_indices = np.where(data > threshold)

    # Get the unique indices along the orientation axis
    unique_indices = np.unique(significant_indices[axis_index])

    # If there are significant voxels, proceed
    if len(unique_indices) > 0:
        # Convert indices to coordinates
        coords = nib.affines.apply_affine(affine, np.eye(4)[:3, :3][axis_index] * unique_indices[:, np.newaxis])

        min_coord = coords.min()
        max_coord = coords.max()

        # Compute the range and the central 80%
        coord_range = max_coord - min_coord
        adjusted_min = min_coord + 0.4 * coord_range
        adjusted_max = max_coord - 0.1 * coord_range

        # Check if significant coordinates cover at least the central 80%
        sig_coords = coords[(coords >= adjusted_min) & (coords <= adjusted_max)]
        if len(sig_coords) > 0:
            min_plot = adjusted_min
            max_plot = adjusted_max
        else:
            min_plot = min_coord
            max_plot = max_coord

        # Generate 12 slices within the determined range
        slices = np.linspace(min_plot, max_plot, 9)

        # Choose colormap
    if colormap == '1':
        cmap = 'Reds'
    elif colormap == '2':
        cmap = 'Blues'
    else:
        print("Invalid colormap argument. Please use 1 for Reds or 2 for Blues.")
        cmap = 'gray'
        #row = 0
    for i, slice_coord in enumerate(slices):
        ax_idx = start_index + i +1
            
        row, col = generate_coordinates(ax_idx)
        current_ax = axes[row, col]  # Access the subplot using row and col

            
        plotting.plot_stat_map(flipped_stat_map_img, bg_img=anatomical_img,
                                   threshold=threshold, display_mode=orientation,
                                   cut_coords=[slice_coord], cmap=cmap, vmin=0.95, vmax=1,
                                   axes=current_ax, colorbar=False)
        current_ax.axis('off')    


# Calculate indices for each orientation
x_indices_above_threshold = np.where(flipped_stat_map_img.get_fdata().max(axis=(1, 2)) > threshold)[0]
y_indices_above_threshold = np.where(flipped_stat_map_img.get_fdata().max(axis=(0, 2)) > threshold)[0]
z_indices_above_threshold = np.where(flipped_stat_map_img.get_fdata().max(axis=(0, 1)) > threshold)[0]

# Plot slices for each orientation
plot_slices('x', axes, start_index=0)   # For X orientation, starting at index 0
plot_slices('y', axes, start_index=9)  # For Y orientation, starting at index 12
plot_slices('z', axes, start_index=18)  # For Z orientation, starting at index 24

if colormap == '1':
    cmap = 'Reds'
elif colormap == '2':
    cmap = 'Blues'
else:
    print("Invalid colormap argument. Please use 1 for Reds or 2 for Blues.")
#Instead of using display._cbar, create a ScalarMappable for the colorbar
norm = mpl.colors.Normalize(vmin=0.95, vmax=1)
sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
sm.set_array([])  # You can set an empty array since the colorbar doesn't need actual data here
  
# Create a new axes on the right side of the figure for the global colorbar
colorbar_ax = fig.add_axes([0.92, 0.15, 0.02, 0.7])  # Adjust the position and size as needed
fig.colorbar(sm, cax=colorbar_ax)

# Extract base directory (first three parts of the path)
base_directory = Path(statistical_map_img).parts[:-3]
base_directory_path = Path(*base_directory)

# Ensure the output directory exists
os.makedirs(base_directory_path, exist_ok=True)

# Save the plot to the base directory with the given image name
output_image_path = base_directory_path / output_image_name

plt.subplots_adjust(wspace=0, hspace=0)

plt.savefig(output_image_path, dpi=300, bbox_inches='tight', pad_inches=0.1)
plt.close()
#plt.show()



