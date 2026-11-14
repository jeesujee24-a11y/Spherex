# SPHEREx Sky Viewer

> **A public-facing web tool to display images of the sky from the NASA SPHEREx mission, making it easy for anyone to quickly see how they change over time.**
> *Created for the NASA Space Apps Challenge.*

🌐 **Live Public Web Application:** [https://spherex-zv5t.onrender.com/](https://spherex-zv5t.onrender.com/)  
📦 **GitHub Repository:** [https://github.com/jeesujee24-a11y/Spherex](https://github.com/jeesujee24-a11y/Spherex)

---

## Disclaimer
**Unofficial Project**: This web application is an independent community project created for the NASA Space Apps Challenge and is **not affiliated with, endorsed by, or sponsored by NASA, JPL-Caltech, or IPAC**.

---

## Key Features

- **Time-Series Astronomy Player**: Interactive time slider and playback engine to step through observations chronologically.
- **Blink Comparison Mode**: High-contrast toggling between paired frames at configurable blink rates (0.5 Hz to 4 Hz) to spot transients and variations.
- **Detector Band Selector**: Dynamic filtering across SPHEREx detector arrays (`SPHEREx-D1` through `SPHEREx-D6`, covering 0.75 μm to 5.0 μm).
- **Target Coordinate Resolver**: Instant celestial coordinate lookup using SIMBAD / Sesame (e.g., `M101`, `NGC 6543`, `Crab Nebula`).
- **Scientific Visualization**:
  - Pixel-perfect astronomical rendering (`image-rendering: pixelated`).
  - Robust non-linear contrast stretching via `astropy.visualization.ZScaleInterval` and `AsinhStretch`.
  - Non-finite pixel handling (NaN/Inf masking and interpolation).
  - Physical central wavelength calculation derived directly from multi-extension `WCS-WAVE` tables.
- **Zero Blank Pages / Graceful Degradation**:
  - Live TAP search against NASA/IPAC IRSA.
  - Progressive frame loading (initial frame displays immediately).
  - Bundled offline sample dataset for offline/fallback mode.
- **Accessibility & Responsiveness**:
  - WCAG-compliant high-contrast theme.
  - Keyboard navigation: `Space` (Play/Pause), `Left`/`Right` (Step Frame), `B` (Blink Mode).
  - Full mobile responsiveness tested down to 390px viewports.
  - Honors `prefers-reduced-motion` settings.

---

## Scientific Context & Limitations

### What is SPHEREx?
The Spectro-Photometer for the History of the Universe, Epoch of Reionization, and Ices Explorer (**SPHEREx**) is a NASA astrophysics Medium-Class Explorer mission. SPHEREx maps the entire sky in 102 near-infrared spectral bands between 0.75 μm and 5.00 μm using Linear Variable Filters (LVFs).

### Crucial Limitation: Time vs. Wavelength
SPHEREx observes using linear variable spectral filters mounted over its detector arrays. As the spacecraft scans the sky, a given celestial position falls onto different locations on the detector, which corresponds to **different wavelengths**.
> **Important Note for Viewers**: Consecutive frames in a time-series for a single point on the sky are captured at **both different observation times and different wavelengths**. Apparent brightness changes between consecutive frames reflect both the physical spectrum (color) of the object and any intrinsic time-domain variability. The central wavelength is displayed for every frame to provide this critical context.

---

## Data Sources & Releases

- **NASA/IPAC Infrared Science Archive (IRSA)**:
  - TAP Service: `https://irsa.ipac.caltech.edu/TAP`
  - Tables: `spherex.artifact` (a) and `spherex.plane` (p)
  - Cutout Service: `https://irsa.ipac.caltech.edu/data/SPHEREx/docs/cutout_tool.html`
- **Current Data Releases**:
  - `DR1` (Data Release 1)
  - `QR3` (Quick Release 3)
  *(Note: QR2 has been superseded and is excluded from searches).*

---

## Quick Start

### Prerequisites
- Python 3.10+ (tested on Python 3.14)
- Git

### 1. Setup Environment
```bash
git clone <repo-url>
cd spherex-viewer
python -m venv .venv

# Windows
.\.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Run the Application
```bash
uvicorn app:app --host 127.0.0.1 --port 8000
```
Open your browser to `http://127.0.0.1:8000`.

### 3. Run Offline / Sample Mode
To run without external internet access using the bundled sample datasets:
```bash
# Windows PowerShell
$env:SPHEREX_OFFLINE="1"; uvicorn app:app --host 127.0.0.1 --port 8000

# macOS / Linux
SPHEREX_OFFLINE=1 uvicorn app:app --host 127.0.0.1 --port 8000
```

---

## Running Verification & Tests

### Automated Unit & API Tests
```bash
# Run mock and unit tests (excludes live network queries)
python -m pytest tests -v -m "not live"

# Run all tests including live IRSA TAP query
python -m pytest tests -v
```

### End-to-End Pipeline Verification
With the server running on `http://127.0.0.1:8000`:
```bash
python verify_pipeline.py
python test_e2e_http.py
```

---

## Project Structure

```
spherex-viewer/
├── app.py                 # FastAPI backend (TAP query, FITS processing, caching)
├── fetch_test.py          # Phase 1 IRSA data access proof script
├── download_samples.py    # Offline sample downloader script
├── verify_pipeline.py     # End-to-end pipeline verification script
├── test_e2e_http.py       # HTTP route & validation test suite
├── requirements.txt       # Production dependencies
├── requirements.lock.txt  # Pinned frozen dependencies
├── pytest.ini             # Pytest configuration
├── samples/               # Bundled real offline sample dataset (< 5 MB)
│   ├── metadata.json      # Offline observation metadata
│   └── *.png              # Pre-processed sample frames
├── static/                # Standalone frontend (Zero CDN dependencies)
│   ├── index.html         # Accessible HTML5 interface
│   ├── styles.css         # Dark theme & responsive CSS
│   └── app.js             # Client state, player, blink, & fetch logic
├── screenshots/           # End-to-end browser verification captures
│   ├── screenshot_m101_desktop.png
│   ├── screenshot_blink_desktop.png
│   ├── screenshot_error_desktop.png
│   └── screenshot_mobile_390.png
└── tests/
    └── test_api.py        # Pytest test suite (19 tests)
```

---

## Credits & Acknowledgements

- **NASA**: The National Aeronautics and Space Administration.
- **JPL / Caltech**: Jet Propulsion Laboratory and California Institute of Technology for managing the SPHEREx mission.
- **IRSA**: The NASA/IPAC Infrared Science Archive for hosting the public SPHEREx data access TAP queries and Cutout services.
- **AstroPy & Astroquery**: Astropy Project for astronomical coordinate and FITS processing tools.
