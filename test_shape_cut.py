"""2026-10-12: 그림 모양 기준 칼선(알고리즘 확인용 합성 이미지 -- 칼선 품질 판단은 실제 파일로 함).
  - 진한 그러데이션·연한 격자·흰 반짝이 배경 위 흰 테두리 스티커: 테두리 바깥선을 칼선으로, 반짝이는 뺀다.
  - 투명 배경 그림 + 유테: 그림에서 유테 간격만큼 띄운 칼선. 무테는 그림 안쪽으로 같은 간격.
  - 칼선이 2mm보다 가까우면 합칠지 따로 자를지 묻는다."""
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
        app.job_type.set("BORDERLESS")                    # 무테 + 투명 배경: 그림 안쪽으로 유테/무테 간격만큼
        assert app._shape_cut_auto(p)
        cut = app._accumulated[-1].offsets["cut"]
        assert len(cut.geoms) == 1
        inset_mm = (101 - (cut.area / np.pi) ** 0.5) / DPI * 25.4
        assert abs(inset_mm - float(app.style_margin_mm.get())) < 0.2, inset_mm
        app._accumulated.clear()
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


def test_close_stickers_ask_merge_or_separate():
    """흰 테두리 사이가 2mm보다 가까운 스티커 두 개 -> 고르는 창. 따로 자르기면 2개, 합치기면 1개."""
    from gui.app import CutLineApp

    h, w = 400, 600
    img = np.zeros((h, w, 3), np.uint8)
    img[...] = (90, 140, 220)
    art = np.zeros((h, w), np.uint8)
    cv2.rectangle(art, (100, 100), (260, 300), 1, -1)
    cv2.rectangle(art, (100 + 160 + 2 * 20 + 12, 100), (520, 300), 1, -1)   # 테두리 사이 12px(약 1mm)
    ring = cv2.dilate(art, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (41, 41)))
    img[ring > 0] = (255, 255, 255)
    img[art > 0] = (40, 90, 60)
    d = tempfile.mkdtemp()
    p = os.path.join(d, "close.png")
    Image.fromarray(img).save(p)
    app = CutLineApp()
    app.withdraw()
    if not hasattr(app, "preview_canvas"):
        app._build_layout()
    try:
        app._show_note_dialog = lambda *a, **k: None
        app.input_path.set(p)
        app.job_type.set("LINE_ART")
        for answer, n_expected in ((False, 2), (True, 1)):
            asked = []
            app._show_confirm_dialog = lambda h_, lines, **k: (asked.append(k), answer)[1]
            app._accumulated.clear()
            assert app._shape_cut_auto(p)
            for _ in range(5):
                app.update()
            assert asked and asked[0]["yes_text"] == "하나로 합치기" and asked[0]["no_text"] == "따로 자르기"
            assert len(app._accumulated[-1].offsets["cut"].geoms) == n_expected
    finally:
        app.destroy()


def _scene_image():
    """여러 색 배경(위 연한 벽 / 가운데 격자 무늬 띠 / 아래 연회색 바닥) 앞에 그림 셋: 띠와 바닥에 걸친 큰 덩어리,
    바닥 위 흰 사각형(바닥과 밝기 차이 작음), 바닥 위 여러 색 줄무늬 기둥. 알고리즘 확인용 합성 이미지."""
    h, w = 600, 800
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = (252, 246, 232)
    img[120:380] = (35, 175, 204)
    img[120:380, ::40] = (122, 195, 217)
    img[120:380:40, :] = (122, 195, 217)
    img[380:] = (240, 240, 244)
    big = np.zeros((h, w), np.uint8)
    cv2.ellipse(big, (400, 300), (150, 200), 0, 0, 360, 1, -1)
    img[big > 0] = (238, 178, 82)
    cv2.circle(img, (400, 300), 30, (0, 168, 179), -1)               # 배경 띠와 비슷한 색이 그림 안에
    paper = np.zeros((h, w), np.uint8)
    cv2.rectangle(paper, (60, 420), (200, 560), 1, -1)
    img[paper > 0] = (255, 255, 255)
    cv2.line(img, (80, 450), (180, 450), (20, 20, 20), 4)
    pill = np.zeros((h, w), np.uint8)
    cv2.rectangle(pill, (640, 430), (720, 570), 1, -1)
    img[pill > 0] = (255, 255, 255)
    img[430:460, 640:721] = (138, 204, 227)
    img[500:520, 640:721] = (0, 131, 76)
    return cv2.GaussianBlur(img, (3, 3), 0), big.astype(bool), paper.astype(bool), pill.astype(bool)


def test_scene_objects_mask_separates_objects_from_multicolor_background():
    img, big, paper, pill = _scene_image()
    m = wl.scene_objects_mask(img, DPI)
    assert m is not None
    n, lab = cv2.connectedComponents(m.astype(np.uint8))
    assert n - 1 == 3
    for obj in (big, paper, pill):
        assert (m & obj).sum() >= 0.97 * obj.sum()                 # 그림은 거의 다(흰 사각형·줄무늬 기둥 포함)
    assert (m & ~(big | paper | pill)).sum() <= 0.02 * m.sum()       # 배경은 거의 안 들어감
    cuts, _ = wl.borderless_cut_from_mask(wl.build_white_mask(m, DPI), DPI, inset_mm=1.2)
    assert len(cuts.geoms) == 3


import pytest


@pytest.mark.parametrize("line", [(30, 30, 30), (215, 190, 160)])   # 검정 선, 옅은 갈색 선
def test_scene_objects_mask_white_face_on_white_background(line):
    """흰 바탕 위, 선으로 그린 흰 얼굴(선에 0.2mm 틈) -- 얼굴 전체가 그림(유테 칼선이 선 바깥으로).
    2026-10-22 멍푸: "유테는 이미지 외곽에 선이 있는거야, 옅은 갈색 같은 색도 있으니 선을 기준으로 만들어"."""
    img = np.full((500, 500, 3), 255, np.uint8)
    cv2.circle(img, (250, 250), 150, line, 4)                         # 흰 얼굴 테두리 선
    img[96:106, 248:250] = 255                                        # 선의 좁은 틈(2px, 약 0.17mm)
    cv2.circle(img, (200, 230), 10, (30, 30, 30), -1)                 # 눈
    cv2.circle(img, (300, 230), 10, (30, 30, 30), -1)
    cv2.ellipse(img, (250, 300), (40, 20), 0, 0, 180, (230, 170, 170), -1)
    face = np.zeros((500, 500), np.uint8)
    cv2.circle(face, (250, 250), 150, 1, -1)
    m = wl.scene_objects_mask(img, DPI)
    assert m is not None
    assert (m & face.astype(bool)).sum() >= 0.98 * face.sum()         # 흰 얼굴 안쪽도 그림
    assert (m & ~cv2.dilate(face, np.ones((7, 7), np.uint8)).astype(bool)).sum() == 0
    cuts = wl.cut_from_white_mask(wl.build_white_mask(m, DPI), DPI, offset_mm=1.2)
    assert len(cuts.geoms) == 1
    r = (cuts.geoms[0].area / np.pi) ** 0.5
    assert r > 152                                                    # 칼선이 선 바깥


def _outlined_and_painted():
    """흰 바탕: 왼쪽은 선으로 그린 얼굴 + 위 말풍선(선), 오른쪽은 선 없이 칠한 그림(가장자리가 안쪽보다 밝음).
    알고리즘 확인용 합성 이미지."""
    img = np.full((600, 900, 3), 255, np.uint8)
    cv2.circle(img, (220, 360), 120, (40, 40, 40), 4)                  # 얼굴(선)
    cv2.ellipse(img, (220, 190), (90, 35), 0, 0, 360, (40, 40, 40), 3)  # 말풍선(선), 얼굴과 약 15px 떨어짐
    yy, xx = np.mgrid[0:600, 0:900]
    d = np.hypot(xx - 650, yy - 330)
    blob = d < 160
    shade = np.clip(110 + (d / 160.0) * 60, 0, 255)                     # 가운데 어둡고 가장자리 밝은 물감 칠
    img[blob] = np.stack([shade, shade * 0.75, shade * 0.5], -1)[blob].astype(np.uint8)
    return img


def test_auto_style_cuts_outline_yute_and_painted_mute():
    img = _outlined_and_painted()
    m = wl.scene_objects_mask(img, DPI)
    n, lab = cv2.connectedComponents(m.astype(np.uint8))
    kinds = {}
    for i in range(1, n):
        comp = lab == i
        ys, xs = np.nonzero(comp)
        kinds[int(xs.mean() > 450)] = kinds.get(int(xs.mean() > 450), []) + [wl.has_outline_line(img, comp, DPI)]
    assert all(kinds[0]) and not any(kinds[1])                          # 선 있는 그림 = 유테, 칠한 그림 = 무테
    cuts, separate, ny, nm, _ = wl.auto_style_cuts(img, m, DPI, 2.2)
    assert ny in (1, 2) and nm == 1                                     # 말풍선이 얼굴과 가까우면 한 덩어리
    assert len(cuts.geoms) == 2                                         # 얼굴+말풍선 하나, 칠한 그림 하나
    left = [g for g in cuts.geoms if g.centroid.x < 450][0]
    right = [g for g in cuts.geoms if g.centroid.x > 450][0]
    assert left.bounds[1] < 190 - 35 - 20 and left.bounds[3] > 360 + 120 + 20   # 유테: 말풍선·얼굴 바깥
    assert right.bounds[0] > 650 - 160                                  # 무테: 그림 안쪽
    assert wl.count_close_pairs(cuts, DPI) == 0                         # 최소 간격(2mm) 유지


def test_mixed_rest_uses_shape_cut_and_excludes_domusong():
    """'무테/유테+도무송'의 ③: 흰 바탕 그림이면 그림 모양 기준(유테/무테 자동)으로, 도무송 칸은 빼고."""
    from gui.app import CutLineApp

    img = _outlined_and_painted()
    d = tempfile.mkdtemp()
    p = os.path.join(d, "mixed.png")
    Image.fromarray(img).save(p)
    app = CutLineApp()
    app.withdraw()
    if not hasattr(app, "preview_canvas"):
        app._build_layout()
    try:
        app._show_note_dialog = lambda *a, **k: None
        app._show_confirm_dialog = lambda *a, **k: True
        app.input_path.set(p)
        app.job_type.set("MIXED_AUTO")
        app.style_margin_mm.set(2.2)
        app._mixed_boxes = [(80, 140, 360, 500), (480, 160, 820, 500)]
        app._mixed_cell_boxes_px = list(app._mixed_boxes)
        app._mixed_groups = [[0], [1]]
        app._mixed_marked = {1}                                         # 오른쪽 칸은 도무송으로 표시
        app._mixed_processed = set()
        app._mixed_added_count = 0
        app._run_mixed_generate_rest_fine(p, [0])
        for _ in range(5):
            app.update()
        cut = app._accumulated[-1].offsets["cut"]
        assert len(cut.geoms) == 1 and cut.geoms[0].centroid.x < 450   # 왼쪽(얼굴+말풍선) 하나만
        assert 0 in app._mixed_processed and 1 not in app._mixed_processed
    finally:
        app.destroy()


def test_label_band_gets_no_cut_and_stickers_stay_apart():
    """시트 위 가로 가격표 띠(초록 바탕 + 흰 글자) 아래에 선 그림 스티커 둘, 하나는 띠에 거의 붙어 있음.
    띠에는 칼선을 만들지 않고, 스티커 칼선은 띠에서 2mm 이상 떨어진다. 알고리즘 확인용 합성 이미지."""
    from shapely.geometry import box

    h, w = 900, 1400
    img = np.full((h, w, 3), 255, np.uint8)
    img[40:200, 40:1360] = (112, 168, 64)                              # 가격표 띠(약 13.5mm)
    cv2.putText(img, "LABEL 2800", (120, 150), cv2.FONT_HERSHEY_SIMPLEX, 3, (255, 255, 255), 8)
    cv2.circle(img, (400, 420), 190, (30, 30, 30), 5)                  # 띠 바로 아래(약 30px, 2.5mm)
    cv2.circle(img, (1000, 560), 180, (30, 30, 30), 5)
    m = wl.scene_objects_mask(img, DPI)
    m2, bands = wl.remove_label_bands(m, DPI)
    assert len(bands) == 1
    x0, y0, x1, y1 = bands[0]
    assert y0 <= 45 and y1 >= 195 and x1 - x0 > 1200
    cuts = wl.cut_from_white_mask(wl.build_white_mask(m2, DPI), DPI, offset_mm=1.2)
    cuts = wl.keep_away_from_bands(cuts, bands, DPI)
    assert len(cuts.geoms) == 2                                         # 스티커 둘, 띠는 없음
    band = box(x0, y0, x1, y1)
    for c in cuts.geoms:
        assert c.distance(band) >= wl._mm_to_px(2.0, DPI) - 1           # 띠와 2mm 이상


def _lollipop_sheet():
    """짙은 바탕 위 흰 테두리 스티커 3개. 그중 하나는 그림 속 가는 막대(0.25mm)가 흰 테두리 끝 바로 앞까지 닿는다."""
    h, w = 600, 900
    img = np.full((h, w, 3), (30, 40, 80), np.uint8)
    art = np.zeros((h, w), np.uint8)
    cv2.circle(art, (180, 300), 90, 1, -1)
    cv2.rectangle(art, (640, 220), (800, 380), 1, -1)
    stick = np.zeros((h, w), np.uint8)
    cv2.circle(stick, (400, 300), 70, 1, -1)
    ring = cv2.dilate(art | stick, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (41, 41)))
    cv2.rectangle(ring, (400, 280), (560, 320), 1, -1)                # 손잡이 흰 테두리
    img[ring > 0] = (255, 255, 255)
    img[art > 0] = (200, 60, 60)
    img[stick > 0] = (240, 200, 40)
    img[298:301, 400:557] = (240, 200, 40)                            # 가는 막대: 테두리 끝 3px 앞까지
    return img, ring.astype(bool)


def test_sticker_border_mask_thin_line_reaching_border_end():
    # 2026-10-25: 막대 손잡이처럼 가는 선이 흰 테두리 끝까지 닿은 스티커를 통째로 놓쳤다.
    img, ring = _lollipop_sheet()
    m = wl.sticker_border_mask(img, DPI)
    assert m is not None
    assert cv2.connectedComponents(m.astype(np.uint8))[0] - 1 == 3
    assert m[300, 400] and m[300, 540]                               # 막대 스티커와 손잡이
    assert (m & ~ring).sum() <= 0.01 * m.sum()


def test_sticker_border_mask_white_touching_sheet_edge():
    # 시트 가장자리에 흰색이 닿아 있어도 다른 스티커의 흰 테두리를 잃지 않는다.
    img, ring = _lollipop_sheet()
    img[:, :6] = (255, 255, 255)
    m = wl.sticker_border_mask(img, DPI)
    assert m is not None
    assert (ring[:, 10:] & ~m[:, 10:]).sum() <= 0.03 * ring.sum()  # 테두리 바깥선까지 덮음


def test_panel_sticker_mask_cuts_each_sticker_not_the_panel():
    # 2026-10-25: 흰 바탕 시트 위 짙은 색 판(패널) 위에 흰 테두리 스티커들 -- 판이 아니라 스티커마다.
    img, ring = _lollipop_sheet()
    sheet = np.full((800, 1100, 3), 255, np.uint8)
    sheet[100:700, 100:1000] = img
    mask = np.zeros(sheet.shape[:2], bool)
    mask[100:700, 100:1000] = True                                    # 그림 모양 기준이면 판 전체가 한 덩어리
    stk, panels = wl.panel_sticker_mask(sheet, mask, DPI)
    assert stk is not None and panels[400, 550]
    assert cv2.connectedComponents(stk.astype(np.uint8))[0] - 1 == 3
    assert not stk[110, 110]                                          # 판 바탕은 칼선 대상이 아님
    plain = np.zeros_like(mask)
    plain[200:400, 200:400] = True                                    # 판이 아닌 보통 그림
    sheet2 = np.full((800, 1100, 3), 255, np.uint8)
    sheet2[200:400, 200:400] = (200, 60, 60)
    assert wl.panel_sticker_mask(sheet2, plain, DPI) == (None, None)
