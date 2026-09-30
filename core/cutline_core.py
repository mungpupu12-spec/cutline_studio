"""
CutLine Studio - core algorithm
--------------------------------
Given a design image (raster PNG with alpha, or a flattened design where the
background is a known color) OR a vector SVG, this module extracts the
design's silhouette and generates three concentric offset outlines used for
sticker/decal die-cutting:

    safety range  (green)  - closest to the artwork
    cutting line  (red)    - the actual die/laser cut path
    bleeding line (blue)   - furthest out, print bleed allowance

All three offsets are expressed in millimeters, outward from the design's
silhouette edge, and are converted to pixels using the artboard's DPI.

The heavy lifting is done with:
  - OpenCV: raster contour extraction (with hierarchy, so holes are kept)
  - Shapely: polygon simplification + buffering (offsetting) + union of
    disjoint shapes (so nearby objects merge into one contour if their
    offsets would overlap, exactly like a real print vendor would do)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import cv2

from . import image_cache as _image_cache
import numpy as np
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union
from shapely.validation import make_valid


MM_PER_INCH = 25.4

# Minimum guaranteed gap (mm) between the artwork edge and ANY offset line,
# including the innermost one. Real print/cut equipment can register (밀림)
# by a small amount between printing and cutting; if a line sits closer to
# the artwork than this, that shift can clip straight into the design. This
# is enforced regardless of what the artist types in, so a too-tight value
# never silently produces an unsafe file.
#
# Raised from 1.0mm to 2.0mm on 2026-08-25 after review of a real finished
# production file: the artist found 1mm still too tight in practice for her
# actual print/cut process.
MIN_GAP_MM = 2.0

# 2026-09-10(40차) 피드백("도무송 칼선은 다른 칼선보다 중심 기준을 두고
# 최소 15mm는 외부에서 안 쪽으로 들어와야해. 저렇게 얇게 칼선이 들어가면
# 무조건 파손돼"): 도무송(CIRCLE/ELLIPSE/SQUARE/RECTANGLE -- 실제 금형이
# 찍어내는 단순한 기하 도형)은 완칼(실루엣을 그대로 따라가는 칼선)과 달리
# 물리적인 금형 자체가 버텨야 하므로, 일반 MIN_GAP_MM(2.0mm)보다 훨씬 큰
# 최소 여유가 필요하다는 실무 기준 -- 도무송 전용 최소 간격을 별도로 둠.
# `core.interactive_cutline.generate_cutline_for_selection`이 cutline_type이
# 도무송인지 아닌지에 따라 이 값과 MIN_GAP_MM 중 하나를 골라
# `OffsetSpec.enforce_minimum_gap`에 넘긴다(완칼은 기존 2.0mm 그대로,
# 회귀 없음).
#
# 53차(실제 인쇄소 도무송 가이드 파일 실측: 다이 사이 최소 여유 2.0mm)에서
# 이 값을 2.0mm로 낮추기로 했고 다른 파일 주석 4곳에도 그렇게 기록돼 있었지만
# 정작 이 상수 변경만 저장에서 빠져 15.0이 그대로 남아 있었다 -- 2026-09-28
# 멍푸 확인("2.0mm, 인쇄소 가이드")으로 실제 값을 바로잡음. 지금은
# MIN_GAP_MM과 같은 값이지만, 도무송 전용 기준을 따로 조정할 수 있게 상수는
# 분리해둔다.
MIN_DOMUSONG_GAP_MM = 2.0


# The subpixel pipeline (load_raster_design/segment_design_in_region) works
# by upsampling the binary mask `supersample`x before re-extracting contours
# -- accurate, but the upsampled canvas is (width*supersample) x
# (height*supersample), so cost grows with the SQUARE of supersample and
# linearly with the image's own resolution. Measured on a real file
# (README, 2026-08-25): 3500x3500 at supersample=4 (upsampled to 14000x14000)
# took ~2s -- fine for a one-off GUI action. The real production files this
# project has actually processed so far top out around 4500-4600px on their
# long side (e.g. 4495x3074, 4491x3084 -- both already validated at the full
# 4x default), so the cap below is set well above that range -- routine real
# files keep full precision unchanged; only a master file considerably
# bigger than anything handled so far ("이미지가 훨씬 커지면") gets stepped
# down. `auto_supersample` caps the upsampled canvas's LONG SIDE at
# `max_upsampled_dim`, stepping the multiplier down (never below 1) until it
# fits -- so asking for 4x precision on a huge image doesn't unknowingly
# balloon into a multi-second (or worse) wait; it quietly asks for less
# upsampling instead, and the caller can report that adjustment (see
# `note_sink` on load_raster_design/segment_design_in_region) so it is never
# a silent quality change either.
DEFAULT_MAX_UPSAMPLED_DIM = 20000.0


def auto_supersample(
    width_px: int, height_px: int, requested: int = 4, max_upsampled_dim: float = DEFAULT_MAX_UPSAMPLED_DIM
) -> int:
    """
    Returns the supersample factor to actually use: `requested` (the user's
    chosen precision / GUI "정밀도" setting), stepped down only as far as
    needed to keep `max(width_px, height_px) * factor` under
    `max_upsampled_dim`. Never returns less than 1 (no upsampling at all is
    always a valid fallback for an already-huge image) and never more than
    `requested` (this only ever makes precision cheaper, never more
    expensive than what was asked for).
    """
    requested = max(1, int(requested))
    if requested <= 1:
        return 1
    long_side = max(int(width_px), int(height_px))
    if long_side <= 0:
        return requested
    factor = requested
    while factor > 1 and long_side * factor > max_upsampled_dim:
        factor -= 1
    return factor


def mm_to_px(mm: float, dpi: float) -> float:
    return mm / MM_PER_INCH * dpi


def px_to_mm(px: float, dpi: float) -> float:
    return px / dpi * MM_PER_INCH


@dataclass
class OffsetSpec:
    """Millimeter offsets, measured outward from the artwork edge."""

    safety_mm: float = 1.0
    cut_mm: float = 2.0
    bleed_mm: float = 3.0

    def sorted_items(self):
        # Always processed from smallest (closest to art) to largest (furthest out)
        return sorted(
            [("safety", self.safety_mm), ("cut", self.cut_mm), ("bleed", self.bleed_mm)],
            key=lambda kv: kv[1],
        )

    def enforce_minimum_gap(self, min_gap_mm: float = MIN_GAP_MM) -> tuple["OffsetSpec", list[str]]:
        """
        If the innermost offset (whichever of safety/cut/bleed is smallest)
        sits closer to the artwork than `min_gap_mm`, push ALL THREE outward
        by the same amount so the closest line ends up exactly at
        `min_gap_mm` -- preserving whatever spacing the artist set between
        safety/cut/bleed rather than collapsing them onto each other.
        Returns the (possibly adjusted) spec plus a human-readable note list
        (empty if nothing needed adjusting).
        """
        innermost = min(self.safety_mm, self.cut_mm, self.bleed_mm)
        if innermost >= min_gap_mm:
            return self, []

        deficit = min_gap_mm - innermost
        note = (
            f"가장 안쪽 선이 도안에서 {innermost:g}mm밖에 안 떨어져 있어서, "
            f"인쇄/커팅 시 밀려도 도안이 잘리지 않도록 세이프티/칼선/블리딩을 "
            f"모두 {deficit:g}mm씩 바깥으로 밀어 최소 {min_gap_mm:g}mm를 확보했습니다."
        )
        return (
            OffsetSpec(
                safety_mm=self.safety_mm + deficit,
                cut_mm=self.cut_mm + deficit,
                bleed_mm=self.bleed_mm + deficit,
            ),
            [note],
        )


@dataclass
class CutlineResult:
    dpi: float
    width_px: int
    height_px: int
    design: MultiPolygon
    offsets: dict  # name -> MultiPolygon (buffered outward)
    offset_mm: OffsetSpec
    adjustments: list = field(default_factory=list)  # notes if any offset got auto-clamped


# --------------------------------------------------------------------------
# Raster (PNG/JPG) -> design silhouette
# --------------------------------------------------------------------------

def _contours_to_polygons(
    mask: np.ndarray, simplify_tol_px: float, min_hole_area_ratio: float = 0.02
) -> list[Polygon]:
    """Extract contours (with holes) from a binary mask and return Shapely polygons.

    `min_hole_area_ratio`: a hole (interior ring) smaller than this fraction of
    its OWN exterior contour's area is dropped (filled in) instead of kept as
    a real hole. Added after a real production file's small/pattern design
    elements (a plaid fabric swatch, a squiggle-shaped snack icon) came back
    from GrabCut with a couple of stray pixel-scale gaps -- not a real
    "donut" hole in the artwork, just segmentation noise where a few
    background-colored pixels slipped through -- and buffering that noise
    outward drew a small, visually confusing SECOND ring inside the real
    cutline ("이중 칼선"). Measured on that real case: the two spurious
    holes were 857px^2 and 1302px^2 against a ~148000px^2 exterior (0.6%/0.9%)
    -- comfortably under this 2% default, while this project's already-
    validated real donut/ring-hole case (see README's "구멍이 있는 도안" ✅)
    is a deliberate, much larger fraction of its own exterior and stays
    unaffected by this filter.
    """
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None:
        return []
    hierarchy = hierarchy[0]  # shape (N, 4): next, prev, first_child, parent

    polygons = []
    # top-level contours (parent == -1) are exteriors; their children are holes
    for i, h in enumerate(hierarchy):
        parent = h[3]
        if parent != -1:
            continue  # this is a hole, handled below via its parent
        exterior_cnt = contours[i]
        if len(exterior_cnt) < 3:
            continue
        exterior = exterior_cnt.squeeze(1).astype(float)
        ext_area = cv2.contourArea(exterior_cnt)

        # collect holes (direct children of this contour), dropping ones too
        # small relative to their own exterior to be a real design hole
        holes = []
        child = h[2]
        while child != -1:
            hole_cnt = contours[child]
            if len(hole_cnt) >= 3:
                hole_area = cv2.contourArea(hole_cnt)
                if ext_area <= 0 or hole_area >= min_hole_area_ratio * ext_area:
                    holes.append(hole_cnt.squeeze(1).astype(float))
            child = hierarchy[child][0]

        try:
            poly = Polygon(exterior, holes)
            if simplify_tol_px > 0:
                poly = poly.simplify(simplify_tol_px, preserve_topology=True)
            if not poly.is_valid:
                poly = make_valid(poly)
            if poly.area > 0:
                polygons.append(poly)
        except Exception:
            continue
    return polygons


def load_raster_design(
    image_path: str,
    alpha_threshold: int = 127,
    simplify_tol_px: float = 0.6,
    min_area_px: float = 25.0,
    supersample: int = 4,
    note_sink: Optional[list] = None,
) -> tuple[MultiPolygon, int, int]:
    """
    Load a PNG (or any image PIL/OpenCV can decode). If the image has an
    alpha channel, the foreground is wherever alpha > alpha_threshold.
    If there is no alpha channel, we fall back to treating near-white pixels
    as background (common when the artist flattened onto a white artboard).

    Sub-pixel edge fidelity ("칼선이 이미지 실루엣에 최대한 가깝게"):
    A naive binary threshold on the native-resolution pixel grid, followed by
    OpenCV contour extraction, only ever produces a blocky staircase outline
    -- any diagonal or curved edge in the artwork gets approximated by a
    jagged pixel-grid stairstep, and simplifying that (Douglas-Peucker) just
    trades jagged-but-close for smooth-but-cutting-corners. Neither actually
    hugs the artwork's real (anti-aliased) edge.

    Instead we:
      1. Threshold + denoise at native resolution (as before) to get a clean
         binary silhouette, immune to stray-pixel noise.
      2. Upsample that binary mask `supersample`x with cubic interpolation --
         this turns every hard pixel-grid step into a smooth 0..255 ramp
         exactly where the true (diagonal/curved) edge crosses each native
         pixel.
      3. Re-threshold at the midpoint (127) and extract contours at this
         higher resolution -- the crossing point of that ramp is a genuine
         sub-pixel-accurate estimate of where the edge actually falls, so the
         resulting polygon follows curves and diagonals far more closely.
      4. Scale the polygon back down into the original image's pixel space.

    `supersample`: the requested precision/upsample factor (the GUI's
    "정밀도" option). For a large source image this is automatically capped
    down (see `auto_supersample`) so precision never silently balloons
    processing time -- pass a list via `note_sink` to receive a note when
    that capping actually happens (e.g. `note_sink=[]`, then read it back
    after the call), so the artist can see the request was pared down rather
    than have it be an invisible slowdown or an invisible quality change.
    """
    img = _image_cache.imread(image_path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise FileNotFoundError(f"Could not read image: {image_path}")

    h, w = img.shape[:2]
    requested_supersample = supersample
    supersample = auto_supersample(w, h, requested=requested_supersample)
    if supersample != requested_supersample and note_sink is not None:
        note_sink.append(
            f"이미지 해상도({w}x{h})가 커서 정밀도를 {requested_supersample}x -> "
            f"{supersample}x로 자동 조정했습니다 (처리 속도 보호)"
        )

    if img.ndim == 3 and img.shape[2] == 4:
        alpha = img[:, :, 3]
        native_mask = (alpha > alpha_threshold).astype(np.uint8) * 255
    else:
        # No alpha channel -> assume a near-white/flattened background.
        # Otsu picks the split point that best separates the (bimodal:
        # background vs ink) histogram automatically, instead of a fixed
        # cutoff that either eats anti-aliased edge pixels (shrinking the
        # design) or lets background noise through.
        if img.ndim == 2:
            gray = img
        else:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, native_mask = cv2.threshold(
            gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
        )

    # light denoise so single stray pixels don't become tiny contours
    kernel = np.ones((3, 3), np.uint8)
    native_mask = cv2.morphologyEx(native_mask, cv2.MORPH_OPEN, kernel)
    native_mask = cv2.morphologyEx(native_mask, cv2.MORPH_CLOSE, kernel)

    supersample = max(1, int(supersample))
    if supersample > 1:
        # INTER_LINEAR (not cubic!) matters here: cubic interpolation of a
        # hard binary (0/255) step can overshoot/undershoot (ringing) near
        # the edge, which re-introduces high-frequency wobble right where we
        # need a clean monotonic ramp to find the true sub-pixel crossing.
        # Linear interpolation of a step is monotonic and its 50% crossing
        # is a well-behaved sub-pixel edge estimate.
        up_w, up_h = w * supersample, h * supersample
        upsampled = cv2.resize(native_mask, (up_w, up_h), interpolation=cv2.INTER_LINEAR)
        # A small Gaussian blur before re-thresholding further smooths the
        # crossing across neighbouring samples instead of snapping to a
        # single upsampled pixel's linear ramp, softening any remaining
        # staircase corners into a true curve.
        blur_k = max(3, (supersample // 2) * 2 + 1)
        upsampled = cv2.GaussianBlur(upsampled, (blur_k, blur_k), 0)
        _, mask = cv2.threshold(upsampled, 127, 255, cv2.THRESH_BINARY)
        simplify_tol_super = simplify_tol_px * supersample
    else:
        mask = native_mask
        simplify_tol_super = simplify_tol_px

    polygons = _contours_to_polygons(mask, simplify_tol_super)
    if supersample > 1:
        from shapely.affinity import scale as shapely_scale

        polygons = [
            shapely_scale(p, xfact=1 / supersample, yfact=1 / supersample, origin=(0, 0))
            for p in polygons
        ]
    polygons = [p for p in polygons if p.area >= min_area_px]

    if not polygons:
        raise ValueError(
            "No design silhouette found. Check that the image has transparency "
            "(or a clean white background) around the artwork."
        )

    design = unary_union(polygons)
    if isinstance(design, Polygon):
        design = MultiPolygon([design])

    return design, w, h


# --------------------------------------------------------------------------
# Vector (SVG) -> design silhouette
# --------------------------------------------------------------------------
#
# Rather than re-implementing SVG curve flattening and fill-rule (nonzero /
# evenodd) semantics by hand -- which is exactly the kind of thing that goes
# subtly wrong with holes, self-intersecting paths, and multi-subpath fonts
# -- we rasterize the SVG with a real renderer (cairosvg) at a resolution
# matched to the requested working DPI, then reuse the exact same
# battle-tested raster contour pipeline used for PNG/JPG input. This keeps a
# single source of truth for "what counts as the design silhouette".

SVG_REFERENCE_DPI = 96.0  # standard CSS px definition (1px = 1/96 inch)


def load_vector_design(
    svg_path: str,
    dpi: float = 300.0,
    simplify_tol_px: float = 0.6,
    supersample: int = 4,
    note_sink: Optional[list] = None,
) -> tuple[MultiPolygon, int, int]:
    """
    Rasterize an SVG at `dpi` (assuming the SVG's own user units follow the
    standard 96dpi CSS definition) and extract the design silhouette using
    the same contour+hole logic as load_raster_design (`supersample`/
    `note_sink` pass straight through to it -- see `auto_supersample`).
    """
    import tempfile
    import os
    import cairosvg

    scale = dpi / SVG_REFERENCE_DPI

    fd, tmp_png = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        cairosvg.svg2png(url=svg_path, write_to=tmp_png, scale=scale)
        design, w, h = load_raster_design(
            tmp_png, alpha_threshold=127, simplify_tol_px=simplify_tol_px,
            supersample=supersample, note_sink=note_sink,
        )
    finally:
        try:
            os.remove(tmp_png)
        except OSError:
            pass

    return design, w, h


# --------------------------------------------------------------------------
# Offsetting
# --------------------------------------------------------------------------

def compute_offsets(
    design: MultiPolygon,
    dpi: float,
    offset_mm: OffsetSpec,
    join_style: int = 1,  # 1 = round
    buffer_resolution: int = 16,
    merge_gap_mm: Optional[float] = None,
    clip_bounds=None,
) -> dict:
    """
    Buffer the design outward by each offset distance (mm -> px).
    Returns dict: name -> MultiPolygon (the buffered outward shape; the
    outer boundary of this shape IS the cut/safety/bleed line).

    `merge_gap_mm`: if two (or more) separate design elements end up with
    lines closer than this to EACH OTHER (not just to their own artwork),
    they are merged into one shared line instead of staying as separate,
    almost-touching lines -- a real cutter can't reliably cut two die lines
    that close together, and a real vendor would merge them anyway. This is
    on by default (using the same MIN_GAP_MM as the artwork clearance,
    unless overridden) via the standard "buffer out, then back in" closing
    trick, which merges anything within `merge_gap_mm` without changing the
    outer size of shapes that were already far apart. Pass 0/None-with-0 (or
    a negative number) to disable and keep every element's line fully
    independent regardless of how close they get.
    """
    if merge_gap_mm is None:
        merge_gap_mm = MIN_GAP_MM

    bounds_poly = None
    if clip_bounds is not None:
        if isinstance(clip_bounds, (tuple, list)):
            bx0, by0, bx1, by1 = clip_bounds
            bounds_poly = Polygon([(bx0, by0), (bx1, by0), (bx1, by1), (bx0, by1)])
        else:
            bounds_poly = clip_bounds

    results = {}
    for name, mm in offset_mm.sorted_items():
        px = mm_to_px(mm, dpi)
        buffered = design.buffer(px, join_style=join_style, resolution=buffer_resolution)
        if merge_gap_mm and merge_gap_mm > 0:
            close_px = mm_to_px(merge_gap_mm, dpi) / 2.0
            # 2026-09-10 피드백("도무송 파란색 선이 모서리가 뾰족해야 해"):
            # 위 바깥쪽 buffer는 이미 join_style을 그대로 받아 뾰족한
            # 모서리(mitre, join_style=2)를 유지할 수 있었지만, 그 다음 이
            # "간격 좁으면 합치기" closing 단계(buffer(+)/buffer(-))는
            # join_style을 안 넘겨줘서 shapely 기본값(round)으로 다시
            # 둥글게 깎아버리고 있었다 -- 실제 파일로 확인된 회귀: 도무송
            # 직사각형의 90도 모서리가 이 단계 때문에 살짝 둥글게 나옴.
            # 같은 join_style을 그대로 넘겨서, 완칼(둥글게가 맞는 경우)은
            # 그대로 둥글고 도무송(뾰족해야 하는 경우)은 끝까지 뾰족하게.
            buffered = buffered.buffer(close_px, join_style=join_style, resolution=buffer_resolution).buffer(
                -close_px, join_style=join_style, resolution=buffer_resolution
            )
        if bounds_poly is not None:
            # A real die can never cut outside the actual printed canvas --
            # clamp every offset line to the artwork/image bounds instead of
            # letting a generous margin (or a 도무송 primitive bigger than
            # its content) run off the edge.
            buffered = buffered.intersection(bounds_poly)
        if isinstance(buffered, Polygon):
            buffered = MultiPolygon([buffered])
        elif buffered.is_empty:
            buffered = MultiPolygon([])
        results[name] = buffered
    return results


def merge_close_elements(
    geom: MultiPolygon, gap_px: float, resolution: int = 16
) -> MultiPolygon:
    """
    The same "buffer out by half the gap, then back in by the same amount"
    trick `compute_offsets` already uses to merge nearby PARTS of one
    design's own offset line -- applied here across a union of MANY
    independent elements' own separately-generated cutlines instead.

    Added after a real production round-trip: 12 small/large elements on one
    tile were each generated in total isolation, then only unioned together
    at the very end (after replicating each tile element to all of its
    real sheet placements). Because nothing ever checked element-to-element
    distance, two small props ended up with their own rectangle cutlines
    overlapping their neighboring characters' cutlines outright -- caught
    directly by the artist ("작은 요소 모두 네모 칼선으로 기존의 큰 요소의
    칼선과 겹치잖아"), who stated a minimum 2mm gap between ANY two
    different elements' lines as a mandatory, top-priority rule ("칼선간
    간격 2mm는 필수 규칙이야... 그 어떤 법칙보다 우선 순위에 둬야해") -- a
    real cutter can't reliably cut two die lines that close together
    either way, so two lines within `gap_px` of each other should always
    become one shared line, exactly like compute_offsets already does for
    sub-parts of a single design.

    Call this ONCE on the full, final union of every element's cutline
    (after replication/placement into page space) rather than per-element
    beforehand -- one pass then correctly catches BOTH within-tile
    neighbors and any elements that end up close across a tile-repeat
    boundary, with no risk of missing a pair that only becomes close once
    everything is placed in its real, final position.

    2026-09-10(47차) 피드백("칼선이 왜 뭉개지고 뚫리고 합쳐지는지"): 시트
    전체를 한 덩어리로 놓고 buffer(+)/buffer(-)를 한 번에 돌리면, 실제로는
    서로 `gap_px` 안에 있지도 않은(즉 합칠 필요가 전혀 없는) 다른 도안들의
    윤곽까지 같은 연산을 그대로 통과하며 미묘하게 깎이거나(뭉개짐), 여러
    조각이 한 화면에서 뒤섞여 union될 때 GEOS가 생성하는 자잘한 구멍(뚫림)이
    같이 생길 위험이 있었다 -- 실제로 조각이 촘촘히 배치된 시트(이번에 문제
    보고된 시트들)일수록 이 전역 연산의 영향 범위가 넓어져 증상이 더 잘
    보였을 것으로 추정됨.

    지금부터는 서로 `gap_px` 이내에 있는 조각들끼리만(그래프의 연결 요소로
    묶어) buffer(+)/buffer(-)를 적용하고, 어디에도 가깝지 않은 조각은 그
    연산 자체를 거치지 않고 원본 그대로 통과시킨다 -- "2mm 안이면 반드시
    합친다"는 기존 규칙(최우선 순위로 지정된 규칙, 절대 약화하지 않음)은
    똑같이 지키면서, 상관없는 도안까지 건드리는 부작용만 없앤다.
    """
    if geom.is_empty:
        return MultiPolygon([])
    polys = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
    n = len(polys)
    if n <= 1:
        return geom if isinstance(geom, MultiPolygon) else MultiPolygon(polys)

    # 서로 gap_px 이내인 조각들을 union-find로 묶는다(간단한 그래프 연결
    # 요소 계산 -- 조각 수가 시트 하나 기준으로 많아야 수십 개라 O(n^2)
    # 거리 비교로 충분히 빠름).
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        for j in range(i + 1, n):
            if polys[i].distance(polys[j]) < gap_px:
                union(i, j)

    groups: dict = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(polys[i])

    half = gap_px / 2.0
    result_polys: list = []
    for members in groups.values():
        if len(members) == 1:
            result_polys.append(members[0])
            continue
        merged = unary_union(members).buffer(half, resolution=resolution).buffer(
            -half, resolution=resolution
        )
        if isinstance(merged, Polygon):
            if not merged.is_empty:
                result_polys.append(merged)
        elif not merged.is_empty:
            result_polys.extend(merged.geoms)

    return MultiPolygon(result_polys)


def smooth_design_naturally(
    design, presmooth_px: float, cap_factor: float = 0.4, resolution: int = 16
):
    """
    "자연스러운 칼선이 중요해, 옵션이 아니라 기본이 되어야해" (2026-09-09(15차)):
    실루엣을 margin만큼 바깥으로 밀기 전에, 그 실루엣 자체를 미리 매끄럽게
    다듬는다 -- 캐릭터 몸통처럼 큰 형태는 굴곡(귀 사이 틈 등)이 완전히
    뭉개지지 않으면서도 눈에 보이는 잔물결이 없어질 만큼 크게(presmooth_px),
    그 옆의 아주 작은 별개 요소(말풍선 속 작은 아이콘, 모서리 표시 등)는
    같은 큰 값을 그대로 쓰면 buffer(+)/buffer(-) 라운드트립 자체가 그 작은
    모양을 통째로 지워버린다(morphological opening이 자기 반지름보다 좁은
    형태를 없애버리는 것과 동일한 원리) -- 실제 12칸 시트 파일로 직접
    측정: 반지름(면적 기준 등가 원반지름)이 14~17px인 작은 요소들에
    presmooth_px=85px를 그대로 쓰면 다 사라짐.
    그래서 조각마다 따로, 그 조각 자기 크기에 맞춰 presmooth 크기를 스스로
    제한한다(equiv_radius * cap_factor) -- 큰 조각은 원래 원하는 크기
    그대로 다듬어지고, 작은 조각은 자기 크기 안에서만 아주 살짝 다듬어져
    사라지지 않는다. 조각들을 따로 다듬은 뒤에 합치므로(먼저 합쳐서
    한꺼번에 다듬는 것과 달리) 큰 조각을 위한 큰 presmooth가 작은 조각까지
    집어삼키는 일이 없다.
    """
    geoms = list(design.geoms) if hasattr(design, "geoms") else [design]
    geoms = [g for g in geoms if g is not None and not g.is_empty]
    if not geoms:
        return design

    smoothed = []
    for g in geoms:
        equiv_radius = math.sqrt(g.area / math.pi) if g.area > 0 else 0.0
        px = min(presmooth_px, equiv_radius * cap_factor)
        px = max(px, 0.0)
        if px <= 0.0:
            smoothed.append(g)
            continue
        sm = g.buffer(px, join_style=1, resolution=resolution).buffer(
            -px, join_style=1, resolution=resolution
        )
        if not sm.is_empty:
            smoothed.append(sm)
        else:
            smoothed.append(g)

    merged = unary_union(smoothed)
    return merged


def generate_cutlines(
    image_path: str,
    is_vector: bool,
    dpi: float,
    offset_mm: OffsetSpec,
    alpha_threshold: int = 127,
    simplify_tol_px: float = 0.6,
    merge_gap_mm: Optional[float] = None,
    clip_to_image_bounds: bool = True,
    supersample: int = 4,
) -> CutlineResult:
    precision_notes: list = []
    if is_vector:
        design, w, h = load_vector_design(
            image_path, dpi=dpi, simplify_tol_px=min(simplify_tol_px, 1.0), supersample=supersample,
            note_sink=precision_notes,
        )
    else:
        design, w, h = load_raster_design(
            image_path, alpha_threshold=alpha_threshold, simplify_tol_px=simplify_tol_px,
            supersample=supersample, note_sink=precision_notes,
        )

    offset_mm, adjustments = offset_mm.enforce_minimum_gap()
    clip_bounds = (0, 0, w, h) if clip_to_image_bounds else None
    offsets = compute_offsets(design, dpi, offset_mm, merge_gap_mm=merge_gap_mm, clip_bounds=clip_bounds)

    return CutlineResult(
        dpi=dpi,
        width_px=w,
        height_px=h,
        design=design,
        offsets=offsets,
        offset_mm=offset_mm,
        adjustments=precision_notes + adjustments,
    )
