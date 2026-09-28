"""
Generate a synthetic "Adobe Illustrator, saved PDF-compatible" demo file with
TWO DIFFERENT embedded raster images placed side by side on one page -- same
convention as samples/make_ai_import_demo.py, but reproducing the real
structural bug found on 2026-09-08(10차) while reviewing every .ai file in
멍푸님's "자료집"(교재) reference folder ("자료집에 있는 일러스트 파일 전부
칼선 분석하고 문제에 반영해"): several real files there (masking-tape roll
templates, a multi-motif reference sheet) place SEVERAL DIFFERENT small
motifs on one page, not just one flattened composite image repeated. The old
core.ai_import.load_ai_as_raster always extracted `images[0]` -- whichever
ONE image happened to be embedded first -- silently discarding every other
motif. The fix falls back to rendering the whole page (which composites
every placement correctly) whenever more than one DISTINCT image is
embedded.

Uses ONLY the two already-approved public demo fixtures samples/ring.png and
samples/scene.png (no new image content) -- re-packaged into one PDF page,
each placed once, at two clearly different locations.
"""

import os

import fitz  # PyMuPDF


def make_ai_import_multi_demo(
    path="samples/ai_import_multi_demo.ai",
    image_path_a="samples/ring.png",
    image_path_b="samples/scene.png",
):
    doc = fitz.open()
    page = doc.new_page(width=420.0, height=200.0)
    page.insert_image(fitz.Rect(10, 10, 190, 190), filename=image_path_a)
    page.insert_image(fitz.Rect(220, 10, 400, 190), filename=image_path_b)
    doc.save(path, garbage=4, deflate=True)
    doc.close()
    return path


if __name__ == "__main__":
    p = make_ai_import_multi_demo()
    print(f"wrote {p} ({os.path.getsize(p)} bytes)")
