"""
Regression test for 2026-09-10(43차) 피드백("모든 칼선 어떤 시점부터 매끄럽지
않아. 원인을 찾아서 해결 해야해"): 800% 확대 스크린샷으로 실제 확인된 문제 --
무테(BORDERLESS) 칼선이 캐릭터 안의 작은 색 경계 디테일(코 안쪽 무늬, 꽃 중심
무늬 등)까지 그대로 따라가며 뾰족뾰족하고 안 매끄러운 선이 되는 현상.

원인: 유테(LINE_ART, core.image_style.generate_style_cutline)는 실루엣을
margin만큼 밀어내기 전에 항상 core.cutline_core.smooth_design_naturally로
먼저 다듬는데(2026-09-09(14~16차)로 실측 검증됨), 무테의 색경계 추적
(_trace_content_silhouette_px) 결과에는 이 다듬기 단계가 아예 없었다.

수정: 무테도 추적된 모양을 halo 판정/안쪽 offset 전에 똑같이
smooth_design_naturally(margin의 8배)로 먼저 다듬도록 함.

작은 합성 도형(톱니 모양의 알려진 다각형 -- 실제 도안 아님)만 사용:
가장자리를 일부러 톱니처럼(작은 지그재그) 그린 뒤, "다듬기 없이 그대로 안쪽
offset"과 "실제 generate_style_cutline(수정 후)"의 결과를 꼭짓점 개수로
비교해서 다듬기가 실제로 효과가 있는지 확인하는 순수 알고리즘 테스트.
"""

import math
import os
import sys
import tempfile

sys.path.insert(0, "/root/cutline_studio")
os.environ.setdefault("CUTLINE_LICENSE_DEV_BYPASS", "1")

from PIL import Image, ImageDraw

from core.image_style import ImageStyle, generate_style_cutline, _trace_content_silhouette_px


def _make_sawtooth_blob(path):
    """cell(20,20,380,280) 안에 큰 타원 하나를, 가장자리를 일부러 작은
    톱니(지그재그)로 그려서 -- 실제 캐릭터 안의 코/꽃무늬처럼 작은 색
    경계 디테일이 윤곽선에 그대로 반영되는 상황을 known-geometry로
    재현한다."""
    img = Image.new("RGB", (400, 300), (255, 255, 255))
    d = ImageDraw.Draw(img)
    cx, cy, rx, ry = 200, 150, 150, 100
    n = 60
    pts = []
    for i in range(n):
        ang = 2 * math.pi * i / n
        # 반지름을 짝/홀 인덱스마다 작게 흔들어서(톱니) 매끈한 타원 위에
        # 작은 지그재그를 얹는다 -- 실제 코/꽃무늬 디테일과 같은 "작은
        # 스케일의 요철"을 재현.
        wobble = 6.0 if i % 2 == 0 else -6.0
        r_scale = 1.0 + wobble / 100.0
        x = cx + (rx * r_scale) * math.cos(ang)
        y = cy + (ry * r_scale) * math.sin(ang)
        pts.append((x, y))
    d.polygon(pts, fill=(60, 120, 200))
    img.save(path)
    return (20, 20, 380, 280)


def test_generate_style_cutline_smooths_the_jagged_detail():
    """같은 톱니 도형에 대해, "다듬기 없이 그대로 안쪽 offset"(수정 전과
    동일한 경로 -- _trace_content_silhouette_px 결과를 바로 buffer)과
    "generate_style_cutline(BORDERLESS)을 그대로 호출"(수정 후, 내부적으로
    smooth_design_naturally를 거침)의 꼭짓점 수를 직접 비교한다. 다듬기가
    실제로 효과가 있다면 후자가 훨씬 적어야 하고(톱니가 뭉툭해짐), 그러면서도
    전체 모양(면적)은 원래 타원과 비슷하게 유지돼야 한다(다듬기가 모양 자체를
    없애버리면 안 됨)."""
    tmp = os.path.join(tempfile.gettempdir(), "_test_sawtooth_sheet.png")
    cell = _make_sawtooth_blob(tmp)
    inset_px = 8.0
    try:
        content = _trace_content_silhouette_px(tmp, cell, min_bridge_px=10.0)
        assert content is not None and not content.is_empty, "톱니 도형 추적 자체가 실패함"
        unsmoothed_line = content.buffer(-inset_px, join_style=1)
        n_verts_unsmoothed = len(unsmoothed_line.exterior.coords)
        print(f"[수정 전과 동일 경로] 다듬기 없이 안쪽 offset한 결과 꼭짓점 수: {n_verts_unsmoothed}")
        assert n_verts_unsmoothed > 200, (
            f"기준선 자체가 톱니를 재현 못 함(꼭짓점 {n_verts_unsmoothed}개) -- 테스트 도형을 다시 봐야 함"
        )

        notes = []
        line = generate_style_cutline(
            tmp, ImageStyle.BORDERLESS, dpi=300.0, selection_px=cell,
            margin_mm=0.68,  # mm_to_px(0.68, 300) ≈ 8.0px, 위와 동일한 폭
            note_sink=notes,
        )
    finally:
        os.remove(tmp)

    assert not line.is_empty, "다듬기 적용 후 무테 칼선이 비어버림(회귀)"
    n_verts_smoothed = len(line.exterior.coords)
    print(f"[수정 후: generate_style_cutline] 다듬기 적용 결과 꼭짓점 수: {n_verts_smoothed}")
    assert n_verts_smoothed < n_verts_unsmoothed * 0.5, (
        f"다듬기 적용 후에도 꼭짓점이 별로 안 줄어듦({n_verts_unsmoothed} -> {n_verts_smoothed}) -- 톱니가 안 뭉툭해짐"
    )

    # 원래 타원 면적(rx*ry*pi ≈ 150*100*pi)과 비교해, 다듬기가 모양 자체를
    # 왜곡/축소시키지 않았는지 확인(대략적인 범위만 확인 -- inset_px만큼
    # 안쪽으로 줄었으니 원래 타원보다는 약간 작아야 정상).
    original_area = math.pi * 150 * 100
    ratio = line.area / original_area
    print(f"면적 비율(다듬기+안쪽offset 후 / 원래 타원): {ratio:.3f}")
    assert 0.5 < ratio < 1.0, f"다듬기 후 모양이 원래 타원과 너무 달라짐(면적비 {ratio:.3f})"
    print(
        f"[OK] 다듬기 적용 후 꼭짓점 수가 크게 줄고({n_verts_unsmoothed} -> {n_verts_smoothed}, "
        "매끄러워짐) 전체 모양은 유지됨"
    )


def main():
    test_generate_style_cutline_smooths_the_jagged_detail()
    print("\nAll BORDERLESS smoothing checks passed.")


if __name__ == "__main__":
    main()
