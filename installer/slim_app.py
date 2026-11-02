"""설치 파일 줄이기(2026-11-02) -- make_installer.py가 프로그램을 빌드한 직후 부른다.

고객 PC의 저장 공간이 부족해 설치하지 못하는 일이 있었다("용량이 많아서 설치를 못하고 있대").
PyInstaller의 --collect-all은 라이브러리를 통째로 넣어서, 이 프로그램이 한 번도 쓰지 않는 파일이
많이 들어간다(설치 후 약 355MB). 그중 쓰지 않는 것이 확실한 것만 빌드 폴더에서 지운다.

  1) OpenCV의 동영상 입출력용 FFmpeg DLL -- 이 프로그램은 동영상을 열지 않는다.
  2) Pillow의 AVIF 이미지 모듈 -- 없으면 Pillow가 AVIF 형식만 못 여는 것으로 처리한다.
  3) scikit-image 예제 그림(skimage/data 안의 그림 파일).
  4) SciPy·scikit-image의 확장 모듈(.pyd) 중 프로그램이 불러오지 않는 것.
     소스에서 core.build_selfcheck를 돌려(도안 자동 인식까지 실제로 실행) 불러온 모듈 목록을
     받고, 그 목록에 없는 .pyd만 지운다. .dll(다른 확장 모듈이 기대는 공용 파일)·.pyi(지연
     불러오기 목록)·데이터 파일은 그대로 둔다.

지운 뒤에는 make_installer.py가 빌드된 프로그램을 --selfcheck로 실행해 소스와 결과가 같은지
확인하고, 다르면 설치 파일을 만들지 않는다.
"""
import json
import os
import subprocess
import sys
import tempfile

# 이름으로 바로 지우는 파일(_internal 기준, 앞부분이 같으면 지움)
STATIC_PREFIXES = (
    "cv2/opencv_videoio_ffmpeg",
    "PIL/_avif.",
)
# 이 폴더 안의 그림·데이터 파일은 지운다(.py/.pyi는 둠)
DATA_DIRS = ("skimage/data/",)
DATA_KEEP_EXT = (".py", ".pyi", ".pyc")
# 불러오지 않는 .pyd를 지우는 패키지
PRUNE_PACKAGES = ("scipy", "skimage")


def module_name_for(relpath: str):
    """'scipy/optimize/_minpack.cp314-win_amd64.pyd' -> 'scipy.optimize._minpack'(.pyd가 아니면 None)."""
    rel = relpath.replace("\\", "/")
    if not rel.lower().endswith(".pyd"):
        return None
    parts = rel.split("/")
    parts[-1] = parts[-1].split(".")[0]
    return ".".join(parts)


def plan_removals(internal_dir: str, loaded_modules=None) -> list:
    """지울 파일 목록 [(상대경로, 크기, 이유)]. loaded_modules가 None이면 4)번(.pyd 정리)은 하지 않는다."""
    loaded = set(loaded_modules or ())
    out = []
    for base, _dirs, files in os.walk(internal_dir):
        for fn in files:
            full = os.path.join(base, fn)
            rel = os.path.relpath(full, internal_dir).replace("\\", "/")
            reason = None
            if rel.startswith(STATIC_PREFIXES):
                reason = "쓰지 않는 기능"
            elif rel.startswith(DATA_DIRS) and not rel.lower().endswith(DATA_KEEP_EXT):
                reason = "예제 그림"
            elif loaded_modules is not None and rel.split("/")[0] in PRUNE_PACKAGES:
                mod = module_name_for(rel)
                if mod and mod not in loaded:
                    reason = "불러오지 않는 모듈"
            if reason:
                out.append((rel, os.path.getsize(full), reason))
    return sorted(out)


def loaded_modules_from_source(root: str, python: str = None):
    """소스에서 자체 점검을 돌려 실제로 불러온 모듈 목록을 받는다(실패하면 None)."""
    out = os.path.join(tempfile.gettempdir(), f"cutline_slim_probe_{os.getpid()}.json")
    try:
        r = subprocess.run([python or sys.executable, "-X", "utf8", "-m", "core.build_selfcheck", out],
                           cwd=root, capture_output=True, timeout=900,
                           creationflags=0x08000000 if os.name == "nt" else 0)
        with open(out, encoding="utf-8") as f:
            res = json.load(f)
        from_src = res.get("modules") or []
        if r.returncode != 0 or "fatal" in res or not any(m.startswith("skimage") for m in from_src):
            return None, res
        return from_src, res
    except Exception:  # noqa: BLE001
        return None, None
    finally:
        try:
            os.remove(out)
        except OSError:
            pass


def slim(app_dir: str, root: str, log=print):
    """빌드 폴더(app_dir)에서 쓰지 않는 파일을 지운다. (지운 바이트, 소스 점검 결과)를 돌려준다."""
    internal = os.path.join(app_dir, "_internal")
    if not os.path.isdir(internal):
        internal = app_dir
    modules, src_res = loaded_modules_from_source(root)
    if modules is None:
        log("    [알림] 소스 점검에 실패해 SciPy·scikit-image 정리는 건너뜁니다(쓰지 않는 기능·예제 그림만 뺌).")
    plan = plan_removals(internal, modules)
    total = 0
    by_reason = {}
    for rel, size, reason in plan:
        os.remove(os.path.join(internal, rel))
        total += size
        by_reason[reason] = by_reason.get(reason, 0) + size
    for reason, size in sorted(by_reason.items(), key=lambda x: -x[1]):
        log(f"    {reason}: {size / 1e6:.1f}MB")
    log(f"    빼낸 파일 {len(plan)}개, {total / 1e6:.0f}MB")
    return total, src_res
