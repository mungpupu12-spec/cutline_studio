"""
Parse a real print-vendor "가이드" (guide) template file -- an Illustrator
file saved PDF-compatible -- into a SheetGuide: the physical page size, the
overall sheet trim/cut boundary, and the individual per-design "slot"
rectangles the guide itself marks with its own warning text (in the guide
this project was built against: "칼선가이드 (라인을 넘어가지 않도록
작업해주세요)" / "재단가이드 (...)").

This lets the tool check real design work against whatever guide the artist
is actually submitting against, instead of guessing print-vendor
conventions -- and the same loader works for any other guide file of this
kind (different paper/coating/size), since nothing here is hardcoded to one
specific template's numbers.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass


PT_PER_INCH = 72.0


def pt_to_mm(pt: float) -> float:
    return pt / PT_PER_INCH * 25.4


def px_to_pt(px: float, dpi: float) -> float:
    return px / dpi * PT_PER_INCH


def pt_to_px(pt: float, dpi: float) -> float:
    return pt / PT_PER_INCH * dpi


@dataclass
class SheetGuide:
    page_w_pt: float
    page_h_pt: float
    trim_rect_pt: tuple  # (x0,y0,x1,y1) - outer sheet trim boundary
    sheet_cut_rect_pt: tuple  # overall usable/cut boundary, inset from trim
    slot_rects_pt: list  # [(x0,y0,x1,y1), ...] one per design slot, reading order

    @property
    def slot_size_mm(self):
        if not self.slot_rects_pt:
            return (0.0, 0.0)
        x0, y0, x1, y1 = self.slot_rects_pt[0]
        return (pt_to_mm(x1 - x0), pt_to_mm(y1 - y0))


def _rect_area(r):
    x0, y0, x1, y1 = r
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def load_guide_from_ai(path: str) -> SheetGuide:
    """
    Reads an .ai file saved "PDF compatible" (like a real Illustrator export)
    via PyMuPDF and classifies its stroked rectangular guide lines into:
      - one or two big (near-full-page) rects -> the sheet's own trim/cut
        boundary
      - a repeated small-rect group (same color, several copies) -> the
        per-design slot grid

    Heuristic-based on purpose: no assumption about exact page size, colors,
    or slot count, so a different guide file (different paper/size) should
    still parse correctly as long as it follows the same "big page-level
    rect(s) + repeated small per-slot rects" convention.
    """
    import fitz  # PyMuPDF; .ai saved "PDF compatible" opens fine as PDF

    doc = fitz.open(path, filetype="pdf")
    page = doc[0]
    page_w, page_h = page.rect.width, page.rect.height
    page_area = page_w * page_h

    drawings = page.get_drawings()
    stroked = [d for d in drawings if d.get("type") == "s" and d.get("color") is not None]

    groups = defaultdict(list)
    for d in stroked:
        r = d["rect"]
        rect = (r.x0, r.y0, r.x1, r.y1)
        color = tuple(round(c, 2) for c in d["color"])
        groups[color].append(rect)

    # A minimum plausible slot size -- this excludes small stroked shapes
    # that are actually vector letterforms of the guide's own warning text
    # ("칼선가이드 (라인을 넘어가지 않도록...)" is drawn character-by-character
    # in the same guide color, and each glyph's bounding box easily beats a
    # naive "smaller than the page" filter). Real print slots are always at
    # least a couple centimeters on a side.
    MIN_SLOT_MM = 15.0
    min_slot_pt = MIN_SLOT_MM / 25.4 * PT_PER_INCH

    big_rects = []  # (color, rect)
    small_groups = []  # (color, [rects])
    for color, rects in groups.items():
        big = [r for r in rects if _rect_area(r) > 0.3 * page_area]
        small = [
            r
            for r in rects
            if _rect_area(r) <= 0.3 * page_area
            and (r[2] - r[0]) >= min_slot_pt
            and (r[3] - r[1]) >= min_slot_pt
        ]
        big_rects.extend((color, r) for r in big)
        if len(small) >= 2:
            small_groups.append((color, small))

    if not big_rects:
        raise ValueError("가이드 파일에서 시트 전체 경계선(재단/칼선 가이드)을 찾지 못했습니다.")

    big_rects.sort(key=lambda cr: _rect_area(cr[1]), reverse=True)
    trim_rect = big_rects[0][1]
    sheet_cut_rect = big_rects[1][1] if len(big_rects) > 1 else trim_rect

    if not small_groups:
        raise ValueError("가이드 파일에서 개별 도안 슬롯(칸) 경계를 찾지 못했습니다.")
    # Prefer the most internally-consistent group (a real slot grid has
    # near-identical cells; leftover furniture/text does not), breaking ties
    # by number of repeats.
    def _area_cv(rects):
        areas = [_rect_area(r) for r in rects]
        mean = sum(areas) / len(areas)
        if mean == 0:
            return float("inf")
        var = sum((a - mean) ** 2 for a in areas) / len(areas)
        return (var**0.5) / mean

    small_groups.sort(key=lambda cg: (round(_area_cv(cg[1]), 2), -len(cg[1])))
    _, slot_rects = small_groups[0]

    # Row-major reading order (top-to-bottom, left-to-right). Rows are
    # bucketed to the nearest 10pt so tiny per-rect jitter doesn't split one
    # visual row into several.
    slot_rects = sorted(slot_rects, key=lambda r: (round(r[1] / 10) * 10, r[0]))

    return SheetGuide(
        page_w_pt=page_w,
        page_h_pt=page_h,
        trim_rect_pt=trim_rect,
        sheet_cut_rect_pt=sheet_cut_rect,
        slot_rects_pt=slot_rects,
    )
