@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo  Solo Tracker - Berantas Web Ilegal
echo ============================================

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python tidak ditemukan di PATH.
  echo Install Python 3.x dari python.org lalu jalankan ulang file ini.
  pause
  exit /b 1
)

echo [1/3] Memeriksa dependensi...
python -c "import requests, pytz" >nul 2>nul
if errorlevel 1 (
  echo       Menginstall requests ^& pytz...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [ERROR] Gagal memasang dependensi.
    pause
    exit /b 1
  )
) else (
  echo       Dependensi sudah tersedia.
)

echo [2/3] Menyalakan server di http://localhost:8765
start "" http://localhost:8765

echo [3/3] Menjalankan aplikasi...
python web\app.py

echo.
echo Server dihentikan.
pause
