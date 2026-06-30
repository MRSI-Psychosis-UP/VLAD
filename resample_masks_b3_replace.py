#!/usr/bin/env python3
import os
import argparse
import shutil
from nilearn.image import load_img, resample_img, index_img
from nilearn.masking import unmask
from pathlib import Path

def resample_masks_to_t1_mni(mask_paths, t1_mni_path, backup_dirname='original',
                             interpolation='nearest', replace=False):
    """
    Resample a list of mask images to the space of a T1_MNI reference.

    3D/4D behavior:
    - If `t1_mni_path` is a 4D image, the **first volume (index 0)** is used as
      the spatial reference (affine + shape).
    - If any mask image is 4D, **all volumes** are resampled to the target grid
      (time dimension preserved).

    Replacement behavior:
    - If `replace` is True: move the original file into a sibling folder named
      `backup_dirname` (default: "original") and write the resampled image back
      to the **same filepath** (in-place replacement).
    - If `replace` is False: write the resampled image **alongside** the original
      using the same name suffixed with "-resampled" (e.g., `mask-resampled.nii.gz`).
      The extension handling preserves multi-part extensions like `.nii.gz`.

    Parameters
    ----------
    mask_paths : list of str
        Filepaths to the mask images you want to resample (3D or 4D).
    t1_mni_path : str
        Filepath to the target T1_MNI image (3D or 4D; if 4D, first vol is used).
    backup_dirname : str, optional
        Name of the directory (created next to each mask) where the original
        mask file will be moved before replacement. Defaults to "original".
    interpolation : {'nearest', 'continuous'}, optional
        Interpolation to use: 'nearest' for labels/masks, 'continuous' for
        probabilistic maps or continuous data.
    replace : bool, optional
        If True, replace originals in place (with backup). If False, write
        alongside as "-resampled". Default is False.
    """
    # Load target reference (support 3D/4D by taking first volume when 4D)
    t1_mni = load_img(t1_mni_path)
    if len(t1_mni.shape) == 4:
        ref_img = index_img(t1_mni, 0)
        print(f"T1 reference is 4D; using first volume as reference: {t1_mni_path}")
    else:
        ref_img = t1_mni

    target_affine = ref_img.affine
    target_shape = ref_img.shape[:3]

    for mask_path in mask_paths:
        p = Path(mask_path)

        # Load original mask (3D or 4D) and compute the resampled image first
        mask_img = load_img(mask_path)
        resampled = resample_img(
            mask_img,
            target_affine=target_affine,
            target_shape=target_shape,
            interpolation=interpolation,
            force_resample=True,
            copy_header=True
        )

        if replace:
            # Prepare backup directory and unique backup path
            backup_dir = p.parent / backup_dirname
            os.makedirs(backup_dir, exist_ok=True)
            backup_path = backup_dir / p.name

            # If a file with the same name already exists in backup, append an incrementing suffix
            if backup_path.exists():
                suffixes = ''.join(p.suffixes)
                stem = p.name[:-len(suffixes)] if suffixes else p.name
                i = 1
                while True:
                    candidate = backup_dir / f"{stem}_orig{i}{suffixes}"
                    if not candidate.exists():
                        backup_path = candidate
                        break
                    i += 1

            # Move the original file to the backup location, then write the resampled image in place
            shutil.move(str(p), str(backup_path))
            resampled.to_filename(str(p))

            print(f"Backed up original to: {backup_path}")
            print(f"Wrote resampled mask in place: {p}")
        else:
            # Write alongside with "-resampled" suffix, preserving multi-part extensions
            suffixes = ''.join(p.suffixes)
            stem = p.name[:-len(suffixes)] if suffixes else p.name
            out_path = p.parent / f"{stem}-resampled{suffixes}"

            # If such a file already exists, append an incrementing suffix to avoid overwrite
            if out_path.exists():
                i = 1
                while True:
                    candidate = p.parent / f"{stem}-resampled{i}{suffixes}"
                    if not candidate.exists():
                        out_path = candidate
                        break
                    i += 1

            resampled.to_filename(str(out_path))
            print(f"Wrote resampled mask to: {out_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Resample mask images to T1 MNI space (3D/4D). By default, writes alongside originals with '-resampled'; use --replace to overwrite in place with backup.")
    parser.add_argument("--masks", nargs="+", required=True, help="Paths to mask images to resample (3D or 4D).")
    parser.add_argument("--t1", dest="t1_mni", required=True, help="Path to the T1 MNI reference image (3D or 4D; if 4D, first volume is used).")
    parser.add_argument("--backup-dirname", dest="backup_dirname", default="original", help="Name of the directory (created next to each mask) to store the original mask before replacement (default: original).")
    parser.add_argument("--replace", action="store_true", help="Replace originals in place (with backup to --backup-dirname). If not set, write alongside with '-resampled'.")
    parser.add_argument("--interp", choices=["nearest", "continuous"], default="nearest", help="Interpolation: nearest for masks, continuous for maps.")
    args = parser.parse_args()

    mask_paths = args.masks
    t1_mni_path = args.t1_mni

    resample_masks_to_t1_mni(mask_paths, t1_mni_path, backup_dirname=args.backup_dirname, interpolation=args.interp, replace=args.replace)
