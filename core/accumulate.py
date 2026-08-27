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

The mandatory minimum-gap rule (core.cutline_core.MIN_GAP_MM /
merge_close_elements) -- "칼선간 간격 2mm는 필수 규칙... 그 어떤 법칙보다
우선 순위" -- is re-applied across the COMBINED geometry of each tier, not
just within one item's own parts, since two different accumulated items
(e.g. a 스티커 element and a neighboring 도무송 shape) can end up close to
each other exactly the same way two parts of one design already could.
"""

from __future__ import annotations

from typing import Optional

from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

from .cutline_core import CutlineResult, MIN_GAP_MM, merge_close_elements, mm_to_px


def _as_multipolygon(geom):
    if geom is None:
        return MultiPolygon([])
    if isinstance(geom, Polygon):
        return MultiPolygon([geom]) if not geom.is_empty else MultiPolygon([])
    if geom.is_empty:
        return MultiPolygon([])
    return geom


def combine_results(
    items: list[CutlineResult],
    merge_gap_mm: Optional[float] = MIN_GAP_MM,
) -> CutlineResult:
    """
    Union every accumulated item's `design` (for the reference-artwork
    overlay) and each of its `offsets` tiers (for the actual cut lines),
    then re-enforce the mandatory minimum gap across the combined geometry
    of each tier. Returns one CutlineResult, ready for the same
    render_preview()/export_svg() calls a single-item result already used.

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

    gap_px = mm_to_px(merge_gap_mm, dpi) if merge_gap_mm else 0.0

    combined_offsets = {}
    for name in tier_names:
        geoms = [
            it.offsets[name]
            for it in items
            if name in it.offsets and it.offsets[name] is not None and not it.offsets[name].is_empty
        ]
        if not geoms:
            combined_offsets[name] = MultiPolygon([])
            continue
        merged = unary_union(geoms)
        merged = _as_multipolygon(merged)
        if gap_px > 0 and not merged.is_empty:
            merged = merge_close_elements(merged, gap_px)
        combined_offsets[name] = merged

    design_geoms = [it.design for it in items if it.design is not None and not it.design.is_empty]
    combined_design = _as_multipolygon(unary_union(design_geoms) if design_geoms else None)

    adjustments: list[str] = []
    for i, it in enumerate(items, start=1):
        for note in it.adjustments:
            adjustments.append(f"[{i}번째 영역] {note}")
    if len(items) > 1:
        adjustments.append(
            f"영역 {len(items)}개가 하나의 파일로 누적됐습니다 -- 서로 다른 영역의 칼선끼리도 "
            f"최소 {merge_gap_mm:g}mm 간격을 확보했습니다."
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
