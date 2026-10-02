"""요소 하나의 칼선을 만드는 순수 계산(화면·Tk 상태 없음) -- 여러 프로세스에서 동시에 돌리려고
gui.app에서 옮겨 온 것(2026-09-30 속도: 스레드로는 파이썬 잠금 때문에 8코어 PC에서도 3.8초
걸려, 진짜 동시 계산이 되는 프로세스로 나눔). 계산 내용은 옮기기 전과 똑같다.
"""
from __future__ import annotations

from .image_style import ImageStyle
from .interactive_cutline import (
    generate_cutline_auto,
    generate_cutline_by_style,
    is_silhouette_undersized,
)
from .style_classify import rectangularity as _shape_rectangularity

RECT_LIKE_THRESHOLD = 0.95
# "낱개 조각" 문맥에서만 쓰는 낮은 실패 기준(자세한 이유는 gui.app 주석 참고)
UNDERSIZED_RATIO_FOR_FINE_SUBELEMENT = 0.10

PURE_AUTO_JOBS = ("AUTO_STYLE", "BORDERLESS", "LINE_ART", "MASKING_TAPE")


def undersized_retry_note(ratio_pct: float, fallback_design) -> str:
    is_rect_like = True
    try:
        if fallback_design is not None and not fallback_design.is_empty:
            is_rect_like = _shape_rectangularity(fallback_design) >= RECT_LIKE_THRESHOLD
    except Exception:
        is_rect_like = True  # 판단 실패 시 기존과 같이 보수적으로 "사각형" 문구 유지
    prefix = (
        f"실루엣 추적 결과가 선택 영역의 {ratio_pct:.0f}%밖에 안 돼(예: 몸통 "
        f"전체가 아니라 배 같은 일부만 잡혔을 가능성) 잘못됐다고 보고, "
    )
    if is_rect_like:
        return prefix + "안전한 사각형(무테 방식) 컷으로 자동 대체했습니다."
    return prefix + (
        "무테 방식으로 다시 실제 선/색 경계를 추적했고, 이번엔 정상적으로 "
        "실루엣을 따라간 모양이 나와 그 결과를 그대로 사용했습니다."
    )


def _with_undersized_fallback(path, result, sel, dpi, margin, precision, bounds=None, min_ratio=None):
    x0, y0, x1, y1 = sel
    cell_area_px = max(0.0, (x1 - x0) * (y1 - y0))
    design_area_px = result.design.area if result.design is not None else 0.0
    kw = {} if min_ratio is None else {"min_ratio": min_ratio}
    if not is_silhouette_undersized(design_area_px, cell_area_px, **kw):
        return result
    extra = {} if bounds is None else {"bounds_px": bounds}
    fallback = generate_cutline_by_style(
        image_path=path, style=ImageStyle.BORDERLESS, dpi=dpi, selection_px=sel,
        margin_mm=margin, supersample=precision, **extra,
    )
    ratio_pct = (design_area_px / cell_area_px * 100) if cell_area_px > 0 else 0.0
    fallback.adjustments = list(result.adjustments or []) + list(fallback.adjustments or []) + [
        undersized_retry_note(ratio_pct, fallback.design)
    ]
    return fallback


def _lighten_cut(result, tol_px: float = 0.25):
    """칼선 다각형의 점을 줄인다(원래 선에서 0.25px = 약 0.02mm 이내).

    2026-10-02 속도(멍푸: "칼선 속도 5초이내로"): 안쪽으로 줄인 칼선은 둥근 모서리마다 점이 촘촘해
    스티커 하나에 점이 1,300개 안팎(시트 전체 10만 개)이라, 뒤이은 겹침 정리·간격 맞추기·미리보기·
    저장이 모두 이 점 수만큼 느렸다. 같은 모양을 점 약 100개로(약 12분의 1)."""
    try:
        cut = result.offsets.get("cut") if result is not None else None
        if cut is None or cut.is_empty:
            return result
        light = cut.simplify(tol_px, preserve_topology=True)
        if light.is_empty or not light.is_valid:
            return result
        from shapely.geometry import MultiPolygon, Polygon

        if isinstance(light, Polygon):
            light = MultiPolygon([light])
        if not isinstance(light, MultiPolygon):
            return result
        result.offsets = {**result.offsets, "cut": light}
    except Exception:  # noqa: BLE001
        pass
    return result


def auto_item(path, job, sel, siblings, art_region, dpi, margin, precision):
    """자동 인식 한 요소(AUTO_STYLE/BORDERLESS/LINE_ART/MASKING_TAPE)."""
    return _lighten_cut(_auto_item(path, job, sel, siblings, art_region, dpi, margin, precision))


def _auto_item(path, job, sel, siblings, art_region, dpi, margin, precision):
    if job == "AUTO_STYLE":
        result = generate_cutline_auto(
            image_path=path, dpi=dpi, selection_px=sel, margin_mm=margin, supersample=precision,
            sibling_boxes_px=list(siblings), art_region_px=art_region,
        )
        return _with_undersized_fallback(path, result, sel, dpi, margin, precision)
    if job == "BORDERLESS":
        return generate_cutline_by_style(
            image_path=path, style=ImageStyle.BORDERLESS, dpi=dpi, selection_px=sel,
            margin_mm=margin, supersample=precision, sibling_boxes_px=list(siblings),
            art_region_px=art_region,
        )
    if job in ("LINE_ART", "MASKING_TAPE"):
        result = generate_cutline_by_style(
            image_path=path, style=ImageStyle.LINE_ART, dpi=dpi, selection_px=sel,
            margin_mm=margin, supersample=precision,
        )
        return _with_undersized_fallback(path, result, sel, dpi, margin, precision)
    raise ValueError(f"순수 계산 대상이 아닌 작업 종류: {job}")


def rest_fine_item(path, sel, bounds, siblings, dpi, margin, precision):
    """"③ 남은 칼선" 한 조각(자동 판정 + 실패 시 무테 대체)."""
    result = generate_cutline_auto(
        image_path=path, dpi=dpi, selection_px=sel, bounds_px=bounds, margin_mm=margin,
        supersample=precision, sibling_boxes_px=list(siblings),
    )
    return _with_undersized_fallback(
        path, result, sel, dpi, margin, precision, bounds=bounds,
        min_ratio=UNDERSIZED_RATIO_FOR_FINE_SUBELEMENT,
    )


def supplement_detect(path, cell_boxes):
    """무테 보조 탐지의 "요소 찾기"(gui.app._supplement_detect에서 옮김, 계산 동일) -> (칸들, 반복
    그룹, 그룹별 요소). 작업 프로세스에서도 돌릴 수 있게 화면 상태 없이."""
    from .interactive_cutline import image_outer_region_px
    from .multi_design import detect_elements_by_background_flood_px, group_content_cells_px
    from .parallel import pmap

    cells, groups = group_content_cells_px(path, [
        c for c in cell_boxes if image_outer_region_px(path, c) is not None
    ])

    def _bg_of(grp):
        out = []
        try:
            detect_elements_by_background_flood_px(path, cells[grp[0]], bg_colors_out=out)
        except Exception:  # noqa: BLE001
            pass
        return out

    # 칸마다 독립 계산이라 동시에(색 모음 순서는 칸 순서 그대로)
    sheet_bg = [c for part in pmap(_bg_of, list(groups)) for c in part]
    elements_per_group = pmap(
        lambda grp: detect_elements_by_background_flood_px(path, cells[grp[0]], known_bg_lab=sheet_bg),
        list(groups),
    )
    return cells, groups, elements_per_group


_SUP_FUTURES: dict = {}


def supplement_key(path, cell_boxes):
    import os

    try:
        st = os.stat(path)
        return (os.path.abspath(path), st.st_mtime_ns, st.st_size,
                tuple(tuple(round(float(v), 3) for v in c) for c in cell_boxes))
    except Exception:  # noqa: BLE001
        return None


_AUTO_MEMO: dict = {}


def _auto_memo_key(args):
    import os

    try:
        st = os.stat(args[0])
        return (os.path.abspath(args[0]), st.st_mtime_ns, st.st_size) + tuple(
            tuple(map(tuple, a)) if isinstance(a, list) else a for a in args[1:]
        )
    except Exception:  # noqa: BLE001
        return None


def run_task(task):
    """프로세스 풀에서 부르는 입구: (종류, 인자 튜플) -> 결과."""
    kind, args = task
    if kind == "auto":
        # 2026-10-02 속도: 파일을 불러오고 "아니오(도무송 없음)"를 고르는 사이 뒤에서 같은 작업을
        # 미리 계산해 둔다(procpool.prefetch_async) -- 같은 입력이면 기억한 결과의 복사본을 준다.
        import copy

        key = _auto_memo_key(args)
        if key is not None and key in _AUTO_MEMO:
            return copy.deepcopy(_AUTO_MEMO[key])
        res = auto_item(*args)
        if key is not None:
            if len(_AUTO_MEMO) > 400:
                _AUTO_MEMO.clear()
            _AUTO_MEMO[key] = copy.deepcopy(res)
        return res
    if kind == "rest":
        return rest_fine_item(*args)
    if kind == "noop":
        return None
    if kind == "warm_image":
        from . import image_cache

        image_cache.imread(args[0])
        return None
    if kind == "warm_cells":
        # 2026-10-02 속도: 파일을 불러오자마자 각 작업 프로세스가 그림을 읽고 칸마다 배경 채우기를
        # 해 둔다(둘 다 프로세스 안에 기억됨) -- 요소 칼선 계산 때 이 둘이 가장 먼저 드는 시간.
        from . import image_cache
        from .multi_design import detect_elements_by_background_flood_px

        path, cells = args
        image_cache.imread(path)
        for c in cells:
            try:
                detect_elements_by_background_flood_px(path, tuple(c))
            except Exception:  # noqa: BLE001
                pass
        return None
    if kind == "auto_boxes":
        # 격자 파일 "자동으로 여러 개 인식"의 빈 칸 거르기 + 칸 안 요소 나누기(gui.app._auto_boxes_for)
        from .interactive_cutline import image_outer_region_px
        from .multi_design import detect_repeat_aware_sub_element_boxes_px

        path, cells = args
        grid = [c for c in cells if image_outer_region_px(path, c) is not None]
        sus: list = []
        boxes, groups = detect_repeat_aware_sub_element_boxes_px(path, grid, suspicious_regions=sus)
        return grid, boxes, groups, sus
    if kind == "supplement_detect":
        return supplement_detect(*args)
    if kind == "design_boxes":
        from .multi_design import detect_design_bboxes_px

        return detect_design_bboxes_px(*args)
    raise ValueError(kind)
