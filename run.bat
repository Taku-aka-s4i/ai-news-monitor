@echo off
cd /d C:\brain\tools\ai-monitor
call venv\Scripts\activate
python monitor.py >> logs\monitor.log 2>&1
