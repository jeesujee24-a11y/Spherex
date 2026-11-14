"""
Phase 1: Prove Data Access
Test TAP query and cutout download for M101 (RA 210.80227, Dec 54.34895)
with SPHEREx-D2 band and 0.1 deg cutout size.
Based on: https://caltech-ipac.github.io/irsa-tutorials/spherex-cutouts
"""

import sys
import time
import os
import requests
import numpy as np

# Astropy imports
from astropy.time import Time
from astropy.io import fits
from astroquery.ipac.irsa import Irsa
from scipy.interpolate import RegularGridInterpolator

# Configuration
RA = 210.80227
DEC = 54.34895
SIZE = 0.1  # degrees
BANDPASS = 'SPHEREx-D2'

print("=" * 70)
print("SPHEREx Fetch Test — M101")
print(f"  RA={RA}, Dec={DEC}, Size={SIZE}°, Band={BANDPASS}")
print("=" * 70)

# ─── Step 1: Check time_bounds_lower units in TAP_SCHEMA ─────────────────────
print("\n--- Step 1: Check time_bounds_lower column metadata ---")
schema_query = """
SELECT column_name, unit, datatype, description
FROM TAP_SCHEMA.columns
WHERE table_name = 'spherex.plane'
  AND column_name = 'time_bounds_lower'
"""
try:
    schema_result = Irsa.query_tap(schema_query)
    schema_table = schema_result.to_table()
    print(schema_table)
    if len(schema_table) > 0:
        unit_val = str(schema_table['unit'][0])
        print(f"  Unit of time_bounds_lower: {unit_val}")
    else:
        print("  WARNING: No metadata found for time_bounds_lower")
except Exception as e:
    print(f"  WARNING: Could not query TAP_SCHEMA: {e}")
    print("  Proceeding with assumption: MJD days")

# ─── Step 2: Run the main TAP query ──────────────────────────────────────────
print("\n--- Step 2: TAP query for M101 cutouts ---")
query = f"""
SELECT
    'https://irsa.ipac.caltech.edu/' || a.uri || '?center={RA},{DEC}d&size={SIZE}' AS uri,
    p.time_bounds_lower,
    p.energy_bandpassname,
    a.uri AS raw_uri
FROM spherex.artifact a
JOIN spherex.plane p ON a.planeid = p.planeid
WHERE 1 = CONTAINS(POINT('ICRS', {RA}, {DEC}), p.poly)
    AND p.energy_bandpassname = '{BANDPASS}'
ORDER BY p.time_bounds_lower
"""

t1 = time.time()
results = Irsa.query_tap(query)
elapsed = time.time() - t1
results_table = results.to_table()

print(f"  Query time: {elapsed:.1f} seconds")
print(f"  Number of images found: {len(results_table)}")

if len(results_table) == 0:
    print("\nFAIL: No results returned from TAP query!")
    sys.exit(1)

# ─── Step 3: Show first 5 rows and convert times ─────────────────────────────
print("\n--- Step 3: First 5 rows ---")
for i, row in enumerate(results_table[:5]):
    mjd = float(row['time_bounds_lower'])
    iso_date = Time(mjd, format='mjd').iso
    raw_uri = str(row['raw_uri'])
    release = 'unknown'
    if '/dr1/' in raw_uri:
        release = 'DR1'
    elif '/qr3/' in raw_uri:
        release = 'QR3'
    elif '/qr2/' in raw_uri:
        release = 'QR2'
    print(f"  [{i}] MJD={mjd:.4f} => {iso_date} | Release={release}")
    print(f"       URI: {row['uri'][:95]}...")

releases = set()
for row in results_table:
    raw_uri = str(row['raw_uri'])
    if '/dr1/' in raw_uri:
        releases.add('DR1')
    elif '/qr3/' in raw_uri:
        releases.add('QR3')
    elif '/qr2/' in raw_uri:
        releases.add('QR2')
print(f"\n  Releases found: {', '.join(sorted(releases))}")

# ─── Step 4: Download one cutout and analyze ──────────────────────────────────
print("\n--- Step 4: Download and analyze one cutout ---")
headers = {
    "User-Agent": "SPHERExSkyViewer/1.0 (NASA Space Apps Challenge; +https://github.com)"
}

download_success = False
cutout_file = "phase1_cutout.fits"

for idx in range(min(5, len(results_table))):
    cutout_url = str(results_table[idx]['uri'])
    print(f"  Trying row [{idx}]: {cutout_url[:95]}...")
    for attempt in range(3):
        try:
            t1 = time.time()
            resp = requests.get(cutout_url, headers=headers, timeout=120)
            elapsed = time.time() - t1
            if resp.status_code == 200 and len(resp.content) > 1000:
                with open(cutout_file, "wb") as f:
                    f.write(resp.content)
                download_success = True
                print(f"  Downloaded in {elapsed:.1f}s ({len(resp.content):,} bytes)")
                break
            else:
                print(f"    Attempt {attempt+1}: HTTP {resp.status_code}, retrying...")
                time.sleep(2 ** attempt)
        except Exception as e:
            print(f"    Attempt {attempt+1}: Error {e}, retrying...")
            time.sleep(2 ** attempt)
    if download_success:
        break

if not download_success:
    print("FAIL: Could not download any cutout.")
    sys.exit(1)

try:
    hdulist = fits.open(cutout_file)
    hdu_names = [hdu.name for hdu in hdulist]
    print(f"  HDU names: {hdu_names}")

    image_hdu = hdulist['IMAGE']
    data = image_hdu.data
    print(f"  Image shape: {data.shape}")
    finite_mask = np.isfinite(data)
    finite_frac = np.sum(finite_mask) / data.size
    print(f"  Finite pixel fraction: {finite_frac:.4f} ({np.sum(finite_mask)}/{data.size})")

    # Central wavelength calculation
    # Method A: Try astropy.wcs
    central_wl = None
    try:
        from astropy.wcs import WCS
        import astropy.units as u
        from astropy.coordinates import SkyCoord
        coord = SkyCoord(ra=RA * u.degree, dec=DEC * u.degree, frame="icrs")
        header = image_hdu.header
        spatial_wcs = WCS(header)
        x, y = spatial_wcs.world_to_pixel(coord)
        spectral_wcs = WCS(header, fobj=hdulist, key="W")
        spectral_wcs.sip = None
        wavelength, bandwidth = spectral_wcs.pixel_to_world(x, y)
        central_wl = float(wavelength.to(u.micrometer).value)
        print(f"  Central wavelength (via astropy.wcs): {central_wl:.4f} um")
    except Exception as e:
        # Method B: Direct evaluation from WCS-WAVE extension
        wave_tab = hdulist['WCS-WAVE'].data
        x_grid = wave_tab['X'][0]
        y_grid = wave_tab['Y'][0]
        values = wave_tab['VALUES'][0]
        img_hdr = image_hdu.header
        crpix1w = img_hdr['CRPIX1W']
        crpix2w = img_hdr['CRPIX2W']
        nx, ny = img_hdr['NAXIS1'], img_hdr['NAXIS2']
        center_x = (nx + 1) / 2.0
        center_y = (ny + 1) / 2.0
        det_x = center_x - crpix1w
        det_y = center_y - crpix2w
        interp_wave = RegularGridInterpolator((y_grid, x_grid), values[:, :, 0], bounds_error=False, fill_value=None)
        central_wl = float(interp_wave((det_y, det_x)))
        print(f"  Central wavelength (via WCS-WAVE table interpolation): {central_wl:.4f} um")

    version = hdulist[0].header.get('VERSION', 'N/A')
    print(f"  Processing version: {version}")
    hdulist.close()

except Exception as e:
    print(f"  FAIL: Error analyzing cutout: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ─── Step 5: Get distinct bands ──────────────────────────────────────────────
print("\n--- Step 5: Get distinct band names ---")
band_query = "SELECT DISTINCT energy_bandpassname FROM spherex.plane ORDER BY energy_bandpassname"
try:
    band_results = Irsa.query_tap(band_query)
    band_table = band_results.to_table()
    bands = [str(row['energy_bandpassname']) for row in band_table]
    print(f"  Bands found: {bands}")
except Exception as e:
    print(f"  WARNING: Could not query bands: {e}")
    bands = []

# ─── Summary ─────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("PHASE 1 PROOF SUMMARY")
print(f"  Row count: {len(results_table)} (expected ~84)")
print(f"  Cutout downloaded: YES")
print(f"  Image shape: {data.shape}")
print(f"  Finite pixel fraction: {finite_frac:.4f}")
print(f"  Central wavelength: {central_wl:.4f} um")
print(f"  Releases: {', '.join(sorted(releases))}")
print(f"  Bands: {', '.join(bands)}")
print("  STATUS: PASS" if len(results_table) > 0 and finite_frac > 0 and central_wl > 0 else "  STATUS: FAIL")
print("=" * 70)
