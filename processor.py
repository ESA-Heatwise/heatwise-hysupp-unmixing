#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HEATWISE HySUPP-based supervised unmixing processor.

This processor reads a CHIME-mimicked hyperspectral GeoTIFF and an endmember
spectral library, runs supervised FCLS unmixing, and writes abundance maps,
a top-1 classification map, class names, and run metadata.

Expected endmember table format
-------------------------------
The endmember file can be CSV or XLSX and must contain one wavelength column
and one column per endmember/material.

Example:

wl_um,asphalt_road,concrete,terracotta,vegetation,metal
0.404,0.12,0.20,0.18,0.04,0.30
0.412,0.13,0.21,0.19,0.05,0.31
...

Input raster convention
-----------------------
The input image is expected as a multiband GeoTIFF with shape:

bands x rows x cols

Band wavelength metadata are read from the raster band tags if available
using the tag "wavelength_um". Alternatively, the user can provide a
wavelength text file through --wavelengths-txt.

Output products
---------------
- abundance_stack.tif
- classification_map.tif
- class_names.json
- run_metadata.json
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import rasterio
from rasterio.shutil import copy as rio_copy

from heatwise_unmixing.processing.fcls import (
    compute_fcls_abundances,
    abundances_to_class_map,
)


NODATA_FLOAT = -9999.0
NODATA_CLASS = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run supervised FCLS unmixing on a CHIME-mimicked hyperspectral image."
    )

    parser.add_argument(
        "--input-image",
        required=True,
        help="Input CHIME-mimicked hyperspectral GeoTIFF.",
    )
    parser.add_argument(
        "--endmembers",
        required=True,
        help="Endmember spectral library in CSV or XLSX format.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory where output products will be written.",
    )
    parser.add_argument(
        "--wavelengths-txt",
        default=None,
        help="Optional wavelength text file for the input image. If omitted, band tags are used.",
    )
    parser.add_argument(
        "--output-prefix",
        default="unmixing",
        help="Prefix used for output filenames.",
    )
    parser.add_argument(
        "--input-nodata",
        type=float,
        default=None,
        help="Optional input nodata override. If omitted, raster nodata metadata are used.",
    )
    parser.add_argument(
        "--scale-factor",
        type=float,
        default=1.0,
        help="Optional multiplicative scale factor applied to the input image.",
    )
    parser.add_argument(
        "--min-abundance",
        type=float,
        default=0.0,
        help="Minimum abundance required for top-1 class assignment.",
    )
    parser.add_argument(
        "--maxiter",
        type=int,
        default=200,
        help="Maximum number of FCLS iterations per pixel.",
    )
    parser.add_argument(
        "--ftol",
        type=float,
        default=1e-8,
        help="FCLS numerical tolerance.",
    )
    parser.add_argument(
        "--log-every",
        type=int,
        default=10000,
        help="Print FCLS progress every N pixels. Use 0 to disable.",
    )

    return parser.parse_args()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [json_safe(v) for v in value]
    return value


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(json_safe(payload), f, indent=2, ensure_ascii=False)
        f.write("\n")


def read_wavelengths_txt(path: str | Path) -> np.ndarray:
    values: list[float] = []
    path = Path(path)

    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            s = line.strip().replace(",", ".")
            if not s or s.lower().startswith("wavelength"):
                continue
            values.append(float(s))

    wl = np.asarray(values, dtype=np.float64)

    if wl.size == 0:
        raise ValueError(f"No wavelengths found in {path}")

    if np.nanmax(wl) > 100.0:
        wl = wl / 1000.0

    return wl


def read_wavelengths_from_raster_tags(src: rasterio.DatasetReader) -> np.ndarray | None:
    values: list[float] = []

    for band_index in range(1, src.count + 1):
        tags = src.tags(band_index)

        value = None
        for key in ("wavelength_um", "wavelength", "center_wavelength"):
            if key in tags:
                value = tags[key]
                break

        if value is None:
            return None

        try:
            values.append(float(str(value).replace(",", ".")))
        except ValueError:
            return None

    wl = np.asarray(values, dtype=np.float64)

    if wl.size != src.count:
        return None

    if np.nanmax(wl) > 100.0:
        wl = wl / 1000.0

    return wl


def read_input_raster(
    image_path: str | Path,
    wavelengths_txt: str | Path | None,
    input_nodata: float | None,
    scale_factor: float,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    image_path = Path(image_path)

    with rasterio.open(image_path) as src:
        cube = src.read().astype(np.float32)
        profile = src.profile.copy()
        transform = src.transform
        crs = src.crs
        nodata = input_nodata if input_nodata is not None else src.nodata

        if wavelengths_txt is not None:
            wl_um = read_wavelengths_txt(wavelengths_txt)
        else:
            wl_um = read_wavelengths_from_raster_tags(src)

        if wl_um is None:
            raise ValueError(
                "Input wavelengths were not found in raster band tags. "
                "Provide them explicitly using --wavelengths-txt."
            )

    if crs is None:
        raise ValueError("Input raster has no CRS.")

    if wl_um.size != cube.shape[0]:
        raise ValueError(
            f"Number of wavelengths ({wl_um.size}) does not match raster bands ({cube.shape[0]})."
        )

    if nodata is not None:
        cube[cube == float(nodata)] = np.nan

    if scale_factor != 1.0:
        cube *= float(scale_factor)

    info = {
        "profile": profile,
        "transform": transform,
        "crs": crs,
        "nodata": nodata,
        "width": profile["width"],
        "height": profile["height"],
        "count": profile["count"],
        "dtype": str(profile["dtype"]),
    }

    return cube, wl_um, info


def read_endmembers(path: str | Path) -> tuple[np.ndarray, np.ndarray, list[str]]:
    path = Path(path)

    if path.suffix.lower() in (".xlsx", ".xls"):
        df = pd.read_excel(path)
    elif path.suffix.lower() in (".csv", ".txt"):
        df = pd.read_csv(path)
    else:
        raise ValueError(f"Unsupported endmember file format: {path.suffix}")

    if df.shape[1] < 3:
        raise ValueError(
            "Endmember file must contain one wavelength column and at least two endmember columns."
        )

    wavelength_candidates = [
        "wl_um",
        "wavelength_um",
        "wavelength",
        "lambda",
        "lambda_um",
        "wl",
    ]

    wl_col = None
    lower_cols = {str(c).lower(): c for c in df.columns}

    for candidate in wavelength_candidates:
        if candidate in lower_cols:
            wl_col = lower_cols[candidate]
            break

    if wl_col is None:
        wl_col = df.columns[0]

    wl = df[wl_col].to_numpy(dtype=np.float64)

    if np.nanmax(wl) > 100.0:
        wl = wl / 1000.0

    endmember_cols = [c for c in df.columns if c != wl_col]
    names = [str(c) for c in endmember_cols]

    E = df[endmember_cols].to_numpy(dtype=np.float64)

    if E.ndim != 2:
        raise ValueError("Invalid endmember matrix.")

    if E.shape[0] != wl.size:
        raise ValueError("Endmember wavelength axis and spectral matrix are inconsistent.")

    valid_rows = np.isfinite(wl) & np.all(np.isfinite(E), axis=1)
    wl = wl[valid_rows]
    E = E[valid_rows, :]

    order = np.argsort(wl)
    wl = wl[order]
    E = E[order, :]

    return wl, E, names


def interpolate_endmembers_to_image_grid(
    wl_endmembers_um: np.ndarray,
    E: np.ndarray,
    wl_image_um: np.ndarray,
) -> np.ndarray:
    wl_e = np.asarray(wl_endmembers_um, dtype=np.float64)
    wl_i = np.asarray(wl_image_um, dtype=np.float64)

    p = E.shape[1]
    E_interp = np.full((wl_i.size, p), np.nan, dtype=np.float64)

    for k in range(p):
        E_interp[:, k] = np.interp(
            wl_i,
            wl_e,
            E[:, k],
            left=np.nan,
            right=np.nan,
        )

    return E_interp


def build_valid_spectral_mask(cube: np.ndarray, E_image_grid: np.ndarray) -> np.ndarray:
    """
    Select spectral bands usable by FCLS.

    A band is kept if:
    - the endmember values are finite for all classes;
    - the image band contains at least one finite pixel.
    """
    image_band_has_data = np.isfinite(cube).any(axis=(1, 2))
    endmembers_valid = np.all(np.isfinite(E_image_grid), axis=1)

    return image_band_has_data & endmembers_valid


def cube_to_matrix(cube: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    Convert cube L x H x W into matrix Y L x N and spatial valid mask.

    A pixel is valid only if all retained spectral bands are finite.
    """
    L, H, W = cube.shape
    Y = cube.reshape(L, H * W)

    valid_pixels = np.all(np.isfinite(Y), axis=0)

    Y_valid = Y[:, valid_pixels].astype(np.float64)

    return Y_valid, valid_pixels


def abundance_matrix_to_cube(A_valid: np.ndarray, valid_pixels: np.ndarray, H: int, W: int) -> np.ndarray:
    p = A_valid.shape[0]
    A_full = np.full((p, H * W), np.nan, dtype=np.float32)
    A_full[:, valid_pixels] = A_valid.astype(np.float32)
    return A_full.reshape(p, H, W)


def labels_to_map(labels_valid: np.ndarray, valid_pixels: np.ndarray, H: int, W: int) -> np.ndarray:
    labels_full = np.full(H * W, NODATA_CLASS, dtype=np.uint16)
    labels_full[valid_pixels] = labels_valid.astype(np.uint16)
    return labels_full.reshape(H, W)


def write_float_cog(
    output_path: str | Path,
    cube: np.ndarray,
    reference_profile: dict[str, Any],
    band_names: list[str] | None = None,
) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    arr = cube.astype(np.float32).copy()
    arr[np.isnan(arr)] = NODATA_FLOAT

    tmp_path = output_path.with_name(f".{output_path.stem}_tmp.tif")
    if tmp_path.exists():
        tmp_path.unlink()
    if output_path.exists():
        output_path.unlink()

    profile = reference_profile.copy()
    profile.update(
        driver="GTiff",
        dtype="float32",
        count=arr.shape[0],
        height=arr.shape[1],
        width=arr.shape[2],
        nodata=NODATA_FLOAT,
        compress="deflate",
        predictor=3,
        tiled=True,
        blockxsize=512,
        blockysize=512,
        BIGTIFF="IF_SAFER",
    )

    try:
        with rasterio.open(tmp_path, "w", **profile) as dst:
            dst.write(arr)

            if band_names is not None:
                for i, name in enumerate(band_names, start=1):
                    dst.update_tags(i, class_name=name)

        rio_copy(
            tmp_path,
            output_path,
            driver="COG",
            COMPRESS="DEFLATE",
            PREDICTOR="3",
            BLOCKSIZE="512",
            BIGTIFF="IF_SAFER",
            OVERVIEW_RESAMPLING="AVERAGE",
            NUM_THREADS="ALL_CPUS",
        )
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def write_classification_cog(
    output_path: str | Path,
    labels: np.ndarray,
    reference_profile: dict[str, Any],
    class_names: list[str],
) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    arr = labels.astype(np.uint16)

    tmp_path = output_path.with_name(f".{output_path.stem}_tmp.tif")
    if tmp_path.exists():
        tmp_path.unlink()
    if output_path.exists():
        output_path.unlink()

    profile = reference_profile.copy()
    profile.update(
        driver="GTiff",
        dtype="uint16",
        count=1,
        height=arr.shape[0],
        width=arr.shape[1],
        nodata=NODATA_CLASS,
        compress="deflate",
        tiled=True,
        blockxsize=512,
        blockysize=512,
        BIGTIFF="IF_SAFER",
    )

    class_mapping = {str(i + 1): name for i, name in enumerate(class_names)}

    try:
        with rasterio.open(tmp_path, "w", **profile) as dst:
            dst.write(arr, 1)
            dst.update_tags(
                class_mapping=json.dumps(class_mapping),
                nodata_class=str(NODATA_CLASS),
            )

        rio_copy(
            tmp_path,
            output_path,
            driver="COG",
            COMPRESS="DEFLATE",
            BLOCKSIZE="512",
            BIGTIFF="IF_SAFER",
            OVERVIEW_RESAMPLING="NEAREST",
            NUM_THREADS="ALL_CPUS",
        )
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def main() -> None:
    args = parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    log_every = None if args.log_every <= 0 else int(args.log_every)

    print("[INFO] Reading input raster...")
    cube, wl_image_um, raster_info = read_input_raster(
        image_path=args.input_image,
        wavelengths_txt=args.wavelengths_txt,
        input_nodata=args.input_nodata,
        scale_factor=args.scale_factor,
    )

    L0, H, W = cube.shape

    print("[INFO] Reading endmembers...")
    wl_endmembers_um, E_raw, class_names = read_endmembers(args.endmembers)

    print("[INFO] Interpolating endmembers to image wavelength grid...")
    E_image_grid = interpolate_endmembers_to_image_grid(
        wl_endmembers_um=wl_endmembers_um,
        E=E_raw,
        wl_image_um=wl_image_um,
    )

    spectral_mask = build_valid_spectral_mask(cube, E_image_grid)

    if spectral_mask.sum() < 2:
        raise RuntimeError(
            "Too few valid spectral bands after matching image and endmembers."
        )

    cube_valid_bands = cube[spectral_mask, :, :]
    E_valid_bands = E_image_grid[spectral_mask, :]
    wl_used_um = wl_image_um[spectral_mask]

    print(f"[INFO] Original image bands: {L0}")
    print(f"[INFO] Bands used for FCLS: {int(spectral_mask.sum())}")
    print(f"[INFO] Number of endmembers/classes: {len(class_names)}")

    print("[INFO] Converting image cube to spectral matrix...")
    Y_valid, valid_pixels = cube_to_matrix(cube_valid_bands)

    print(f"[INFO] Valid pixels for FCLS: {int(valid_pixels.sum())} / {H * W}")

    if valid_pixels.sum() == 0:
        raise RuntimeError("No valid pixels available for FCLS.")

    print("[INFO] Running FCLS...")
    result = compute_fcls_abundances(
        Y=Y_valid,
        E=E_valid_bands,
        maxiter=args.maxiter,
        ftol=args.ftol,
        log_every=log_every,
    )

    A_cube = abundance_matrix_to_cube(
        A_valid=result.abundances,
        valid_pixels=valid_pixels,
        H=H,
        W=W,
    )

    labels_valid = abundances_to_class_map(
        result.abundances,
        nodata_value=NODATA_CLASS,
        min_abundance=args.min_abundance,
    )

    class_map = labels_to_map(
        labels_valid=labels_valid,
        valid_pixels=valid_pixels,
        H=H,
        W=W,
    )

    prefix = args.output_prefix

    abundance_stack_path = output_dir / f"{prefix}_abundance_stack_COG.tif"
    classification_path = output_dir / f"{prefix}_classification_map_COG.tif"
    class_names_path = output_dir / f"{prefix}_class_names.json"
    metadata_path = output_dir / f"{prefix}_run_metadata.json"
    wavelengths_used_path = output_dir / f"{prefix}_wavelengths_used_um.txt"

    reference_profile = raster_info["profile"].copy()
    reference_profile.update(
        height=H,
        width=W,
        transform=raster_info["transform"],
        crs=raster_info["crs"],
    )

    print("[INFO] Writing abundance stack...")
    write_float_cog(
        abundance_stack_path,
        A_cube,
        reference_profile=reference_profile,
        band_names=class_names,
    )

    print("[INFO] Writing classification map...")
    write_classification_cog(
        classification_path,
        class_map,
        reference_profile=reference_profile,
        class_names=class_names,
    )

    np.savetxt(
        wavelengths_used_path,
        wl_used_um,
        fmt="%.6f",
        header="wavelength_um",
        comments="",
    )

    class_payload = {
        "nodata": NODATA_CLASS,
        "classes": [
            {
                "label": i + 1,
                "name": name,
            }
            for i, name in enumerate(class_names)
        ],
    }
    write_json(class_names_path, class_payload)

    metadata = {
        "processor": {
            "name": "heatwise-hysupp-unmixing",
            "version": "1.0.0",
            "method": "supervised FCLS",
        },
        "created_utc": utc_now_iso(),
        "inputs": {
            "input_image": Path(args.input_image).name,
            "endmembers": Path(args.endmembers).name,
            "wavelengths_txt": Path(args.wavelengths_txt).name if args.wavelengths_txt else None,
            "scale_factor": float(args.scale_factor),
            "input_nodata": args.input_nodata,
        },
        "image": {
            "width": int(W),
            "height": int(H),
            "original_bands": int(L0),
            "bands_used_for_fcls": int(spectral_mask.sum()),
            "valid_pixels": int(valid_pixels.sum()),
            "total_pixels": int(H * W),
            "crs": str(raster_info["crs"]),
        },
        "endmembers": {
            "count": int(len(class_names)),
            "names": class_names,
            "original_wavelength_min_um": float(np.nanmin(wl_endmembers_um)),
            "original_wavelength_max_um": float(np.nanmax(wl_endmembers_um)),
        },
        "fcls": {
            "maxiter": int(args.maxiter),
            "ftol": float(args.ftol),
            "success_rate": float(result.success_rate),
            "failed_pixels": int(result.failed_pixels),
            "min_abundance_for_classification": float(args.min_abundance),
        },
        "outputs": {
            "abundance_stack": abundance_stack_path.name,
            "classification_map": classification_path.name,
            "class_names": class_names_path.name,
            "wavelengths_used": wavelengths_used_path.name,
            "run_metadata": metadata_path.name,
        },
    }

    write_json(metadata_path, metadata)

    print(f"[OK] Wrote abundance stack: {abundance_stack_path}")
    print(f"[OK] Wrote classification map: {classification_path}")
    print(f"[OK] Wrote class names: {class_names_path}")
    print(f"[OK] Wrote wavelengths used: {wavelengths_used_path}")
    print(f"[OK] Wrote metadata: {metadata_path}")


if __name__ == "__main__":
    main()
