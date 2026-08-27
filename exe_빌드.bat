@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   CutLine Studio - exe로 빌드하기
echo ============================================
echo.
echo 이 창은 CutLine Studio.exe 하나만 있으면 Python 설치 없이도 더블클릭으로
echo 바로 실행되는 실행 파일을 만듭니다. 완성되면 dist 폴더 안에
echo "CutLine Studio.exe"가 생깁니다.
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [오류] 이 컴퓨터에서 python 명령을 찾을 수 없습니다.
    echo 먼저 설치_및_실행.bat을 한 번 실행해서 Python/패키지를 설치해주세요.
    echo.
    pause
    exit /b 1
)

rem "python"이 실제로 설치돼 있는지, 아니면 Windows가 기본으로 깔아두는 가짜
rem 실행 파일(Microsoft Store로 안내만 하는 자리 표시자)인지 확인 -- 설치_및_
rem 실행.bat과 같은 검사.
set "PYCHECK=%TEMP%\cutline_studio_pycheck.txt"
python --version > "%PYCHECK%" 2>&1
set "PYOK=0"
findstr /r /c:"Python [0-9][0-9]*\.[0-9]" "%PYCHECK%" >nul 2>nul
if not errorlevel 1 set "PYOK=1"
del "%PYCHECK%" >nul 2>nul

if "%PYOK%"=="0" (
    echo [오류] "python" 명령은 있지만 실제 Python이 설치돼 있지 않은 것 같습니다.
    echo 먼저 설치_및_실행.bat을 한 번 실행해주세요.
    echo.
    pause
    exit /b 1
)

echo Python 확인됨. 필요한 패키지를 설치합니다 -- 인터넷 연결 필요, 처음 한 번만 시간이 걸립니다.
echo.
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-build.txt

if errorlevel 1 (
    echo.
    echo [오류] 패키지 설치 중 문제가 발생했습니다. 위 오류 메시지를 확인해주세요.
    pause
    exit /b 1
)

echo.
echo 앱 아이콘을 준비합니다...
python assets\make_icon.py

if errorlevel 1 (
    echo.
    echo [오류] 아이콘 준비 중 문제가 발생했습니다. 위 오류 메시지를 확인해주세요.
    pause
    exit /b 1
)

echo.
echo exe로 빌드합니다 -- 컴퓨터에 따라 몇 분 정도 걸릴 수 있습니다...
echo.
python -m PyInstaller --noconfirm --onefile --windowed ^
    --name "CutLine Studio" ^
    --icon assets\app_icon.ico ^
    --collect-all customtkinter ^
    --collect-all cv2 ^
    --collect-all shapely ^
    --collect-all fitz ^
    --collect-all PIL ^
    --collect-all cryptography ^
    --add-data "assets\app_icon.ico;assets" ^
    --add-data "assets\app_icon.png;assets" ^
    gui\app.py

if errorlevel 1 (
    echo.
    echo [오류] exe 빌드 중 문제가 발생했습니다. 위 오류 메시지를 확인해주세요.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   완료! dist 폴더 안의 "CutLine Studio.exe" 파일이 만들어졌습니다.
echo   이 파일 하나만 원하는 곳(바탕화면 등)에 복사해서 더블클릭하면
echo   Python 설치 없이 바로 실행됩니다.
echo ============================================
echo.
start "" explorer "dist"
pause

endlocal
