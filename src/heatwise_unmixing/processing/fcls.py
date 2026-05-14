"""
FCLS unmixing core for the HEATWISE HySUPP-based unmixing processor.

This module provides a compact, dependency-light implementation of
Fully Constrained Least Squares (FCLS) for supervised hyperspectral unmixing.

Input convention
----------------
Y : array, shape (L, N)
    Hyperspectral data matrix, where L is the number of spectral bands and
    N is the number of pixels.

E : array, shape (L, p)
    Endmember matrix, where p is the number of endmembers/classes.

Output convention
-----------------
A : array, shape (p, N)
    Abundance matrix. Each column contains the abundance fractions for one pixel.

Constraints
-----------
For each pixel abundance vector a:

    a_i >= 0
    sum_i a_i = 1

This follows the classical FCLS formulation for the linear mixing model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize

logger = logging.getLogger(__name__)


@dataclass
class FCLSResult:
    """Container for FCLS output."""

    abundances: np.ndarray
    success_rate: float
    failed_pixels: int


def _validate_inputs(Y: np.ndarray, E: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    Validate and cast input matrices.

    Parameters
    ----------
    Y : np.ndarray
        Hyperspectral data matrix with shape (L, N).
    E : np.ndarray
        Endmember matrix with shape (L, p).

    Returns
    -------
    Y : np.ndarray
        Float64 validated data matrix.
    E : np.ndarray
        Float64 validated endmember matrix.
    """
    Y = np.asarray(Y, dtype=np.float64)
    E = np.asarray(E, dtype=np.float64)

    if Y.ndim != 2:
        raise ValueError(f"Y must be a 2D array with shape (L, N). Got {Y.shape}.")

    if E.ndim != 2:
        raise ValueError(f"E must be a 2D array with shape (L, p). Got {E.shape}.")

    if Y.shape[0] != E.shape[0]:
        raise ValueError(
            "Y and E must have the same number of spectral bands. "
            f"Got Y.shape={Y.shape}, E.shape={E.shape}."
        )

    if E.shape[1] < 2:
        raise ValueError("At least two endmembers are required for FCLS.")

    if not np.all(np.isfinite(E)):
        raise ValueError("Endmember matrix E contains NaN or infinite values.")

    return Y, E


def _fallback_abundance(y: np.ndarray, E: np.ndarray) -> np.ndarray:
    """
    Fallback abundance estimate used if SLSQP does not converge.

    This computes an unconstrained least-squares solution, clips negative values,
    and normalizes to sum to one. It is not the primary FCLS solution, but it
    prevents the processor from crashing on a single problematic pixel.
    """
    p = E.shape[1]

    try:
        a, *_ = np.linalg.lstsq(E, y, rcond=None)
    except np.linalg.LinAlgError:
        a = np.ones(p, dtype=np.float64) / p

    a = np.asarray(a, dtype=np.float64)
    a[~np.isfinite(a)] = 0.0
    a = np.clip(a, 0.0, None)

    s = float(a.sum())
    if s <= 0.0:
        return np.ones(p, dtype=np.float64) / p

    return a / s


def _solve_pixel_fcls(
    y: np.ndarray,
    E: np.ndarray,
    x0: np.ndarray,
    maxiter: int,
    ftol: float,
) -> tuple[np.ndarray, bool]:
    """
    Solve FCLS for a single pixel.

    Objective:
        min_a 0.5 * ||E a - y||^2

    Subject to:
        a_i >= 0
        sum(a_i) = 1
    """
    p = E.shape[1]

    def objective(a: np.ndarray) -> float:
        r = E @ a - y
        return 0.5 * float(r @ r)

    def gradient(a: np.ndarray) -> np.ndarray:
        r = E @ a - y
        return E.T @ r

    constraints = [
        {
            "type": "eq",
            "fun": lambda a: np.sum(a) - 1.0,
            "jac": lambda a: np.ones_like(a),
        }
    ]

    bounds = [(0.0, 1.0) for _ in range(p)]

    res = minimize(
        objective,
        x0,
        method="SLSQP",
        jac=gradient,
        bounds=bounds,
        constraints=constraints,
        options={
            "maxiter": maxiter,
            "ftol": ftol,
            "disp": False,
        },
    )

    if res.success and np.all(np.isfinite(res.x)):
        a = np.clip(res.x, 0.0, 1.0)
        s = float(a.sum())
        if s > 0.0:
            return a / s, True

    return _fallback_abundance(y, E), False


def compute_fcls_abundances(
    Y: np.ndarray,
    E: np.ndarray,
    *,
    maxiter: int = 200,
    ftol: float = 1e-8,
    log_every: int | None = 10000,
) -> FCLSResult:
    """
    Compute FCLS abundances for a hyperspectral image.

    Parameters
    ----------
    Y : np.ndarray
        Hyperspectral data matrix with shape (L, N).
    E : np.ndarray
        Endmember matrix with shape (L, p).
    maxiter : int
        Maximum number of SLSQP iterations per pixel.
    ftol : float
        SLSQP convergence tolerance.
    log_every : int or None
        If not None, print progress every `log_every` pixels.

    Returns
    -------
    FCLSResult
        Object containing:
        - abundances: array with shape (p, N)
        - success_rate: fraction of pixels solved successfully by SLSQP
        - failed_pixels: number of pixels where fallback was used
    """
    Y, E = _validate_inputs(Y, E)

    L, N = Y.shape
    _, p = E.shape

    A = np.zeros((p, N), dtype=np.float32)

    x0 = np.ones(p, dtype=np.float64) / p
    failed = 0
    valid_processed = 0

    for j in range(N):
        y = Y[:, j]

        if not np.all(np.isfinite(y)):
            A[:, j] = np.nan
            failed += 1
            continue

        a, ok = _solve_pixel_fcls(
            y=y,
            E=E,
            x0=x0,
            maxiter=maxiter,
            ftol=ftol,
        )

        A[:, j] = a.astype(np.float32)
        valid_processed += 1

        if not ok:
            failed += 1

        if log_every is not None and log_every > 0 and (j + 1) % log_every == 0:
            logger.info("FCLS processed %d / %d pixels", j + 1, N)

    success_rate = 0.0
    if valid_processed > 0:
        success_rate = 1.0 - (failed / valid_processed)

    return FCLSResult(
        abundances=A,
        success_rate=float(success_rate),
        failed_pixels=int(failed),
    )


def abundances_to_class_map(
    A: np.ndarray,
    *,
    nodata_value: int = 0,
    min_abundance: float = 0.0,
) -> np.ndarray:
    """
    Convert abundance matrix to top-1 classification labels.

    Parameters
    ----------
    A : np.ndarray
        Abundance matrix with shape (p, N).
    nodata_value : int
        Label used for invalid pixels.
    min_abundance : float
        Minimum abundance required to assign a class.

    Returns
    -------
    labels : np.ndarray
        Integer labels with shape (N,), where classes are 1-based.
    """
    A = np.asarray(A)

    if A.ndim != 2:
        raise ValueError(f"A must be a 2D array with shape (p, N). Got {A.shape}.")

    valid = np.all(np.isfinite(A), axis=0)
    max_abund = np.nanmax(A, axis=0)
    labels = np.full(A.shape[1], nodata_value, dtype=np.uint16)

    assigned = valid & (max_abund >= min_abundance)
    labels[assigned] = np.argmax(A[:, assigned], axis=0).astype(np.uint16) + 1

    return labels
