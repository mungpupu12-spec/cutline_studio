"""베타 테스터용 설치 파일(setup .exe)을 만든다 -- 베타_설치파일_만들기.bat에서 부름.

추가 프로그램 없이 Windows에 기본으로 들어 있는 IExpress로 "압축을 풀고 설치 스크립트를
실행하는" 실행 파일 하나를 만든다. IExpress는 폴더 구조를 담지 못해서, 빌드된 프로그램 폴더
(dist\\컷라인 스튜디오)를 zip 하나로 묶어 넣고 설치 스크립트(install.ps1)가 풀어 준다.

결과: dist\\컷라인스튜디오_베타_설치_<날짜>.exe
--edition sales(판매용_설치파일_만들기.bat): dist\\컷라인스튜디오_설치_<날짜>.exe -- 프로그램은 같고,
설치 창 문구·안내 파일·"앱 및 기능" 이름만 판매용(라이선스 키 입력 안내)으로 바뀐다(2026-10-02).
"""
import datetime
import hashlib
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.join(ROOT, "installer")
APP_DIR = os.path.join(ROOT, "dist", "컷라인 스튜디오")
EXE_NAME = "컷라인 스튜디오.exe"
WORK = os.path.join(ROOT, "build", "installer_work")
TMP_TARGET = os.path.join(ROOT, "dist", "CutLineStudio_Beta_Setup.exe")  # IExpress에는 영문 경로만


def _write_text(path, text, bom=False):
    """Windows PowerShell 5.1이 한글을 제대로 읽도록 .ps1은 BOM 있는 UTF-8 + CRLF."""
    text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    with open(path, "w", encoding="utf-8-sig" if bom else "utf-8", newline="") as f:
        f.write(text)


def _read(name):
    with open(os.path.join(HERE, name), encoding="utf-8-sig") as f:
        return f.read()


def _build_app():
    """exe_빌드.bat을 "auto"로 실행(최신 코드로 다시 빌드). 한글 파일 이름을 배치 파일 안에서
    call로 부르면 cmd가 이름을 못 읽어서(멍푸 PC에서 확인) 파이썬에서 직접 부른다."""
    bat = os.path.join(ROOT, "exe_빌드.bat")
    r = subprocess.run(["cmd", "/c", bat, "auto"], cwd=ROOT)
    return r.returncode == 0


# 판매용 설치 파일에서 바꾸는 문구(설치 스크립트 원문 -> 판매용)
_SALES_PS1 = [
    ("$Title = '컷라인 스튜디오 베타 설치'", "$Title = '컷라인 스튜디오 설치'"),
    ("'beta_readme.txt'", "'readme.txt'"),
    ("'베타 테스터 안내.txt'", "'사용 안내.txt'"),
    ('"$AppName (베타)"', '"$AppName"'),
    ("받은 베타 코드(또는 라이선스 키)를 입력해주세요.", "구매할 때 받은 라이선스 키를 입력해주세요."),
]


def _edition():
    if "--edition" in sys.argv:
        i = sys.argv.index("--edition")
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return "beta"


def main():
    sales = _edition() == "sales"
    if "--build" in sys.argv and not _build_app():
        print("[오류] 프로그램 빌드에 실패했습니다. 위 메시지를 확인해주세요.")
        return 1
    if not os.path.isfile(os.path.join(APP_DIR, EXE_NAME)):
        print("[오류] 빌드된 프로그램이 없습니다:", os.path.join(APP_DIR, EXE_NAME))
        return 1
    today = datetime.date.today()
    version = f"1.0.{today:%Y%m%d}" if sales else f"beta-{today:%Y%m%d}"
    if os.path.isdir(WORK):
        shutil.rmtree(WORK)
    os.makedirs(WORK)

    print("1/3 프로그램 폴더를 묶는 중...")
    zpath = os.path.join(WORK, "app.zip")
    n = 0
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for base, _dirs, files in os.walk(APP_DIR):
            for fn in files:
                full = os.path.join(base, fn)
                z.write(full, os.path.relpath(full, APP_DIR))
                n += 1
    print(f"    파일 {n}개, {os.path.getsize(zpath) / 1e6:.0f}MB")

    ps1 = _read("install.ps1")
    readme_name = "beta_readme.txt"
    if sales:
        for a, b in _SALES_PS1:
            if a not in ps1:
                print("[오류] 설치 스크립트에서 바꿀 문구를 찾지 못했습니다:", a)
                return 1
            ps1 = ps1.replace(a, b)
        readme_name = "readme.txt"
    _write_text(os.path.join(WORK, "install.ps1"), ps1, bom=True)
    _write_text(os.path.join(WORK, "uninstall.ps1"), _read("uninstall.ps1"), bom=True)
    _write_text(os.path.join(WORK, "install.cmd"), _read("install.cmd"))
    _write_text(os.path.join(WORK, readme_name), _read("sales_readme.txt" if sales else "beta_readme.txt"), bom=True)
    _write_text(os.path.join(WORK, "version.txt"), version + "\n")

    print("2/3 설치 파일을 만드는 중(IExpress, 몇 분 걸릴 수 있음)...")
    files = ["app.zip", "install.ps1", "uninstall.ps1", "install.cmd", readme_name, "version.txt"]
    sed = [
        "[Version]", "Class=IEXPRESS", "SEDVersion=3",
        "[Options]", "PackagePurpose=InstallApp", "ShowInstallProgramWindow=1", "HideExtractAnimation=0",
        "UseLongFileName=1", "InsideCompressed=0", "CAB_FixedSize=0", "CAB_ResvCodeSigning=0",
        "RebootMode=N", "InstallPrompt=%InstallPrompt%", "DisplayLicense=%DisplayLicense%",
        "FinishMessage=%FinishMessage%", "TargetName=%TargetName%", "FriendlyName=%FriendlyName%",
        "AppLaunched=%AppLaunched%", "PostInstallCmd=%PostInstallCmd%",
        "AdminQuietInstCmd=%AdminQuietInstCmd%", "UserQuietInstCmd=%UserQuietInstCmd%",
        "SourceFiles=SourceFiles",
        "[Strings]", "InstallPrompt=", "DisplayLicense=", "FinishMessage=",
        f"TargetName={TMP_TARGET}", "FriendlyName=CutLine Studio " + ("Setup" if sales else "Beta Setup"),
        "AppLaunched=cmd.exe /c install.cmd", "PostInstallCmd=<None>",
        "AdminQuietInstCmd=", "UserQuietInstCmd=",
    ]
    sed += [f'FILE{i}="{f}"' for i, f in enumerate(files)]
    sed += ["[SourceFiles]", f"SourceFiles0={WORK}\\", "[SourceFiles0]"]
    sed += [f"%FILE{i}%=" for i in range(len(files))]
    sed_path = os.path.join(WORK, "setup.sed")
    with open(sed_path, "w", encoding="mbcs" if os.name == "nt" else "utf-8", newline="\r\n") as f:
        f.write("\n".join(sed) + "\n")
    if os.path.exists(TMP_TARGET):
        os.remove(TMP_TARGET)
    iexpress = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32", "iexpress.exe")
    subprocess.run([iexpress, "/N", "/Q", sed_path], check=False)
    if not os.path.isfile(TMP_TARGET):
        print("[오류] IExpress가 설치 파일을 만들지 못했습니다.")
        return 1

    print("3/3 이름 정리...")
    final = os.path.join(ROOT, "dist", f"컷라인스튜디오_{'' if sales else '베타_'}설치_{today:%Y%m%d}.exe")
    if os.path.exists(final):
        os.remove(final)
    os.replace(TMP_TARGET, final)
    h = hashlib.sha256(open(final, "rb").read()).hexdigest()
    print(f"완료: {final}")
    print(f"    크기 {os.path.getsize(final) / 1e6:.0f}MB, SHA-256 {h[:16]}...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
