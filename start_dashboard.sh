#!/bin/bash
# Starts both the FastAPI backend and Python HTTP module for the frontend locally.

# Activate virtual environment if it exists
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

echo "🚀 Starting Market Intelligence API Backend..."
uvicorn src.api.main:app --port 8000 --reload &
API_PID=$!

echo "🖥️  Starting Market Intelligence Dashboard UI..."
cd src/web && python -m http.server 8080 &
WEB_PID=$!

sleep 2
echo "========================================================="
echo "✅ Backend API running on: http://localhost:8000/"
echo "✅ Dashboard running on:   http://localhost:8080/"
echo "========================================================="
echo "Press [CTRL + C] to stop all services."

# Trap Ctrl+C to kill both background processes
trap "kill $API_PID $WEB_PID; exit" INT TERM
wait
