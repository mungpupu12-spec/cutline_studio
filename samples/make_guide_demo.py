"""
Generate a synthetic print-vendor "sheet guide" PDF for the public demo/test
-- same shape as a real Illustrator "가이드" template saved PDF-compatible
(one big trim rect, one big cut rect inset from it, and a repeated grid of
small per-design slot rects, all as stroked paths), but entirely made up so
it's safe to commit and lets anyone cloning this repo run test_sheet_demo.py
without needing a real print vendor's file.
"""

import fitz  # PyMuPDF


def make_guide_demo(path="samples/guide_demo.pdf", cols=3, rows=2):
    page_w, page_h = 400.0, 300.0
    doc = fitz.open()
    page = doc.new_page(width=page_w, height=page_h)

    trim_color = (0.0, 0.63, 0.91)  # blue -- outer sheet trim boundary
    cut_color = (0.89, 0.0, 0.50)  # magenta -- cut boundary + slot grid

    margin = 20.0
    page.draw_rect(
        fitz.Rect(margin, margin, page_w - margin, page_h - margin),
        color=trim_color,
        width=1.2,
    )
    inset = 10.0
    page.draw_rect(
        fitz.Rect(margin + inset, margin + inset, page_w - margin - inset, page_h - margin - inset),
        color=cut_color,
        width=1.0,
    )

    inner_x0, inner_y0 = margin + inset * 2, margin + inset * 2
    inner_x1, inner_y1 = page_w - margin - inset * 2, page_h - margin - inset * 2
    grid_w, grid_h = inner_x1 - inner_x0, inner_y1 - inner_y0
    gap = 8.0
    slot_w = (grid_w - gap * (cols - 1)) / cols
    slot_h = (grid_h - gap * (rows - 1)) / rows

    for r in range(rows):
        for c in range(cols):
            x0 = inner_x0 + c * (slot_w + gap)
            y0 = inner_y0 + r * (slot_h + gap)
            page.draw_rect(
                fitz.Rect(x0, y0, x0 + slot_w, y0 + slot_h),
                color=cut_color,
                width=0.75,
            )

    doc.save(path)
    return path


if __name__ == "__main__":
    p = make_guide_demo()
    print(f"wrote {p}")
