# AI Diagram Grader (Flowchart + ER)

Web application for teachers to check diagram exams using Computer Vision + OCR.

## Features
- Login with roles (admin/teacher/ta)
- Create exam
- Upload answer key (image or PDF first page)
- Configure rubric weights
- Batch upload student submissions
- Auto analyze and score
- Detail feedback + OCR text
- Manual score override with audit log
- Export results to CSV/XLSX

## Default Accounts
- `admin / admin123`
- `teacher / teacher123`
- `ta / ta123`

## Quick Start
1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Run app:
   ```bash
   python app.py
   ```
3. Open:
   - http://127.0.0.1:5000

## Easy Run/Stop Commands (Windows)
Use these scripts so you do not need to remember which Python interpreter to use:

```powershell
.\run_web.ps1
```

```powershell
.\stop_web.ps1
```

Or double-click command files:
- `run_web.cmd`
- `stop_web.cmd`

## Notes
- OCR uses `pytesseract`. For best OCR, install Tesseract OCR binary in your OS.
- Detection is heuristic-based OpenCV pipeline (MVP), ready to be replaced by YOLO model in next phase.
- Database file: `data/app.db`
- Uploads: `uploads/`
- Exports: `data/exports/`

## OCR Provider Modes
- Default: `local` (Tesseract in machine)
- Web API mode: `ocrspace`

Set environment variables before run:

```powershell
$env:OCR_PROVIDER = "ocrspace"
$env:OCRSPACE_API_KEY = "your_api_key"   # optional, default = helloworld
python app.py
```

If you use local Tesseract and binary is not in PATH:

```powershell
$env:TESSERACT_CMD = "C:\\Program Files\\Tesseract-OCR\\tesseract.exe"
python app.py
```
