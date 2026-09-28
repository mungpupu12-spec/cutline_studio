"""
Replicate ONE already-generated, verified cutline across every repeated
instance of the same design on a sheet, instead of re-segmenting/re-tracing
each copy independently.

Real sheets very often print the exact same design tiled several times
(e.g. this project's reference file: one scene repeated 6-7 times across a
grid). Since every instance is pixel-identical (or at least placed on an
exact, known grid), the correct/robust approach is: generate one accurate
cutline for a single instance (however that was produced -- traced,
GrabCut-segmented, or a 도무송 primitive), then translate an exact copy of
that same geometry to every other instance's position. This is both far
cheaper than re-running segmentation per copy, and immune to segmentation
noise making equivalent copies slightly different from each other (which a
real print vendor would reasonably object to -- identical printed art should
get identical cutlines).

`replicate_by_offsets`/`grid_offsets_from_origins` only ever handled a pure
translation grid -- every file validated against before this round happened
to place its repeated tiles unrotated. A real reference file broke that:
it repeats its tile 7 times, but one of those instances is
physically rotated 90 degrees into a leftover corner of the sheet (and its
reused "bunny corner" design is placed rotated too). `replicate_by_placements`
below is the rotation-aware sibling: instead of a plain (dx, dy) translate,
it re-projects the SAME tile-local pixel-space cutline through each
instance's own real PDF placement matrix (`core.image_placement.
ImagePlacement`, built from `page.get_image_rects(xref, transform=True)`),
so a rotated instance's cutline comes out correctly rotated too -- not
translated sideways into the wrong orientation.
"""

from __future__ import annotations

from shapely.affinity import affine_transform as shapely_affine_transform
from shapely.affinity import translate as shapely_translate
from shapely.geometry import MultiPolygon, Polygon

from .cutline_core import CutlineResult
from .image_placement import ImagePlacement, map_polygon_pixel_to_point


def replicate_by_offsets(geom, offsets_px: list):
    """
    `offsets_px`: list of (dx, dy) pixel translations relative to the
    geometry's current position -- e.g. [(0,0), (tile_w,0), (0,tile_h), ...]
    for a design already generated at the first (0,0) tile and repeated on a
    regular grid. Include (0, 0) explicitly if you want the original
    position back in the result list.

    Returns a list of geometries, one per offset, each an independent copy
    (translating never mutates the input).
    """
    return [shapely_translate(geom, xoff=dx, yoff=dy) for dx, dy in offsets_px]


def grid_offsets_from_origins(tile_origins_px: list, base_index: int = 0):
    """
    Convenience: given the top-left (or any consistent reference corner) of
    each repeated tile in pixel space, return the (dx, dy) offsets needed to
    move a cutline generated at `tile_origins_px[base_index]` to every other
    tile in the list (including itself, as (0, 0)).
    """
    bx, by = tile_origins_px[base_index]
    return [(ox - bx, oy - by) for ox, oy in tile_origins_px]


def replicate_by_placements(geom_px, placements: list):
    """
    Rotation-aware sibling of `replicate_by_offsets`: `geom_px` is a
    cutline already generated in ONE tile instance's own raw pixel space
    (e.g. the 1772x945 repeat-tile raster), and `placements` is a list of
    `ImagePlacement` -- one per real instance of that same image on the
    page (from `page.get_image_rects(xref, transform=True)`, wrapped via
    `ImagePlacement.from_pymupdf`). Returns one geometry PER placement,
    each mapped into PAGE POINT SPACE (not pixel space, since different
    instances can be rotated relative to each other -- there is no single
    shared pixel space to translate within once rotation is involved).

    Every returned geometry traces back to the exact same verified
    tile-local cutline; only the placement differs, so all instances stay
    visually identical modulo their real on-page position/rotation --
    exactly like `replicate_by_offsets`, generalized to matrices instead of
    plain offsets.
    """
    return [map_polygon_pixel_to_point(geom_px, placement) for placement in placements]


def union_all(geoms) -> MultiPolygon:
    """Flatten a list of Polygon/MultiPolygon results (e.g. from
    replicate_by_offsets) into a single MultiPolygon, e.g. for rendering all
    replicated copies together in one preview/export pass."""
    polys = []
    for g in geoms:
        if isinstance(g, MultiPolygon):
            polys.extend(g.geoms)
        elif isinstance(g, Polygon):
            polys.append(g)
    return MultiPolygon(polys)


def translate_cutline_result(result: CutlineResult, dx: float, dy: float, note: str | None = None) -> CutlineResult:
    """
    2026-09-07(6차) 피드백("반복되는 스티커는 한개의 칼선을 먼저 완성하고
    나머지에 그대로 적용해")의 실제 복제 단계: 이미 완성된 CutlineResult
    하나(대표 인스턴스에서 실제로 세그멘테이션/칼선 생성을 마친 것) 전체를
    -- design 실루엣과 offsets(safety/cut/bleed 등 모든 단) 전부 -- 순수
    평행이동(dx, dy)만 해서 새 CutlineResult로 돌려준다. 새로 세그멘테이션을
    돌리지 않으므로 원본과 정확히 같은 모양이 보장되고(반복마다 GrabCut이
    미세하게 다르게 잡는 문제가 없음), 계산 자체도 사실상 공짜라 빠르다.

    `note`가 주어지면 이 결과가 복제된 것임을 adjustments 목록에 사람이
    읽을 수 있는 설명으로 남긴다(원본 result의 adjustments는 그대로
    보존하고 뒤에 이어붙임 -- 원본이 이미 "실루엣이 작아 무테로 대체됨" 같은
    노트를 갖고 있었다면 그 정보도 함께 유지됨)."""
    new_design = shapely_translate(result.design, xoff=dx, yoff=dy) if result.design is not None else None
    new_offsets = {name: shapely_translate(mp, xoff=dx, yoff=dy) for name, mp in result.offsets.items()}
    adjustments = list(result.adjustments or [])
    if note:
        adjustments = adjustments + [note]
    return CutlineResult(
        dpi=result.dpi,
        width_px=result.width_px,
        height_px=result.height_px,
        design=new_design,
        offsets=new_offsets,
        offset_mm=result.offset_mm,
        adjustments=adjustments,
    )


def fit_cutline_result_to_box(
    result: CutlineResult, from_box_px: tuple, to_box_px: tuple, note: str | None = None
) -> CutlineResult:
    """
    2026-09-10(48차) 피드백("칼선이 겹치거나 뭉쳐" -- 반복되는 곰 캐릭터
    시트에서 실측 재현): `translate_cutline_result`는 순수 평행이동만
    한다 -- 대표 인스턴스의 칸(`from_box_px`)과 지금 옮겨 붙일 칸
    (`to_box_px`)의 크기가 완전히 같을 때만 정확하다. 그런데
    `group_identical_boxes_px`는 실측으로 확인된 대로 같은 반복이라도 칸
    크기가 최대 8px까지 자연스럽게 들쭉날쭉한 것을 "같은 그림"으로 허용한다
    (2026-09-09/29차, max_size_diff_px=8.0) -- 이 경우 순수 평행이동만 하면
    대표 칼선이 원래 크기 그대로 옮겨져서, 대상 칸이 그보다 작으면 옆 칸을
    침범해 겹치고(칼선 겹침), 크면 안쪽에 뜬 채로 남는다(칼선이 칸 크기와
    안 맞아 뭉쳐 보이는 현상의 원인 중 하나로 확인됨).

    `from_box_px`의 네 모서리가 정확히 `to_box_px`의 네 모서리로 가도록
    x/y축을 각각 독립적으로 늘이거나 줄이는 아핀 변환을 적용한 뒤 옮긴다 --
    크기가 완전히 같으면(스케일 1.0) 기존 `translate_cutline_result`와
    결과가 동일하고, 몇 px 차이나는 실제 반복에서는 그 차이만큼 정확히
    맞춰 옮겨 붙인다.
    """
    fx0, fy0, fx1, fy1 = from_box_px
    tx0, ty0, tx1, ty1 = to_box_px
    fw, fh = (fx1 - fx0), (fy1 - fy0)
    sx = (tx1 - tx0) / fw if fw else 1.0
    sy = (ty1 - ty0) / fh if fh else 1.0
    # affine_transform matrix [a, b, d, e, xoff, yoff]:
    #   x' = a*x + b*y + xoff ;  y' = d*x + e*y + yoff
    # (x,y)=(fx0,fy0) 이 정확히 (tx0,ty0)로 가도록 xoff/yoff를 역산한다.
    matrix = [sx, 0.0, 0.0, sy, tx0 - fx0 * sx, ty0 - fy0 * sy]

    def _apply(geom):
        if geom is None:
            return None
        return shapely_affine_transform(geom, matrix)

    new_design = _apply(result.design)
    new_offsets = {name: _apply(mp) for name, mp in result.offsets.items()}
    adjustments = list(result.adjustments or [])
    if note:
        adjustments = adjustments + [note]
    return CutlineResult(
        dpi=result.dpi,
        width_px=result.width_px,
        height_px=result.height_px,
        design=new_design,
        offsets=new_offsets,
        offset_mm=result.offset_mm,
        adjustments=adjustments,
    )
