"""2026-09-30(2차) 칼선 보완 -- 합성 이미지로 알고리즘 정확성만 확인(칼선 품질 판단은 실제
테스트 파일 대조로 따로 함).

  - 가는 목으로 이어진 두 덩어리 칼선은 목에서 끊고, 한쪽에만 붙은 가는 끝은 그대로 둔다.
  - 재단선 격자 밖 띠에 그림이 있으면 칸으로 더하고, 빈 띠는 더하지 않는다.
  - 서로 맞닿게 그린 단색 그림(잎·꽃)은 그림별로 나누고, 한 그림 안의 부분은 나누지 않는다.
  - 배경 채우기 덩어리에서 영역 가장자리까지 이어진 바깥 조각만 떼어 낸다.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("CUTLINE_LICENSE_DEV_BYPASS", "1")

import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import Point, box
from shapely.ops import unary_union

MM = 300 / 25.4


def _save(img):
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    img.save(path)
    return path


def test_narrow_neck_is_cut_but_single_sided_tip_is_kept():
    from core.cut_split import split_narrow_necks

    a = Point(0, 0).buffer(15 * MM)
    b = Point(40 * MM, 0).buffer(12 * MM)
    neck = box(14 * MM, -0.2 * MM, 29 * MM, 0.2 * MM)  # 폭 0.4mm 목
    pieces = split_narrow_necks(unary_union([a, b, neck]), 300.0)
    assert len(pieces) == 2
    assert min(p.distance(q) for p in pieces for q in pieces if p is not q) > 0.5 * MM

    tip = box(14 * MM, -0.2 * MM, 22 * MM, 0.2 * MM)  # 한쪽에만 붙은 가는 끝
    assert len(split_narrow_necks(unary_union([a, tip]), 300.0)) == 1


def test_uncovered_strip_with_art_becomes_a_cell_but_blank_margin_does_not():
    from core.multi_design import add_uncovered_content_strips_px

    img = Image.new("RGB", (1600, 800), (230, 240, 245))
    d = ImageDraw.Draw(img)
    d.ellipse((300, 300, 500, 500), fill=(200, 80, 60))       # 격자 칸 안 그림
    d.rounded_rectangle((1330, 200, 1480, 420), 20, fill=(40, 120, 200))  # 격자 밖 오른쪽 띠 그림
    path = _save(img)
    try:
        cells = [(0, 0, 600, 800), (600, 0, 1200, 800)]
        out = add_uncovered_content_strips_px(path, cells)
        assert len(out) == 3 and out[-1][0] == 1200
        img2 = Image.new("RGB", (1600, 800), (230, 240, 245))
        ImageDraw.Draw(img2).ellipse((300, 300, 500, 500), fill=(200, 80, 60))
        path2 = _save(img2)
        try:
            assert add_uncovered_content_strips_px(path2, cells) == cells
        finally:
            os.remove(path2)
    finally:
        os.remove(path)


def test_touching_flat_leaf_and_flower_split_but_character_parts_do_not():
    from core.image_style import _split_flat_color_layers

    img = Image.new("RGB", (700, 500), (170, 210, 225))
    d = ImageDraw.Draw(img)
    d.ellipse((90, 150, 244, 300), fill=(100, 170, 110))      # 잎(초록)
    d.ellipse((240, 120, 460, 340), fill=(255, 255, 255))     # 꽃(흰색) -- 잎 끝에 닿음
    arr = np.array(img)[:, :, ::-1].copy()
    art = unary_union([Point(167, 225).buffer(76), Point(350, 230).buffer(108)])
    layers = _split_flat_color_layers(arr, art, 300.0)
    assert layers is not None and len(layers) == 2

    img = Image.new("RGB", (700, 500), (170, 210, 225))
    d = ImageDraw.Draw(img)
    d.ellipse((200, 60, 420, 260), fill=(230, 165, 95))       # 머리
    d.rectangle((230, 230, 390, 420), fill=(255, 255, 255))   # 몸(길게 맞닿음)
    arr = np.array(img)[:, :, ::-1].copy()
    art = unary_union([Point(310, 160).buffer(100), box(230, 230, 390, 420)])
    assert _split_flat_color_layers(arr, art, 300.0) is None


def test_edge_touching_background_scrap_is_trimmed_from_flood_blob():
    from core.image_style import _trim_edge_extras

    region = (0, 0, 400, 300)
    region_edge = box(*region).exterior.buffer(3.0)
    design = box(100, 100, 300, 250)
    body_pocket = box(150, 60, 250, 100)       # 실루엣 밖이지만 가장자리에 안 닿는 그림
    stripe = box(0, 150, 100, 200)             # 가장자리까지 이어진 배경 줄무늬 조각
    blob = unary_union([design, body_pocket, stripe])
    out = _trim_edge_extras(blob, design, region_edge)
    assert out.contains(Point(200, 80))
    assert not out.contains(Point(40, 175))
