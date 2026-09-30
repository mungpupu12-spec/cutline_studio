"""2026-09-29(멍푸: "svg 파일 원본 이미지 크기에 맞게 적용되게 해줘"): SVG가 실제 크기(mm)로
저장되고, AI 파일이면 원본 대지 크기/이미지 위치 그대로 겹치는지."""
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shapely.geometry import MultiPolygon, box

from core.cutline_core import CutlineResult, OffsetSpec
from core.svg_export import export_svg


def _result(w=3000, h=1500, dpi=300.0):
    mp = MultiPolygon([box(300, 300, 600, 600)])
    return CutlineResult(dpi=dpi, width_px=w, height_px=h, design=mp, offsets={"cut": mp},
                         offset_mm=OffsetSpec(1, 2, 3))


def _export(res, **kw):
    fd, path = tempfile.mkstemp(suffix=".svg")
    os.close(fd)
    try:
        export_svg(res, path, **kw)
        return open(path, encoding="utf-8").read()
    finally:
        os.remove(path)


def test_png_svg_has_real_size_in_mm():
    txt = _export(_result())
    w, h = map(float, re.search(r'width="([\d.]+)mm" height="([\d.]+)mm"', txt).groups())
    # 3000px / 300dpi = 10in = 254mm (예전엔 단위가 없어 3000pt = 1058mm로 열렸음)
    assert abs(w - 254.0) < 1e-3 and abs(h - 127.0) < 1e-3


def test_ai_svg_matches_artboard_and_image_position():
    placement = {"page_w_pt": 1315.28, "page_h_pt": 892.91, "x0_pt": 74.6, "y0_pt": 66.2,
                 "pt_per_px": 0.24, "placements": 1}
    txt = _export(_result(), placement=placement)
    w, h = map(float, re.search(r'width="([\d.]+)mm" height="([\d.]+)mm"', txt).groups())
    assert abs(w - 1315.28 * 25.4 / 72) < 1e-3 and abs(h - 892.91 * 25.4 / 72) < 1e-3
    tx, ty, sc = map(float, re.search(r'translate\(([\d.]+),([\d.]+)\) scale\(([\d.]+)\)', txt).groups())
    # 이미지 픽셀 (300, 300)은 대지에서 (74.6 + 300*0.24)pt 자리
    k = 25.4 / 72
    assert abs((tx + 300 * sc) - (74.6 + 300 * 0.24) * k) < 1e-3
    assert abs((ty + 300 * sc) - (66.2 + 300 * 0.24) * k) < 1e-3
    # 선 두께는 0.75pt 그대로
    sw = float(re.search(r'<g id="cut"[^>]*><path[^>]*stroke-width="([\d.]+)"', txt).group(1))
    assert abs(sw * sc - 0.75 * k) < 1e-3


def test_cut_is_magenta_line_only_without_fill():
    """2026-09-29(멍푸: "칼선은 마젠타 100으로 선으로 있어야해 왜 안쪽에 색이 있어? 있으면 안돼")."""
    txt = _export(_result())
    assert "artwork_reference" not in txt
    assert 'fill-opacity' not in txt
    cut = re.search(r'<g id="cut"[^>]*><path[^>]*/>', txt).group(0)
    assert 'fill="none"' in cut and 'stroke="#EC008C"' in cut
