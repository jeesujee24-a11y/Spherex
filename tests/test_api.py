"""
Tests for SPHEREx Sky Viewer Backend API.
Includes mocked tests for input validation, error handling, sorting, sample mode,
and one marked live network test.
"""

import os
import json
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
import numpy as np
from astropy.table import Table

from app import app, process_image_array, make_id, parse_release, parse_version

client = TestClient(app)

# ─── Unit tests ──────────────────────────────────────────────────────────────

def test_make_id():
    id1 = make_id("uri_one")
    id2 = make_id("uri_two")
    assert len(id1) == 12
    assert "/" not in id1
    assert id1 != id2
    assert id1 == make_id("uri_one")


def test_parse_release():
    assert parse_release("ibe/data/spherex/dr1/level2/something.fits") == "DR1"
    assert parse_release("ibe/data/spherex/qr3/level2/something.fits") == "QR3"
    assert parse_release("ibe/data/spherex/qr2/level2/something.fits") == "QR2"
    assert parse_release("ibe/data/spherex/dr2/level2/something.fits") == "unknown"


def test_parse_version():
    assert parse_version("level2_2025W19_2B_0346_1D2_spx_l2b-v27-2026-198.fits") == "l2b-v27-2026-198"
    assert parse_version("level2_2025W21_2C_0401_4D2_spx_l2b_retry-v27-2026-199.fits") == "l2b_retry-v27-2026-199"


def test_process_image_array_valid():
    # Random normal distribution
    rng = np.random.default_rng(42)
    data = rng.normal(loc=1.0, scale=0.2, size=(60, 60))
    png_bytes = process_image_array(data)
    assert len(png_bytes) > 500
    assert png_bytes[:8] == b'\x89PNG\r\n\x1a\n'


def test_process_image_array_all_nan():
    data = np.full((50, 50), np.nan)
    with pytest.raises(ValueError, match="all-NaN"):
        process_image_array(data)


def test_process_image_array_constant():
    data = np.full((50, 50), 5.0)
    with pytest.raises(ValueError, match="constant"):
        process_image_array(data)


# ─── API endpoint tests (Mocked) ─────────────────────────────────────────────

def test_api_bands():
    response = client.get("/api/bands")
    assert response.status_code == 200
    data = response.json()
    assert "bands" in data
    assert "SPHEREx-D1" in data["bands"]
    assert "SPHEREx-D2" in data["bands"]
    assert "SPHEREx-D6" in data["bands"]


def test_api_validation_ra_out_of_range():
    response = client.get("/api/observations?ra=365.0&dec=0.0&band=SPHEREx-D2&size=0.1")
    assert response.status_code == 400
    assert "RA must be in [0, 360)" in response.json()["detail"]


def test_api_validation_dec_out_of_range():
    response = client.get("/api/observations?ra=100.0&dec=-95.0&band=SPHEREx-D2&size=0.1")
    assert response.status_code == 400
    assert "Dec must be in [-90, 90]" in response.json()["detail"]


def test_api_validation_size_out_of_range():
    response = client.get("/api/observations?ra=100.0&dec=0.0&band=SPHEREx-D2&size=0.5")
    assert response.status_code == 400
    assert "Size must be in (0, 0.2]" in response.json()["detail"]


def test_api_validation_invalid_band():
    response = client.get("/api/observations?ra=100.0&dec=0.0&band=INVALID_BAND&size=0.1")
    assert response.status_code == 400
    assert "not supported" in response.json()["detail"]


def test_api_sample_endpoint():
    response = client.get("/api/sample")
    assert response.status_code == 200
    data = response.json()
    assert data["sample_mode"] is True
    assert "observations" in data
    assert len(data["observations"]) > 0
    first = data["observations"][0]
    assert "id" in first
    assert "time_iso" in first
    assert "time_mjd" in first
    assert "detector" in first
    assert "release" in first


def test_api_sample_image_endpoint():
    sample_data = client.get("/api/sample").json()
    first_id = sample_data["observations"][0]["id"]
    response = client.get(f"/api/image/{first_id}.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert len(response.content) > 1000


def test_api_sample_meta_endpoint():
    sample_data = client.get("/api/sample").json()
    first_id = sample_data["observations"][0]["id"]
    response = client.get(f"/api/meta/{first_id}")
    assert response.status_code == 200
    meta = response.json()
    assert meta["id"] == first_id
    assert "central_wavelength_um" in meta
    assert meta["central_wavelength_um"] > 0


def test_api_offline_mode_env():
    with patch.dict(os.environ, {"SPHEREX_OFFLINE": "1"}):
        response = client.get("/api/observations?ra=210.8&dec=54.3&band=SPHEREx-D2&size=0.1")
        assert response.status_code == 200
        data = response.json()
        assert data["sample_mode"] is True


def test_api_observations_mocked_sorting():
    # Mock TAP return with unordered timestamps
    mock_table = Table({
        "uri": [
            "https://irsa.ipac.caltech.edu/b.fits?center=0,0&size=0.1",
            "https://irsa.ipac.caltech.edu/a.fits?center=0,0&size=0.1",
        ],
        "raw_uri": ["b.fits", "a.fits"],
        "time_bounds_lower": [60900.0, 60800.0],
        "energy_bandpassname": ["SPHEREx-D2", "SPHEREx-D2"],
    })

    mock_res = MagicMock()
    mock_res.to_table.return_value = mock_table

    with patch("app.Irsa.query_tap", return_value=mock_res):
        response = client.get("/api/observations?ra=10.0&dec=10.0&band=SPHEREx-D2&size=0.1")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
        obs = data["observations"]
        assert obs[0]["time_mjd"] <= obs[1]["time_mjd"]


def test_api_resolve_mocked_success():
    with patch("astropy.coordinates.SkyCoord.from_name") as mock_coord:
        mock_obj = MagicMock()
        mock_obj.ra.deg = 210.80227
        mock_obj.dec.deg = 54.34895
        mock_coord.return_value = mock_obj

        response = client.get("/api/resolve?name=M101")
        assert response.status_code == 200
        data = response.json()
        assert data["ra"] == 210.80227
        assert data["dec"] == 54.34895


def test_api_resolve_mocked_failure():
    with patch("astropy.coordinates.SkyCoord.from_name", side_effect=Exception("Object not found")):
        response = client.get("/api/resolve?name=NonExistentObjectXYZ")
        assert response.status_code == 404
        assert "Could not resolve" in response.json()["detail"]


# ─── Live network test ───────────────────────────────────────────────────────

@pytest.mark.network
def test_api_live_resolve():
    response = client.get("/api/resolve?name=M101")
    assert response.status_code == 200
    data = response.json()
    assert 210.0 < data["ra"] < 211.5
    assert 54.0 < data["dec"] < 55.0
