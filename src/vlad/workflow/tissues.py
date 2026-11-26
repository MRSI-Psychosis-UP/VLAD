"""Validate optional tissue masks for guided report histograms."""
from pathlib import Path

import nibabel as nib
from nibabel.processing import resample_from_to
import numpy as np

from vlad.gui.templates import tissue_probabilities
from vlad.preparation.bids import image_entities


TISSUES = {'gm': 'Gray matter (GM)', 'wm': 'White matter (WM)'}


def tissue_mask_paths(mapping, space):
    """Resolve a complete pair and check any declared BIDS space."""
    if not any(mapping.values()):
        return {}
    result = {}
    for key, label in TISSUES.items():
        path = Path(mapping.get(key, '')).expanduser()
        if not path.is_file() or not path.name.endswith(('.nii', '.nii.gz')):
            raise ValueError(f'{label}: choose an existing binary NIfTI. The report needs both tissue masks.')
        declared_space = image_entities(path).entities.get('space')
        if space and declared_space and declared_space != space.removeprefix('space-'):
            raise ValueError(f'{label}: mask filename has a different space- label.')
        result[key] = str(path.resolve())
    return result


def report_tissue_inputs(plan):
    space = plan['data'].get('space', '').removeprefix('space-')
    if masks := tissue_mask_paths(plan.get('tissue_masks', {}), space):
        return masks, False
    if saved := plan.get('tissue_probabilities'):
        return tissue_mask_paths(saved, space), True
    return tissue_probabilities(space), True


def load_mask_image(path, label):
    """Read a 3D mask header and validate its affine."""
    try:
        image = nib.load(path)
    except nib.filebasedimages.ImageFileError as exc:
        raise ValueError(f'{label}: unable to read NIfTI mask {path}.') from exc
    if len(image.shape) != 3 or not np.isfinite(image.affine).all() or np.linalg.matrix_rank(image.affine[:3, :3]) != 3:
        raise ValueError(f'{label}: the mask must be 3D with a valid affine.')
    return image


def load_white_matter_image(path):
    """Read the header of a 3D white-matter NIfTI."""
    return load_mask_image(Path(path).expanduser(), TISSUES['wm'])


def _align_binary(data, image, reference):
    if image.shape != reference.shape or not np.allclose(image.affine, reference.affine, atol=1e-4, rtol=0):
        binary = nib.Nifti1Image(data.astype(np.uint8), image.affine)
        data = np.asanyarray(resample_from_to(binary, (reference.shape, reference.affine), order=0).dataobj)
    return data.astype(bool)


def align_white_matter(path, reference, *, probability=False):
    """Binarize a WM probability map or sample a binary WM mask on the DWI grid."""
    image = load_white_matter_image(path)
    values = image.get_fdata(dtype=np.float32)
    if probability:
        if not np.isfinite(values).all() or values.min() < 0 or values.max() > 1:
            raise ValueError('White-matter probabilities must be finite and between 0 and 1.')
        values = values >= .5
    elif not np.isin(values, (0, 1)).all():
        raise ValueError('The white-matter mask must be binary (0 or 1).')
    aligned = _align_binary(values, image, reference)
    if not aligned.any():
        raise ValueError('The white-matter mask is empty on the DWI grid. Check the registration space and mask.')
    return aligned


def align_tissue_masks(paths, reference, *, probabilities=False):
    """Sample binary masks on the reference grid using their existing affines."""
    images = {key: load_mask_image(path, TISSUES[key]) for key, path in paths.items()}
    values = {key: image.get_fdata(dtype=np.float32) for key, image in images.items()}
    if probabilities:
        gm, wm = images['gm'], images['wm']
        if gm.shape != wm.shape or not np.allclose(gm.affine, wm.affine, atol=1e-4, rtol=0):
            raise ValueError('TemplateFlow GM and WM probability maps must share a grid.')
        if any(not np.isfinite(data).all() or data.min() < 0 or data.max() > 1 for data in values.values()):
            raise ValueError('TemplateFlow tissue probabilities must be finite and between 0 and 1.')
        gm_values, wm_values = values['gm'], values['wm']
        values = {'gm': (gm_values >= .5) & (gm_values > wm_values),
                  'wm': (wm_values >= .5) & (wm_values > gm_values)}
    aligned = {}
    for key, image in images.items():
        label = TISSUES[key]
        data = values[key]
        if not probabilities and (not np.isin(data, (0, 1)).all() or not data.any()):
            raise ValueError(f'{label}: the tissue mask must be nonempty and binary (0 or 1).')
        aligned[key] = _align_binary(data, image, reference)
    if aligned and np.any(aligned['gm'] & aligned['wm']):
        raise ValueError('GM and WM tissue masks overlap on the analysis grid. Use non-overlapping binary masks.')
    return aligned
