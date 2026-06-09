@echo off
cd /d C:\brain\tools\ai-monitor
call venv\Scripts\activate
python monitor.py --mode ai >> logs\monitor-ai.log 2>&1
