#!/bin/bash
set -euo pipefail

PROJECT_DIR="/Users/takuma/Projects/ai-news-monitor"
MODE="${1:-ai}"

cd "$PROJECT_DIR"
mkdir -p logs

TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
echo "[$TIMESTAMP] === run.sh start (mode=$MODE) ===" >> logs/monitor.log

if "$PROJECT_DIR/venv/bin/python" monitor.py --mode "$MODE" >> logs/monitor.log 2>&1; then
    echo "[$TIMESTAMP] === run.sh success ===" >> logs/monitor.log
else
    EXIT_CODE=$?
    echo "[$TIMESTAMP] === run.sh FAILED (exit=$EXIT_CODE) ===" >> logs/monitor.log
    exit $EXIT_CODE
fi
