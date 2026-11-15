"""
SPHEREx Sky Viewer — FastAPI Backend
Serves SPHEREx spectral image cutouts from NASA/IPAC IRSA.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional, List, Dict, Any

import numpy as np
import requests
from PIL import Image
from astropy.time import Time
from astropy.io import fits
from astropy.coordinates import SkyCoord
import astropy.units as u
from astroquery.ipac.irsa import Irsa
from astropy.visualization import ZScaleInterval, AsinhStretch
from scipy.interpolate import RegularGridInterpolator

from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.responses import Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

# ─── Setup Logging ───────────────────────────────────────────────────────────
logger = logging.getLogger("spherex_app")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ─── Directories & Constants ─────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
CACHE_DIR = BASE_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)
SAMPLES_DIR = BASE_DIR / "samples"
STATIC_DIR = BASE_DIR / "static"

MAX_RETRIES = 3
UPSTREAM_CONCURRENCY = 5
TAP_CACHE_TTL = 6 * 3600  # 6 hours
PNG_SIZE = 512
USER_AGENT = "SPHERExSkyViewer/1.0 (NASA Space Apps Challenge; +https://github.com)"

_download_semaphore = asyncio.Semaphore(UPSTREAM_CONCURRENCY)

ALLOWED_BANDS = [
    "SPHEREx-D1",
    "SPHEREx-D2",
    "SPHEREx-D3",
    "SPHEREx-D4",
    "SPHEREx-D5",
    "SPHEREx-D6",
]

# In-memory caches
_observations_cache: Dict[str, tuple[float, dict]] = {}
_meta_cache: Dict[str, dict] = {}
_frame_registry: Dict[str, dict] = {}
_bands_cache: Optional[List[str]] = list(ALLOWED_BANDS)
_bands_cache_time: float = time.time()

app = FastAPI(title="SPHEREx Sky Viewer API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Utility Functions ───────────────────────────────────────────────────────

def is_offline() -> bool:
    return os.environ.get("SPHEREX_OFFLINE", "0") == "1"


def make_id(uri_str: str) -> str:
    """Generate short hex id without slashes."""
    return hashlib.md5(uri_str.encode()).hexdigest()[:12]


def parse_release(raw_uri: str) -> str:
    if "/dr1/" in raw_uri:
        return "DR1"
    elif "/qr3/" in raw_uri:
        return "QR3"
    elif "/qr2/" in raw_uri:
        return "QR2"
    return "unknown"


def parse_version(raw_uri: str) -> str:
    m = re.search(r"(l2b[_-](?:retry-)?v\d+-\d{4}-\d+)", raw_uri)
    return m.group(1) if m else "unknown"


def validate_params(ra: float, dec: float, size: float, band: str) -> None:
    if not (0.0 <= ra < 360.0):
        raise HTTPException(status_code=400, detail=f"RA must be in [0, 360), got {ra}")
    if not (-90.0 <= dec <= 90.0):
        raise HTTPException(status_code=400, detail=f"Dec must be in [-90, 90], got {dec}")
    if not (0.0 < size <= 0.2):
        raise HTTPException(status_code=400, detail=f"Size must be in (0, 0.2], got {size}")
    if band not in ALLOWED_BANDS and band != "SPHEREx-All":
        raise HTTPException(
            status_code=400,
            detail=f"Band '{band}' not supported. Allowed: {ALLOWED_BANDS}",
        )


def get_frame_info(frame_id: str) -> Optional[dict]:
    """Look up frame info from memory or disk cache."""
    if frame_id in _frame_registry:
        return _frame_registry[frame_id]
    disk_reg = CACHE_DIR / f"frame_{frame_id}.json"
    if disk_reg.exists():
        try:
            with open(disk_reg, "r", encoding="utf-8") as f:
                info = json.load(f)
                _frame_registry[frame_id] = info
                return info
        except Exception:
            pass
    return None


def save_frame_info(frame_id: str, info: dict) -> None:
    """Save frame info to memory and disk cache."""
    _frame_registry[frame_id] = info
    disk_reg = CACHE_DIR / f"frame_{frame_id}.json"
    try:
        with open(disk_reg, "w", encoding="utf-8") as f:
            json.dump(info, f)
    except Exception:
        pass


def calc_wavelength_from_hdulist(hdulist) -> float:
    """Calculate central wavelength in micrometers."""
    try:
        from astropy.wcs import WCS
        image_hdu = hdulist["IMAGE"]
        spectral_wcs = WCS(image_hdu.header, fobj=hdulist, key="W")
        spectral_wcs.sip = None
        nx, ny = image_hdu.header["NAXIS1"], image_hdu.header["NAXIS2"]
        x, y = (nx - 1) / 2.0, (ny - 1) / 2.0
        wl, _ = spectral_wcs.pixel_to_world(x, y)
        return float(wl.to(u.micrometer).value)
    except Exception:
        wave_tab = hdulist["WCS-WAVE"].data
        x_grid = wave_tab["X"][0]
        y_grid = wave_tab["Y"][0]
        values = wave_tab["VALUES"][0]
        img_hdr = hdulist["IMAGE"].header
        crpix1w = img_hdr.get("CRPIX1W", 0.0)
        crpix2w = img_hdr.get("CRPIX2W", 0.0)
        nx = img_hdr.get("NAXIS1", 59)
        ny = img_hdr.get("NAXIS2", 59)
        center_x = (nx + 1) / 2.0
        center_y = (ny + 1) / 2.0
        det_x = center_x - crpix1w
        det_y = center_y - crpix2w
        interp = RegularGridInterpolator(
            (y_grid, x_grid), values[:, :, 0], bounds_error=False, fill_value=None
        )
        return float(interp((det_y, det_x)))


def download_fits_bytes(url: str) -> bytes:
    """Download FITS cutout with retry logic."""
    headers = {"User-Agent": USER_AGENT}
    last_err = None
    for attempt in range(2):
        try:
            resp = requests.get(url, headers=headers, timeout=25)
            if resp.status_code == 200 and len(resp.content) > 1000:
                return resp.content
            last_err = f"HTTP {resp.status_code}"
            time.sleep(1 + attempt)
        except Exception as e:
            last_err = str(e)
            time.sleep(1 + attempt)
    raise RuntimeError(f"Failed to download cutout: {last_err}")


def process_image_array(data: np.ndarray, vmin=None, vmax=None) -> bytes:
    """Process 2D FITS image array to 512px PNG bytes with ZScale and Asinh."""
    if data is None or data.size == 0:
        raise ValueError("Image data is empty")

    finite_mask = np.isfinite(data)
    if not np.any(finite_mask):
        raise ValueError("Image is all-NaN")

    finite_data = data[finite_mask]
    if np.std(finite_data) == 0:
        raise ValueError("Image has zero variance (constant)")

    clean = data.copy()
    clean[~finite_mask] = np.nanmedian(finite_data)

    if vmin is None or vmax is None:
        try:
            zscale = ZScaleInterval()
            vmin, vmax = zscale.get_limits(clean)
        except Exception:
            vmin, vmax = float(np.nanpercentile(clean, 1)), float(np.nanpercentile(clean, 99))

    if vmax <= vmin:
        vmax = vmin + 1.0

    norm_val = np.clip((clean - vmin) / (vmax - vmin), 0.0, 1.0)
    stretch = AsinhStretch()
    stretched = np.clip(stretch(norm_val), 0.0, 1.0)

    img_array = (stretched * 255).astype(np.uint8)
    img = Image.fromarray(img_array[::-1], mode="L")

    w, h = img.size
    if w > 0:
        scale = max(1, PNG_SIZE // w)
        img = img.resize((w * scale, h * scale), Image.NEAREST)

    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_unavailable_placeholder(frame_id: str, reason: str) -> bytes:
    """Generate an informative fallback image when upstream IRSA cutout is unavailable."""
    from PIL import Image, ImageDraw
    import io
    img = Image.new("L", (PNG_SIZE, PNG_SIZE), color=18)
    draw = ImageDraw.Draw(img)
    # Draw subtle grid lines
    for coord in range(0, PNG_SIZE, 64):
        draw.line([(coord, 0), (coord, PNG_SIZE)], fill=35)
        draw.line([(0, coord), (PNG_SIZE, coord)], fill=35)
    # Draw crosshair
    mid = PNG_SIZE // 2
    draw.line([(mid - 16, mid), (mid + 16, mid)], fill=80)
    draw.line([(mid, mid - 16), (mid, mid + 16)], fill=80)
    
    # Text annotations
    draw.text((24, 180), "SPHEREx CUTOUT UNAVAILABLE", fill=230)
    draw.text((24, 215), f"Frame ID: {frame_id}", fill=160)
    clean_reason = (reason[:55] + "...") if len(reason) > 55 else reason
    draw.text((24, 245), f"Reason: {clean_reason}", fill=140)
    draw.text((24, 290), "IRSA upstream archive did not return FITS data.", fill=120)
    draw.text((24, 320), "Step forward or backward to view adjacent epochs.", fill=190)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ─── API Endpoints ───────────────────────────────────────────────────────────

@app.get("/api/resolve")
async def resolve_name(name: str = Query(..., min_length=1)):
    """Resolve an astronomical object name to RA/Dec (15 s timeout)."""
    def _do_resolve():
        try:
            coord = SkyCoord.from_name(name)
            return {"ra": round(float(coord.ra.deg), 6), "dec": round(float(coord.dec.deg), 6), "name": name}, None
        except Exception as e:
            return None, str(e)

    try:
        result, err = await asyncio.wait_for(asyncio.to_thread(_do_resolve), timeout=15.0)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Could not resolve '{name}': {err}")
        return result
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail=f"Resolver timed out looking up '{name}'")


@app.get("/api/bands")
async def get_bands():
    """Return the list of real SPHEREx detector bands."""
    global _bands_cache, _bands_cache_time
    if _bands_cache and (time.time() - _bands_cache_time) < TAP_CACHE_TTL:
        return {"bands": _bands_cache}

    if is_offline():
        return {"bands": ALLOWED_BANDS}

    def _query():
        q = "SELECT DISTINCT energy_bandpassname FROM spherex.plane ORDER BY energy_bandpassname"
        res = Irsa.query_tap(q)
        raw_bands = [str(r["energy_bandpassname"]) for r in res.to_table()]
        return [b for b in raw_bands if b.startswith("SPHEREx-D")]

    try:
        bands = await asyncio.wait_for(asyncio.to_thread(_query), timeout=3.0)
        if bands:
            _bands_cache = bands
            _bands_cache_time = time.time()
            return {"bands": bands}
    except Exception as e:
        logger.warning(f"Could not query bands dynamically: {e}")

    return {"bands": ALLOWED_BANDS}


@app.get("/api/observations")
async def get_observations(
    ra: float = Query(...),
    dec: float = Query(...),
    band: str = Query("SPHEREx-D2"),
    size: float = Query(0.1),
):
    """Query IRSA for SPHEREx observations matching coordinates and detector band."""
    validate_params(ra, dec, size, band)

    if is_offline():
        sample = load_sample_data()
        if sample:
            sample["sample_mode"] = True
            sample["sample_reason"] = "Offline mode requested (SPHEREX_OFFLINE=1)"
            return sample
        raise HTTPException(status_code=503, detail="Offline mode requested but no sample data available")

    cache_key = f"{ra:.5f}_{dec:.5f}_{size:.4f}_{band}"
    if cache_key in _observations_cache:
        cached_time, cached_data = _observations_cache[cache_key]
        if time.time() - cached_time < TAP_CACHE_TTL:
            return cached_data

    query = f"""
    SELECT
        'https://irsa.ipac.caltech.edu/' || a.uri || '?center={ra},{dec}d&size={size}' AS uri,
        p.time_bounds_lower,
        p.energy_bandpassname,
        a.uri AS raw_uri
    FROM spherex.artifact a
    JOIN spherex.plane p ON a.planeid = p.planeid
    WHERE 1 = CONTAINS(POINT('ICRS', {ra}, {dec}), p.poly)
        AND p.energy_bandpassname = '{band}'
    ORDER BY p.time_bounds_lower
    """

    def _run_tap():
        return Irsa.query_tap(query)

    try:
        results = await asyncio.wait_for(asyncio.to_thread(_run_tap), timeout=12.0)
        table = results.to_table()
    except Exception as e:
        logger.warning(f"TAP query failed or timed out: {e}")
        sample = load_sample_data()
        if sample:
            sample["sample_mode"] = True
            sample["sample_reason"] = "NASA IRSA archive is temporarily busy. Displaying verified SPHEREx observation dataset."
            return sample
        raise HTTPException(status_code=502, detail=f"TAP query failed: {str(e)}")

    observations = []
    rows = list(table)
    if len(rows) > 120:
        step = len(rows) / 120.0
        indices = [int(i * step) for i in range(120)]
        rows = [rows[i] for i in indices]

    for row in rows:
        raw_uri = str(row["raw_uri"])
        uri = str(row["uri"])
        mjd = float(row["time_bounds_lower"])
        iso_time = Time(mjd, format="mjd").iso
        frame_id = make_id(raw_uri)
        release = parse_release(raw_uri)
        version = parse_version(raw_uri)

        info = {
            "id": frame_id,
            "uri": uri,
            "raw_uri": raw_uri,
            "ra": ra,
            "dec": dec,
            "size": size,
            "band": band,
            "mjd": mjd,
            "release": release,
            "version": version,
        }
        save_frame_info(frame_id, info)

        observations.append({
            "id": frame_id,
            "time_iso": iso_time,
            "time_mjd": mjd,
            "detector": str(row["energy_bandpassname"]),
            "release": release,
            "version": version,
        })

    # Ensure sorted by observation time
    observations.sort(key=lambda o: o["time_mjd"])

    result_json = {
        "observations": observations,
        "count": len(observations),
        "sample_mode": False,
        "releases": sorted(list({o["release"] for o in observations})),
    }

    _observations_cache[cache_key] = (time.time(), result_json)
    return result_json


@app.get("/api/meta/{frame_id}")
async def get_meta(frame_id: str):
    """Return metadata (central wavelength and version) for a frame."""
    if frame_id in _meta_cache:
        return _meta_cache[frame_id]

    # Check disk cache for meta
    meta_cache_file = CACHE_DIR / f"meta_{frame_id}.json"
    if meta_cache_file.exists():
        try:
            with open(meta_cache_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
                _meta_cache[frame_id] = meta
                return meta
        except Exception:
            pass

    # Check sample data
    sample_data = load_sample_data()
    if sample_data and "meta" in sample_data and frame_id in sample_data["meta"]:
        return sample_data["meta"][frame_id]

    frame_info = get_frame_info(frame_id)
    if not frame_info:
        raise HTTPException(status_code=404, detail=f"Unknown frame ID '{frame_id}'")

    def _fetch_meta():
        content = download_fits_bytes(frame_info["uri"])
        import io
        with fits.open(io.BytesIO(content)) as hdul:
            wl = calc_wavelength_from_hdulist(hdul)
            version = hdul[0].header.get("VERSION", frame_info.get("version", "unknown"))
        meta = {
            "id": frame_id,
            "central_wavelength_um": round(wl, 4),
            "version": str(version),
            "release": frame_info.get("release", "unknown"),
        }
        try:
            with open(meta_cache_file, "w", encoding="utf-8") as f:
                json.dump(meta, f)
        except Exception:
            pass
        return meta

    async with _download_semaphore:
        try:
            meta = await asyncio.to_thread(_fetch_meta)
            _meta_cache[frame_id] = meta
            return meta
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to fetch metadata: {str(e)}")


@app.get("/api/image/{frame_id}.png")
async def get_image(frame_id: str):
    """Return processed PNG image for a frame (cached on disk)."""
    # 1. Check disk cache
    cached_png = CACHE_DIR / f"{frame_id}.png"
    if cached_png.exists():
        return Response(content=cached_png.read_bytes(), media_type="image/png")

    # 2. Check offline sample files
    sample_png = SAMPLES_DIR / f"{frame_id}.png"
    if sample_png.exists():
        return Response(content=sample_png.read_bytes(), media_type="image/png")

    # 3. Check frame info
    frame_info = get_frame_info(frame_id)
    if not frame_info:
        raise HTTPException(status_code=404, detail=f"Unknown frame ID '{frame_id}'")

    def _fetch_and_process():
        content = download_fits_bytes(frame_info["uri"])
        import io
        with fits.open(io.BytesIO(content)) as hdul:
            image_hdu = None
            for h in hdul:
                if h.name == "IMAGE":
                    image_hdu = h
                    break
            if image_hdu is None and len(hdul) > 1:
                image_hdu = hdul[1]
            if image_hdu is None:
                raise ValueError("No IMAGE extension in FITS cutout")

            data = image_hdu.data.astype(np.float32)
            png_bytes = process_image_array(data)

            # Also cache metadata
            wl = calc_wavelength_from_hdulist(hdul)
            version = hdul[0].header.get("VERSION", frame_info.get("version", "unknown"))
            meta = {
                "id": frame_id,
                "central_wavelength_um": round(wl, 4),
                "version": str(version),
                "release": frame_info.get("release", "unknown"),
            }
            _meta_cache[frame_id] = meta
            meta_cache_file = CACHE_DIR / f"meta_{frame_id}.json"
            try:
                with open(meta_cache_file, "w", encoding="utf-8") as f:
                    json.dump(meta, f)
            except Exception:
                pass

            return png_bytes

    async with _download_semaphore:
        try:
            png_bytes = await asyncio.to_thread(_fetch_and_process)
            cached_png.write_bytes(png_bytes)
            return Response(content=png_bytes, media_type="image/png")
        except ValueError as ve:
            placeholder_bytes = make_unavailable_placeholder(frame_id, str(ve))
            return Response(content=placeholder_bytes, media_type="image/png", headers={"X-Frame-Status": "invalid"})
        except Exception as e:
            logger.warning(f"Cutout unavailable for {frame_id}: {e}")
            placeholder_bytes = make_unavailable_placeholder(frame_id, str(e))
            return Response(content=placeholder_bytes, media_type="image/png", headers={"X-Frame-Status": "unavailable"})


@app.get("/api/sample")
async def get_sample():
    """Return offline sample dataset."""
    data = load_sample_data()
    if not data:
        raise HTTPException(status_code=404, detail="No sample data bundled")
    data["sample_mode"] = True
    data["sample_reason"] = "Sample demonstration dataset"
    return data


def load_sample_data() -> Optional[dict]:
    meta_path = SAMPLES_DIR / "metadata.json"
    if not meta_path.exists():
        return None
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading sample data: {e}")
        return None


# ─── Static files (MUST BE MOUNTED LAST) ─────────────────────────────────────
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static_dir")
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
else:
    @app.get("/")
    async def root():
        return {"message": "SPHEREx Sky Viewer Backend running. Static files not yet created."}


if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    logger.info(f"Starting server on http://{host}:{port}")
    uvicorn.run("app:app", host=host, port=port, reload=False)
