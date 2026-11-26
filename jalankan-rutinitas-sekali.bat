@echo off
cd /d "%~dp0"
python -m osint.rutinitas --seeds data\seeds.txt --check-alive
if errorlevel 1 goto gagal
echo.
echo Selesai. Log: data\rutinitas.log
echo Backup: data\backups\
goto akhir
:gagal
echo.
echo Rutinitas gagal. Periksa pesan di atas dan data\rutinitas.log.
:akhir
pause
