#!/bin/bash
# Lanzador para macOS: doble clic en este archivo.
# Crea el entorno la primera vez, instala dependencias y abre la app en el navegador.

cd "$(dirname "$0")" || exit 1

echo "==============================================="
echo "  Kratos - Herramienta de P&L y Cash Flow"
echo "==============================================="
echo ""

PYTHON_BIN="$(command -v python3)"
if [ -z "$PYTHON_BIN" ]; then
  echo "ERROR: no se encontro Python 3. Instalalo desde https://www.python.org/downloads/ y vuelve a intentar."
  read -r -p "Pulsa Enter para cerrar..."
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "Primera vez: preparando el entorno (esto tarda 1-2 minutos)..."
  "$PYTHON_BIN" -m venv .venv || { echo "ERROR creando el entorno."; read -r -p "Enter para cerrar..."; exit 1; }
  ".venv/bin/python" -m pip install --upgrade pip >/dev/null
  ".venv/bin/python" -m pip install -r requirements.txt || { echo "ERROR instalando dependencias."; read -r -p "Enter para cerrar..."; exit 1; }
  echo "Entorno listo."
fi

echo "Abriendo la app en el navegador..."
echo "(Para cerrar la herramienta: cierra esta ventana o pulsa Ctrl+C aqui)"
echo ""
".venv/bin/python" -m streamlit run app.py
