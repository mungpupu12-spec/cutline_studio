"""
Generate a synthetic "Adobe Illustrator, saved PDF-compatible" demo file for
the public demo/test suite -- same convention as samples/make_guide_demo.py:
made up so it's safe to commit and lets anyone cloning this repo run the
.ai-import regression test without needing one of the artist's real
production files.

This one embeds the ALREADY-APPROVED public demo fixture samples/ring.png as
its one placed raster image (exactly the structure this project's real
production files have: one flattened print-resolution image placed on the
page) -- no new image content is generated, only re-packaged into a PDF
container, so core.ai_import's "extract the file's own embedded raster"
path has something real to extract in a mechanical/logic test.
"""

import os

import fitz  # PyMuPDF


def make_ai_import_demo(path="samples/ai_import_demo.ai", image_path="samples/ring.png"):
    doc = fitz.open()
    page = doc.new_page(width=200.0, height=200.0)
    page.insert_image(fitz.Rect(10, 10, 190, 190), filename=image_path)
    doc.save(path, garbage=4, deflate=True)
    doc.close()
    return path


if __name__ == "__main__":
    p = make_ai_import_demo()
    print(f"wrote {p} ({os.path.getsize(p)} bytes)")
