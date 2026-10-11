"""2026-10-12: 그림 모양 기준 칼선(알고리즘 확인용 합성 이미지 -- 칼선 품질 판단은 실제 파일로 함).
  - 진한 그러데이션·연한 격자·흰 반짝이 배경 위 흰 테두리 스티커: 테두리 바깥선을 칼선으로, 반짝이는 뺀다.
  - 투명 배경 그림 + 유테: 그림에서 유테 간격만큼 띄운 칼선. 무테는 예전 방식(False)."""
import os
import tempfile

import cv2
import numpy as np
from PIL import Image

from core import white_layer as wl

DPI = 300.0


def _sticker_sheet(white_bg=False):
    h, w = 700, 900
    y = np.linspace(0, 1, h)[:, None]
    img = np.zeros((h, w, 3), np.uint8)
    img[...] = (255, 255, 255) if white_bg else np.dstack([
        np.repeat(110 - 40 * y, w, 1), np.repeat(160 + 40 * y, w, 1), np.repeat(235 - 20 * y, w, 1)]).astype(np.uint8)
    if not white_bg:
        img[::60, :] = (140, 180, 240)          # 연한 격자(흰색 아님)
        img[:, ::60] = (140, 180, 240)
        for cx, cy in [(80, 80), (820, 640), (450, 650)]:   # 흰 반짝이(가는 십자 + 작은 점)
            cv2.line(img, (cx - 12, cy), (cx + 12, cy), (255, 255, 255), 2)
            cv2.line(img, (cx, cy - 12), (cx, cy + 12), (255, 255, 255), 2)
            cv2.circle(img, (cx, cy), 3, (255, 255, 255), -1)
    art = np.zeros((h, w), np.uint8)
    cv2.ellipse(art, (250, 300), (120, 160), 20, 0, 360, 1, -1)
    cv2.rectangle(art, (560, 200), (760, 420), 1, -1)
    ring = cv2.dilate(art, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (41, 41)))   # 흰 테두리 약 1.7mm
    img[ring > 0] = (255, 255, 255)
    img[art > 0] = (60, 120, 90)
    img[(art > 0) & ((np.indices((h, w))[0] % 25) < 6)] = (250, 250, 250)   # 그림 안 흰 줄무늬
    return img, art.astype(bool), ring.astype(bool)


def test_sticker_border_mask_on_busy_background():
    img, art, ring = _sticker_sheet()
    m = wl.sticker_border_mask(img, DPI)
    assert m is not None
    assert (m & art).sum() == art.sum()                      # 그림을 모두 덮음
    assert (m & ~ring).sum() <= 0.01 * m.sum()               # 흰 테두리 바깥으로 거의 안 나감
    assert (ring & ~m).sum() <= 0.02 * ring.sum()            # 테두리 바깥선까지
    assert not m[80, 80] and not m[640, 820]                 # 반짝이는 스티커가 아님
    cuts = wl.cut_from_white_mask(m, DPI, offset_mm=0.0)
    assert len(cuts.geoms) == 2


def test_sticker_border_mask_skips_white_background():
    img, _, _ = _sticker_sheet(white_bg=True)
    assert wl.sticker_border_mask(img, DPI) is None
    flat = np.full((300, 300, 3), (90, 140, 220), np.uint8)   # 스티커 없는 배경
    assert wl.sticker_border_mask(flat, DPI) is None


def test_shape_cut_source_kinds():
    img, _, _ = _sticker_sheet()
    d = tempfile.mkdtemp()
    p = os.path.join(d, "s.png")
    Image.fromarray(img).save(p)
    kind, m = wl.shape_cut_source(p, DPI)
    assert kind == "sticker" and m.any()
    rgba = np.zeros((200, 200, 4), np.uint8)
    cv2.circle(rgba, (100, 100), 50, (200, 80, 60, 255), -1)
    p2 = os.path.join(d, "t.png")
    Image.fromarray(rgba).save(p2)
    kind, m = wl.shape_cut_source(p2, DPI)
    assert kind == "alpha" and m[100, 100] and not m[5, 5]


def test_gui_shape_cut_line_art_transparent_and_sticker():
    from gui.app import CutLineApp

    d = tempfile.mkdtemp()
    rgba = np.zeros((400, 400, 4), np.uint8)
    cv2.circle(rgba, (200, 200), 100, (150, 200, 230, 255), -1)   # 밝은 하늘색(예전엔 배경으로 오인)
    cv2.circle(rgba, (200, 200), 100, (30, 30, 30, 255), 4)       # 선화
    p = os.path.join(d, "t.png")
    Image.fromarray(rgba).save(p)
    img, art, _ = _sticker_sheet()
    ps = os.path.join(d, "s.png")
    Image.fromarray(img).save(ps)
    app = CutLineApp()
    app.withdraw()
    if not hasattr(app, "preview_canvas"):
        app._build_layout()
    try:
        app._show_note_dialog = lambda *a, **k: None
        app.input_path.set(p)
        app.job_type.set("BORDERLESS")
        assert not app._shape_cut_auto(p)                 # 무테 + 투명 배경은 예전 방식
        app.job_type.set("LINE_ART")
        assert app._shape_cut_auto(p)
        item = app._accumulated[-1]
        cut = item.offsets["cut"]
        assert len(cut.geoms) == 1
        r_px = (cut.area / np.pi) ** 0.5
        margin_mm = (r_px - 101) / DPI * 25.4
        assert abs(margin_mm - float(app.style_margin_mm.get())) < 0.15, margin_mm
        app._accumulated.clear()
        app.input_path.set(ps)
        app.job_type.set("BORDERLESS")
        dialogs = []
        app._show_note_dialog = lambda *a, **k: dialogs.append(a)
        assert app._shape_cut_auto(ps)                    # 흰 테두리 스티커는 무테도
        assert len(app._accumulated[-1].offsets["cut"].geoms) == 2
        for _ in range(5):
            app.update()
        # 스티커 칼선(간격 0)이 유테/무테 간격 칸을 덮어쓰지 않고, 안내 창도 띄우지 않는다(실제 PC에서 생긴 문제)
        assert abs(float(app.style_margin_mm.get()) - 1.2) < 1e-9 and not dialogs
        assert "흰 테두리 스티커 2개" in app.status.get()
    finally:
        app.destroy()
