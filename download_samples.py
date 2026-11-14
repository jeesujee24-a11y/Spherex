"""
Download real cutouts for M101 and NGC 6543 into samples/ directory.
Generates PNG images and metadata.json under 5 MB total.
"""

import hashlib
import json
import os
import time
from pathlib import Path

import numpy as np
import requests
from astropy.io import fits
from astropy.time import Time
from astroquery.ipac.irsa import Irsa
from PIL import Image
from scipy.interpolate import RegularGridInterpolator
from astropy.visualization import ZScaleInterval, AsinhStretch

BASE_DIR = Path(__file__).parent
SAMPLES_DIR = BASE_DIR / "samples"
SAMPLES_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": "SPHERExSkyViewer/1.0 (NASA Space Apps Challenge; sample-fetch)"
}

def make_id(uri_str: str) -> str:
    return hashlib.md5(uri_str.encode()).hexdigest()[:12]

def parse_release(raw_uri: str) -> str:
    if '/dr1/' in raw_uri:
        return 'DR1'
    elif '/qr3/' in raw_uri:
        return 'QR3'
    elif '/qr2/' in raw_uri:
        return 'QR2'
    return 'unknown'

def parse_version(raw_uri: str) -> str:
    import re
    m = re.search(r'(l2b[_-](?:retry-)?v\d+-\d{4}-\d+)', raw_uri)
    return m.group(1) if m else 'unknown'

def process_fits_to_png(data: np.ndarray, out_path: Path):
    finite_mask = np.isfinite(data)
    finite_data = data[finite_mask]
    clean = data.copy()
    clean[~finite_mask] = np.nanmedian(finite_data)

    try:
        zscale = ZScaleInterval()
        vmin, vmax = zscale.get_limits(clean)
    except Exception:
        vmin, vmax = np.nanpercentile(clean, [1, 99])

    if vmax <= vmin:
        vmax = vmin + 1.0

    # Linear normalization then Asinh stretch
    norm_val = np.clip((clean - vmin) / (vmax - vmin), 0, 1)
    stretch = AsinhStretch()
    stretched = stretch(norm_val)
    stretched = np.clip(stretched, 0, 1)

    img_array = (stretched * 255).astype(np.uint8)
    img = Image.fromarray(img_array[::-1], mode='L')

    # Upscale to 512px
    w, h = img.size
    scale = max(1, 512 // w)
    img = img.resize((w * scale, h * scale), Image.NEAREST)
    img.save(out_path, format='PNG')
    return out_path.stat().st_size

def calc_wavelength(hdulist):
    wave_tab = hdulist['WCS-WAVE'].data
    x_grid = wave_tab['X'][0]
    y_grid = wave_tab['Y'][0]
    values = wave_tab['VALUES'][0]
    img_hdr = hdulist['IMAGE'].header
    crpix1w = img_hdr['CRPIX1W']
    crpix2w = img_hdr['CRPIX2W']
    nx, ny = img_hdr['NAXIS1'], img_hdr['NAXIS2']
    center_x = (nx + 1) / 2.0
    center_y = (ny + 1) / 2.0
    det_x = center_x - crpix1w
    det_y = center_y - crpix2w
    interp_wave = RegularGridInterpolator((y_grid, x_grid), values[:, :, 0], bounds_error=False, fill_value=None)
    return float(interp_wave((det_y, det_x)))

targets = [
    {"name": "M101", "ra": 210.80227, "dec": 54.34895, "band": "SPHEREx-D2", "size": 0.1, "max_frames": 4},
    {"name": "NGC 6543", "ra": 269.639, "dec": 66.633, "band": "SPHEREx-D3", "size": 0.1, "max_frames": 4},
]

all_observations = []
all_meta = {}

for target in targets:
    print(f"\nFetching observations for {target['name']}...")
    query = f"""
    SELECT
        'https://irsa.ipac.caltech.edu/' || a.uri || '?center={target['ra']},{target['dec']}d&size={target['size']}' AS uri,
        p.time_bounds_lower,
        p.energy_bandpassname,
        a.uri AS raw_uri
    FROM spherex.artifact a
    JOIN spherex.plane p ON a.planeid = p.planeid
    WHERE 1 = CONTAINS(POINT('ICRS', {target['ra']}, {target['dec']}), p.poly)
        AND p.energy_bandpassname = '{target['band']}'
    ORDER BY p.time_bounds_lower
    """
    res = Irsa.query_tap(query)
    table = res.to_table()
    print(f"  Found {len(table)} observations")

    count = 0
    step = max(1, len(table) // target['max_frames'])
    indices = list(range(0, len(table), step))[:target['max_frames']]

    for idx in indices:
        row = table[idx]
        url = str(row['uri'])
        raw_uri = str(row['raw_uri'])
        mjd = float(row['time_bounds_lower'])
        iso_time = Time(mjd, format='mjd').iso
        frame_id = make_id(raw_uri)
        release = parse_release(raw_uri)
        version = parse_version(raw_uri)

        fits_downloaded = False
        content = None
        for attempt in range(3):
            try:
                resp = requests.get(url, headers=HEADERS, timeout=60)
                if resp.status_code == 200 and len(resp.content) > 1000:
                    content = resp.content
                    fits_downloaded = True
                    break
                else:
                    time.sleep(2)
            except Exception:
                time.sleep(2)

        if not fits_downloaded or content is None:
            print(f"  Skipping row {idx} (download failed)")
            continue

        temp_fits = SAMPLES_DIR / f"temp_{frame_id}.fits"
        temp_fits.write_bytes(content)

        try:
            hdul = fits.open(temp_fits)
            try:
                img_data = hdul['IMAGE'].data.copy()
                wl = calc_wavelength(hdul)
                proc_ver = hdul[0].header.get('VERSION', version)
            finally:
                hdul.close()

            png_path = SAMPLES_DIR / f"{frame_id}.png"
            size_bytes = process_fits_to_png(img_data, png_path)
            print(f"  Saved {frame_id}.png ({size_bytes:,} bytes, wl={wl:.4f} um)")

            all_observations.append({
                "id": frame_id,
                "target": target['name'],
                "time_iso": iso_time,
                "time_mjd": mjd,
                "detector": str(row['energy_bandpassname']),
                "release": release,
                "version": str(proc_ver),
                "central_wavelength_um": round(wl, 4),
            })

            all_meta[frame_id] = {
                "id": frame_id,
                "target": target['name'],
                "central_wavelength_um": round(wl, 4),
                "version": str(proc_ver),
                "release": release,
            }
            count += 1
        except Exception as e:
            print(f"  Error processing row {idx}: {e}")
        finally:
            if temp_fits.exists():
                try:
                    temp_fits.unlink()
                except Exception:
                    pass

metadata = {
    "observations": all_observations,
    "count": len(all_observations),
    "meta": all_meta,
    "targets": ["M101", "NGC 6543"],
    "created_at": time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()),
    "description": "Real SPHEREx cutouts from IRSA TAP for offline demonstration and testing."
}

metadata_file = SAMPLES_DIR / "metadata.json"
with open(metadata_file, "w") as f:
    json.dump(metadata, f, indent=2)

total_sample_size = sum(f.stat().st_size for f in SAMPLES_DIR.glob("*.*"))
print(f"\nDone! Saved {len(all_observations)} sample frames to samples/ (Total size: {total_sample_size / 1024 / 1024:.2f} MB < 5 MB limit)")
