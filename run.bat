@echo off
REM Lanzador para Windows: doble clic en este archivo.
cd /d "%~dp0"

echo ===============================================
echo   Kratos - Herramienta de P^&L y Cash Flow
echo ===============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo ERROR: no se encontro Python. Instalalo desde https://www.python.org/downloads/
  pause
  exit /b 1
)

if not exist ".venv" (
  echo Primera vez: preparando el entorno ^(1-2 minutos^)...
  python -m venv .venv || (echo ERROR creando el entorno. & pause & exit /b 1)
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || (echo ERROR instalando dependencias. & pause & exit /b 1)
  echo Entorno listo.
)

echo Abriendo la app en el navegador...
echo (Para cerrar: cierra esta ventana o pulsa Ctrl+C)
echo.
".venv\Scripts\python.exe" -m streamlit run app.py
