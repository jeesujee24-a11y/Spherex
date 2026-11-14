# One-Line Start & Deployment Commands

🌐 **Live Public Deployment:** [https://spherex-zv5t.onrender.com/](https://spherex-zv5t.onrender.com/)

Quick start commands for running the SPHEREx Sky Viewer locally or deploying across platforms.

---

### Windows (PowerShell)
```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -r requirements.txt; python -m uvicorn app:app --host 127.0.0.1 --port 8000
```
*If script execution is disabled on PowerShell, use:*
```cmd
py -3 -m venv .venv && .\.venv\Scripts\pip.exe install -r requirements.txt && .\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

---

### macOS (Terminal)
```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && uvicorn app:app --host 127.0.0.1 --port 8000
```

---

### Linux (Bash)
```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && uvicorn app:app --host 127.0.0.1 --port 8000
```

---

### Offline / Sample Mode (Any Platform)

**Windows PowerShell:**
```powershell
$env:SPHEREX_OFFLINE="1"; .\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

**macOS / Linux:**
```bash
SPHEREX_OFFLINE=1 .venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000
```
