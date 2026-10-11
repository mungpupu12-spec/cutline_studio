"""2026-10-12: 윈도우에서 cv2.imread는 한글 경로를 못 읽어(None) PNG를 열 수 없었다(멍푸: "PNG 파일이
불러오기가 안돼"). 리눅스 cv2는 한글 경로를 읽으므로 윈도우처럼 비ASCII 경로에서 None을 주게 바꿔 놓고
확인한다(알고리즘 확인용 합성 이미지)."""
import os
import shutil
import tempfile

import cv2
import numpy as np

from core import image_cache


def _make_png(path):
    img = np.zeros((40, 60, 4), np.uint8)
    img[10:30, 15:45] = (0, 0, 255, 255)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    buf.tofile(path)


def test_imread_reads_korean_path_when_cv2_imread_fails(monkeypatch):
    real = cv2.imread

    def windows_like(path, flags=cv2.IMREAD_COLOR):
        return None if any(ord(c) > 127 for c in path) else real(path, flags)

    monkeypatch.setattr(image_cache.cv2, "imread", windows_like)
    d = tempfile.mkdtemp()
    try:
        folder = os.path.join(d, "바탕화면 샘플")
        os.makedirs(folder)
        p = os.path.join(folder, "투명_스티커.png")
        _make_png(p)
        assert windows_like(p) is None                       # 고치기 전 상태 재현
        a = image_cache.imread(p, cv2.IMREAD_UNCHANGED)
        assert a is not None and a.shape == (40, 60, 4) and a[20, 30, 3] == 255 and a[0, 0, 3] == 0
        c = image_cache.imread(p)                            # 기본(컬러 3채널)도 같은 뜻
        assert c is not None and c.shape == (40, 60, 3)
        assert image_cache.imread(os.path.join(folder, "없는파일.png")) is None
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_generate_cutlines_on_korean_path(monkeypatch):
    real = cv2.imread
    monkeypatch.setattr(image_cache.cv2, "imread",
                        lambda path, flags=cv2.IMREAD_COLOR: None if any(ord(c) > 127 for c in path) else real(path, flags))
    from core.cutline_core import OffsetSpec, generate_cutlines

    d = tempfile.mkdtemp()
    try:
        p = os.path.join(d, "한글폴더", "그림.png")
        os.makedirs(os.path.dirname(p))
        _make_png(p)
        r = generate_cutlines(image_path=p, is_vector=False, dpi=300, offset_mm=OffsetSpec(), supersample=1)
        assert r.width_px == 60 and r.height_px == 40 and r.design is not None and not r.design.is_empty
    finally:
        shutil.rmtree(d, ignore_errors=True)
