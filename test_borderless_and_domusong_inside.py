"""
2026-09-28 무테/도무송 "이미지 안쪽" 규칙과 칸 사이 얇은 배경 틈 문제에 대한
알고리즘 정확성 테스트. 실제 파일이 아닌 알려진 기하 도형/합성 이미지만
쓴다(품질 판단이 아니라 규칙을 수학적으로 지키는지만 확인).

규칙 근거: 테스트 폴더 실제 손 칼선 실측 -- 무테 시트는 요소마다 칼선이
있고 요소 실루엣 약 0.7~1.5mm 안쪽, 도무송 카드 칸은 이미지 외곽 약 3mm 안쪽.
"""

import math
import os
import sys
import tempfile

sys.path.insert(0, "/root/cutline_studio")
os.environ.setdefault("CUTLINE_LICENSE_DEV_BYPASS", "1")

from PIL import Image, ImageDraw
from shapely.geometry import LineString, Point, Polygon, box

from core.cutline_core import OffsetSpec, mm_to_px
from core.cutline_types import CutlineType
from core.image_style import _inset_inside_art
from core.interactive_cutline import generate_domusong_inside_cutline, image_outer_region_px
from core.multi_design import _is_uncuttable_sliver_box


def test_thin_gap_between_cells_is_not_an_element():
    """칸(945x888) 경계의 폭 7px x 높이 745px 틈은 요소가 아니다."""
    assert _is_uncuttable_sliver_box((3546, 100, 3553, 845), (3546, 0, 4491, 888))


def test_normal_and_ribbon_like_elements_are_kept():
    cell = (0, 0, 945, 888)
    assert not _is_uncuttable_sliver_box((100, 100, 160, 160), cell)
    assert not _is_uncuttable_sliver_box((100, 100, 130, 400), cell)
    assert not _is_uncuttable_sliver_box((100, 100, 110, 120), cell)


def _u_shape():
    outer = box(0, 0, 300, 300)
    gap = box(120, 120, 180, 300)  # 두 다리 사이 배경
    return outer.difference(gap), gap


def test_borderless_line_never_leaves_the_art_or_crosses_background():
    shape, gap = _u_shape()
    line = _inset_inside_art(shape, inset_px=14.0)
    assert not line.is_empty
    assert line.difference(shape).area < 1.0
    assert line.intersection(gap).area < 1.0


def test_borderless_line_is_about_margin_inside():
    shape, _ = _u_shape()
    line = _inset_inside_art(shape, inset_px=14.0)
    coords = list(line.exterior.coords)
    assert min(shape.boundary.distance(Point(c)) for c in coords) >= 14.0 * 0.9


def _radial_wobble(g):
    c = g.centroid
    rs = []
    for k in range(720):
        a = 2 * math.pi * k / 720
        ray = LineString([(c.x, c.y), (c.x + 400 * math.cos(a), c.y + 400 * math.sin(a))])
        inter = ray.intersection(g.exterior)
        pts = getattr(inter, "geoms", [inter])
        rs.append(max((math.hypot(p.x - c.x, p.y - c.y) for p in pts), default=0.0))
    n, w = len(rs), 24
    return max(abs(rs[i] - sum(rs[(i + j) % n] for j in range(-w, w + 1)) / (2 * w + 1)) for i in range(n))


def test_sawtooth_edges_are_shaved_not_bridged():
    pts = []
    for i in range(60):
        a = 2 * math.pi * i / 60
        r = 100 * (1.06 if i % 2 == 0 else 0.94)
        pts.append((200 + 1.5 * r * math.cos(a), 150 + r * math.sin(a)))
    shape = Polygon(pts)
    line = _inset_inside_art(shape, inset_px=8.0)
    assert line.difference(shape).area < 1.0
    assert _radial_wobble(line) < _radial_wobble(shape) * 0.5


def _sheet_with_card(card=(100, 50, 900, 750), w=1000, h=800):
    path = os.path.join(tempfile.gettempdir(), "_test_inside_card.png")
    img = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = card
    colors = [(70, 110, 160), (240, 230, 200), (220, 220, 225), (235, 215, 120)]
    step = (x1 - x0) // 8
    for i in range(8):
        d.rectangle([x0 + i * step, y0, x0 + (i + 1) * step if i < 7 else x1, y1], fill=colors[i % 4])
    img.save(path)
    return path


def test_image_outer_region_ignores_white_margin_and_stripes():
    path = _sheet_with_card()
    try:
        region = image_outer_region_px(path, (60, 20, 940, 780))
        blank = image_outer_region_px(path, (905, 50, 995, 750))
    finally:
        os.remove(path)
    assert blank is None
    assert region is not None and region.equals(box(100, 50, 900, 750)) or (
        region.symmetric_difference(box(100, 50, 900, 750)).area < 2000
    )


def test_domusong_lines_stay_inside_the_image_for_every_shape():
    card = (100, 50, 900, 750)
    path = _sheet_with_card(card)
    dpi = 300.0
    img_poly = box(*card)
    try:
        for ct in CutlineType:
            res = generate_domusong_inside_cutline(
                path, (60, 20, 940, 780), ct, dpi, OffsetSpec(1.0, 2.0, 3.0)
            )
            assert "safety" not in res.offsets
            cut, bleed = res.offsets["cut"], res.offsets["bleed"]
            assert not cut.is_empty and not bleed.is_empty, ct
            assert bleed.difference(img_poly).area < 1.0, ct
            # 칼선은 이미지 외곽에서 블리딩 폭(3mm) 안쪽
            assert cut.difference(img_poly.buffer(-mm_to_px(3.0, dpi) + 1.0)).area < 1.0, ct
            assert bleed.area >= cut.area, ct
    finally:
        os.remove(path)


def test_background_flood_finds_elements_on_striped_background():
    """가로 줄무늬 배경 위 요소 3개(하나는 칸 오른쪽 가장자리에 걸침)를 모두
    찾고, 줄무늬 색 경계는 요소로 잡지 않는다."""
    from core.multi_design import detect_elements_by_background_flood_px
    path = os.path.join(tempfile.gettempdir(), "_test_flood_cell.png")
    w, h = 900, 500
    img = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(img)
    stripes = [(235, 215, 120), (250, 240, 225), (225, 225, 228), (70, 110, 160)]
    for k in range(4):
        d.rectangle([0, k * 125, w, (k + 1) * 125], fill=stripes[k])
    d.ellipse([100, 100, 260, 300], fill=(230, 160, 90))       # 캐릭터
    d.ellipse([400, 180, 520, 330], fill=(90, 170, 90))        # 잎
    d.ellipse([760, 150, 960, 380], fill=(200, 80, 90))        # 오른쪽 가장자리에 걸친 요소
    img.save(path)
    try:
        els = detect_elements_by_background_flood_px(path, (0, 0, w, h))
    finally:
        os.remove(path)
    assert len(els) == 3, len(els)
    centers = sorted((round(e.centroid.x), round(e.centroid.y)) for e in els)
    assert centers[0][0] < 300 and 300 < centers[1][0] < 600 and centers[2][0] > 700, centers


def test_white_border_decides_line_art_vs_borderless():
    """유테 정의(흰색/검은색 테두리 선) 판정: 흰 테두리가 있는 요소만 True,
    테두리 없는 요소와 속까지 흰 요소(흰 꽃)는 False."""
    from shapely.geometry import Point as _Pt
    from core.interactive_cutline import has_white_or_black_border
    path = os.path.join(tempfile.gettempdir(), "_test_border.png")
    img = Image.new("RGB", (1200, 500), (70, 170, 170))
    d = ImageDraw.Draw(img)
    d.ellipse([60, 60, 440, 440], fill=(255, 255, 255))      # 흰 테두리(약 2mm)
    d.ellipse([85, 85, 415, 415], fill=(220, 120, 80))       # 안쪽 그림
    d.ellipse([500, 60, 880, 440], fill=(220, 120, 80))      # 테두리 없음
    d.ellipse([920, 60, 1180, 320], fill=(255, 255, 255))    # 속까지 흰 요소
    img.save(path)
    dpi = 300.0
    try:
        with_border_full = _Pt(250, 250).buffer(190)
        with_border_inner = _Pt(250, 250).buffer(165)          # 실루엣이 테두리 안쪽에서 잡힌 경우
        no_border = _Pt(690, 250).buffer(190)
        white_body = _Pt(1050, 190).buffer(130)
        assert has_white_or_black_border(path, with_border_full, dpi)
        assert has_white_or_black_border(path, with_border_inner, dpi)
        assert not has_white_or_black_border(path, no_border, dpi)
        assert not has_white_or_black_border(path, white_body, dpi)
    finally:
        os.remove(path)


def main():
    test_thin_gap_between_cells_is_not_an_element()
    test_normal_and_ribbon_like_elements_are_kept()
    test_borderless_line_never_leaves_the_art_or_crosses_background()
    test_borderless_line_is_about_margin_inside()
    test_sawtooth_edges_are_shaved_not_bridged()
    test_image_outer_region_ignores_white_margin_and_stripes()
    test_domusong_lines_stay_inside_the_image_for_every_shape()
    test_background_flood_finds_elements_on_striped_background()
    test_white_border_decides_line_art_vs_borderless()
    print("All borderless/domusong inside checks passed.")


if __name__ == "__main__":
    main()


def test_uncuttable_thread_like_part_is_dropped_but_main_part_kept():
    """옆 박스를 뺀 뒤 남는 실 같은 조각(폭 0.3mm)은 버리고, 본체와 폭이
    충분한 작은 조각은 남긴다."""
    from shapely.geometry import MultiPolygon, box as sbox
    from core.image_style import _drop_uncuttable_slivers

    dpi = 300.0
    main = sbox(0, 0, 200, 200)
    thread = sbox(300, 0, 340, 3.5)       # 폭 약 0.3mm
    small_ok = sbox(300, 100, 330, 130)   # 폭 약 2.5mm
    out = _drop_uncuttable_slivers(MultiPolygon([main, thread, small_ok]), dpi)
    parts = list(out.geoms) if hasattr(out, "geoms") else [out]
    assert main in parts or any(p.equals(main) for p in parts)
    assert any(p.equals(small_ok) for p in parts)
    assert not any(p.equals(thread) for p in parts)


def test_all_thin_parts_give_empty_when_keep_largest_false():
    from shapely.geometry import MultiPolygon, box as sbox
    from core.image_style import _drop_uncuttable_slivers

    specks = MultiPolygon([sbox(0, 0, 9, 6), sbox(100, 0, 109, 6)])
    assert _drop_uncuttable_slivers(specks, 300.0, keep_largest=False).is_empty
    assert not _drop_uncuttable_slivers(specks, 300.0).is_empty
