"""
Regression test for the 2026-09-08(11차) 재설계 of 무테(BORDERLESS) cutline
generation (core.image_style._trace_content_silhouette_px + its wiring into
generate_style_cutline).

멍푸님 피드백: "무테 칼선은 요소의 외곽 색을 기준으로 라인을 생성하고
생성한 라인을 축소 및 재배치해 칼선을 생성해." -- 예전엔 무테가 선택/셀
사각형 자체를 그냥 안쪽으로 줄이기만 했는데(색/선 경계를 전혀 안 봄), 이제는
먼저 실제 선/색 경계를 추적한 뒤 그 모양을 안쪽으로 줄인다. 여러 요소가
따로 있으면(예: 캐릭터 여러 마리) 전부 하나로 묶어("그룹 스티커") 처리하고,
추적할 경계가 아예 없으면(빈 칸/연속 무늬) 안전하게 예전처럼 사각형 자체를
줄이는 방식으로 되돌아간다.

2026-09-09(12차) 추가: 추적된 모양이 있어도, 그 모양이 셀/선택 영역 사방
중 한 면이라도 가장자리에 닿아 있으면(halo 없음 -- 배경까지 채우는 연속
무늬라는 원래 무테 정의 그 자체) 그 추적 결과를 버리고 사각형 축소로
되돌아간다. 실제 화면 스크린샷으로 확인된 "장식이 셀 가장자리까지 흩어져
있으면 지그재그로 지저분한 칼선이 나온다" 문제의 회귀 테스트.

작은 합성 도형(알려진 기하 구조)만 사용 -- 실제 파일로 "칼선 품질"을
판단하지 않는다는 원칙과 같은 이유로, test_fidelity.py와 같은 성격의 순수
알고리즘 동작 확인 테스트.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageDraw
from shapely.geometry import Polygon

from core.image_style import ImageStyle, generate_style_cutline, _trace_content_silhouette_px


def check(label, cond):
    status = "OK" if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        raise SystemExit(1)


def _make_two_separate_blobs(path):
    """흰 배경 위에 서로 멀리 떨어진 색 도형 2개(원, 사각형) -- "칸 안에
    캐릭터 여러 마리가 따로 있다"는 실제 상황을 known-geometry로 재현."""
    img = Image.new("RGB", (400, 200), "white")
    d = ImageDraw.Draw(img)
    d.ellipse((20, 60, 120, 160), fill=(200, 60, 60))       # blob A, 100x100
    d.rectangle((280, 70, 360, 150), fill=(60, 120, 200))   # blob B, 80x80
    img.save(path)
    return {"rect": (0, 0, 400, 200), "blob_a": (20, 60, 120, 160), "blob_b": (280, 70, 360, 150)}


def _make_blank_region(path):
    """완전히 단색인 이미지 -- Canny가 아무 경계도 못 찾는 경우(연속 무늬로
    셀 전체가 꽉 찬 무테의 원래 시나리오)를 재현."""
    img = Image.new("RGB", (200, 200), (230, 230, 230))
    img.save(path)
    return (0, 0, 200, 200)


def test_trace_groups_multiple_elements_and_shrinks_inward():
    tmp = "/tmp/_borderless_two_blobs.png"
    info = _make_two_separate_blobs(tmp)

    content = _trace_content_silhouette_px(tmp, info["rect"])
    check("서로 떨어진 두 요소가 있으면 추적 결과가 있음(None 아님)", content is not None)
    check(
        "추적 결과가 셀 전체 사각형이 아니라 실제 두 도형 쪽에 가깝게 좁혀짐 "
        f"(bounds={content.bounds})",
        content.bounds[0] > 5 and content.bounds[2] < 395,
    )

    line = generate_style_cutline(
        tmp, ImageStyle.BORDERLESS, dpi=150.0, selection_px=info["rect"], margin_mm=1.2,
    )
    check("무테 결과가 빈 도형이 아님", not line.is_empty)
    # 멍푸님 표현("하나의 그룹 스티커 칼선이 되는거야", 단수)대로, 서로
    # 떨어진 요소가 여러 개 있어도 최종 칼선은 항상 하나로 이어진 도형
    # 하나여야 한다 -- 실제 파일로 검증하다가 발견된 문제(그냥 unary_union만
    # 하면 카드형 디자인 하나가 흩어진 조각 여러 개의 칼선으로 보임)를 막기
    # 위해 _bridge_into_one_shape로 항상 하나의 Polygon으로 합친다.
    check(
        f"서로 멀리 떨어진 두 요소도 최종적으로는 하나로 이어진 도형(Polygon) 하나가 됨 "
        f"(실제 타입: {type(line).__name__})",
        isinstance(line, Polygon),
    )


def test_blank_region_falls_back_to_rect_shrink_safely():
    tmp = "/tmp/_borderless_blank.png"
    rect = _make_blank_region(tmp)

    content = _trace_content_silhouette_px(tmp, rect)
    check("완전히 단색인 영역은 추적 결과가 없음(None)", content is None)

    notes = []
    line = generate_style_cutline(
        tmp, ImageStyle.BORDERLESS, dpi=150.0, selection_px=rect, margin_mm=1.2, note_sink=notes,
    )
    check("추적 실패 시에도 무테 결과가 빈 도형이 아님(사각형 축소로 안전하게 대체)", not line.is_empty)
    check(
        "추적 실패(폴백) 시에는 '추적 경로' 노트를 남기지 않음",
        not any("실제 선/색 경계를 추적" in n for n in notes),
    )
    # 원래 사각형(0,0,200,200)을 margin_mm=1.2mm(150dpi 기준 약 7.09px)만큼
    # 안쪽으로 줄인 것과 거의 같아야 한다(기존 폴백 동작 그대로 유지).
    lx0, ly0, lx1, ly1 = line.bounds
    check(
        f"폴백 결과가 예전처럼 사각형을 안쪽으로 줄인 모양과 일치함 (bounds={line.bounds})",
        6.5 < lx0 < 7.5 and 6.5 < ly0 < 7.5 and 192.5 < lx1 < 193.5 and 192.5 < ly1 < 193.5,
    )


def _make_edge_to_edge_scattered_pattern(path):
    """캐릭터/장식이 셀 가장자리까지 흩어져 채워진 "연속 무늬" 무테를
    known-geometry로 재현 -- 2026-09-09(12차) 실제 화면 스크린샷으로 직접
    확인된 문제(캐릭터 머리/하트/꽃잎 장식이 셀 위/아래/옆 가장자리에 거의
    닿아 있어서, 추적 결과가 그 장식들 사이를 지그재그로 넘나드는 지저분한
    모양이 됨)와 같은 구조: 작은 도형 여러 개를 셀(400x200) 가장자리에
    바로 붙여서 뿌려놓는다(중앙에는 진짜 빈 배경)."""
    img = Image.new("RGB", (400, 200), "white")
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, 40, 40), fill=(200, 60, 60))          # 좌상단 모서리에 닿음
    d.ellipse((360, 0, 400, 40), fill=(60, 120, 200))      # 우상단 모서리에 닿음
    d.rectangle((150, 160, 200, 200), fill=(120, 180, 60))  # 아래쪽 가장자리에 닿음
    img.save(path)
    return (0, 0, 400, 200)


def test_edge_to_edge_scattered_pattern_falls_back_to_rect_shrink():
    """2026-09-09(12차) -- 실제 화면 스크린샷(캐릭터+흩날리는 꽃잎/하트가
    셀 가장자리까지 채운 무늬)에서 직접 확인된 "지그재그로 지저분한 칼선"
    문제의 회귀 테스트. 이런 경우는 추적된 모양이 셀 사방 중 한 면이라도
    가장자리에 닿아 있으므로("배경까지 채우는 연속 무늬" -- 원래 무테
    정의), _bridge_into_one_shape로 하나로 묶는 데는 성공하더라도 그
    추적 결과를 쓰지 않고 예전처럼 사각형 자체를 안쪽으로 줄여야 한다."""
    tmp = "/tmp/_borderless_edge_to_edge.png"
    rect = _make_edge_to_edge_scattered_pattern(tmp)

    content = _trace_content_silhouette_px(tmp, rect)
    check("가장자리에 닿은 장식들도 하나로 묶인 추적 결과가 나옴(None 아님)", content is not None)
    cx0, cy0, cx1, cy1 = content.bounds
    check(
        f"추적된 모양이 실제로 셀 가장자리에 닿아 있음(halo 없음) (bounds={content.bounds})",
        cx0 <= 1.0 or cy0 <= 1.0 or cx1 >= 399.0 or cy1 >= 199.0,
    )

    notes = []
    line = generate_style_cutline(
        tmp, ImageStyle.BORDERLESS, dpi=150.0, selection_px=rect, margin_mm=1.2, note_sink=notes,
    )
    check("연속 무늬 폴백 시에도 무테 결과가 빈 도형이 아님", not line.is_empty)
    check(
        "halo 없는 추적 결과는 버려지고 '연속 무늬' 폴백 노트가 남음",
        any("배경까지 채우는 연속 무늬" in n for n in notes),
    )
    check(
        "'실제 선/색 경계를 추적' 노트는 남지 않음(추적 결과를 쓰지 않았으므로)",
        not any("실제 선/색 경계를 추적한 모양을 기준으로" in n for n in notes),
    )
    lx0, ly0, lx1, ly1 = line.bounds
    check(
        f"결과가 사각형(0,0,400,200)을 margin_mm=1.2mm만큼 안쪽으로 줄인 것과 일치 (bounds={line.bounds})",
        6.5 < lx0 < 7.5 and 6.5 < ly0 < 7.5 and 392.5 < lx1 < 393.5 and 192.5 < ly1 < 193.5,
    )


def test_fallback_safety_warning_still_fires_when_tracing_fails():
    """content-tracing 자체가 (예외 등으로) 실패해서 사각형 폴백으로 넘어간
    경우에는, 2026-08-26에 추가됐던 기존 안전장치(선택 여유가 실제 그림보다
    부족하면 경고)가 여전히 정상 동작해야 한다 -- 이번 재설계가 그 기존
    보호장치까지 없애버리지 않았는지 확인."""
    import core.image_style as image_style_mod

    tmp = "/tmp/_borderless_force_fallback.png"
    # 실제 내용물(사각형)이 이미지 대부분을 채우는 그림을 만들어서, 아주
    # 좁은 여유만 남기고 선택하면 사각형 축소가 그림을 잘라먹게 한다 --
    # 2026-08-26 케이스와 동일한 조건.
    img = Image.new("RGB", (200, 200), "white")
    ImageDraw.Draw(img).rectangle((10, 10, 190, 190), fill=(50, 50, 50))
    img.save(tmp)
    rect = (5, 5, 195, 195)  # 실제 그림 가장자리까지 겨우 5px 여유

    original = image_style_mod._trace_content_silhouette_px
    image_style_mod._trace_content_silhouette_px = lambda *a, **k: None
    try:
        notes = []
        image_style_mod.generate_style_cutline(
            tmp, image_style_mod.ImageStyle.BORDERLESS, dpi=150.0, selection_px=rect,
            margin_mm=1.2, note_sink=notes,
        )
        check(
            "추적이 실패해 사각형 폴백으로 넘어간 경우, 여유 부족 시 기존 '무테 칼선 경고'가 그대로 남음",
            any("무테 칼선 경고" in n for n in notes),
        )
    finally:
        image_style_mod._trace_content_silhouette_px = original


def main():
    test_trace_groups_multiple_elements_and_shrinks_inward()
    test_blank_region_falls_back_to_rect_shrink_safely()
    test_edge_to_edge_scattered_pattern_falls_back_to_rect_shrink()
    test_fallback_safety_warning_still_fires_when_tracing_fails()
    print("\nAll borderless-silhouette checks passed.")


if __name__ == "__main__":
    main()
