@echo off
title RoadGuardian ITS Control Room Launcher
echo ======================================================================
echo           Starting RoadGuardian ITS Control Room Engine
echo ======================================================================
echo.
echo [1/2] Launching FastAPI Backend on http://localhost:8000 ...
start "RoadGuardian Backend (FastAPI)" cmd /k "cd /d %~dp0 && uvicorn app.dashboard.server:app --host 0.0.0.0 --port 8000"

echo [2/2] Launching React Vite Frontend on http://localhost:5173 ...
start "RoadGuardian Frontend (React/Vite)" cmd /k "cd /d %~dp0frontend && npm run dev"

echo.
echo Dashboard running!
echo Frontend: http://localhost:5173
echo Backend API Docs: http://localhost:8000/docs
echo ======================================================================
pause
