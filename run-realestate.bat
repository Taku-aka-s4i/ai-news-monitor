@echo off
cd /d C:\brain\tools\ai-monitor
call venv\Scripts\activate
python monitor.py --mode realestate >> logs\monitor-realestate.log 2>&1
