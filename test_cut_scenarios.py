"""2026-10-24 멍푸: "발생할 수 있는 변수 예상 해서 테스트 해" -- 그림 모양 기준 칼선(흰 바탕·투명·여러 색 배경,
유테/무테 자동, 최소 간격, 가격표 띠)에서 생길 수 있는 경우를 미리 만들어 확인한다.
알고리즘 확인용 합성 이미지(칼선 품질 판단은 실제 파일로 함)."""
import os
import tempfile
import time

import cv2
import numpy as np
import pytest
from PIL import Image

from core import white_layer as wl

DPI = 300.0
MM = DPI / 25.4


def _blank(h=900, w=1200, color=(255, 255, 255)):
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = color
    return img


def _line_face(img, c, r, line=(30, 30, 30), th=5, fill=None):
    if fill is not None:
        cv2.circle(img, c, r, fill, -1)
    cv2.circle(img, c, r, line, th)
    cv2.circle(img, (c[0] - r // 3, c[1] - r // 5), max(3, r // 10), line, -1)
    cv2.circle(img, (c[0] + r // 3, c[1] - r // 5), max(3, r // 10), line, -1)


def _painted(img, c, r, base=(150, 100, 60)):
    yy, xx = np.mgrid[0:img.shape[0], 0:img.shape[1]]
    d = np.hypot(xx - c[0], yy - c[1])
    blob = d < r
    k = np.clip(0.75 + 0.35 * d / r, 0, 1.2)
    col = np.stack([np.clip(base[0] * k, 0, 255), np.clip(base[1] * k, 0, 255), np.clip(base[2] * k, 0, 255)], -1)
    img[blob] = col[blob].astype(np.uint8)


def _n(mask):
    return cv2.connectedComponents(np.asarray(mask, np.uint8))[0] - 1


# ---- 바탕 -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("bg", [(255, 255, 255), (252, 247, 236), (232, 232, 232)])   # 흰색, 크림색, 연회색
def test_plain_backgrounds_find_each_art(bg):
    img = _blank(color=bg)
    _line_face(img, (300, 450), 180)
    _painted(img, (850, 450), 180)
    m = wl.scene_objects_mask(img, DPI)
    assert m is not None and _n(m) == 2


def test_jpeg_noise_does_not_split_or_merge():
    img = _blank()
    _line_face(img, (300, 450), 180)
    _painted(img, (850, 450), 180)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 60])
    jpg = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    m = wl.scene_objects_mask(jpg, DPI)
    assert m is not None and _n(m) == 2


def test_empty_sheet_gives_nothing():
    assert wl.scene_objects_mask(_blank(), DPI) is None


def test_art_touching_sheet_edge_still_found():
    img = _blank()
    _painted(img, (120, 450), 200)            # 왼쪽 끝에 걸침
    _line_face(img, (800, 450), 180)
    m = wl.scene_objects_mask(img, DPI)
    assert m is not None and m[450, 800] and m[450, 60]


# ---- 유테/무테 구분 --------------------------------------------------------------------------------------
@pytest.mark.parametrize("line,fill,expect", [
    ((30, 30, 30), None, True),                 # 검정 선 + 흰 속
    ((200, 170, 140), None, True),              # 옅은 갈색 선
    ((30, 30, 30), (240, 170, 90), True),       # 검정 선 + 색 속
    ((150, 60, 40), (250, 200, 150), True),     # 진한 갈색 선 + 살구색 속
])
def test_outline_is_yute(line, fill, expect):
    img = _blank()
    _line_face(img, (600, 450), 250, line=line, th=6, fill=fill)
    m = wl.scene_objects_mask(img, DPI)
    comp = cv2.connectedComponents(m.astype(np.uint8))[1] == 1
    assert wl.has_outline_line(img, comp, DPI) is expect


@pytest.mark.parametrize("kind", ["painted", "flat"])
def test_no_outline_is_mute(kind):
    img = _blank()
    if kind == "painted":
        _painted(img, (600, 450), 250)
    else:
        cv2.circle(img, (600, 450), 250, (230, 160, 80), -1)
        cv2.circle(img, (600, 450), 80, (60, 120, 200), -1)       # 안쪽 무늬(선 아님)
    m = wl.scene_objects_mask(img, DPI)
    comp = cv2.connectedComponents(m.astype(np.uint8))[1] == 1
    assert wl.has_outline_line(img, comp, DPI) is False


# ---- 선의 틈, 가는 부분 ----------------------------------------------------------------------------------
def test_small_gap_in_outline_does_not_leak():
    img = _blank()
    _line_face(img, (600, 450), 250)
    img[195:206, 597:601] = 255                                     # 선에 약 0.3mm 틈
    m = wl.scene_objects_mask(img, DPI)
    assert m is not None and m[450, 600]                             # 흰 얼굴 속이 그림으로 남음


def test_thin_stem_stays_with_art_for_yute():
    img = _blank()
    _line_face(img, (600, 500), 200)
    cv2.line(img, (600, 300), (600, 150), (30, 30, 30), 5)          # 0.4mm 줄기
    m = wl.scene_objects_mask(img, DPI)
    assert _n(m) == 1 and m[200, 600]
    cuts, _, ny, nm, _ = wl.auto_style_cuts(img, m, DPI, 1.2)
    assert len(cuts.geoms) == 1 and cuts.geoms[0].bounds[1] < 150


# ---- 간격 ------------------------------------------------------------------------------------------------
def test_close_arts_merge_and_min_gap_holds():
    img = _blank()
    _line_face(img, (330, 450), 200)
    _line_face(img, (330 + 400 + int(1.0 * MM), 450), 200)          # 선 사이 1mm
    _painted(img, (1000, 200), 90)
    _painted(img, (1000, 200 + 180 + int(1.5 * MM)), 90)            # 무테 둘, 1.5mm
    m = wl.scene_objects_mask(_pad(img), DPI)
    cuts, sep, ny, nm, _ = wl.auto_style_cuts(_pad(img), m, DPI, 1.2)
    assert wl.count_close_pairs(cuts, DPI) == 0                      # 합친 쪽은 최소 간격을 지킴


def _pad(img, p=150):
    return cv2.copyMakeBorder(img, p, p, p, p, cv2.BORDER_CONSTANT, value=(255, 255, 255))


def test_bubble_above_face_becomes_one_yute_cut():
    img = _blank()
    _line_face(img, (600, 560), 220)
    cv2.ellipse(img, (600, 250), (200, 60), 0, 0, 360, (30, 30, 30), 4)   # 말풍선, 얼굴과 약 2.6mm
    m = wl.scene_objects_mask(img, DPI)
    cuts, _, ny, nm, _ = wl.auto_style_cuts(img, m, DPI, 2.2)
    assert nm == 0 and len(cuts.geoms) == 1


# ---- 띠, 격자 -------------------------------------------------------------------------------------------
def test_bands_top_and_bottom_removed_middle_wide_sticker_kept():
    img = _blank(1400, 1200)
    img[30:180, 30:1170] = (90, 90, 170)                            # 위 띠
    img[1250:1370, 30:1170] = (90, 160, 90)                          # 아래 띠
    img[650:780, 40:1160] = (240, 180, 80)                           # 가운데 가로로 긴 글자 스티커
    _line_face(img, (300, 420), 150)
    _line_face(img, (850, 1030), 150)
    m = wl.scene_objects_mask(img, DPI)
    m2, bands = wl.remove_label_bands(m, DPI)
    assert len(bands) == 2
    assert m2[715, 600]                                              # 가운데 스티커는 남음


def test_grid_sheet_falls_back():
    g = _blank(1200, 1600)
    for x in range(100, 1600, 350):
        cv2.line(g, (x, 100), (x, 1100), (0, 0, 0), 3)
    for y in range(100, 1200, 333):
        cv2.line(g, (100, y), (1500, y), (0, 0, 0), 3)
    for x in range(275, 1500, 350):
        for y in range(266, 1100, 333):
            cv2.circle(g, (x, y), 80, (40, 90, 200), -1)
    assert wl.scene_objects_mask(g, DPI) is None


def test_one_big_character_is_still_used():
    f = _blank(1200, 1200)
    _line_face(f, (600, 600), 520)
    assert wl.scene_objects_mask(f, DPI) is not None


# ---- 해상도, 크기, 속도 -----------------------------------------------------------------------------------
@pytest.mark.parametrize("dpi", [72.0, 150.0, 600.0])
def test_various_dpi(dpi):
    img = _blank()
    _line_face(img, (300, 450), 180)
    _painted(img, (850, 450), 180)
    m = wl.scene_objects_mask(img, dpi)
    assert m is not None
    cuts, sep, ny, nm, _ = wl.auto_style_cuts(img, m, dpi, 1.2)
    assert ny == 1 and nm == 1 and len(sep.geoms) >= 1


def test_big_sheet_speed():
    rng = np.random.default_rng(1)
    img = _blank(3000, 4000)
    for k in range(48):
        c = (250 + (k % 8) * 470, 250 + (k // 8) * 470)
        if k % 2:
            _line_face(img, c, 170)
        else:
            cv2.circle(img, c, 170, tuple(int(v) for v in rng.integers(60, 220, 3)), -1)
    t = time.time()
    m = wl.scene_objects_mask(img, DPI)
    m, bands = wl.remove_label_bands(m, DPI)
    cuts, sep, ny, nm, _ = wl.auto_style_cuts(img, m, DPI, 1.2)
    dt = time.time() - t
    assert ny == 24 and nm == 24
    assert dt < 6.0, dt


# ---- 파일 형식 ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["RGB", "RGBA_opaque", "P", "L", "CMYK_jpg", "I;16"])
def test_file_modes(mode):
    img = _blank()
    _line_face(img, (300, 450), 180)
    _painted(img, (850, 450), 180)
    d = tempfile.mkdtemp()
    p = os.path.join(d, "그림.png")
    im = Image.fromarray(img)
    if mode == "RGBA_opaque":
        im.convert("RGBA").save(p)
    elif mode == "P":
        im.convert("P", palette=Image.ADAPTIVE, colors=64).save(p)
    elif mode == "L":
        im.convert("L").save(p)
    elif mode == "CMYK_jpg":
        p = os.path.join(d, "그림.jpg")
        im.convert("CMYK").save(p, quality=95)
    elif mode == "I;16":
        Image.fromarray((np.asarray(im.convert("L")).astype(np.uint16) * 257)).save(p)
    else:
        im.save(p)
    kind, mask = wl.shape_cut_source(p, DPI, scene=True, scene_single=True)
    assert kind == "scene" and _n(mask) == 2, (kind, None if mask is None else _n(mask))


def test_transparent_png_uses_alpha():
    rgba = np.zeros((600, 800, 4), np.uint8)
    cv2.circle(rgba, (300, 300), 150, (240, 170, 90, 255), -1)
    d = tempfile.mkdtemp()
    p = os.path.join(d, "t.png")
    Image.fromarray(rgba).save(p)
    kind, mask = wl.shape_cut_source(p, DPI, scene=True, scene_single=True)
    assert kind == "alpha" and mask[300, 300]


# ---- 프로그램 흐름 --------------------------------------------------------------------------------------
def _app():
    from gui.app import CutLineApp

    app = CutLineApp()
    app.withdraw()
    if not hasattr(app, "preview_canvas"):
        app._build_layout()
    app._show_note_dialog = lambda *a, **k: None
    app._show_confirm_dialog = lambda *a, **k: True
    return app


@pytest.mark.parametrize("job", ["LINE_ART", "BORDERLESS", "AUTO_STYLE", "MASKING_TAPE"])
def test_jobs_run_on_white_sheet(job):
    img = _blank()
    _line_face(img, (300, 450), 180)
    _painted(img, (850, 450), 180)
    d = tempfile.mkdtemp()
    p = os.path.join(d, "시트.png")
    Image.fromarray(img).save(p, dpi=(300, 300))
    app = _app()
    try:
        app.input_path.set(p)
        app.job_type.set(job)
        handled = app._shape_cut_auto(p)
        for _ in range(5):
            app.update()
        if job == "MASKING_TAPE":
            return                                                   # 흰 바탕 테이프는 예전 방식(바뀌지 않음)
        assert handled and len(app._accumulated[-1].offsets["cut"].geoms) == 2
    finally:
        app.destroy()


def test_empty_sheet_job_falls_back_without_error():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "빈.png")
    Image.fromarray(_blank()).save(p)
    app = _app()
    try:
        app.input_path.set(p)
        app.job_type.set("AUTO_STYLE")
        assert app._shape_cut_auto(p) is False
    finally:
        app.destroy()


def test_mixed_rest_keeps_away_from_domusong_cell():
    img = _blank(900, 1400)
    _line_face(img, (300, 450), 200)
    cv2.rectangle(img, (560, 250), (900, 650), (90, 150, 220), -1)   # 도무송으로 표시할 카드(왼쪽 얼굴과 가까움)
    _painted(img, (1150, 450), 160)
    d = tempfile.mkdtemp()
    p = os.path.join(d, "mix.png")
    Image.fromarray(img).save(p)
    app = _app()
    try:
        app.input_path.set(p)
        app.job_type.set("MIXED_AUTO")
        app.style_margin_mm.set(2.2)
        app._mixed_boxes = [(80, 230, 520, 670), (560, 250, 900, 650), (980, 280, 1320, 620)]
        app._mixed_cell_boxes_px = list(app._mixed_boxes)
        app._mixed_groups = [[0], [1], [2]]
        app._mixed_marked = {1}
        app._mixed_processed = set()
        app._mixed_added_count = 0
        app._run_mixed_generate_rest_fine(p, [0, 2])
        for _ in range(5):
            app.update()
        from shapely.geometry import box

        cut = app._accumulated[-1].offsets["cut"]
        assert len(cut.geoms) == 2
        card = box(560, 250, 900, 650)
        assert all(g.distance(card) >= 2.0 * MM - 1 for g in cut.geoms)
    finally:
        app.destroy()


@pytest.mark.parametrize("mode", ["P", "L", "CMYK_jpg", "I;16", "RGBA_opaque"])
def test_auto_style_job_on_file_modes_and_korean_path(mode):
    img = _blank()
    _line_face(img, (300, 450), 180)
    _painted(img, (850, 450), 180)
    d = tempfile.mkdtemp(prefix="한글 폴더 ")
    p = os.path.join(d, "멍푸 시트.png")
    im = Image.fromarray(img)
    if mode == "P":
        im.convert("P", palette=Image.ADAPTIVE, colors=64).save(p)
    elif mode == "L":
        im.convert("L").save(p)
    elif mode == "CMYK_jpg":
        p = os.path.join(d, "멍푸 시트.jpg")
        im.convert("CMYK").save(p, quality=95)
    elif mode == "I;16":
        Image.fromarray((np.asarray(im.convert("L")).astype(np.uint16) * 257)).save(p)
    else:
        im.convert("RGBA").save(p)
    app = _app()
    try:
        app.input_path.set(p)
        app.job_type.set("AUTO_STYLE")
        assert app._shape_cut_auto(p)
        for _ in range(5):
            app.update()
        cut = app._accumulated[-1].offsets["cut"]
        assert len(cut.geoms) == 2
        left = [g for g in cut.geoms if g.centroid.x < 600][0]
        right = [g for g in cut.geoms if g.centroid.x > 600][0]
        assert left.bounds[0] < 300 - 180                              # 선 그림 = 유테(바깥)
        assert right.bounds[0] > 850 - 180                             # 칠한 그림 = 무테(안쪽)
    finally:
        app.destroy()
