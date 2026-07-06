#!/bin/bash
set -uo pipefail

PROJECT_DIR="/Users/takuma/Projects/ai-news-monitor"
MODE="${1:-ai}"
MAX_SECONDS=480   # 上限8分。ハング時に強制終了し、launchdの次回実行がブロックされるのを防ぐ番犬

cd "$PROJECT_DIR"
mkdir -p logs

TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
echo "[$TIMESTAMP] === run.sh start (mode=$MODE) ===" >> logs/monitor.log

# monitor.py をバックグラウンド起動し、番犬プロセスで上限時間を監視する
"$PROJECT_DIR/venv/bin/python" monitor.py --mode "$MODE" >> logs/monitor.log 2>&1 &
PY_PID=$!

(
    sleep "$MAX_SECONDS"
    if kill -0 "$PY_PID" 2>/dev/null; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] === run.sh TIMEOUT (killing monitor.py after ${MAX_SECONDS}s) ===" >> logs/monitor.log
        kill -TERM "$PY_PID" 2>/dev/null
        sleep 5
        kill -KILL "$PY_PID" 2>/dev/null
    fi
) &
WATCHDOG_PID=$!

if wait "$PY_PID"; then
    EXIT_CODE=0
else
    EXIT_CODE=$?
fi

# 正常終了時は番犬がまだsleep中なので後始末する
kill "$WATCHDOG_PID" 2>/dev/null
wait "$WATCHDOG_PID" 2>/dev/null

if [ "$EXIT_CODE" -eq 0 ]; then
    echo "[$TIMESTAMP] === run.sh success ===" >> logs/monitor.log
else
    echo "[$TIMESTAMP] === run.sh FAILED (exit=$EXIT_CODE) ===" >> logs/monitor.log
    exit "$EXIT_CODE"
fi
