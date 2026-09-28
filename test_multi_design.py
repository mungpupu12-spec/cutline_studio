"""
Logic/mechanics smoke test for core.multi_design.detect_design_bboxes_px --
NOT a cutline-quality test. Uses small script-generated images with KNOWN,
analytic blob positions/count (a controlled-geometry check, same spirit as
test_fidelity.py's known-radius circle) purely to verify the detection
ALGORITHM is correct -- never used to judge cutline quality on real artwork.
Also runs the same function on the committed samples/ring.png fixture (a
single real blob) as a sanity check.

2026-09-07: the detection algorithm was rewritten (color-distance-from-
corner -> Canny-edge + hole-fill) after a real user report that a real
multi-design print sheet was being detected as a single blob. Two new
cases below reproduce the two suspected root causes with controlled,
synthetic geometry (not her real art) so the fix can be verified without
ever touching her actual files:
  - _sheet_solid_bg_with_light_holes(): a solid-colored "canvas" background
    (like her real sheet's teal fill) with light/white circular "sticker"
    insets sitting on top and tightly packed (narrow gaps) -- under the OLD
    corner-color-distance algorithm this whole canvas was one connected
    foreground blob (a plane with holes punched in it is still one
    connected region), which is exactly the reported bug.
  - _sheet_with_border_marks(): plain white background with small dark
    "crop mark" squares actually AT the four image corners plus 3 real
    designs -- under the OLD algorithm this could throw off the corner-
    sampled background-color estimate; the new edge-based algorithm
    ignores corner color entirely so this should not matter.
"""

import sys
sys.path.insert(0, "/root/cutline_studio")

from PIL import Image, ImageDraw

from core.multi_design import detect_design_bboxes_px, find_design_bbox_at_point_px

W, H = 900, 600
BG = (230, 230, 235, 255)
CANVAS_TEAL = (30, 150, 150, 255)


def _sheet_with_known_blobs(path):
    """3 well-separated filled circles + 1 tiny speck (should be filtered
    out as noise) on a uniform background -- ground truth: exactly 3 boxes,
    roughly centered where each circle was drawn."""
    im = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(im)
    circles = [(150, 150, 90), (450, 150, 90), (300, 420, 90)]
    for cx, cy, r in circles:
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(200, 80, 40, 255))
    # a 3x3px speck of noise -- must NOT produce a 4th box
    d.rectangle([10, 10, 12, 12], fill=(0, 0, 0, 255))
    im.save(path)
    return circles


def _one_connected_blob(path):
    """Two circles joined by a THICK bar (bar height close to the circles'
    own diameter, so the shape has no real "neck/waist") -- one genuinely
    fused shape, so this must detect exactly ONE box (not two).

    2026-09-07 업데이트: 예전엔 이 테스트가 "가늘게 이어붙인 막대"였고
    "무조건 하나로 유지"가 기대값이었는데, 실제 사용자 파일로 확인된
    버그(맞닿아 배치된 서로 다른 스티커 여러 개가 하나로
    합쳐져 인식됨)를 고치면서 거리 변환+워터쉐드 분리(_split_touching_
    blobs)를 추가했고, 그 결과 "가느다란 목으로 이어진 두 덩어리"는 이제
    의도적으로 둘로 나뉜다(아래 _two_designs_touching_thin_neck 테스트가
    바로 그 경우). 그래서 이 테스트는 "그래도 절대 나뉘면 안 되는" 진짜
    하나로 붙은 모양(목이 원래 없을 만큼 굵게 이어짐)으로 바꿔서, 워터쉐드
    분리가 진짜 하나인 도안까지 과하게 쪼개지는 건 아닌지 계속 지켜본다."""
    im = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(im)
    d.ellipse([100, 200, 280, 380], fill=(40, 120, 200, 255))
    d.ellipse([620, 200, 800, 380], fill=(40, 120, 200, 255))
    d.rectangle([190, 230, 710, 350], fill=(40, 120, 200, 255))
    im.save(path)


def _two_designs_touching_thin_neck(path):
    """두 원이 가느다란 막대(목)로만 이어진 모양 -- 실제로는 서로 거의
    맞닿게 배치된 별개의 스티커 두 개를 흉내낸 것(진짜 배경 틈이 아주
    좁거나 없는 경우). 2026-09-07 실제 사용자 파일에서 바로 이런 모양으로
    서로 다른 스티커들이 하나의 블롭으로 잘못 합쳐지는
    게 확인돼서, 거리 변환+워터쉐드 분리를 추가했다 -- 이제 이런 "가는
    목"은 정확히 그 지점에서 둘로 나뉘어야 한다."""
    im = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(im)
    d.ellipse([100, 200, 280, 380], fill=(40, 120, 200, 255))
    d.ellipse([620, 200, 800, 380], fill=(40, 120, 200, 255))
    d.rectangle([190, 270, 710, 310], fill=(40, 120, 200, 255))
    im.save(path)


def _sheet_solid_bg_with_light_holes(path):
    """2026-09-07 실제 사용자 버그 재현: 시트 전체가 하나의 진한 색(청록)으로
    칠해진 '캔버스' 위에, 밝은 색 스티커 도안 6개가 촘촘한 간격(좁은 틈)으로
    얹혀 있는 구성. 배경색(청록)과 도안(밝은 원)의 경계에는 뚜렷한 엣지가
    있으므로, 새 알고리즘은 이 경계를 따라 6개의 개별 블롭을 찾아야 한다."""
    im = Image.new("RGB", (W, H), CANVAS_TEAL)
    d = ImageDraw.Draw(im)
    centers = []
    r = 60
    gap = 18  # 원래 버전(close_ratio=0.012 -> 약 7px 커널)도 위험할 만큼 좁은 틈
    for row in range(2):
        for col in range(3):
            cx = 120 + col * (2 * r + gap)
            cy = 160 + row * (2 * r + gap)
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(245, 245, 240))
            # 도안 내부에 약간의 디테일(선)을 넣어 "무테(내부 디테일 없음)"와
            # "유테(테두리 있음)" 두 경우 모두를 대략 흉내
            d.line([cx - 20, cy, cx + 20, cy], fill=(80, 80, 80), width=3)
            centers.append((cx, cy))
    im.convert("RGBA").save(path)
    return centers, r


def _sheet_with_border_marks(path):
    """2026-09-07 실제 사용자 버그의 또 다른 의심 원인 재현: 흰 배경에 네
    모서리마다 작은 크롭마크(검은 사각형)가 있고, 진짜 도안 3개가 중앙 쪽에
    떨어져 있는 구성. 모서리 색만으로 배경을 추정하던 옛 방식은 이 크롭마크에
    속을 수 있음 -- 새 알고리즘(엣지 기반)은 모서리 색을 아예 안 보므로
    영향이 없어야 한다."""
    im = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(im)
    mark = 6
    for (mx, my) in [(0, 0), (W - mark, 0), (0, H - mark), (W - mark, H - mark)]:
        d.rectangle([mx, my, mx + mark, my + mark], fill=(0, 0, 0))
    circles = [(200, 200, 70), (700, 200, 70), (450, 420, 70)]
    for cx, cy, r in circles:
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(210, 90, 60))
    im.convert("RGBA").save(path)
    return circles


def main():
    tmp1 = "/tmp/_multi_design_known.png"
    tmp2 = "/tmp/_multi_design_joined.png"
    tmp3 = "/tmp/_multi_design_solid_bg_holes.png"
    tmp4 = "/tmp/_multi_design_border_marks.png"
    circles = _sheet_with_known_blobs(tmp1)
    _one_connected_blob(tmp2)
    hole_centers, hole_r = _sheet_solid_bg_with_light_holes(tmp3)
    marks_circles = _sheet_with_border_marks(tmp4)

    boxes = detect_design_bboxes_px(tmp1)
    assert len(boxes) == 3, f"expected 3 blobs (+1 filtered speck), got {len(boxes)}: {boxes}"
    print(f"[OK] 3 well-separated blobs detected (speck correctly filtered out): {boxes}")

    # each detected box should actually contain the matching circle's center
    for (cx, cy, r) in circles:
        hit = any(x0 <= cx <= x1 and y0 <= cy <= y1 for (x0, y0, x1, y1) in boxes)
        assert hit, f"no detected box contains known circle center ({cx},{cy})"
    print("[OK] every known circle center falls inside some detected box")

    # reading order: top row (y~150) before bottom row (y~420); left before right within a row
    ys = [((y0 + y1) / 2) for (_, y0, _, y1) in boxes]
    assert ys[0] < ys[2] and ys[1] < ys[2], f"expected the two top circles before the bottom one: {boxes}"
    xs_top_row = [b[0] for b in boxes[:2]]
    assert xs_top_row[0] < xs_top_row[1], f"expected left-to-right order within the top row: {boxes}"
    print("[OK] boxes come back in top-to-bottom, left-to-right reading order")

    boxes_joined = detect_design_bboxes_px(tmp2)
    assert len(boxes_joined) == 1, f"two circles joined by a THICK bar should be ONE connected blob, got {len(boxes_joined)}: {boxes_joined}"
    print(f"[OK] two shapes joined by a thick bridge (no real neck) stay a single blob: {boxes_joined}")

    # ---- 2026-09-07: 실제 사용자 파일로 확인된 버그 --
    # 서로 거의 맞닿게 배치된 스티커들이 하나로 합쳐져 인식되던 것을
    # 거리 변환+워터쉐드 분리(_split_touching_blobs)로 고침. 가는 목으로만
    # 이어진 두 덩어리는 이제 정확히 그 지점에서 둘로 나뉘어야 한다.
    tmp5 = "/tmp/_multi_design_thin_neck.png"
    _two_designs_touching_thin_neck(tmp5)
    boxes_thin_neck = detect_design_bboxes_px(tmp5)
    assert len(boxes_thin_neck) == 2, (
        f"two designs touching through only a thin neck should now split into "
        f"2 separate boxes (real-file-driven fix), got {len(boxes_thin_neck)}: {boxes_thin_neck}"
    )
    print(f"[OK] two designs touching through a thin neck are correctly split apart: {boxes_thin_neck}")

    # ---- 2026-09-07 regression: solid-color canvas background with tightly
    # packed light-colored insets (this is the shape of the actual reported
    # bug) -- must detect all 6, NOT collapse into 1.
    boxes_holes = detect_design_bboxes_px(tmp3)
    assert len(boxes_holes) == 6, (
        f"solid-bg sheet with 6 tightly-packed light insets: expected 6 separate "
        f"blobs, got {len(boxes_holes)}: {boxes_holes} -- this is the exact shape "
        f"of the reported real-world bug (whole sheet collapsing into 1 blob)"
    )
    for (cx, cy) in hole_centers:
        hit = any(x0 <= cx <= x1 and y0 <= cy <= y1 for (x0, y0, x1, y1) in boxes_holes)
        assert hit, f"no detected box contains known inset center ({cx},{cy})"
    print(f"[OK] solid-color-canvas sheet with 6 tightly-packed insets detects all 6 separately: {boxes_holes}")

    # ---- 2026-09-07 regression: corner crop-marks must not confuse detection
    boxes_marks = detect_design_bboxes_px(tmp4)
    assert len(boxes_marks) == 3, (
        f"expected 3 real designs (corner crop-marks filtered as noise), got "
        f"{len(boxes_marks)}: {boxes_marks}"
    )
    for (cx, cy, r) in marks_circles:
        hit = any(x0 <= cx <= x1 and y0 <= cy <= y1 for (x0, y0, x1, y1) in boxes_marks)
        assert hit, f"no detected box contains known circle center ({cx},{cy})"
    print(f"[OK] corner crop-marks don't confuse detection, 3 real designs found: {boxes_marks}")

    # sanity check against a real committed fixture (single design, no siblings)
    # -- 2026-09-07: 워터쉐드 분리를 추가하면서 "진짜 하나인 도안까지 과하게
    # 쪼개지 않는지"가 새로운 걱정거리가 됐으므로, ">=1"이 아니라 정확히
    # 1개인지(안 쪼개졌는지)까지 확인하도록 강화.
    boxes_ring = detect_design_bboxes_px("/root/cutline_studio/samples/ring.png")
    assert len(boxes_ring) == 1, (
        f"samples/ring.png is a single real design -- watershed splitting must "
        f"not shred it into pieces, got {len(boxes_ring)}: {boxes_ring}"
    )
    print(f"[OK] samples/ring.png (real fixture) stays exactly 1 blob (not over-split): {boxes_ring}")

    # ---- 2026-09-07: 워터쉐드 분리가 "오목한 모양(concave)이지만 진짜
    # 하나인 도안"까지 과하게 쪼개지 않는지 순수 기하학으로 확인(별 모양은
    # test_fidelity.py의 "알려진 반지름 원" 검사와 같은 성격의, 실제 도안
    # 품질 판단이 아닌 알고리즘 정확성 검사용 합성 이미지).
    import math

    star_path = "/tmp/_multi_design_star.png"
    im_star = Image.new("RGBA", (500, 500), BG)
    d_star = ImageDraw.Draw(im_star)
    cx, cy, r_out, r_in = 250, 250, 200, 80
    pts = []
    for i in range(10):
        ang = math.pi / 2 + i * math.pi / 5
        r = r_out if i % 2 == 0 else r_in
        pts.append((cx + r * math.cos(ang), cy - r * math.sin(ang)))
    d_star.polygon(pts, fill=(200, 80, 40, 255))
    im_star.save(star_path)
    boxes_star = detect_design_bboxes_px(star_path)
    assert len(boxes_star) == 1, (
        f"a single concave (5-pointed star) shape must stay ONE blob, not be "
        f"shredded at its points by watershed splitting, got {len(boxes_star)}: {boxes_star}"
    )
    print(f"[OK] a single concave star-shaped design stays exactly 1 blob (not over-split): {boxes_star}")

    # empty/blank image -> no blobs, not an error
    blank_path = "/tmp/_multi_design_blank.png"
    Image.new("RGBA", (200, 200), BG).save(blank_path)
    assert detect_design_bboxes_px(blank_path) == [], "a blank image should detect zero blobs"
    print("[OK] a blank/background-only image correctly detects zero blobs")

    # ---- find_design_bbox_at_point_px: click-to-select helper
    cx0, cy0 = hole_centers[0]
    hit_box = find_design_bbox_at_point_px(tmp3, (cx0, cy0))
    assert hit_box is not None, "clicking dead-center of a known design should find its box"
    x0, y0, x1, y1 = hit_box
    assert x0 <= cx0 <= x1 and y0 <= cy0 <= y1
    print(f"[OK] find_design_bbox_at_point_px finds the design under a direct hit: {hit_box}")

    near_miss = (cx0 + hole_r + 3, cy0)  # just outside the circle, still close
    near_box = find_design_bbox_at_point_px(tmp3, near_miss)
    assert near_box is not None, "clicking just outside a design (near-miss) should still snap to it"
    print(f"[OK] find_design_bbox_at_point_px snaps to the nearest design on a near-miss: {near_box}")

    far_click = (5, 5)  # far corner of the solid-bg sheet, nowhere near any design
    far_box = find_design_bbox_at_point_px(tmp3, far_click)
    assert far_box is None, f"clicking far from every design should give up (None), got {far_box}"
    print("[OK] find_design_bbox_at_point_px gives up (None) when the click is far from every design")

    print("\nAll multi_design smoke checks passed.")


if __name__ == "__main__":
    main()
