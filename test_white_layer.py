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


def test_page_render_hides_cut_white_guide_layers():
    """2026-10-11: 페이지를 통째로 그릴 때(서로 다른 그림 여러 장) 칼선·화이트·참고 레이어는 빼고 그림 레이어만.
    레이어 이름 규칙을 보는 시험이라 PDF를 직접 만든다."""
    import fitz
    from PIL import Image
    from core.ai_import import load_ai_as_raster

    d = tempfile.mkdtemp()
    src = os.path.join(d, "t.ai")
    doc = fitz.open()
    page = doc.new_page(width=200, height=100)
    art = doc.add_ocg("图案도안")
    cut = doc.add_ocg("刀线칼선")
    ref = doc.add_ocg("参考삭제x")
    for i, (x, oc) in enumerate(((20, art), (120, art))):   # 서로 다른 그림 두 장 -> 페이지 통째로 그리는 경로
        pm = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20 + i, 20), False)
        pm.set_rect(pm.irect, (200, 30 * i, 40))
        page.insert_image(fitz.Rect(x, 30, x + 40, 70), pixmap=pm, oc=oc)
    page.draw_rect(fitz.Rect(5, 5, 195, 95), color=(1, 0, 1), width=2, oc=cut)
    page.draw_line(fitz.Point(0, 50), fitz.Point(200, 50), color=(0, 1, 0), width=2, oc=ref)
    doc.save(src)
    out = os.path.join(d, "r.png")
    load_ai_as_raster(src, out, dpi=72)
    a = np.asarray(Image.open(out).convert("RGBA"))
    assert a[50, 40, 3] == 255                     # 그림
    assert a[6, 100, 3] == 0 and a[50, 100, 3] == 0  # 칼선 레이어 선, 참고 레이어 선은 안 그림


def test_kisscut_auto_uses_shape_based_cut_for_transparent_art():
    from gui.app import CutLineApp

    app = CutLineApp()
    app.withdraw()
    if not hasattr(app, "preview_canvas"):
        app._build_layout()
    try:
        app._show_note_dialog = lambda *a, **k: None
        shown = []
        app.after = lambda ms, f, *a: shown.append(f)
        src = os.path.join(SAMPLES, "scene.png")
        app.input_path.set(src)
        app.job_type.set("MASKING_TAPE")
        assert app._wl_kisscut_auto(src)
        cut = app._last_result.offsets["cut"]
        assert len(cut.geoms) >= 1 and shown
        # 꽉 찬(투명하지 않은) 그림이면 예전 방식으로 넘긴다
        from PIL import Image
        flat = os.path.join(tempfile.mkdtemp(), "flat.png")
        Image.open(src).convert("RGB").save(flat)
        assert not app._wl_kisscut_auto(flat)
    finally:
        app.destroy()


def test_white_checkbox_toggles_save_and_preview():
    """2026-10-11: "화이트 없음" 글자 대신 체크칸 -- 끄면 화면·저장에서 빠지고 켜면 다시 들어간다."""
    from core import preview
    from gui.app import CutLineApp

    app = CutLineApp()
    app.withdraw()
    if not hasattr(app, "preview_canvas"):
        app._build_layout()
    try:
        app._show_note_dialog = lambda *a, **k: None
        src = os.path.join(SAMPLES, "sheet_char_1.png")
        app.input_path.set(src)
        assert not app.white_on.get() and app._wl_active_geom() is None
        mask, geom, source = app._wl_compute(src)
        app._wl_made(src, mask, geom, source)
        assert app.white_on.get() and app._wl_active_geom() is geom and preview._WHITE_OVERLAY["geom"] is geom
        app.white_on.set(False)
        app._on_white_check()
        assert app._wl_active_geom() is None and preview._WHITE_OVERLAY["geom"] is None
        app.white_on.set(True)
        app._on_white_check()
        assert app._wl_active_geom() is geom and preview._WHITE_OVERLAY["geom"] is geom
        app._wl_clear()
        assert not app.white_on.get()
    finally:
        app.destroy()


def test_white_only_view_hides_art():
    """2026-10-11: '화이트만' 보기는 그림을 빼고 화이트(파란색)만, '적용된 모습'은 그림을 얹어서."""
    from core import preview
    from core.cutline_core import CutlineResult, OffsetSpec
    from PIL import Image

    src = os.path.join(SAMPLES, "sheet_char_1.png")
    a = np.asarray(Image.open(src).convert("RGBA"))
    m = a[..., 3] >= 64
    g = wl.mask_to_geom(m)
    res = CutlineResult(dpi=300.0, width_px=a.shape[1], height_px=a.shape[0], design=g, offsets={},
                        offset_mm=OffsetSpec(0, 0, 0))
    d = tempfile.mkdtemp()
    try:
        preview.set_white_overlay(g)
        ys, xs = np.nonzero(a[..., 3] == 255)
        y, x = int(ys[len(ys) // 2]), int(xs[len(xs) // 2])
        preview.set_white_only(False)
        on = np.asarray(Image.open(preview.render_preview(res, os.path.join(d, "a.png"), original_image_path=src)))
        preview.set_white_only(True)
        only = np.asarray(Image.open(preview.render_preview(res, os.path.join(d, "b.png"), original_image_path=src)))
        mg = 40
        assert tuple(only[y + mg, x + mg]) == preview.WHITE_PREVIEW_RGB          # 화이트만: 파란색
        assert tuple(on[y + mg, x + mg]) == tuple(a[y, x, :3])                  # 적용된 모습: 그림 색
    finally:
        preview.set_white_only(False)
        preview.set_white_overlay(None)


def test_help_guide_opens_with_every_section():
    from gui.app import CutLineApp
    from gui.help_guide import HELP_SECTIONS

    app = CutLineApp()
    app.withdraw()
    if not hasattr(app, "preview_canvas"):
        app._build_layout()
    try:
        win = app._show_help_guide()
        app.update()
        texts = []

        def walk(w):
            for c in w.winfo_children():
                try:
                    texts.append(c.cget("text"))
                except Exception:  # noqa: BLE001
                    pass
                walk(c)
        walk(win)
        for title, _what, _hows in HELP_SECTIONS:
            assert title in texts, title
        assert any("화이트만" in t for t in texts if isinstance(t, str))
        assert app._show_help_guide() is None  # 이미 열려 있으면 그 창을 앞으로
        win.destroy()
    finally:
        app.destroy()


def test_tooltips_short_and_cover_every_control():
    """2026-10-11: 마우스를 2초 올려 두면 15자 이내 설명이 뜬다. 모든 버튼·체크박스·숫자 칸에 설명이 있어야 한다."""
    import customtkinter as ctk
    from gui.app import CutLineApp
    from gui import tooltips as tt

    for t in list(tt.TIPS.values()) + list(tt.FIELD_TIPS.values()) + list(tt.SECTION_TIPS.values()):
        assert 0 < len(t) <= tt.MAX_TIP_LEN, t
        assert t.endswith("니다"), t   # 짧은 문장(~합니다/~입니다)
    assert tt.MAX_TIP_LEN == 15
    assert tt.TIP_DELAY_MS == 2000

    app = CutLineApp()
    app.withdraw()
    if not hasattr(app, "preview_canvas"):
        app._build_layout()
    try:
        app.update()
        mgr = app._tooltips
        missing = []

        def has_tip(w):
            while w is not None:
                if mgr.tip_for(w):
                    return True
                w = getattr(w, "master", None)
            return False

        def walk(w):
            for c in w.winfo_children():
                if isinstance(c, ctk.CTkToplevel) or c is getattr(app, "_license_gate_frame", None):
                    continue   # 튜토리얼 창·라이선스 입력 화면은 제외
                # 묶음 버튼(CTkSegmentedButton)은 안쪽 버튼 하나하나를 확인한다
                if isinstance(c, (ctk.CTkButton, ctk.CTkCheckBox, ctk.CTkEntry)):
                    if not has_tip(c):
                        try:
                            missing.append(c.cget("text"))
                        except Exception:  # noqa: BLE001
                            missing.append(str(c))
                walk(c)
        walk(app)
        assert not missing, missing

        # 카드 제목(굵은 글씨)에도 모두 설명이 있다
        titles = []

        def walk_titles(w):
            for c in w.winfo_children():
                if isinstance(c, ctk.CTkToplevel) or c is getattr(app, "_license_gate_frame", None):
                    continue
                if isinstance(c, ctk.CTkLabel) and c.cget("font") is app.font_section:
                    titles.append(c)
                walk_titles(c)
        walk_titles(app)
        assert len(titles) >= len(tt.SECTION_TIPS)
        assert not [c.cget("text") for c in titles if not mgr.tip_for(c)]

        # 버튼 안쪽(캔버스)에 마우스가 들어가면 2초 뒤 설명이 뜨고, 안쪽끼리 옮겨 다녀도 유지된다.
        btn = next(c for c in app.winfo_children() for c in c.winfo_children()
                   if isinstance(c, ctk.CTkButton) and c.cget("text") == "도움말")
        inner = btn.winfo_children()[0]

        class Ev:
            widget = inner
        mgr.delay_ms = 50
        mgr._on_enter(Ev)
        assert mgr._job is not None and mgr.visible_text is None
        mgr._on_enter(Ev)                    # 같은 버튼 안 -> 예약을 새로 만들지 않음
        # 예약된 표시를 바로 실행(전체 시험 중에는 다른 창의 실제 Enter/Leave가 끼어들어 기다리면 흔들림 --
        # 실제 마우스로 2초에 뜨는 것은 Xvfb에서 따로 확인함)
        app.after_cancel(mgr._job)
        mgr._show()
        assert mgr.visible_text == "기능 설명을 봅니다"
        mgr._on_press(Ev)                    # 누르면 사라짐
        assert mgr.visible_text is None
    finally:
        app.destroy()


def test_main_window_wording_uses_plain_sentences():
    """2026-10-11: 화면 문구 정리(빨간 표시). 옛 문구가 남아 있지 않은지 확인."""
    from gui.app import CutLineApp, JOB_TYPE_LABELS
    from gui.help_guide import HELP_SECTIONS

    app = CutLineApp()
    app.withdraw()
    if not hasattr(app, "preview_canvas"):
        app._build_layout()
    try:
        app.update()
        texts = []

        def walk(w):
            for c in w.winfo_children():
                try:
                    t = c.cget("text")
                    if isinstance(t, str):
                        texts.append(t)
                except Exception:  # noqa: BLE001
                    pass
                walk(c)
        walk(app)
        joined = "\n".join(texts)
        for old in ("먼저 골라주세요", "예, 있음", "아니오, 없음", "(GrabCut)", "동시 적용)", "무테/유테 선택 시에만",
                    "여러 번 추가해도", "이 영역 추가 + 미리보기", "(모두 지우기)", "칼선 내보내기 (PNG)",
                    "고르세요."):
            assert old not in joined, old
        for new in ("있음", "없음", "선택 영역 추가", "누적 초기화", "완성 파일 내보내기", "무테 칼선도 함께 추가"):
            assert new in texts, new
        assert app.accum_status.get().endswith("니다.")
        assert JOB_TYPE_LABELS["MASKING_TAPE"] == "키스컷 (마스킹테이프)"
        help_text = "\n".join(w + "\n" + "\n".join(h) for _t, w, h in HELP_SECTIONS)
        for old in ("(완칼)", "세밀함", "섞인 시트", "안에 든 인쇄용 그림"):
            assert old not in help_text, old
        for _t, what, hows in HELP_SECTIONS:     # 문장은 ~다.로 끝나고, 도움말 창에서 두 줄 이내(한 줄 약 29자)
            for line in [what] + hows:
                assert line.endswith("다."), line
                assert len(line) <= 48, line
    finally:
        app.destroy()
