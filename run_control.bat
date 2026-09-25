@echo off
cd /d "%~dp0"
.venv\Scripts\python.exe control_center.py --server ws://127.0.0.1:8000/ws
pause
