"""2026-10-20: 원본 사본 + 칼선 레이어 저장(core.layered_export). 합성 이미지 -- 좌표·레이어 구조 확인용."""
import os
import tempfile
from types import SimpleNamespace

import cv2
import fitz
import numpy as np
from PIL import Image
from shapely.geometry import MultiPolygon, Polygon

from core.layered_export import export_ai_copy_with_cut, export_layered_pdf

SQ = MultiPolygon([Polygon([(50, 50), (250, 50), (250, 250), (50, 250)])])
WH = MultiPolygon([Polygon([(70, 70), (230, 70), (230, 230), (70, 230)])])


def _png(d):
    rgba = np.zeros((300, 400, 4), np.uint8)
    cv2.circle(rgba, (150, 150), 80, (200, 60, 60, 255), -1)
    p = os.path.join(d, "a.png")
    Image.fromarray(rgba).save(p, dpi=(300, 300))
    return p


def _only(doc, name):
    for c in doc.layer_ui_configs():
        doc.set_layer_ui_config(c["number"], action=0 if c["text"] == name else 2)


def _dark_bbox(page, dpi):
    pix = page.get_pixmap(dpi=dpi)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)
    ys, xs = np.where(a[..., 1] < 128)
    return xs.min(), xs.max(), ys.min(), ys.max()


def test_layered_pdf_from_png():
    d = tempfile.mkdtemp()
    p = _png(d)
    out = os.path.join(d, "a_칼선.pdf")
    st = export_layered_pdf(p, SimpleNamespace(offsets={"cut": SQ}), out, 300, white_geom=WH)
    assert st["cut_rings"] == 1 and st["white_rings"] == 1
    doc = fitz.open(out)
    assert [c["text"] for c in doc.layer_ui_configs()] == ["화이트레이어", "도안", "칼선"]
    assert abs(doc[0].rect.width - 400 * 72 / 300) < 0.01
    _only(doc, "칼선")
    x0, x1, y0, y1 = _dark_bbox(doc[0], 300)          # 칼선이 이미지 픽셀 자리 그대로
    assert abs(x0 - 50) <= 2 and abs(x1 - 250) <= 2 and abs(y0 - 50) <= 2 and abs(y1 - 250) <= 2
    assert len(doc[0].get_images()) == 1


def test_ai_copy_adds_cut_layer_at_image_placement():
    d = tempfile.mkdtemp()
    p = _png(d)
    src = fitz.open()
    pg = src.new_page(width=500, height=400)
    pg.insert_image(fitz.Rect(36, 72, 36 + 400 * 0.24, 72 + 300 * 0.24), filename=p)
    ai = os.path.join(d, "fake.ai")
    src.save(ai)
    before = open(ai, "rb").read()
    out = os.path.join(d, "fake_칼선.ai")
    st = export_ai_copy_with_cut(ai, out, SimpleNamespace(offsets={"cut": SQ}),
                                 {"pt_per_px": 0.24, "x0_pt": 36, "y0_pt": 72}, white_geom=WH)
    assert open(ai, "rb").read() == before                   # 원본은 그대로
    assert st["cut_rings"] == 1
    doc = fitz.open(out)
    assert {c["text"] for c in doc.layer_ui_configs()} == {"화이트레이어", "칼선"}
    full = doc[0].get_pixmap(dpi=288)
    a = np.frombuffer(full.samples, np.uint8).reshape(full.h, full.w, full.n)
    assert tuple(a[int((72 + 150 * .24) * 4), int((36 + 150 * .24) * 4)][:3]) == (200, 60, 60)   # 화이트는 그림 밑
    _only(doc, "칼선")
    x0, x1, y0, y1 = _dark_bbox(doc[0], 288)
    assert abs(x0 / 4 - (36 + 50 * .24)) <= 0.6 and abs(x1 / 4 - (36 + 250 * .24)) <= 0.6
    assert abs(y0 / 4 - (72 + 50 * .24)) <= 0.6 and abs(y1 / 4 - (72 + 250 * .24)) <= 0.6
    try:
        export_ai_copy_with_cut(ai, ai, SimpleNamespace(offsets={"cut": SQ}), {"pt_per_px": 0.24})
        assert False, "원본에 덮어쓰면 안 됨"
    except ValueError:
        pass


def test_gui_export_layered_copy_without_illustrator():
    """일러스트레이터가 없는 환경(이 시험 환경): PNG -> PyMuPDF 레이어 PDF, AI -> 같은 이름 PDF로 대신."""
    from gui.app import CutLineApp

    d = tempfile.mkdtemp()
    p = _png(d)
    app = CutLineApp()
    app.withdraw()
    if not hasattr(app, "preview_canvas"):
        app._build_layout()
    try:
        app._show_note_dialog = lambda *a, **k: None
        app.input_path.set(p)
        app.job_type.set("LINE_ART")
        assert app._shape_cut_auto(p)
        for _ in range(5):
            app.update()
        out = os.path.join(d, "a_칼선.pdf")
        lines = app._export_layered_copy(out, p, None, None, white=None, img=p, img_dpi=300)
        assert lines[0] == out and os.path.isfile(out)
        assert [c["text"] for c in fitz.open(out).layer_ui_configs()] == ["도안", "칼선"]
        # AI: 일러스트레이터가 없으면 .ai 대신 .pdf
        src = fitz.open()
        pg = src.new_page(width=500, height=400)
        pg.insert_image(fitz.Rect(36, 72, 36 + 400 * 0.24, 72 + 300 * 0.24), filename=p)
        ai = os.path.join(d, "fake.ai")
        src.save(ai)
        out_ai = os.path.join(d, "fake_칼선.ai")
        lines = app._export_layered_copy(out_ai, ai, ai, {"pt_per_px": 0.24, "x0_pt": 36, "y0_pt": 72},
                                         white=WH, img=p, img_dpi=300)
        assert not os.path.exists(out_ai) and lines[0].endswith("fake_칼선.pdf") and os.path.isfile(lines[0])
        assert {"칼선", "화이트레이어"} <= {c["text"] for c in fitz.open(lines[0]).layer_ui_configs()}
    finally:
        app.destroy()
