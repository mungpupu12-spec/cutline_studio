@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   CutLine Studio - 설치 및 실행
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [오류] 이 컴퓨터에서 python 명령을 찾을 수 없습니다.
    echo.
    echo https://www.python.org/downloads/ 에서 Python 3.10 이상을 내려받아 설치하세요.
    echo 설치 화면 맨 아래 "Add python.exe to PATH"에 반드시 체크하세요.
    echo 설치가 끝나면 이 창을 닫고 이 파일을 다시 더블클릭하세요.
    echo.
    pause
    exit /b 1
)

rem "python"이 실제로 설치돼 있는지, 아니면 Windows가 기본으로 깔아두는 가짜
rem 실행 파일(Microsoft Store로 안내만 하는 자리 표시자)인지 확인. 진짜
rem Python이라면 "python --version"이 "Python 3.x.x" 형태로 버전 숫자를
rem 출력하지만, 가짜 자리 표시자는 버전 숫자 없이 아무 것도 제대로 출력하지
rem 않음 -- "where python"만으로는 이 가짜 파일도 찾아지므로 구분이 안 됨.
set "PYCHECK=%TEMP%\cutline_studio_pycheck.txt"
python --version > "%PYCHECK%" 2>&1
set "PYOK=0"
findstr /r /c:"Python [0-9][0-9]*\.[0-9]" "%PYCHECK%" >nul 2>nul
if not errorlevel 1 set "PYOK=1"
del "%PYCHECK%" >nul 2>nul

if "%PYOK%"=="0" (
    echo [오류] "python" 명령은 있지만 실제 Python이 설치돼 있지 않은 것 같습니다.
    echo.
    echo Windows에는 python이라고 치면 Microsoft Store로 안내만 하는 자리 표시자가
    echo 기본으로 들어있는 경우가 많은데, 지금 이 컴퓨터가 그 상태로 보입니다.
    echo.
    echo 해결 방법 -- 아래 순서대로 해주세요:
    echo  1. https://www.python.org/downloads/ 에서 Python 3.10 이상을 내려받아 설치
    echo  2. 설치 화면 맨 아래 "Add python.exe to PATH"에 반드시 체크
    echo  3. 설치가 끝나면 이 창을 닫고 이 파일을 다시 더블클릭
    echo.
    echo 설치 후에도 이 메시지가 또 뜨면, Windows 설정에서 다음을 확인해주세요:
    echo  설정 -^> 앱 -^> 고급 앱 설정 -^> 앱 실행 별칭
    echo  거기서 "python.exe"와 "python3.exe" 항목을 꺼주세요 -- 토글을 OFF로.
    echo.
    pause
    exit /b 1
)

echo Python 확인됨. 필요한 패키지를 설치합니다 -- 인터넷 연결 필요, 처음 한 번만 시간이 걸립니다.
echo.
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo [오류] 패키지 설치 중 문제가 발생했습니다. 위 오류 메시지를 확인해주세요.
    pause
    exit /b 1
)

echo.
echo 설치 완료. 프로그램을 실행합니다...
echo.
python gui\app.py

if errorlevel 1 (
    echo.
    echo [오류] 프로그램 실행 중 문제가 발생했습니다. 위 오류 메시지를 확인해주세요.
    echo tkinter 관련 오류라면, Python을 다시 설치하면서 tcl/tk and IDLE 항목에
    echo 체크가 되어 있는지 확인해주세요 -- 기본 설치에는 보통 포함되어 있습니다.
    pause
)

endlocal
