# 컷라인 스튜디오 베타 설치 스크립트 (설치 파일 안에 들어가 실행됨)
# - 관리자 권한 없이 사용자 폴더(%LOCALAPPDATA%\Programs\CutLineStudio)에 설치
# - 바탕화면·시작 메뉴 바로가기, "앱 및 기능" 목록에 제거 항목 등록
# - 라이선스 정보(%APPDATA%\CutLineStudio)는 건드리지 않음(다시 설치해도 키를 다시 넣을 필요 없음)
param(
    [string]$Dest = "",
    [switch]$NoShortcuts,
    [switch]$Quiet
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.IO.Compression.FileSystem

$Title = '컷라인 스튜디오 베타 설치'
$AppName = '컷라인 스튜디오'
$ExeName = '컷라인 스튜디오.exe'
$Src = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Dest) { $Dest = Join-Path $env:LOCALAPPDATA 'Programs\CutLineStudio' }
$Version = (Get-Content -Raw -Encoding UTF8 (Join-Path $Src 'version.txt')).Trim()

function Show-Msg($text, $buttons = 'OK', $icon = 'Information') {
    if ($Quiet) { return 'OK' }
    return [System.Windows.Forms.MessageBox]::Show($text, $Title, $buttons, $icon)
}

try {
    # 이미 설치된 프로그램이 실행 중이면 파일을 바꿀 수 없으므로 닫을 때까지 기다림
    while (Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($Dest, [System.StringComparison]::OrdinalIgnoreCase) }) {
        $r = Show-Msg "컷라인 스튜디오가 실행 중입니다.`n창을 모두 닫은 뒤 [확인]을 눌러주세요." 'OKCancel' 'Warning'
        if ($r -ne 'OK') { exit 1 }
    }

    if (Test-Path $Dest) { Remove-Item -LiteralPath $Dest -Recurse -Force }
    New-Item -ItemType Directory -Path $Dest -Force | Out-Null
    [System.IO.Compression.ZipFile]::ExtractToDirectory((Join-Path $Src 'app.zip'), $Dest)
    Copy-Item -LiteralPath (Join-Path $Src 'uninstall.ps1') -Destination (Join-Path $Dest 'uninstall.ps1') -Force
    Copy-Item -LiteralPath (Join-Path $Src 'beta_readme.txt') -Destination (Join-Path $Dest '베타 테스터 안내.txt') -Force

    $Exe = Join-Path $Dest $ExeName
    if (-not (Test-Path -LiteralPath $Exe)) { throw "프로그램 파일을 찾을 수 없습니다: $Exe" }

    if (-not $NoShortcuts) {
        $Shell = New-Object -ComObject WScript.Shell
        $Desktop = [Environment]::GetFolderPath('Desktop')
        $Programs = Join-Path ([Environment]::GetFolderPath('Programs')) $AppName
        New-Item -ItemType Directory -Path $Programs -Force | Out-Null
        foreach ($lnkPath in @((Join-Path $Desktop "$AppName.lnk"), (Join-Path $Programs "$AppName.lnk"))) {
            $l = $Shell.CreateShortcut($lnkPath)
            $l.TargetPath = $Exe
            $l.WorkingDirectory = $Dest
            $l.IconLocation = "$Exe,0"
            $l.Save()
        }
        $u = $Shell.CreateShortcut((Join-Path $Programs "$AppName 제거.lnk"))
        $u.TargetPath = "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe"
        $u.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Dest\uninstall.ps1`""
        $u.Save()

        # Windows "앱 및 기능" 목록에 등록(여기서도 제거 가능)
        $Reg = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\CutLineStudio'
        New-Item -Path $Reg -Force | Out-Null
        Set-ItemProperty -Path $Reg -Name DisplayName -Value "$AppName (베타)"
        Set-ItemProperty -Path $Reg -Name DisplayVersion -Value $Version
        Set-ItemProperty -Path $Reg -Name Publisher -Value 'CutLine Studio'
        Set-ItemProperty -Path $Reg -Name DisplayIcon -Value "$Exe,0"
        Set-ItemProperty -Path $Reg -Name InstallLocation -Value $Dest
        Set-ItemProperty -Path $Reg -Name UninstallString -Value "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Dest\uninstall.ps1`""
        Set-ItemProperty -Path $Reg -Name NoModify -Value 1 -Type DWord
        Set-ItemProperty -Path $Reg -Name NoRepair -Value 1 -Type DWord
    }

    $r = Show-Msg "설치가 끝났습니다 (버전 $Version).`n`n바탕화면의 '컷라인 스튜디오'로 실행하고, 처음 실행할 때 받은 베타 코드(또는 라이선스 키)를 입력해주세요.`n`n지금 실행할까요?" 'YesNo' 'Information'
    if ($r -eq 'Yes') { Start-Process -FilePath $Exe -WorkingDirectory $Dest }
    exit 0
}
catch {
    Show-Msg "설치 중 문제가 생겼습니다.`n`n$($_.Exception.Message)" 'OK' 'Error' | Out-Null
    exit 1
}
