@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   컷라인 스튜디오 - exe로 빌드하기
echo ============================================
echo.
echo 이 창은 Python 설치 없이도 더블클릭으로 바로 실행되는 프로그램을
echo 만듭니다. 완성되면 dist\컷라인 스튜디오 폴더가 생기고, 그 안의
echo "컷라인 스튜디오.exe"를 더블클릭하면 실행됩니다.
echo (2026-08-31: 이전엔 exe 파일 하나만 있으면 됐지만, 실행할 때마다
echo  내부적으로 압축을 푸는 방식이라 컴퓨터에 따라 이 과정이 느려
echo  "검은 화면만 뜨고 멈춘 것처럼" 보이는 문제가 있었습니다. 지금은
echo  폴더 안에 필요한 파일이 미리 풀려있는 방식이라 훨씬 빠르고
echo  안정적으로 실행됩니다 -- 대신 exe 파일만 옮기지 말고, 폴더
echo  전체를 통째로 복사해서 옮겨야 합니다.)
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
echo 튜토리얼 슬라이드 이미지를 준비합니다...
python assets\make_tutorial_slides.py

if errorlevel 1 (
    echo.
    echo [오류] 튜토리얼 이미지 준비 중 문제가 발생했습니다. 위 오류 메시지를 확인해주세요.
    pause
    exit /b 1
)

rem 2026-09-30: 예전에 빌드한 dist 폴더의 프로그램이 실행 중이면 그 안의 파일이 잠겨 있어
rem PyInstaller가 옛 폴더를 지우다가 "PermissionError: 액세스가 거부되었습니다"로 멈췄다
rem (멍푸 PC에서 실제로 발생). 빌드 전에 dist 폴더에서 실행 중인 프로그램이 있는지 확인하고,
rem 있으면 닫을 때까지 기다린다.
:check_running
powershell -NoProfile -Command "$d = Join-Path (Get-Location) 'dist'; if (Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($d, [System.StringComparison]::OrdinalIgnoreCase) }) { exit 1 } else { exit 0 }"
if errorlevel 1 (
    echo.
    echo [알림] 예전에 만든 컷라인 스튜디오가 아직 실행 중입니다.
    echo        실행 중이면 옛 파일을 지울 수 없어 빌드가 실패합니다.
    echo        컷라인 스튜디오 창을 모두 닫은 뒤, 이 창에서 아무 키나 누르세요.
    pause >nul
    goto check_running
)

echo.
echo exe로 빌드합니다 -- 컴퓨터에 따라 몇 분 정도 걸릴 수 있습니다...
echo.
rem 2026-08-31: "--onefile"은 실행할 때마다 매번 임시 폴더에 내용을 다시
rem 풀어야 해서, 무거운 라이브러리(cv2/shapely/fitz 등)와 겹치면 시작이
rem 느려지고 그 사이 검은 화면만 보이다 닫히는 것처럼 보일 수 있었음.
rem "--onedir"은 한 번만 풀어서 폴더에 고정해두는 방식이라 매 실행이
rem 훨씬 빠르고 안정적임 -- 대신 결과물이 exe 파일 하나가 아니라 폴더
rem 전체가 됨(아래 완료 안내 참고).
python -m PyInstaller --noconfirm --onedir --windowed ^
    --name "컷라인 스튜디오" ^
    --icon assets\app_icon.ico ^
    --collect-all customtkinter ^
    --collect-all cv2 ^
    --collect-all shapely ^
    --collect-all fitz ^
    --collect-all PIL ^
    --collect-all cryptography ^
    --collect-all skimage ^
    --add-data "assets\app_icon.ico;assets" ^
    --add-data "assets\app_icon.png;assets" ^
    --add-data "assets\tutorial;assets\tutorial" ^
    gui\app.py

if errorlevel 1 (
    echo.
    echo [오류] exe 빌드 중 문제가 발생했습니다. 위 오류 메시지를 확인해주세요.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   완료! dist\컷라인 스튜디오 폴더가 만들어졌습니다.
echo   그 폴더 전체를 원하는 곳(바탕화면 등)에 통째로 복사한 뒤,
echo   폴더 안의 "컷라인 스튜디오.exe"를 더블클릭하면 실행됩니다.
echo   (exe 파일만 따로 복사하면 실행되지 않으니 꼭 폴더째로 옮기세요.)
echo ============================================
echo.
start "" explorer "dist"
pause

endlocal
