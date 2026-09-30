"""
Extract a single design's silhouette from an arbitrary (possibly colored,
gradient, or busy multi-element) background, given only a rough rectangle
drawn around it -- e.g. the artist dragging a loose selection box over one
character in a finished, flattened print-layer scene.

This is the piece that makes cutline generation possible on REAL sticker
sheets: almost all real 씰스티커 work happens over a colored background (that
is exactly why it takes so long by hand), not over a clean transparent PNG.
core.cutline_core's alpha/near-white pipeline only ever handled the easy
case; this module handles the actual common case using GrabCut, an
interactive foreground/background segmentation algorithm built for exactly
this kind of "rough box around one thing on a busy background" input.

The rectangle does NOT need to be precise -- GrabCut treats it as "probably
foreground inside, definitely background outside", then iteratively refines
color-distribution models for each side. A generous, sloppy drag still
converges to a tight silhouette as long as the box fully contains the
design and mostly excludes its neighbors.
"""

from __future__ import annotations

import cv2

from . import image_cache as _image_cache
import numpy as np
from shapely.affinity import scale as shapely_scale, translate as shapely_translate
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

from .cutline_core import _contours_to_polygons, auto_supersample
from .multi_design import _fill_enclosed_regions, _odd

# 2026-09-09(32차) 발견: cv2.grabCut은 내부적으로 OpenCV의 전역 RNG(난수
# 생성기, K-means로 초기 색 분포를 나눌 때 사용)를 쓰는데, 이 RNG는 시드를
# 고정해주지 않으면 "이번 프로세스에서 grabCut을 몇 번째로 호출했는지"에
# 따라 매번 다른 상태에서 시작한다. 그래서 크롭 픽셀이 완전히 똑같아도,
# 그 직전에 (같은 프로그램 실행 안에서) grabCut이 몇 번 더 호출됐었는지에
# 따라 같은 칸의 결과가 달라질 수 있음을 실제 파일로 확인함(예: 한 칸을
# 격리해서 새 프로세스로 여러 번 돌리면 완전히 동일한 결과가 나오지만,
# 같은 프로세스 안에서 다른 칼선 작업을 먼저 하고 나서 돌리면 결과가
# 달라짐). 매 grabCut 호출 직전에 항상 같은 시드로 되돌려주면, 어떤 순서로
# 몇 번을 호출하든 같은 크롭에는 항상 같은 결과가 나오게 고정된다 -- 즉
# "가끔 운 나쁘면 다른 결과가 나오는" 문제 자체를 원천적으로 없앤다.
GRABCUT_RNG_SEED = 42


def _edge_outline_mask(
    crop: np.ndarray, inner: tuple, edge_low: int = 25, edge_high: int = 80, close_ratio: float = 0.008,
):
    """2026-09-07(7차) 피드백("요소 외곽에 라인이 들어가면 그 라인을
    기준으로 칼선을 만들면 돼"): GrabCut은 전경/배경의 '색 분포'가 서로
    충분히 달라야만 동작하는데, 배경과 색이 비슷하거나 대비가 약한
    도안에서는 아예 전경을 하나도 못 찾고 실패할 수 있다(실제로 확인된
    "GrabCut found no foreground inside the selection" 케이스). 그런데
    실제 스티커 원화는 캐릭터 몸 전체를 둘러싼 굵은 외곽선(테두리 선)이
    그려져 있는 경우가 많다 -- 이 선 자체가 이미 정확한 실루엣 경계이므로,
    색이 아니라 "선(엣지)"을 직접 찾으면 GrabCut이 실패하는 바로 그
    상황에서도 정확한 실루엣을 얻을 수 있다.

    core.multi_design.detect_design_bboxes_px가 여러 도안의 '위치'를 찾을
    때 쓰는 것과 정확히 같은 방법(Canny 엣지 -> 살짝 팽창/닫기로 끊어진
    선 잇기 -> 그 안쪽을 채우기)을 재사용해서, 이번엔 위치가 아니라 '이
    선택 영역 안에서 어디가 그 도안의 실제 실루엣인가'를 알아낸다.

    `inner`는 cv2.grabCut과 같은 (x, y, w, h) 포맷, crop 로컬 좌표.
    선택 영역(inner) 안에서 가장 크게 겹치는 하나의 닫힌 덩어리를 그 도안의
    실루엣으로 보고 그 마스크(crop과 같은 크기, 255=도안)를 돌려준다 --
    선택 영역과 겹치는 닫힌 덩어리를 하나도 못 찾으면(배경에도 진짜
    도안에도 뚜렷한 선이 전혀 없는 경우) None을 돌려줘서, 호출하는 쪽이
    기존과 동일하게 "실패"로 처리하도록 한다."""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, edge_low, edge_high)
    ch, cw = gray.shape
    short_side = min(ch, cw)
    close_px = _odd(max(3, min(21, short_side * close_ratio)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_px, close_px))
    mask = cv2.dilate(edges, kernel, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = _fill_enclosed_regions(mask)

    n_labels, labels = cv2.connectedComponents(mask, connectivity=8)
    if n_labels <= 1:
        return None

    ix, iy, iw, ih = inner
    ix0, iy0 = max(0, ix), max(0, iy)
    ix1, iy1 = min(cw, ix + iw), min(ch, iy + ih)
    if ix1 <= ix0 or iy1 <= iy0:
        return None
    region_labels = labels[iy0:iy1, ix0:ix1]
    vals, counts = np.unique(region_labels[region_labels > 0], return_counts=True)
    if len(vals) == 0:
        return None
    best_label = int(vals[np.argmax(counts)])
    return np.where(labels == best_label, 255, 0).astype(np.uint8)


_SEGMENT_CACHE: "dict" = {}
_SEGMENT_CACHE_MAX = 512
_SEGMENT_CACHE_LOCK = __import__("threading").Lock()


def segment_design_in_region(
    image_path: str,
    rect_px: tuple,
    margin_px: int = 40,
    iterations: int = 5,
    supersample: int = 4,
    simplify_tol_px: float = 0.6,
    min_area_px: float = 25.0,
    note_sink: list | None = None,
    max_grabcut_dim: int = 420,
) -> MultiPolygon:
    """같은 이미지·같은 영역·같은 설정으로 다시 부르면 GrabCut을 다시 돌리지 않고 이전 결과를
    돌려준다(2026-09-30 속도 조사: 요소 하나마다 자동 판정용과 무테 칼선용으로 똑같은 GrabCut을
    두 번씩 돌리고 있었음 -- ③에서 56번 중 28번이 중복). 그때 남긴 안내 문구도 똑같이 다시
    남긴다. 이미지 파일이 바뀌면(수정 시각/크기) 새로 계산한다."""
    import os as _os

    try:
        st = _os.stat(image_path)
        key = (_os.path.abspath(image_path), st.st_mtime_ns, st.st_size,
               tuple(round(float(v), 3) for v in rect_px), int(margin_px), int(iterations),
               int(supersample), float(simplify_tol_px), float(min_area_px), int(max_grabcut_dim))
    except (OSError, TypeError, ValueError):
        key = None
    if key is not None:
        with _SEGMENT_CACHE_LOCK:
            hit = _SEGMENT_CACHE.get(key)
        if hit is not None:
            geom, notes = hit
            if note_sink is not None:
                note_sink.extend(notes)
            return geom
    notes: list = []
    geom = _segment_design_in_region_uncached(
        image_path, rect_px, margin_px=margin_px, iterations=iterations, supersample=supersample,
        simplify_tol_px=simplify_tol_px, min_area_px=min_area_px, note_sink=notes,
        max_grabcut_dim=max_grabcut_dim,
    )
    if note_sink is not None:
        note_sink.extend(notes)
    if key is not None:
        with _SEGMENT_CACHE_LOCK:
            if len(_SEGMENT_CACHE) >= _SEGMENT_CACHE_MAX:
                _SEGMENT_CACHE.pop(next(iter(_SEGMENT_CACHE)))
            _SEGMENT_CACHE[key] = (geom, list(notes))
    return geom


def _segment_design_in_region_uncached(
    image_path: str,
    rect_px: tuple,
    margin_px: int = 40,
    iterations: int = 5,
    supersample: int = 4,
    simplify_tol_px: float = 0.6,
    min_area_px: float = 25.0,
    note_sink: list | None = None,
    max_grabcut_dim: int = 420,
) -> MultiPolygon:
    """
    rect_px: (x0, y0, x1, y1) -- a rough rectangle around ONE design, in the
    FULL image's own pixel coordinates (e.g. from a drag-select in the GUI).
    Does not need to hug the design tightly; a bit of slack on every side is
    fine and expected.

    `supersample`: requested precision (see `core.cutline_core.auto_supersample`)
    -- capped down automatically based on the SELECTION CROP's own size
    (that is what actually gets upsampled here, not the full source image),
    so a small drag-selection on a huge sheet still gets full precision.
    Pass `note_sink=[]` to receive a note when the cap actually reduces it.

    `max_grabcut_dim`: 2026-09-07(6차) 피드백("자동인식 처리 속도가 너무
    느려... 최소 5초 안에는 완성되어야") 대응. 프로파일링 결과 이 함수 전체
    시간의 80% 이상이 cv2.grabCut 자체였고(3000x2000 시트, ~580x480 크롭
    기준 실측 0.73초/0.92초), grabCut의 비용은 대체로 넘겨준 이미지의
    픽셀 수에 비례한다. 반면 최종 칼선의 정밀도는 grabCut 이후 별도
    supersample 업샘플링 단계(아래, 항상 원래 크롭 해상도 기준으로 동작)에서
    나오므로, grabCut 자체는 원본 해상도로 돌릴 필요가 없다 -- 그래서
    크롭의 긴 변이 이 값을 넘으면 grabCut '만' 축소된 사본에서 실행하고,
    그 결과 마스크를 원래 크롭 해상도로 다시 확대해서(선형 보간 + 재이진화,
    아래 supersample 단계와 동일한 방식) 이후 파이프라인은 지금까지와
    완전히 동일하게 진행한다. 실측: 580x480 크롭 기준 420px로 축소 시
    grabCut 시간이 0.73초 -> 약 0.26초로 줄어듦(이미 그 크롭보다 작은
    선택 영역은 이 축소 자체가 적용되지 않아 기존과 동일).

    2026-09-09(30차) 조사 기록("외관선이 있어서 작업 하기 쉬운 햄스터 모자는
    칼선이 없고"): 실제 파일로 재현해본 결과, 재단선 격자 한 칸에 캐릭터가
    여러 개 나란히 있는 크롭에서 배경(노란 줄무늬)과 색이 비슷한(둘 다
    노란-주황 계열) 캐릭터의 복슬복슬한 머리/털 부분이, 이 축소 단계 때문에
    통째로 배경으로 오인되는 경우를 실제로 확인함(420px에서는 머리가 빠짐,
    원본 해상도에서는 정상적으로 잡힘). 이 값을 800으로 올리면 그 특정
    사례는 고쳐지지만, 이미 검증해둔 다른 실제 파일(12칸 시트, 작은 장식
    요소들이 촘촘한 칸)에서는 반대로 결과가 불안정해져(같은 칸을 여러 번
    돌리면 어떤 때는 깨끗하게, 어떤 때는 작은 요소가 11개까지 과도하게
    쪼개짐 -- grabCut 자체의 반복 최적화가 크기에 따라 다른 지점에 수렴하는
    비선형적 특성 때문) 이미 확인된 다른 실제 파일을 새로 망가뜨릴 위험이
    실측으로 확인됨. "sure foreground" 시드를 선택 영역 중앙에 추가로 주는
    방법도 시도해봤으나, 이번엔 반대로 여러 캐릭터가 나란히 있는 칸에서
    캐릭터 사이 배경 틈까지 전경으로 묻혀버려(캐릭터 8개가 모두 하나의
    큰 덩어리로 합쳐짐) 이 프로젝트의 다른 핵심 요구사항(캐릭터별로 각각
    칼선)을 깨버리는 것도 실측으로 확인함. 그래서 두 시도 모두 되돌리고
    기본값은 420으로 유지한다 -- 이 특정 문제(배경과 색이 비슷한 머리/털이
    빠지는 것)는 원인은 확인했지만, 이미 검증된 다른 실제 파일들을 깨지
    않는 안전한 해결책은 아직 못 찾았다는 뜻으로 정직하게 남겨둔다.

    2026-09-09(32차, "오늘 시간을 들여서 고치자"): 위 1번/4번을 계속
    고쳐보다가, 이 둘과는 별개인 더 근본적인 문제를 실제 파일로 발견함 --
    cv2.grabCut이 내부적으로 쓰는 전역 난수(RNG, K-means로 초기 색 분포를
    나눌 때 사용)가 시드 고정 없이는 "이 프로그램이 켜진 뒤 grabCut을 몇
    번째로 호출했는지"에 따라 달라져서, 크롭 픽셀이 완전히 똑같은 같은
    칸도 (같은 프로그램 실행 안에서 그 앞에 다른 칼선 작업이 있었는지에
    따라) 결과가 달라질 수 있었음(실측: 한 칸을 격리해서 새 프로그램으로
    여러 번 돌리면 매번 똑같이 나오지만, 같은 실행 안에서 다른 작업을
    먼저 시키고 나서 돌리면 조각이 7개에서 53개까지 늘어나며 지저분해짐).
    매 grabCut 호출 직전에 항상 `GRABCUT_RNG_SEED`로 시드를 되돌려서, 몇
    번째 호출이든 같은 크롭에는 항상 같은 결과가 나오도록 고쳤다 -- "가끔
    운 나쁘면 결과가 달라지는" 문제 자체를 없애는 것으로, 이미 검증된 모든
    실제 파일에서 회귀 없이 안전함을 확인함(오히려 일부 칸은 이 시드 값
    자체로 더 깨끗해지는 부수 효과도 있었음).

    1번/4번 자체를 고치려고 "칸 전체를 먼저 대충 훑고, 조각이 여러 개면
    각 조각만 다시 타이트하게 잘라 재추적하는" 2단계 방식도 시도했으나
    (다른 조각을 삼키지 않는지, 배경을 삼키지 않는지 각각 안전장치를
    추가하고 실제 파일 전체로 반복 검증했음에도), 이미 깨끗하게 잘 나오던
    캐릭터를 재추적하는 과정에서 외곽선이 지저분하게 갈라지는 새로운
    부작용이 실측으로 확인되어 최종적으로 되돌렸다. 그래서 1번(배경 소품이
    캐릭터와 함께 도안으로 잡힘)과 4번(배경과 색이 비슷한 머리/털이 빠짐)은
    오늘 시점에도 여전히 미해결로 남겨둔다 -- 정직하게, 안전하지 않은
    해결책을 급하게 넣기보다는 이번 RNG 고정 수정만 반영한다.
    """
    img = _image_cache.imread(image_path, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"Could not read image: {image_path}")
    h, w = img.shape[:2]

    x0, y0, x1, y1 = [int(round(v)) for v in rect_px]
    x0, y0 = max(0, x0 - margin_px), max(0, y0 - margin_px)
    x1, y1 = min(w, x1 + margin_px), min(h, y1 + margin_px)
    if x1 <= x0 or y1 <= y0:
        raise ValueError("Selection rectangle is empty/out of bounds.")

    crop = img[y0:y1, x0:x1]
    requested_supersample = supersample
    supersample = auto_supersample(x1 - x0, y1 - y0, requested=requested_supersample)
    if supersample != requested_supersample and note_sink is not None:
        note_sink.append(
            f"선택 영역({x1-x0}x{y1-y0}px)이 커서 정밀도를 {requested_supersample}x -> "
            f"{supersample}x로 자동 조정했습니다 (처리 속도 보호)"
        )

    # The GrabCut init rect is the artist's ORIGINAL drag box, expressed
    # relative to this crop (the margin we added around it is treated as
    # "definitely background" by virtue of being outside this inner rect).
    inner = (
        max(0, int(round(rect_px[0])) - x0),
        max(0, int(round(rect_px[1])) - y0),
        min(crop.shape[1], int(round(rect_px[2])) - x0) - max(0, int(round(rect_px[0])) - x0),
        min(crop.shape[0], int(round(rect_px[3])) - y0) - max(0, int(round(rect_px[1])) - y0),
    )

    crop_h, crop_w = crop.shape[:2]
    gc_scale = 1.0
    longest = max(crop_h, crop_w)
    if max_grabcut_dim and longest > max_grabcut_dim:
        gc_scale = max_grabcut_dim / float(longest)
    if gc_scale < 1.0:
        gc_crop = cv2.resize(
            crop, (max(1, round(crop_w * gc_scale)), max(1, round(crop_h * gc_scale))),
            interpolation=cv2.INTER_AREA,
        )
        gc_inner = tuple(max(0, int(round(v * gc_scale))) for v in inner)
    else:
        gc_crop = crop
        gc_inner = inner

    # 2026-09-29(멍푸 PC에서 키스컷 롤 파일 실제 사용 중 발견): 요소가 이미지 위아래
    # 끝까지 닿아 선택 사각형이 잘라낸 영역 전체를 덮으면 "배경" 표본이 하나도 없어
    # GrabCut이 OpenCV 오류(bgdSamples.empty)로 실패했다(롤 1개에서 2개 요소 실패,
    # 사용자에게는 OpenCV 원문 오류가 그대로 보임). 배경 표본이 없으면 가장자리 색으로
    # 4px 테두리를 덧대어 실행하고 결과에서 다시 떼어낸다.
    pad = 0
    gx, gy, gw, gh = gc_inner
    if gx <= 0 and gy <= 0 and gx + gw >= gc_crop.shape[1] and gy + gh >= gc_crop.shape[0]:
        pad = 4
    elif gw * gh >= gc_crop.shape[0] * gc_crop.shape[1]:
        pad = 4
    if pad:
        ring = np.concatenate([gc_crop[0], gc_crop[-1], gc_crop[:, 0], gc_crop[:, -1]])
        fill = [int(v) for v in np.median(ring, axis=0)]
        gc_work = cv2.copyMakeBorder(gc_crop, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=fill)
        gc_rect = (gx + pad, gy + pad, gw, gh)
    else:
        gc_work, gc_rect = gc_crop, gc_inner
    gc_mask = np.zeros(gc_work.shape[:2], np.uint8)
    bgd_model = np.zeros((1, 65), np.float64)
    fgd_model = np.zeros((1, 65), np.float64)
    cv2.setRNGSeed(GRABCUT_RNG_SEED)
    cv2.grabCut(gc_work, gc_mask, gc_rect, bgd_model, fgd_model, iterations, cv2.GC_INIT_WITH_RECT)
    if pad:
        gc_mask = gc_mask[pad:-pad, pad:-pad]

    native_mask = np.where(
        (gc_mask == cv2.GC_FGD) | (gc_mask == cv2.GC_PR_FGD), 255, 0
    ).astype(np.uint8)
    if gc_scale < 1.0:
        # grabCut ran on a smaller working copy purely for speed -- scale its
        # binary decision back up to the crop's real resolution before the
        # existing morphology/supersample pipeline below (which already
        # always operates at the crop's real resolution) takes over.
        native_mask = cv2.resize(native_mask, (crop_w, crop_h), interpolation=cv2.INTER_LINEAR)
        _, native_mask = cv2.threshold(native_mask, 127, 255, cv2.THRESH_BINARY)

    # GrabCut needs a real color difference between foreground and
    # background to work at all -- it can come back completely empty, or
    # (more often, since it always outputs SOMETHING) with just a tiny
    # sliver nowhere near the size of the actual selection, when a design's
    # fill color happens to match its background and only a drawn outline
    # marks the real boundary. Before giving up (or silently returning that
    # tiny sliver), try tracing the design's own drawn line directly instead
    # of by color (see _edge_outline_mask) -- this only ever runs when
    # GrabCut has already essentially failed (found less than 15% of the
    # actual selection area -- a real, reasonably-cropped design normally
    # fills far more of its own selection box than that), so it can't
    # change the result of any selection that currently works well.
    inner_area = max(1, inner[2] * inner[3])
    native_area = int((native_mask > 0).sum())
    if native_area < 0.15 * inner_area:
        edge_mask = _edge_outline_mask(crop, inner)
        if edge_mask is not None and int((edge_mask > 0).sum()) > native_area:
            native_mask = edge_mask
            if note_sink is not None:
                note_sink.append(
                    "배경과 도안의 색 구분이 어려워, 도안에 그려진 외곽선을 기준으로 "
                    "실루엣을 추적했습니다."
                )

    # Same denoise + sub-pixel supersample/re-threshold pipeline used for
    # clean alpha/near-white designs, applied here to GrabCut's mask edge --
    # keeps cutline fidelity consistent regardless of which path a design
    # came in through.
    #
    # 2026-09-28(67차, GrabCut 보조 기능 작업): 이 아래 "native_mask 확보 이후"
    # 후처리 전체를 segment_design_with_hints(트라이맵 힌트 보정 도구)와 공유할
    # 수 있도록 _polygons_from_native_mask로 뽑아냈다 -- 이 함수(기존, 이미
    # 검증된 경로) 쪽은 그 헬퍼를 그대로 호출하기만 해서 동작이 한 글자도
    # 바뀌지 않는다(순수 리팩터링, 전체 회귀 스위트로 확인).
    return _polygons_from_native_mask(
        native_mask, x0, y0, supersample, simplify_tol_px, min_area_px
    )


def _polygons_from_native_mask(
    native_mask: np.ndarray,
    x0: int,
    y0: int,
    supersample: int,
    simplify_tol_px: float,
    min_area_px: float,
) -> MultiPolygon:
    """`segment_design_in_region`의 "native_mask 확보 이후" 후처리(디노이즈 ->
    서브픽셀 supersample -> 컨투어 추출 -> 작은 노이즈/내부 디테일 조각 판정
    -> 크롭 로컬 좌표에서 원본 이미지 좌표로 이동 -> union)를 공유 함수로
    뽑아둔 것 -- segment_design_in_region과 segment_design_with_hints(트라이맵
    힌트 보정 도구) 둘 다 이 함수를 호출해서 완전히 동일한 후처리를 받는다."""
    kernel = np.ones((3, 3), np.uint8)
    native_mask = cv2.morphologyEx(native_mask, cv2.MORPH_OPEN, kernel)
    native_mask = cv2.morphologyEx(native_mask, cv2.MORPH_CLOSE, kernel)

    supersample = max(1, int(supersample))
    if supersample > 1:
        ch, cw = native_mask.shape
        upsampled = cv2.resize(
            native_mask, (cw * supersample, ch * supersample), interpolation=cv2.INTER_LINEAR
        )
        blur_k = max(3, (supersample // 2) * 2 + 1)
        upsampled = cv2.GaussianBlur(upsampled, (blur_k, blur_k), 0)
        _, mask = cv2.threshold(upsampled, 127, 255, cv2.THRESH_BINARY)
        simplify_tol_super = simplify_tol_px * supersample
    else:
        mask = native_mask
        simplify_tol_super = simplify_tol_px

    polygons = _contours_to_polygons(mask, simplify_tol_super)
    if supersample > 1:
        polygons = [
            shapely_scale(p, xfact=1 / supersample, yfact=1 / supersample, origin=(0, 0))
            for p in polygons
        ]
    polygons = [p for p in polygons if p.area >= min_area_px]
    if not polygons:
        raise ValueError(
            "GrabCut found no foreground inside the selection -- try a looser "
            "rectangle that fully contains the design."
        )

    # Drop only GENUINE stray GrabCut noise -- a pixel-scale speck stuck
    # right against the design's main body (measured on a real file: a
    # 71px^2 speck touching a 115206px^2 main character), which if kept as
    # its own tiny polygon draws a small extra floating line right next to
    # the real cutline ("이중 칼선").
    #
    # 2026-09-09(13차) 피드백("여전히 칼선이 엉망이야... 너가 직접 프로그램을
    # 사용해봐")에서, 실제 12칸 시트 파일로 자동인식(전체 셀 선택 -> LINE_ART
    # 스타일) 파이프라인을 직접 돌려본 결과, 이전의 "가장 큰 조각 대비 2%
    # 미만이면 버린다"는 규칙이 위 진짜 노이즈뿐 아니라 같은 칸 안의 다른
    # 진짜 인쇄 요소(말풍선 옆 작은 'Rec' 아이콘, 네 귀퉁이 괄호 표시, 서류/
    # 음료 아이콘의 색 밴드 조각 등)까지 지워버리고 있었다는 게 실측으로
    # 확인됐다 -- 이런 진짜 요소들은 592~8050px^2였고 가장 큰 형태(캐릭터
    # 몸통)와 34~274px 떨어져 있어(전혀 붙어있지 않음) 위 노이즈 사례(크기
    # 71px^2, 거리 0에 가까움)와는 크기·거리 둘 다 뚜렷이 다른 자리에
    # 있었다. 그래서 이제는 "가장 큰 조각 대비 비율"이 아니라, "절대 크기가
    # 노이즈 수준으로 작고(NOISE_FLECK_MAX_AREA_PX 이하) *동시에* 이미 남긴
    # 다른(더 큰) 조각에 거의 붙어있는(NOISE_FLECK_ADJACENCY_PX 이내)" 경우에만
    # 버린다 -- 그 두 조건을 같이 만족하는 조각은 실제로도 그 큰 조각에
    # 합쳐질 정도로 가까우므로(아래 unary_union이 자동으로 이어붙임) 잃는
    # 실제 인쇄 면적이 없고, 둘 중 하나라도 아니면(멀리 떨어져 있거나,
    # 노이즈보다 뚜렷이 크면) 독립된 진짜 디자인 요소로 보고 그대로 남긴다.
    NOISE_FLECK_MAX_AREA_PX = 150.0
    NOISE_FLECK_ADJACENCY_PX = 5.0

    # 2026-09-09(29차) 피드백("다중 칼선이 많아짐... 형태가 구불구불한
    # 캐릭터의 외곽선을 따라 안쪽에 칼선 작업을 해야해")에서, 실제 10칸
    # 시트 파일로 이 자동 인식 경로를 직접 재현해본 결과: 위 두 조건(절대
    # 크기 NOISE_FLECK_MAX_AREA_PX 이하 *동시에* NOISE_FLECK_ADJACENCY_PX
    # 이내)만으로는 못 잡는 또 다른 실제 사례가 나왔다 -- 캐릭터 얼굴 안의
    # 두 눈(흰 눈동자+검은 동공) 부분이, 그 둘레를 두른 얇은 윤곽선 획 때문에
    # 몸통 본체 덩어리와 완전히 끊어진 별도 섬(같은 실측: 3065px^2, 몸통
    # 79648px^2와의 거리 약 6.9px)으로 나와, 절대 크기(150px^2)는 훌쩍
    # 넘지만 실제로는 그 캐릭터 얼굴 "안"의 디테일일 뿐이었다. 결과물에는
    # 몸통 외곽선과 전혀 안 이어진 작은 눈 모양 고리가 하나 더 떠서(다중
    # 칼선) 나왔다.
    #
    # 이 프로젝트에서 지금까지 실측으로 확인된 "진짜로 서로 다른, 독립된
    # 디자인 요소"는 늘 34~274px 떨어져 있었다(위 592~8050px^2 요소들).
    # 반대로 이번 눈 사례(6.9px)와 기존 순수 노이즈 사례(71px^2, 거리 0)는
    # 둘 다 10px 미만이다. 즉 "크기"가 아니라 "거리"가 진짜 판별 기준이라는
    # 뜻 -- 크기와 무관하게, 이미 남긴 더 큰 조각과 충분히 가까우면(아래
    # FRAGMENT_ADJACENCY_PX, 실측된 두 구간 6.9px와 34px 사이에 넉넉히
    # 걸쳐지는 값) 그 캐릭터 자신의 내부 디테일(눈, 입 등)이거나 GrabCut
    # 노이즈로 보고 버린다 -- 그 큰 조각 자체의 실제 윤곽선(외곽 칼선)에는
    # 전혀 영향이 없다(눈 부분은 이미 몸통 실루엣 안쪽이므로).
    FRAGMENT_ADJACENCY_PX = 20.0
    polygons = _drop_attached_fragments(polygons, NOISE_FLECK_MAX_AREA_PX, NOISE_FLECK_ADJACENCY_PX, FRAGMENT_ADJACENCY_PX)

    # shift from crop-local coordinates back into the full image's space
    polygons = [shapely_translate(p, xoff=x0, yoff=y0) for p in polygons]

    design = unary_union(polygons)
    if isinstance(design, Polygon):
        design = MultiPolygon([design])
    return design


def segment_design_with_hints(
    image_path: str,
    rect_px: tuple,
    fg_hints_px: list | None = None,
    bg_hints_px: list | None = None,
    hint_radius_px: float = 10.0,
    margin_px: int = 40,
    iterations: int = 5,
    supersample: int = 4,
    simplify_tol_px: float = 0.6,
    min_area_px: float = 25.0,
    note_sink: list | None = None,
    max_grabcut_dim: int = 420,
) -> MultiPolygon:
    """2026-09-28 "갬뱃(GrabCut)을 지원하는 보조 기능" 1차: 저대비/약한 경계
    때문에 GrabCut이 실루엣 일부(또는 전체)를 놓쳤을 때, 사람이 "여긴 확실히
    캐릭터(전경)다" / "여긴 확실히 배경이다"라고 원본 이미지 위에 점 찍어
    표시한 힌트를 실제로 GrabCut 재계산에 반영하는 트라이맵(trimap) 보정
    도구.

    자동으로 아무것도 고치지 않는다 -- 사람이 직접 확인하고 표시한 좌표만
    받아서 그 지점 주변(hint_radius_px)만 "무조건 확정"으로 덮어쓴 뒤 다시
    계산한다. segment_design_in_region(기존, 이미 검증된 자동 경로)은 이
    함수 안에서 전혀 호출되지 않고 완전히 별도로 동작하므로, 이 기능을 새로
    추가해도 기존 자동 인식 결과는 단 하나도 바뀌지 않는다(회귀 위험 없음).

    동작 원리: 먼저 기존과 동일하게 rect_px 기반 GrabCut(GC_INIT_WITH_RECT)을
    한 번 돌려 뼈대 마스크(GC_PR_FGD/GC_PR_BGD)를 얻는다. 힌트가 하나도 없으면
    거기서 멈추고(기존 경로와 동등하게 저대비 실패 시 외곽선 폴백까지 적용),
    힌트가 있으면 그 위에 사람이 준 점 주변만 GC_FGD/GC_BGD로 확정 표시해서
    GC_INIT_WITH_MASK로 한 번 더 반복 -- OpenCV 문서가 설명하는 표준
    트라이맵 보정 방식과 동일하다.

    `rect_px`, `fg_hints_px`, `bg_hints_px`는 전부 원본 이미지의 절대 픽셀
    좌표(GUI에서 이미 쓰고 있는 좌표계와 동일) -- 이 함수 내부에서만 크롭/
    축소 좌표로 변환한다.
    """
    img = _image_cache.imread(image_path, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"Could not read image: {image_path}")
    h, w = img.shape[:2]

    x0, y0, x1, y1 = [int(round(v)) for v in rect_px]
    x0, y0 = max(0, x0 - margin_px), max(0, y0 - margin_px)
    x1, y1 = min(w, x1 + margin_px), min(h, y1 + margin_px)
    if x1 <= x0 or y1 <= y0:
        raise ValueError("Selection rectangle is empty/out of bounds.")

    crop = img[y0:y1, x0:x1]
    requested_supersample = supersample
    supersample = auto_supersample(x1 - x0, y1 - y0, requested=requested_supersample)
    if supersample != requested_supersample and note_sink is not None:
        note_sink.append(
            f"선택 영역({x1-x0}x{y1-y0}px)이 커서 정밀도를 {requested_supersample}x -> "
            f"{supersample}x로 자동 조정했습니다 (처리 속도 보호)"
        )

    inner = (
        max(0, int(round(rect_px[0])) - x0),
        max(0, int(round(rect_px[1])) - y0),
        min(crop.shape[1], int(round(rect_px[2])) - x0) - max(0, int(round(rect_px[0])) - x0),
        min(crop.shape[0], int(round(rect_px[3])) - y0) - max(0, int(round(rect_px[1])) - y0),
    )

    crop_h, crop_w = crop.shape[:2]
    gc_scale = 1.0
    longest = max(crop_h, crop_w)
    if max_grabcut_dim and longest > max_grabcut_dim:
        gc_scale = max_grabcut_dim / float(longest)
    if gc_scale < 1.0:
        gc_crop = cv2.resize(
            crop, (max(1, round(crop_w * gc_scale)), max(1, round(crop_h * gc_scale))),
            interpolation=cv2.INTER_AREA,
        )
        gc_inner = tuple(max(0, int(round(v * gc_scale))) for v in inner)
    else:
        gc_crop = crop
        gc_inner = inner

    gc_mask = np.zeros(gc_crop.shape[:2], np.uint8)
    bgd_model = np.zeros((1, 65), np.float64)
    fgd_model = np.zeros((1, 65), np.float64)
    cv2.setRNGSeed(GRABCUT_RNG_SEED)
    cv2.grabCut(gc_crop, gc_mask, gc_inner, bgd_model, fgd_model, iterations, cv2.GC_INIT_WITH_RECT)

    fg_hints_px = fg_hints_px or []
    bg_hints_px = bg_hints_px or []
    any_hints = bool(fg_hints_px) or bool(bg_hints_px)
    if any_hints:
        gh, gw = gc_mask.shape[:2]
        hint_radius_scaled = max(1, int(round(hint_radius_px * gc_scale)))

        def _stamp_hints(points, label):
            for (hx, hy) in points:
                lx = (hx - x0) * gc_scale
                ly = (hy - y0) * gc_scale
                if not (0 <= lx < gw and 0 <= ly < gh):
                    continue
                cv2.circle(gc_mask, (int(round(lx)), int(round(ly))), hint_radius_scaled, label, -1)

        # 배경 힌트를 먼저 찍고 전경 힌트를 나중에 찍어서, 만약 두 힌트가
        # 실수로 겹치면 "확실히 전경"이라는 사람의 표시가 우선하게 한다.
        _stamp_hints(bg_hints_px, cv2.GC_BGD)
        _stamp_hints(fg_hints_px, cv2.GC_FGD)
        cv2.setRNGSeed(GRABCUT_RNG_SEED)
        cv2.grabCut(gc_crop, gc_mask, gc_inner, bgd_model, fgd_model, iterations, cv2.GC_INIT_WITH_MASK)
        if note_sink is not None:
            note_sink.append(
                f"사용자 힌트(전경 {len(fg_hints_px)}개 / 배경 {len(bg_hints_px)}개)를 반영해 "
                f"실루엣을 다시 계산했습니다."
            )

    native_mask = np.where(
        (gc_mask == cv2.GC_FGD) | (gc_mask == cv2.GC_PR_FGD), 255, 0
    ).astype(np.uint8)
    if gc_scale < 1.0:
        native_mask = cv2.resize(native_mask, (crop_w, crop_h), interpolation=cv2.INTER_LINEAR)
        _, native_mask = cv2.threshold(native_mask, 127, 255, cv2.THRESH_BINARY)

    # 힌트가 전혀 없을 때는 기존 segment_design_in_region과 동등하게 동작하도록
    # (이 함수를 힌트 없이 호출해도 결과가 달라지지 않게) 같은 저대비 폴백을
    # 적용한다.
    inner_area = max(1, inner[2] * inner[3])
    native_area = int((native_mask > 0).sum())
    if native_area < 0.15 * inner_area and not any_hints:
        edge_mask = _edge_outline_mask(crop, inner)
        if edge_mask is not None and int((edge_mask > 0).sum()) > native_area:
            native_mask = edge_mask
            if note_sink is not None:
                note_sink.append(
                    "배경과 도안의 색 구분이 어려워, 도안에 그려진 외곽선을 기준으로 "
                    "실루엣을 추적했습니다."
                )

    return _polygons_from_native_mask(
        native_mask, x0, y0, supersample, simplify_tol_px, min_area_px
    )


def _drop_attached_fragments(
    polygons: list,
    noise_max_area_px: float = 150.0,
    noise_adjacency_px: float = 5.0,
    attached_adjacency_px: float = 20.0,
) -> list:
    """`segment_design_in_region`의 노이즈/내부 디테일 조각 버림 판정을
    독립 함수로 뽑아둔 것 -- 나중에 다른 곳(예: 정제/재시도 단계)에서도
    같은 조각 목록에 똑같은 판정을 다시 적용해야 할 경우를 위해 재사용
    가능한 형태로 분리해둔다."""
    kept_fragments: list = []
    for p in sorted(polygons, key=lambda p: -p.area):
        if not kept_fragments:
            kept_fragments.append(p)
            continue
        nearest_dist = min(p.distance(k) for k in kept_fragments)
        is_noise_fleck = p.area <= noise_max_area_px and nearest_dist <= noise_adjacency_px
        is_attached_detail = nearest_dist <= attached_adjacency_px
        if is_noise_fleck or is_attached_detail:
            continue
        kept_fragments.append(p)
    return kept_fragments

