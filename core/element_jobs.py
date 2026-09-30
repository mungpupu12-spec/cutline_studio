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


def auto_item(path, job, sel, siblings, art_region, dpi, margin, precision):
    """자동 인식 한 요소(AUTO_STYLE/BORDERLESS/LINE_ART/MASKING_TAPE)."""
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


def run_task(task):
    """프로세스 풀에서 부르는 입구: (종류, 인자 튜플) -> 결과."""
    kind, args = task
    if kind == "auto":
        return auto_item(*args)
    if kind == "rest":
        return rest_fine_item(*args)
    if kind == "noop":
        return None
    raise ValueError(kind)
