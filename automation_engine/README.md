# XHS Automation Engine (Mobile Worker)

This is the industrial-grade mobile and browser automation engine for `XHS_ALL_IN_ONE`.

## 📌 Architecture
This module has been extracted from the legacy `scripts/` directory to form a cohesive, decoupled microservice and execution engine. 
It operates independently from the Web Backend and Frontend, allowing it to be deployed on edge nodes (e.g., local machines connected to Android devices via USB) or GPU-enabled servers (for PaddleOCR).

### Directory Structure
- `mobile_core/`: Core SDK containing state machines, vision logic, watchdogs, and agentless drivers.
- `tools/`: Utility scripts for device optimization and template generation.
- `start_mobile_driver_v2.py`: Entrypoint for the purely visual, physical-simulation-based Android driver.
- `start_ocr_server.py`: FastAPI microservice wrapping PaddleOCR to provide text extraction without inflating the main Backend environment.
- `start_browser_automation.py`: Browser-based CDP/Playwright execution engine.
- `start_single_run.py`: Development/testing entrypoint for single execution flows.

## 🛠 Setup & Installation

Due to the heavy dependencies (OpenCV, PaddleOCR), it is highly recommended to use a separate Python virtual environment for this engine.

```bash
cd automation_engine
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 🚀 Running the Services

**1. Start the OCR Microservice**
```bash
uvicorn start_ocr_server:app --host 0.0.0.0 --port 8000
```

**2. Start the Mobile Driver**
```bash
python start_mobile_driver_v2.py
```
