@echo off
chcp 65001 >nul
title Bambu Studio (.3MF / .STL) -^> SolidWorks (.STEP / .SLDPRT / .PRT) Kurulum ve Baslatici
cd /d "%~dp0"

echo ===============================================================================
echo   ⚙️  BAMBU STUDIO (.3MF / .STL) -^> SOLIDWORKS (.STEP / .SLDPRT / .PRT)
echo   💻 Masaustu Programi ve Web Studyosu Otomatik Kurulum / Baslatici
echo ===============================================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [1/2] Ilk calistirma tespit edildi: Python sanal ortami (.venv) hazirlaniyor...
    where uv >nul 2>nul
    if %errorlevel% equ 0 (
        uv venv .venv
        uv pip install --python .\.venv\Scripts\python.exe -r requirements.txt
    ) else (
        python -m venv .venv
        .\.venv\Scripts\python.exe -m pip install --upgrade pip
        .\.venv\Scripts\pip.exe install -r requirements.txt
    )
    echo [✔] Kurulum tamamlandi!
    echo.
)

echo Nasil baslatmak istersiniz?
echo   [1] Masaustu Programi Olarak Ac (Windows GUI Penceresi)
echo   [2] Renkli Web Studyosu Olarak Ac (Tarayicida + SolidWorks Baglantili)
echo.
set /p secim="Seciminiz (1 veya 2, Varsayilan=1): "

if "%secim%"=="2" (
    echo.
    echo 🌈 Web Studyosu baslatiliyor...
    ".\.venv\Scripts\python.exe" web_app.py
) else (
    echo.
    echo 🖥️ Masaustu Programi baslatiliyor...
    ".\.venv\Scripts\python.exe" app.py
)
pause
