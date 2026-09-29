"""
2026-09-29 칼선 위치 규칙(합성 이미지로 알고리즘 정확성만 확인 -- 품질 판단은
실제 테스트 파일 대조로 따로 함).

  - 흰색/검은색 테두리 선이 없는 요소는 자동 판정이 어떤 경로로 가든 무테
    (그림 안쪽) -- 칼선이 배경으로 나가지 않는다.
  - 한 덩어리 그림(펼친 책처럼 가운데 선이 있는 것)은 칼선 하나.
    얇은 줄기로만 닿은 두 요소는 따로.
  - GrabCut이 그림 속 일부를 배경으로 빼먹어 U자가 되어도 메워서 칼선 하나.
"""

import os
import sys
import tempfile

sys.path.insert(0, "/root/cutline_studio")
os.environ.setdefault("CUTLINE_LICENSE_DEV_BYPASS", "1")

from PIL import Image, ImageDraw
from shapely.affinity import scale, translate
from shapely.geometry import Point, box

from core.image_style import _fill_art_pockets_from_background_flood
from core.interactive_cutline import generate_cutline_auto
from core.multi_design import merge_boxes_of_one_art_piece_px


def _save(img):
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    img.save(path)
    return path


def test_blob_without_border_is_cut_inside_the_art():
    """사각형이 아닌 테두리 없는 요소(사각형도 < 기준) -> 무테(안쪽)."""
    img = Image.new("RGB", (600, 600), (235, 225, 200))
    d = ImageDraw.Draw(img)
    d.ellipse((150, 120, 450, 480), fill=(80, 160, 90))
    d.ellipse((330, 90, 470, 230), fill=(80, 160, 90))  # 귀 모양 돌기
    path = _save(img)
    try:
        res = generate_cutline_auto(path, 300.0, (110, 60, 500, 520), margin_mm=1.2, sibling_boxes_px=[])
        cut = res.offsets["cut"]
        body = translate(scale(Point(0, 0).buffer(1), 150, 180), 300, 300)
        ear = translate(scale(Point(0, 0).buffer(1), 70, 70), 400, 160)
        shape = body.union(ear).buffer(2)
        assert cut.difference(shape).area < 0.01 * cut.area
        assert any("무테" in n for n in res.adjustments)
    finally:
        os.remove(path)


def _stripes(w, h):
    img = Image.new("RGB", (w, h), (225, 235, 240))
    d = ImageDraw.Draw(img)
    for y in range(0, h, 120):
        d.rectangle((0, y, w, y + 59), fill=(240, 230, 190))
    return img, d


def test_open_book_halves_merge_but_stem_touching_parts_do_not():
    img, d = _stripes(800, 500)
    # 펼친 책: 왼쪽/오른쪽 쪽이 넓게 붙어 있음(가운데 책등 선)
    d.rectangle((100, 100, 250, 300), fill=(230, 210, 110))
    d.rectangle((250, 100, 400, 300), fill=(230, 210, 110))
    d.line((250, 100, 250, 300), fill=(90, 70, 40), width=3)
    # 꽃과 잎: 폭 4px 줄기로만 이어짐
    d.ellipse((480, 120, 620, 260), fill=(250, 250, 250))
    d.rectangle((620, 188, 660, 192), fill=(60, 140, 70))
    d.ellipse((660, 140, 760, 240), fill=(60, 140, 70))
    path = _save(img)
    try:
        boxes = [(95, 95, 252, 305), (248, 95, 405, 305), (475, 115, 625, 265), (655, 135, 765, 245)]
        out = merge_boxes_of_one_art_piece_px(path, (0, 0, 800, 500), boxes)
        assert len(out) == 3
        assert any(b[0] <= 100 and b[2] >= 400 for b in out)  # 책은 하나로
    finally:
        os.remove(path)


def test_pocket_missed_by_segmentation_is_filled():
    img = Image.new("RGB", (600, 400), (225, 235, 240))
    d = ImageDraw.Draw(img)
    d.rectangle((100, 100, 500, 330), fill=(180, 175, 170))   # 매트
    d.ellipse((200, 60, 400, 260), fill=(230, 170, 90))      # 매트 위 캐릭터(위로 삐져나옴)
    path = _save(img)
    try:
        mat = box(100, 100, 500, 330)
        u_shape = mat.difference(box(200, 60, 400, 260))  # 캐릭터가 빠진 U자
        filled = _fill_art_pockets_from_background_flood(path, u_shape, (100, 60, 500, 330))
        assert filled.area > u_shape.area + 0.7 * box(200, 100, 400, 260).area
        assert filled.difference(box(95, 55, 505, 335)).area < 1.0  # 배경은 안 더함
    finally:
        os.remove(path)


def test_small_item_with_light_border_band_is_cut_inside_the_band_not_the_art():
    """2026-09-29(실제 사용 피드백 "너무 좁은 영역"): 크림색 테두리 띠를 두른 작은
    소품은 칼선이 그림 몸통 안이 아니라 띠 안에 있어야 한다(배경으로는 안 나감)."""
    from core.image_style import ImageStyle, generate_style_cutline

    img = Image.new("RGB", (400, 400), (230, 190, 120))       # 황토색 배경
    d = ImageDraw.Draw(img)
    d.ellipse((140, 140, 260, 260), fill=(245, 228, 200))     # 크림색 테두리 띠(폭 약 2.5mm)
    d.ellipse((170, 170, 230, 230), fill=(60, 40, 30))        # 초콜릿 몸통(지름 약 5mm)
    path = _save(img)
    try:
        cut = generate_style_cutline(
            path, ImageStyle.BORDERLESS, 300.0, selection_px=(165, 165, 235, 235),
            sibling_boxes_px=[], art_region_px=(0, 0, 400, 400),
        )
        body = Point(200, 200).buffer(30)
        band = Point(200, 200).buffer(60)
        assert cut.contains(body.buffer(-1))            # 몸통을 자르지 않음
        assert cut.difference(band.buffer(1)).area < 1  # 배경으로 안 나감
    finally:
        os.remove(path)


def test_grabcut_does_not_fail_when_element_fills_whole_strip():
    """롤(띠) 파일에서 요소가 위아래 끝까지 닿아도 GrabCut 오류 없이 실루엣을 낸다."""
    from core.segmentation import segment_design_in_region

    img = Image.new("RGB", (300, 80), (240, 235, 225))
    d = ImageDraw.Draw(img)
    d.ellipse((100, 0, 200, 79), fill=(200, 60, 60))
    path = _save(img)
    try:
        geom = segment_design_in_region(path, (0, 0, 300, 80), margin_px=0)
        assert not geom.is_empty
    finally:
        os.remove(path)
