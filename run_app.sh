#!/bin/bash
# Lanzador usado por la vista previa (.claude/launch.json).
cd "$(dirname "$0")" || exit 1
exec ".venv/bin/python" -m streamlit run app.py \
  --server.port 8501 --server.headless true --browser.gatherUsageStats false
