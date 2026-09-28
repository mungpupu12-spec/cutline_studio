"""
Combine several independently-generated CutlineResult objects -- each one
built from a single drag-selection plus one job-type/option choice -- into
ONE final CutlineResult, applied to a single output file.

This exists directly because of how the artist described the tool's basic
working principle (2026-08-26): "드래그하고 해당 옵션명을 선택해 각각의
칼선이 하나의 파일에 동시 적용 되는 원리" -- drag a region, pick an option
for it, and repeat; ALL of the choices apply to the same file at once. When
asked to confirm how 완칼/스티커/도무송 choices combine across several
drags, her answer was explicit: "여러 번 드래그 + 옵션 선택 → 전부 한 파일에
누적" (multiple drags + option choices -> all accumulate into one file).
Before this, the GUI's "generate" action replaced whatever the previous
drag had produced -- a one-shot workflow, not the accumulating one she
described.

Each accumulated item can have a DIFFERENT shape of `offsets` dict:
  - 완칼 (whole image, or FULL_CUT on one selection) and 도무송 (a
    CutlineType primitive on one selection) both go through
    core.cutline_core.compute_offsets and so carry all three tiers
    ("safety"/"cut"/"bleed").
  - 스티커 (유테/무테, core.image_style/generate_cutline_by_style) is a
    single-margin style with only one tier ("cut").
Combining unions each tier NAME across whichever items actually have it --
an all-"cut"-tier mix of 스티커 items combines into a "cut"-only result,
while mixing in one 완칼/도무송 item (with safety/cut/bleed) adds those
tiers too, populated only by the items that have them.

2026-09-14(실제 파일로 확인된 문제, "칼선이 방황한다"): 예전엔 이 자리에서
core.cutline_core.MIN_GAP_MM/merge_close_elements(2mm 미만이면 강제로
합치기)를 서로 다른 도안(캐릭터) 사이에도 그대로 적용했었다. 실제 시트
파일(인쇄 효율을 위해 캐릭터들을 서로 아주 가깝게, 종종 거의 맞닿게 배치)로
돌려보니, 서로 무관한 캐릭터 여러 개가 하나의 구불구불한 윤곽으로 뭉쳐져버려
"개체마다 반드시 1개의 칼선이 있어야 한다"는 훨씬 더 근본적인 정의를
어기는 결과가 나왔다 -- 심지어 오늘 새로 만든 코드를 전부 꺼도 이미
72개 중 54개가 서로 거리 0(맞닿음)으로 측정될 만큼 이 파일 자체가 원래
그렇게 촘촘하게 배치돼 있었다(실측, 회귀 아님 -- 원래부터 있던 문제).

멍푸님 확인(2026-09-14): "개체를 둘러싸는 칼선이어야해... 각각의 요소에
개별 칼선이 있어야해"가 이 2mm 자동 병합 규칙보다 우선한다 -- 서로 다른
누적 항목(도안)끼리는 아무리 가깝거나 겹쳐도 이제 다시는 하나로 합치지
않는다(각 항목의 원래 모양을 그대로, 조금도 변형 없이 보존). 2mm 최소
간격 자체가 필요 없어진 게 아니라, 그 강제 규칙을 "말없이 모양을 합쳐서
지키는" 대신 사람이 직접 판단할 문제로 남겨둔다는 뜻 -- 한 도안 자기
자신의 여러 조각을 하나로 잇는 용도(core.cutline_core.compute_offsets가
단일 항목 안에서 쓰는 merge_gap_mm)는 이 결정과 무관하게 그대로 유지된다.
"""

from __future__ import annotations

from typing import Optional

from shapely.geometry import MultiPolygon, Polygon

from .cutline_core import CutlineResult, MIN_GAP_MM


def _concat_multipolygon(geoms) -> MultiPolygon:
    """
    Concatenate each item's own polygon(s) into one MultiPolygon WITHOUT any
    unary_union/merge_close_elements across items -- every item's exact
    original shape is preserved untouched, even if two items' shapes are
    touching or overlapping in space (2026-09-14 결정, see module docstring).
    """
    polys: list[Polygon] = []
    for g in geoms:
        if g is None or g.is_empty:
            continue
        if isinstance(g, Polygon):
            polys.append(g)
        else:
            # MultiPolygon or GeometryCollection-like: take each part as-is.
            polys.extend(part for part in g.geoms if isinstance(part, Polygon) and not part.is_empty)
    return MultiPolygon(polys) if polys else MultiPolygon([])


def combine_results(
    items: list[CutlineResult],
    merge_gap_mm: Optional[float] = MIN_GAP_MM,
) -> CutlineResult:
    """
    Combine every accumulated item's `design` (for the reference-artwork
    overlay) and each of its `offsets` tiers (for the actual cut lines) into
    one CutlineResult, ready for the same render_preview()/export_svg()
    calls a single-item result already used.

    2026-09-14부터: 서로 다른 누적 항목(items)의 칼선 지오메트리는 더 이상
    unary_union되거나 merge_close_elements로 병합되지 않는다 -- 각 항목의
    도형을 그대로 보존한 채 MultiPolygon으로 이어붙이기만 한다(위 모듈
    docstring 참고). `merge_gap_mm`은 더 이상 이 함수 안에서 지오메트리에
    쓰이지 않고, 안내 문구(adjustments)에만 남아 있다 -- 하위 호환을 위해
    인자는 유지하되, 값이 있어도 병합 동작은 절대 일으키지 않는다.

    `items` must be non-empty and share the same `dpi` (true by
    construction in this project -- every item comes from the same loaded
    file/DPI setting within one accumulation session).
    """
    if not items:
        raise ValueError("누적된 항목이 없습니다 -- 영역을 하나 이상 추가한 뒤 다시 시도하세요.")

    dpi = items[0].dpi
    width_px = max((it.width_px for it in items), default=0)
    height_px = max((it.height_px for it in items), default=0)

    tier_names: list[str] = []
    for it in items:
        for name in it.offsets.keys():
            if name not in tier_names:
                tier_names.append(name)

    combined_offsets = {}
    for name in tier_names:
        geoms = [
            it.offsets[name]
            for it in items
            if name in it.offsets and it.offsets[name] is not None and not it.offsets[name].is_empty
        ]
        combined_offsets[name] = _concat_multipolygon(geoms)

    design_geoms = [it.design for it in items if it.design is not None and not it.design.is_empty]
    combined_design = _concat_multipolygon(design_geoms)

    adjustments: list[str] = []
    for i, it in enumerate(items, start=1):
        for note in it.adjustments:
            adjustments.append(f"[{i}번째 영역] {note}")
    if len(items) > 1:
        adjustments.append(
            f"영역 {len(items)}개가 하나의 파일로 누적됐습니다 -- 각 영역의 칼선은 서로 "
            f"가깝거나 겹치더라도 합쳐지지 않고 개별 도형으로 유지됩니다."
        )

    # The combined offset_mm is only ever used for display/reflection
    # purposes downstream -- the geometry above already reflects each
    # item's OWN margin faithfully. The most recently added item's spec is
    # the most relevant one to show back to the artist.
    representative_offset_mm = items[-1].offset_mm

    return CutlineResult(
        dpi=dpi,
        width_px=width_px,
        height_px=height_px,
        design=combined_design,
        offsets=combined_offsets,
        offset_mm=representative_offset_mm,
        adjustments=adjustments,
    )
