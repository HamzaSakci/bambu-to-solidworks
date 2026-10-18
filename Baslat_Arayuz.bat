@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ======================================================================
echo   Bambu Studio (.3MF / .STL) -^> SolidWorks (.STEP / .SLDPRT / .PRT)
echo ======================================================================
".\.venv\Scripts\python.exe" app.py
pause
