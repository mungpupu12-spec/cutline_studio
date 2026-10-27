"""2026-10-27(멍푸: "ai 파일 여니까 이중 칼선이야"): 원본 AI에 이미 '칼선' 레이어(작가가 넣어 둔 칼선 그림)가 있으면
사본에 더한 칼선과 두 겹으로 보였다. 원래 레이어는 지우지 않고 '(원본)'을 붙여 끄고, 새 '칼선' 레이어에 넣는다."""
import os
import tempfile
import types

import fitz
from shapely.geometry import MultiPolygon, box

from core import illustrator_bridge as ib
from core import layered_export as lx


def _pdf_with_cut_layer(path):
    doc = fitz.open()
    page = doc.new_page(width=400, height=400)
    old = doc.add_ocg("칼선", on=True)
    art = doc.add_ocg("스티커", on=True)
    page.draw_rect(fitz.Rect(50, 50, 150, 150), color=(1, 0, 1), oc=old)
    page.draw_rect(fitz.Rect(40, 40, 160, 160), color=(0, 0, 1), fill=(0, 0, 1), oc=art)
    doc.save(path)
    doc.close()


def test_pdf_copy_hides_existing_cut_layer():
    d = tempfile.mkdtemp()
    src = os.path.join(d, "src.ai")
    out = os.path.join(d, "out.pdf")
    _pdf_with_cut_layer(src)
    res = types.SimpleNamespace(offsets={"cut": MultiPolygon([box(10, 10, 90, 90)])})
    st = lx.export_ai_copy_with_cut(src, out, res, {"pt_per_px": 1.0, "x0_pt": 0.0, "y0_pt": 0.0})
    assert st["hidden_layers"] == 1 and st["cut_rings"] == 1
    doc = fitz.open(out)
    layers = {v["name"]: v["on"] for v in doc.get_ocgs().values()}
    assert layers["칼선(원본)"] is False                       # 원래 칼선은 남아 있지만 꺼짐
    assert layers["칼선"] is True                              # 새 칼선
    assert layers["스티커"] is True                            # 다른 레이어는 그대로


def test_illustrator_script_uses_fresh_cut_layer():
    # 일러스트레이터 스크립트: 같은 이름 레이어가 있으면 '(원본)'으로 바꿔 숨기고 새 레이어에 넣는다
    jsx = ib._JSX_ADD_TO_AI
    assert "function freshLayer" in ib._JSX_COMMON
    assert "freshLayer(doc, D.cut_layer)" in jsx and "freshLayer(doc, D.white_layer)" in jsx
    assert "(원본)" in ib._JSX_COMMON and "L.visible = false" in ib._JSX_COMMON
