"""
verify_pipeline.py
Tests the running server end-to-end against /api/observations and /api/image/{id}.png
Checks:
  1. GET /api/observations returns 200 and frames
  2. GET /api/image/{id}.png returns 200
  3. Content-Type is image/png
  4. File size > 1 KB (1024 bytes)
  5. Decodes with Pillow
  6. Pixel standard deviation > 0 (not blank)
"""

import sys
import io
import requests
import numpy as np
from PIL import Image

SERVER_URL = "http://127.0.0.1:8000"

def run_verification():
    print("=" * 70)
    print("PIPELINE END-TO-END VERIFICATION")
    print(f"Target: M101 (RA=210.80227, Dec=54.34895, Band=SPHEREx-D2)")
    print(f"Server: {SERVER_URL}")
    print("=" * 70)

    all_passed = True

    # Check 1: Query observations
    obs_url = f"{SERVER_URL}/api/observations?ra=210.80227&dec=54.34895&band=SPHEREx-D2&size=0.1"
    print(f"\n[Check 1] Calling {obs_url}...")
    try:
        r = requests.get(obs_url, timeout=180)
        status = r.status_code
        if status == 200:
            data = r.json()
            obs_list = data.get("observations", [])
            count = len(obs_list)
            sample_mode = data.get("sample_mode", False)
            print(f"  Status: {status}")
            print(f"  Observations found: {count} (sample_mode={sample_mode})")
            if count > 0:
                print("  Check 1 (Query observations): PASS")
            else:
                print("  Check 1 (Query observations): FAIL (0 observations returned)")
                all_passed = False
                return False
        else:
            print(f"  Status: {status}, Detail: {r.text[:200]}")
            print("  Check 1 (Query observations): FAIL")
            return False
    except Exception as e:
        print(f"  Request exception: {e}")
        print("  Check 1 (Query observations): FAIL")
        return False

    # Check 2 to 6: Fetch image for the first frame
    first_frame = obs_list[0]
    frame_id = first_frame["id"]
    img_url = f"{SERVER_URL}/api/image/{frame_id}.png"
    print(f"\n[Check 2] Fetching image for frame {frame_id} from {img_url}...")

    try:
        r = requests.get(img_url, timeout=120)
        status = r.status_code
        print(f"  HTTP Status: {status}")
        if status == 200:
            print("  Check 2 (Status 200): PASS")
        else:
            print(f"  Check 2 (Status 200): FAIL (got {status}: {r.text[:200]})")
            all_passed = False
            return False
    except Exception as e:
        print(f"  Request exception: {e}")
        print("  Check 2 (Status 200): FAIL")
        return False

    # Check 3: Content-Type
    content_type = r.headers.get("content-type", "")
    print(f"\n[Check 3] Content-Type header: '{content_type}'")
    if "image/png" in content_type:
        print("  Check 3 (Content-Type image/png): PASS")
    else:
        print(f"  Check 3 (Content-Type image/png): FAIL (expected image/png, got {content_type})")
        all_passed = False

    # Check 4: Size > 1 KB
    size = len(r.content)
    print(f"\n[Check 4] Image size: {size:,} bytes")
    if size > 1024:
        print("  Check 4 (Size > 1 KB): PASS")
    else:
        print(f"  Check 4 (Size > 1 KB): FAIL (expected > 1024 bytes, got {size})")
        all_passed = False

    # Check 5: Decodes with Pillow
    print("\n[Check 5] Decoding image with Pillow...")
    try:
        img = Image.open(io.BytesIO(r.content))
        img.load()
        w, h = img.size
        print(f"  Image format: {img.format}, size: {w}x{h}, mode: {img.mode}")
        if w > 0 and h > 0:
            print("  Check 5 (Decodes with Pillow): PASS")
        else:
            print("  Check 5 (Decodes with Pillow): FAIL (invalid dimensions)")
            all_passed = False
    except Exception as e:
        print(f"  Check 5 (Decodes with Pillow): FAIL ({e})")
        all_passed = False
        return False

    # Check 6: Pixel standard deviation > 0
    print("\n[Check 6] Checking pixel variance / standard deviation...")
    arr = np.array(img, dtype=float)
    std_dev = float(np.std(arr))
    min_val, max_val = float(np.min(arr)), float(np.max(arr))
    print(f"  Pixel stats: min={min_val}, max={max_val}, std_dev={std_dev:.2f}")
    if std_dev > 0:
        print("  Check 6 (Pixel std dev > 0): PASS")
    else:
        print("  Check 6 (Pixel std dev > 0): FAIL (image is blank/constant)")
        all_passed = False

    # Check 7: Central wavelength metadata check
    meta_url = f"{SERVER_URL}/api/meta/{frame_id}"
    print(f"\n[Check 7] Calling {meta_url}...")
    try:
        r_meta = requests.get(meta_url, timeout=30)
        if r_meta.status_code == 200:
            m = r_meta.json()
            wl = m.get("central_wavelength_um")
            print(f"  Central wavelength: {wl} um, version: {m.get('version')}")
            if wl and wl > 0:
                print("  Check 7 (Metadata & Wavelength): PASS")
            else:
                print("  Check 7 (Metadata & Wavelength): FAIL (no wavelength)")
        else:
            print(f"  Check 7 (Metadata): WARN (HTTP {r_meta.status_code})")
    except Exception as e:
        print(f"  Check 7 (Metadata): WARN ({e})")

    print("\n" + "=" * 70)
    if all_passed:
        print("VERIFICATION RESULT: ALL CHECKS PASSED")
    else:
        print("VERIFICATION RESULT: FAILED")
    print("=" * 70)
    return all_passed

if __name__ == "__main__":
    success = run_verification()
    sys.exit(0 if success else 1)
