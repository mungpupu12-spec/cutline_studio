"""2026-10-05(멍푸: "칼선을 보완하고 화이트 레이어를 만들어주는 기능"): core.white_layer 계산과 화면 동작.

계산 시험은 알고리즘이 맞는지 보는 것이라 도형(원·사각형)을 직접 만든다(칼선 품질 판단용 아님 -- 품질은
실제 납품 파일 "좋은 예"로 따로 대조했다, 일지 2026-10-05). 화면 시험 그림은 저장소의 samples/만 쓴다."""
import os
import sys
import tempfile

import numpy as np
import cv2
from shapely.geometry import MultiPolygon, Point, Polygon, box

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import white_layer as wl  # noqa: E402

SAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples")
DPI = 300.0
PX = DPI / 25.4  # 1mm = 11.8px


def _disc_mask(shape, centers_r):
    m = np.zeros(shape, np.uint8)
    for (x, y, r) in centers_r:
        cv2.circle(m, (int(x), int(y)), int(r), 1, -1)
    return m.astype(bool)


def test_small_holes_filled_large_kept():
    m = np.zeros((400, 400), bool)
    m[50:350, 50:350] = True
    m[100:110, 100:110] = False          # 약 0.85mm 구멍 -> 메움
    m[200:300, 200:300] = False          # 약 8.5mm 구멍 -> 그대로
    out = wl.fill_small_holes(m, 2.3 * PX)
    assert out[105, 105] and not out[250, 250]
    assert out[60, 60] and not out[10, 10]


def test_choke_and_geometry_roundtrip():
    m = _disc_mask((300, 300), [(150, 150, 100)])
    g = wl.mask_to_geom(wl.build_white_mask(m, DPI))
    assert abs(g.area - m.sum()) / m.sum() < 0.03
    r = wl.rasterize_geom(g, 300, 300)
    assert (r & m).sum() / (r | m).sum() > 0.98
    ch = wl.build_white_mask(m, DPI, choke_mm=0.5)
    shrink_px = (np.sqrt(m.sum() / np.pi) - np.sqrt(ch.sum() / np.pi))
    assert abs(shrink_px - 0.5 * PX) < 1.5


def test_cut_offset_and_merge_gap():
    # 원 둘: 가장자리 사이 3mm(칼선 1.3mm씩 띄우면 0.4mm -> 하나로 합침), 셋째는 15mm 떨어짐(따로)
    r = 6 * PX
    c1 = (100, 150)
    c2 = (c1[0] + 2 * r + 3 * PX, 150)
    c3 = (c2[0] + 2 * r + 15 * PX, 150)
    m = _disc_mask((300, 900), [(c1[0], c1[1], r), (c2[0], c2[1], r), (c3[0], c3[1], r)])
    cuts = wl.cut_from_white_mask(m, DPI, offset_mm=1.3, merge_gap_mm=2.0)
    assert len(cuts.geoms) == 2
    lone = min(cuts.geoms, key=lambda p: p.distance(Point(c3)))
    # 혼자인 원의 칼선은 원에서 1.3mm 바깥
    d_mm = (np.sqrt(lone.area / np.pi) - r) / PX
    assert abs(d_mm - 1.3) < 0.15, d_mm
    # 합친 칼선끼리 / 칼선 사이 간격은 2mm 이상
    a, b = cuts.geoms
    assert a.distance(b) / PX >= 2.0


def test_supplement_adds_only_missing_and_reports_close():
    r = 5 * PX
    pa, pb, pc = (100, 150), (600, 150), (100 + 2 * r + 3.6 * PX, 150)   # c는 a와 3.6mm(칼선끼리 1mm)
    m = _disc_mask((300, 900), [(pa[0], pa[1], r), (pb[0], pb[1], r), (pc[0], pc[1], r)])
    g = wl.mask_to_geom(m)
    existing = MultiPolygon([Point(pa).buffer(r + 1.3 * PX)])        # a에만 칼선
    added, close, n_missing = wl.supplement_missing_cuts(existing, m, g, DPI, offset_mm=1.3, merge_gap_mm=2.0)
    assert n_missing == 2
    assert len(added.geoms) == 1 and added.geoms[0].contains(Point(pb))  # 멀리 있는 b만 추가
    assert len(close) == 1                                                # a 옆 c는 위치만 알림


def test_svg_and_png_export_white_and_tiles():
    from core.cutline_core import CutlineResult, OffsetSpec
    from core.png_export import export_white_png
    from core.svg_export import export_svg
    from PIL import Image

    design = MultiPolygon([Point(100, 100).buffer(60)])
    cut = MultiPolygon([Point(100, 100).buffer(75)])
    white = MultiPolygon([Polygon(Point(100, 100).buffer(60).exterior, [Point(100, 100).buffer(10).exterior.coords])])
    res = CutlineResult(dpi=300.0, width_px=200, height_px=200, design=design, offsets={"cut": cut},
                        offset_mm=OffsetSpec(1, 1, 1))
    d = tempfile.mkdtemp()
    svg = os.path.join(d, "a.svg")
    placement = {"page_w_pt": 300, "page_h_pt": 100, "x0_pt": 0, "y0_pt": 0, "pt_per_px": 0.24,
                 "tiles": [(0, 0), (100, 0), (200, 0)]}
    export_svg(res, svg, placement=placement, white_geom=white)
    txt = open(svg, encoding="utf-8").read()
    assert txt.count('id="화이트레이어"') == 1 and txt.count('화이트레이어"') == 3
    assert txt.count('fill="#FFFFFF"') == 3 and txt.count('<g id="cut"') == 1 and txt.count("_cut\"") == 2
    assert txt.index("화이트레이어") < txt.index('<g id="cut"')        # 화이트가 칼선 아래
    png = os.path.join(d, "w.png")
    export_white_png(white, 200, 200, png, dpi=300)
    a = np.asarray(Image.open(png))
    assert a.shape == (200, 200, 4)
    assert a[100, 140, 3] == 255 and tuple(a[100, 140, :3]) == (255, 255, 255)   # 화이트
    assert a[100, 100, 3] == 0 and a[5, 5, 3] == 0                               # 구멍·바깥은 투명


def test_gui_make_white_and_cut_from_white():
    from gui.app import CutLineApp

    app = CutLineApp()
    app.withdraw()
    if not hasattr(app, "preview_canvas"):
        app._build_layout()
    try:
        app._show_note_dialog = lambda *a, **k: None
        app._show_confirm_dialog = lambda *a, **k: True
        src = os.path.join(SAMPLES, "sheet_char_1.png")
        app.input_path.set(src)
        app.job_type.set("MASKING_TAPE")
        mask, geom, source = app._wl_compute(src)
        assert source == "alpha" and geom is not None and not geom.is_empty
        app._wl_made(src, mask, geom, source)
        assert app._preview_full_image is not None
        assert app._white_geom is not None and "화이트" in app.white_status.get()
        # 칼선이 아직 없으니 화이트 기준 칼선으로 새로 만듦(시험은 mainloop가 없어 계산을 이 스레드에서)
        import threading
        started = []
        app_thread = threading.Thread
        threading.Thread = lambda target, args, daemon=True: type("T", (), {"start": lambda self: started.append(
            (target, args))})()
        try:
            app._on_white_cut()
        finally:
            threading.Thread = app_thread
        assert started and started[0][0].__name__ == "_wl_run_cut"
        started[0][0](*started[0][1])
        app.update()
        cut = app._last_result.offsets["cut"]
        assert not cut.is_empty
        # 화이트는 칼선 안쪽에(어디서나 1mm 넘게 떨어짐)
        for p in cut.geoms:
            for w in geom.geoms:
                if p.intersects(w):
                    assert p.exterior.distance(w) / PX > 1.0
        app._wl_clear()
        assert app._white_geom is None
    finally:
        app.destroy()
