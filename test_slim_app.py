"""저용량 설치 파일(2026-11-02) -- installer/slim_app.py와 core/build_selfcheck.py."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "installer"))

from slim_app import module_name_for, plan_removals  # noqa: E402
from core.build_selfcheck import comparable, problems, run  # noqa: E402


def _touch(root, rel, size=10):
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(b"x" * size)


def test_module_name_for():
    assert module_name_for("scipy/optimize/_minpack.cp314-win_amd64.pyd") == "scipy.optimize._minpack"
    assert module_name_for("scipy\\ndimage\\_nd_image.cp312-win_amd64.pyd") == "scipy.ndimage._nd_image"
    assert module_name_for("scipy/special/libsf_error_state.dll") is None
    assert module_name_for("skimage/filters/__init__.pyi") is None


def test_plan_removals_keeps_loaded_and_shared_files(tmp_path):
    root = str(tmp_path)
    keep = [
        "scipy/ndimage/_nd_image.cp314-win_amd64.pyd",      # 불러오는 모듈
        "scipy/special/libsf_error_state.dll",              # 공용 DLL은 그대로
        "skimage/feature/__init__.pyi",                     # 지연 불러오기 목록
        "skimage/data/__init__.pyi",
        "cv2/cv2.pyd",                                      # OpenCV 본체
        "PIL/_imaging.cp314-win_amd64.pyd",
        "numpy/_core/_multiarray_umath.cp314-win_amd64.pyd",  # 정리 대상 패키지 아님
    ]
    drop = [
        "scipy/optimize/_minpack.cp314-win_amd64.pyd",
        "skimage/restoration/_denoise_cy.cp314-win_amd64.pyd",
        "skimage/data/astronaut.png",
        "cv2/opencv_videoio_ffmpeg500_64.dll",
        "PIL/_avif.cp314-win_amd64.pyd",
    ]
    for rel in keep + drop:
        _touch(root, rel)
    loaded = {"scipy.ndimage._nd_image", "PIL._imaging"}
    plan = plan_removals(root, loaded)
    assert sorted(r for r, _s, _why in plan) == sorted(drop)


def test_plan_removals_without_probe_only_static(tmp_path):
    root = str(tmp_path)
    for rel in ("scipy/optimize/_minpack.cp314-win_amd64.pyd", "cv2/opencv_videoio_ffmpeg500_64.dll",
                "skimage/data/camera.png"):
        _touch(root, rel)
    plan = [r for r, _s, _why in plan_removals(root, None)]
    assert plan == ["cv2/opencv_videoio_ffmpeg500_64.dll", "skimage/data/camera.png"]


def test_selfcheck_from_source_passes():
    res = run([])
    assert problems(res) == [], problems(res)
    assert res["test_sheet"]["lib_calls"]["peak"] > 0      # 시험 시트가 scikit-image 쪼개기를 실제로 거침
    assert "skimage.segmentation._watershed_cy" in res["modules"]


def test_problems_catch_silent_fallback():
    res = run([])
    broken = dict(res, libs=dict(res["libs"], split_touching="ok available=False parts=1"))
    assert any("scikit-image" in p for p in problems(broken))
    other = dict(res, test_sheet=dict(res["test_sheet"], boxes_fp="0"))
    assert comparable(other) != comparable(res)
