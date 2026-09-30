# 컷라인 스튜디오 베타 제거 스크립트 (설치 폴더에 복사되어 "제거" 바로가기/앱 및 기능에서 실행됨)
# 라이선스 정보(%APPDATA%\CutLineStudio)는 남겨 둔다 -- 다시 설치하면 키를 다시 넣지 않아도 됨.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
$Title = '컷라인 스튜디오 제거'
$AppName = '컷라인 스튜디오'
$Dest = Split-Path -Parent $MyInvocation.MyCommand.Path
$r = [System.Windows.Forms.MessageBox]::Show("컷라인 스튜디오를 이 컴퓨터에서 제거할까요?", $Title, 'YesNo', 'Question')
if ($r -ne 'Yes') { exit 0 }
try {
    while (Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($Dest, [System.StringComparison]::OrdinalIgnoreCase) }) {
        $r = [System.Windows.Forms.MessageBox]::Show("컷라인 스튜디오가 실행 중입니다.`n창을 모두 닫은 뒤 [확인]을 눌러주세요.", $Title, 'OKCancel', 'Warning')
        if ($r -ne 'OK') { exit 1 }
    }
    $Desktop = [Environment]::GetFolderPath('Desktop')
    $Programs = Join-Path ([Environment]::GetFolderPath('Programs')) $AppName
    Remove-Item -LiteralPath (Join-Path $Desktop "$AppName.lnk") -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $Programs -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\CutLineStudio' -Recurse -Force -ErrorAction SilentlyContinue
    Set-Location $env:TEMP
    Remove-Item -LiteralPath $Dest -Recurse -Force
    [System.Windows.Forms.MessageBox]::Show("제거했습니다.", $Title, 'OK', 'Information') | Out-Null
}
catch {
    [System.Windows.Forms.MessageBox]::Show("제거 중 문제가 생겼습니다.`n`n$($_.Exception.Message)", $Title, 'OK', 'Error') | Out-Null
    exit 1
}
