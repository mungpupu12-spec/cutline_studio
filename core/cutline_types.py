"""
Cutline TYPE classification -- the second half of what real 씰스티커 die-cut
work actually needs, alongside *where* to cut (core.segmentation) and *how
far out* (core.cutline_core.OffsetSpec).

Real production cutlines fall into two families:

  도무송 (Thomson-die) cutlines -- a small set of fixed geometric shapes
  (perfect circle / ellipse / square / rectangle) that a physical steel-rule
  die is pre-built to. This is the MOST commonly used cutline in practice
  (cheaper tooling, faster cutting) -- a design doesn't need a custom die
  shaped exactly like itself, it just needs a standard shape big enough to
  contain it with a safe margin.

  완칼 (full/complete cut) -- a custom die that follows one specific design's
  own silhouette exactly. More expensive/slower to produce, used when the
  product itself IS the cut-out shape (e.g. a character-shaped sticker).

Both are just a different choice of "design" polygon to hand to
core.cutline_core.compute_offsets -- a 도무송 shape is a plain geometric
primitive sized around the design's content, while 완칼 is the traced
silhouette itself. Everything downstream (min-gap enforcement, safety/cut/
bleed offsetting, SVG/layer export) is identical either way.
"""

from __future__ import annotations

import math
from enum import Enum

from shapely.affinity import scale as shapely_scale, translate as shapely_translate
from shapely.geometry import MultiPolygon, Point, Polygon


class CutlineType(Enum):
    CIRCLE = "정원형"       # 도무송: perfect circle
    ELLIPSE = "타원형"      # 도무송: oval
    SQUARE = "정사각형"     # 도무송: square
    RECTANGLE = "직사각형"  # 도무송: rectangle
    FULL_CUT = "완칼"       # follows the design's own outer silhouette

    @property
    def is_domusong(self) -> bool:
        return self is not CutlineType.FULL_CUT


def _content_bounds(content) -> tuple:
    """Accepts a shapely geometry OR a plain (x0,y0,x1,y1) tuple."""
    if isinstance(content, (tuple, list)):
        return tuple(content)
    return content.bounds


def fit_domusong_shape(content, cutline_type: CutlineType, circle_ellipse_pad: float = 0.0):
    """
    Build the BASE shape (zero extra margin beyond the content itself -- the
    artist's safety/cut/bleed mm offsets are applied afterwards by the
    existing compute_offsets pipeline, exactly like a traced silhouette
    would be) for one of the four 도무송 primitives, sized to just contain
    `content`'s bounding box.

    `content` is either a shapely geometry (its .bounds is used) or an
    explicit (x0, y0, x1, y1) tuple -- e.g. the artist's own drag-selection
    rectangle, if the shape should be sized from the selection rather than
    the auto-segmented content.

    `circle_ellipse_pad` (0..1) lets CIRCLE/ELLIPSE hug the content a little
    tighter or looser than "exactly touching the bbox edges" -- 0 means the
    ellipse/circle passes exactly through the midpoints of the bbox's four
    sides (the standard "oval/round sticker just containing this" look).
    """
    x0, y0, x1, y1 = _content_bounds(content)
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    w, h = (x1 - x0), (y1 - y0)

    if cutline_type == CutlineType.RECTANGLE:
        return Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])

    if cutline_type == CutlineType.SQUARE:
        side = max(w, h)
        hs = side / 2.0
        return Polygon(
            [(cx - hs, cy - hs), (cx + hs, cy - hs), (cx + hs, cy + hs), (cx - hs, cy + hs)]
        )

    if cutline_type == CutlineType.ELLIPSE:
        rx = (w / 2.0) * (1.0 + circle_ellipse_pad)
        ry = (h / 2.0) * (1.0 + circle_ellipse_pad)
        unit_circle = Point(0, 0).buffer(1.0, resolution=64)
        ellipse = shapely_scale(unit_circle, xfact=rx, yfact=ry, origin=(0, 0))
        return shapely_translate(ellipse, xoff=cx, yoff=cy)

    if cutline_type == CutlineType.CIRCLE:
        r = (max(w, h) / 2.0) * (1.0 + circle_ellipse_pad)
        return Point(cx, cy).buffer(r, resolution=64)

    raise ValueError(f"fit_domusong_shape does not handle {cutline_type} (use the traced design for FULL_CUT)")


def build_cutline_design(content, cutline_type: CutlineType, circle_ellipse_pad: float = 0.0):
    """
    Returns a MultiPolygon ready to hand to core.cutline_core.compute_offsets.

    - FULL_CUT: `content` must already be the traced silhouette (a shapely
      Polygon/MultiPolygon from load_raster_design / segment_design_in_region)
      -- returned as-is (wrapped in MultiPolygon if needed).
    - Any 도무송 type: `content` may be either the traced silhouette (its
      bbox is used to size the primitive) or a plain (x0,y0,x1,y1) selection
      rect -- either way, one shape is built to contain it.

      2026-09-09(29차) 피드백("도뭉송 칼선은 왜 안쪽으로 배치 되지 않고
      직접하려면 커다란 사각형이 돼")로 실제 파일 재현: `content`가 실루엣
      추적(GrabCut) 결과이고, 그 선택 영역 안에 서로 떨어진 캐릭터가 여러
      개 있으면(실제 재단선 격자 한 칸에 캐릭터 4~5개가 나란히 있는 경우
      등), 예전에는 그 여러 조각을 통째로 감싸는 하나의 bbox로 도형 하나를
      만들어버려서 -- 사실상 선택 영역 전체만큼 "커다란 사각형"이 나왔다
      (실측: 칸 면적의 88.6%). 완칼/유테는 이미 조각마다 따로 실루엣을
      그리는데 도무송만 그렇지 않았던 것 -- 이제 도무송도 똑같이, 서로
      떨어진 조각마다 자기 bbox에 딱 맞는 도형을 각각 만들어 그 전체를
      돌려준다(조각이 1개뿐이면 예전과 동일하게 동작 -- 회귀 없음).
    """
    if cutline_type == CutlineType.FULL_CUT:
        design = content
    elif isinstance(content, (tuple, list)):
        design = fit_domusong_shape(content, cutline_type, circle_ellipse_pad=circle_ellipse_pad)
    else:
        fragments = list(content.geoms) if hasattr(content, "geoms") else [content]
        fragments = [f for f in fragments if f is not None and not f.is_empty]
        shapes = [
            fit_domusong_shape(f, cutline_type, circle_ellipse_pad=circle_ellipse_pad)
            for f in fragments
        ]
        design = MultiPolygon(shapes) if len(shapes) != 1 else shapes[0]

    if isinstance(design, Polygon):
        design = MultiPolygon([design])
    return design
