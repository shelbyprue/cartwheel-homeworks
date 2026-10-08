#!/bin/sh
# Local stand-in for .github/workflows/monitor.yml. Run by hand: launchd cannot read
# ~/Desktop (macOS privacy controls), see monitoring/README.md.
#
# Same command as the workflow's "Monitor the previous 24 hours" step. The copy
# afterwards replaces "Upload the monitoring output": it runs whatever the exit
# status, like the workflow's `if: always()`. Archives go to .hw7-logs/scheduled-runs/
# (gitignored), one folder per run.
set -u
cd "$(dirname "$0")/.."
stamp=$(date -u +%Y-%m-%dT%H-%M-%SZ)
archive=".hw7-logs/scheduled-runs/$stamp"
mkdir -p "$archive"

.venv/bin/python -m monitoring.run --last-hours 24 --yes > "$archive/run.log" 2>&1
status=$?

cp -R monitoring/output "$archive/output"
echo "exit status: $status" >> "$archive/run.log"
exit "$status"
