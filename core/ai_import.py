"""
Load an .ai file (saved "PDF compatible", like a real Illustrator export) as
the GUI's primary "1. 도안 파일" input -- distinct from the other two .ai-
related modules in this project: core.guide (parses a guide file's own
structural rects, not artwork) and core.ai_cutline_reader/core.ai_layer_export
(read/write an EXISTING production file's own cutline layer, which requires
the file to already have one). This module is the newest and simplest of the
three -- given ANY .ai file the artist wants to start a FRESH cutline for
(2026-08-26: "주 입력 파일은 어도비 일러스트야"), it produces one plain raster
image file that the rest of this project's pipeline (GrabCut, drag-select,
sticker/domusong generation) can work with exactly like an already-opened
PNG/JPG -- no changes needed anywhere else in the pipeline.

Prefers extracting the file's own embedded print-resolution raster (same
technique already used and verified in core.ai_cutline_reader.load_real_cutlines
against this project's real production files, which always embed one
flattened print image) so the working image is the artist's own pixels at
their own resolution -- never a re-rendered copy that could lose quality.
Falls back to flattening the whole page at the requested DPI only when the
file has no embedded raster at all (a purely vector illustration with no
placed bitmap).
"""

from __future__ import annotations


def load_ai_as_raster(ai_path: str, out_path: str, dpi: float = 300.0, page_index: int = 0) -> str:
    """
    Reads `ai_path` (read-only) and writes a plain raster image to
    `out_path` (PNG), returning `out_path`. Never modifies `ai_path` itself.
    """
    import fitz  # PyMuPDF; .ai saved "PDF compatible" opens fine as PDF

    doc = fitz.open(ai_path)
    try:
        if page_index >= doc.page_count:
            raise ValueError(
                f"{ai_path}: 페이지 {page_index}가 없습니다 (전체 {doc.page_count}페이지)."
            )
        page = doc[page_index]

        images = page.get_images(full=True)
        if images:
            # 실제 인쇄용 래스터가 이미 심겨 있으면(이 프로젝트가 검증한 실제
            # 완성 파일들은 전부 이런 구조) 그 원본 픽셀을 그대로 추출 --
            # 다시 렌더링해서 해상도/화질을 잃지 않도록.
            xref = images[0][0]
            info = doc.extract_image(xref)
            with open(out_path, "wb") as f:
                f.write(info["image"])
            return out_path

        # 심겨 있는 래스터가 없는 순수 벡터 일러스트 -- 요청한 DPI로 페이지
        # 자체를 통째로 래스터화 (알파 유지).
        zoom = max(dpi, 1.0) / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=True)
        pix.save(out_path)
        return out_path
    finally:
        doc.close()
