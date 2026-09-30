"""2026-09-30 속도 개선 + 칼선 보완 3가지 + PNG 내보내기 (합성 이미지로 알고리즘 정확성만 확인
-- 칼선 품질 판단은 실제 테스트 파일 대조로 따로 함).

  - 둘러싸인 영역 채우기: 새(연결 성분) 방식이 예전 floodFill 반복 방식과 결과가 완전히 같다.
  - 크림색(거의 흰색) 바탕 위 캐릭터는 흰 테두리(유테)로 보지 않는다 / 진짜 흰 테두리는 유테.
  - 무테 안쪽 칼선이 초승달처럼 길게 뻗은 끝을 뭉툭하게 자르지 않는다.
  - 칸 모서리에 걸친 요소는 같은 색이 반대쪽 가장자리에 있어도 배경으로 채우지 않는다.
  - 칼선 PNG: 원본과 같은 크기·해상도, 투명 배경, 마젠타 선만.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("CUTLINE_LICENSE_DEV_BYPASS", "1")

import cv2
import numpy as np
from PIL import Image, ImageDraw
from shapely.affinity import translate
from shapely.geometry import MultiPolygon, Point, box


def _save(img):
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    img.save(path)
    return path


def _old_fill_enclosed(mask_u8, border_margin_px=0):
    """예전 구현(가장자리 픽셀마다 floodFill) -- 비교용 기준."""
    background = cv2.bitwise_not(mask_u8)
    padded = cv2.copyMakeBorder(background, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=255)
    flood_mask = np.zeros((padded.shape[0] + 2, padded.shape[1] + 2), np.uint8)
    reached = padded.copy()
    ph, pw = padded.shape[:2]
    pts = set()
    for x in range(pw):
        pts.add((x, 0)); pts.add((x, ph - 1))
    for y in range(ph):
        pts.add((0, y)); pts.add((pw - 1, y))
    if border_margin_px > 0:
        h0, w0 = background.shape[:2]
        m = min(border_margin_px, h0, w0)
        cols = list(range(0, m)) + list(range(max(0, w0 - m), w0))
        rows = list(range(0, m)) + list(range(max(0, h0 - m), h0))
        for y in range(h0):
            for x in cols:
                pts.add((x + 1, y + 1))
        for x in range(w0):
            for y in rows:
                pts.add((x + 1, y + 1))
    for bx, by in pts:
        if reached[by, bx] == 255:
            cv2.floodFill(reached, flood_mask, (bx, by), 128)
    reached = reached[1:-1, 1:-1]
    enclosed = (background == 255) & (reached != 128)
    out = mask_u8.copy()
    out[enclosed] = 255
    return out


def test_fill_enclosed_regions_matches_previous_implementation():
    from core.multi_design import _fill_enclosed_regions

    rng = np.random.default_rng(3)
    for t in range(40):
        h, w = rng.integers(30, 200, 2)
        m = (rng.random((h, w)) < rng.uniform(0.2, 0.6)).astype(np.uint8) * 255
        if t % 2:
            m = cv2.dilate(m, np.ones((3, 3), np.uint8))
        bm = int(rng.integers(0, 10))
        assert np.array_equal(_fill_enclosed_regions(m, border_margin_px=bm), _old_fill_enclosed(m, bm))


def test_cream_background_is_not_a_white_border_but_a_real_white_border_is():
    from core.interactive_cutline import has_white_or_black_border

    img = Image.new("RGB", (600, 600), (249, 243, 230))       # 크림색 바탕(거의 흰색)
    d = ImageDraw.Draw(img)
    d.ellipse((200, 200, 400, 400), fill=(230, 170, 99))        # 테두리 없는 캐릭터
    path = _save(img)
    try:
        body = Point(300, 300).buffer(100)
        assert not has_white_or_black_border(path, body, 300.0)
    finally:
        os.remove(path)

    img = Image.new("RGB", (600, 600), (40, 150, 170))          # 진한 바탕
    d = ImageDraw.Draw(img)
    d.ellipse((190, 190, 410, 410), fill=(255, 255, 255))       # 흰 테두리 선(약 0.9mm)
    d.ellipse((200, 200, 400, 400), fill=(230, 170, 99))
    path = _save(img)
    try:
        assert has_white_or_black_border(path, Point(300, 300).buffer(100), 300.0)
    finally:
        os.remove(path)


def test_inward_cut_keeps_long_crescent_tips():
    from core.image_style import _inset_inside_art

    moon = Point(0, 0).buffer(120).difference(Point(55, -25).buffer(105))
    moon = translate(moon, 300, 300)
    inset = 14.2  # 1.2mm @300dpi
    cut = _inset_inside_art(moon, inset)
    plain = moon.buffer(-inset)
    assert cut.difference(moon).area < 1.0                       # 그림 밖으로 안 나감
    # 끝까지 뻗은 정도: 단순 안쪽 오프셋 대비 90% 이상의 길이를 유지
    def extent(g):
        x0, y0, x1, y1 = g.bounds
        return y1 - y0
    assert extent(cut) >= 0.9 * extent(plain)


def test_corner_element_with_same_color_on_opposite_edge_is_not_background():
    from core.multi_design import detect_elements_by_background_flood_px

    img = Image.new("RGB", (900, 500), (40, 150, 170))          # 하늘(배경)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 350, 900, 500), fill=(248, 240, 226))       # 바닥(배경 띠)
    d.pieslice((-170, -170, 170, 170), 0, 90, fill=(210, 100, 120))   # 왼쪽 위 모서리 나무(요소, 칸의 약 5%)
    d.rectangle((860, 0, 900, 40), fill=(210, 100, 120))        # 오른쪽 가장자리 같은 색 조각
    d.ellipse((400, 200, 520, 320), fill=(230, 170, 99))        # 가운데 캐릭터
    path = _save(img)
    try:
        objs = detect_elements_by_background_flood_px(path, (0, 0, 900, 500))
        tree = [o for o in objs if o.contains(Point(60, 60))]
        assert tree, "모서리 나무가 요소로 잡혀야 함"
        assert any(o.contains(Point(460, 260)) for o in objs)
    finally:
        os.remove(path)


def test_cut_png_matches_source_size_and_is_transparent_magenta_lines():
    from core.cutline_core import CutlineResult, OffsetSpec
    from core.png_export import CUT_RGB, export_cut_png

    mp = MultiPolygon([box(100, 100, 300, 250)])
    res = CutlineResult(dpi=300.0, width_px=640, height_px=480, design=mp, offsets={"cut": mp},
                        offset_mm=OffsetSpec(1, 2, 3))
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        export_cut_png(res, path, dpi=300.0)
        im = Image.open(path)
        assert im.size == (640, 480) and im.mode == "RGBA"
        assert abs(im.info["dpi"][0] - 300) < 0.5
        a = np.array(im)
        assert a[200, 200, 3] == 0                 # 칼선 안쪽 채우기 없음(투명)
        assert a[10, 10, 3] == 0                   # 바깥 투명
        assert tuple(a[100, 200, :3]) == CUT_RGB   # 선 위는 마젠타
    finally:
        os.remove(path)


def test_prefetch_reuse_key_ignores_far_neighbors_only():
    """① 직후 미리 계산한 요소 결과를 ③에서 다시 쓰는 기준: 가까운 이웃·경계·설정이 같으면 같은
    열쇠, 먼 이웃 목록만 달라지면(카드 칸을 뺀 경우) 같은 열쇠, 가까운 이웃이 달라지면 다른 열쇠."""
    from core.procpool import local_task_key

    sel = (1000, 1000, 1200, 1200)
    near = (1250, 1000, 1400, 1200)
    far1, far2 = (3000, 3000, 3100, 3100), (4000, 100, 4100, 200)
    t_all = ("rest", ("a.png", sel, sel, [near, far1, far2], 300.0, 1.2, 4))
    t_less_far = ("rest", ("a.png", sel, sel, [near, far1], 300.0, 1.2, 4))
    t_no_near = ("rest", ("a.png", sel, sel, [far1, far2], 300.0, 1.2, 4))
    assert local_task_key(t_all) == local_task_key(t_less_far)
    assert local_task_key(t_all) != local_task_key(t_no_near)
