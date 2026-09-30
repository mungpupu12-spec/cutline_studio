@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================
echo   컷라인 스튜디오 - 베타 테스터용 설치 파일 만들기
echo ============================================
echo.
echo 1) 최신 코드로 프로그램을 다시 빌드하고(exe_빌드.bat과 같음)
echo 2) 그 결과를 설치 파일 하나(dist\컷라인스튜디오_베타_설치_날짜.exe)로 묶습니다.
echo    베타 테스터에게는 이 설치 파일 하나와 각자의 라이선스 키만 보내면 됩니다.
echo.

python installer\make_installer.py --build
if errorlevel 1 (
    echo.
    echo [오류] 설치 파일을 만들지 못했습니다. 위 메시지를 확인해주세요.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   완료! dist 폴더의 "컷라인스튜디오_베타_설치_날짜.exe"를 보내면 됩니다.
echo ============================================
start "" explorer "dist"
pause
endlocal
