"""Voxelwise gauge correction shared by the guided workflow and tests."""

from __future__ import annotations

import numpy as np


def gauge_from_group_means(group_mean):
    """Compute spatial z maps and their cross-metabolite mean.

    ``group_mean`` has shape ``(metabolite, X, Y, Z)``.
    """
    group_mean = np.asarray(group_mean, dtype=float)
    if group_mean.ndim != 4 or group_mean.shape[0] == 0:
        raise ValueError('Gauge group means must have shape (metabolite, X, Y, Z).')
    met_mean = np.nanmean(group_mean, axis=(1, 2, 3))
    met_std = np.nanstd(group_mean, axis=(1, 2, 3))
    with np.errstate(divide='ignore', invalid='ignore'):
        z = ((group_mean - met_mean[:, None, None, None]) /
             met_std[:, None, None, None])
    gauge_term = np.nanmean(z, axis=0)
    return met_std, z, gauge_term


def compute_gauge(stack):
    """Compute the supplied gauge formulation for a full input stack.

    ``stack`` has shape ``(metabolite, subject, X, Y, Z)``.
    """
    stack = np.asarray(stack, dtype=float)
    if stack.ndim != 5 or stack.shape[0] == 0 or stack.shape[1] == 0:
        raise ValueError('Gauge input must have shape (metabolite, subject, X, Y, Z).')
    group_mean = np.nanmean(stack, axis=1)
    met_std, z, gauge_term = gauge_from_group_means(group_mean)
    return group_mean, met_std, z, gauge_term


def correct_map_voxelwise(raw, met_std, gauge_term):
    """Subtract the scaled gauge term while preserving raw zero voxels."""
    raw = np.asarray(raw, dtype=float)
    gauge_term = np.asarray(gauge_term, dtype=float)
    if raw.shape != gauge_term.shape:
        raise ValueError('Raw map and gauge term must have the same 3D shape.')
    corrected = raw - gauge_term * float(met_std)
    corrected[raw == 0] = 0.0
    return corrected
