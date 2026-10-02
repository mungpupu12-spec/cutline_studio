"""2026-10-02(멍푸: "수정 하는 거 너무 어려워, 어도비 패스 기능 같은거로 만들어" -- 점 끌기·삭제와
어도비 핸들까지): 칼선 패스 편집의 계산(core.bezier_path)과 화면 동작(gui.path_editor)을 확인한다.

곡선 맞춤·점 넣기/지우기는 계산이 맞는지 보는 시험이라 도형(원·사각형)을 직접 만든다(칼선 품질을
판단하는 시험이 아님). 화면 시험의 그림은 저장소에 이미 있는 samples/ring.png만 쓴다."""
import os
import sys
import tempfile

import numpy as np
from shapely.geometry import MultiPolygon, Point, Polygon, box

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import bezier_path as bz  # noqa: E402

SAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples")


def _circle(cx, cy, r, n=720):
    return Point(cx, cy).buffer(r, quad_segs=n // 4)


def test_fit_circle_few_nodes_and_close_to_original():
    P = _circle(300, 300, 120)
    path = bz.fit_polygon(P, tol=1.2)
    assert 4 <= len(path) <= 12, len(path)
    Q = path.to_polygon(step=1.0)
    assert P.exterior.hausdorff_distance(Q.exterior) < 2.0
    assert all(n.smooth for n in path.nodes)  # 원에는 뾰족한 점이 없음


def test_fit_rectangle_keeps_corners_sharp():
    P = box(100, 100, 400, 260)
    path = bz.fit_polygon(P, tol=1.0)
    Q = path.to_polygon(step=1.0)
    assert P.exterior.hausdorff_distance(Q.exterior) < 3.0
    sharp = [n for n in path.nodes if not n.smooth]
    assert len(sharp) == 4, len(sharp)


def test_insert_keeps_shape_and_delete_removes_node():
    path = bz.fit_polygon(_circle(200, 200, 80), tol=1.0)
    before = path.to_polygon(step=0.5)
    n0 = len(path)
    seg, t, _d = path.nearest((280, 200))
    idx = path.insert_at(seg, t)
    assert len(path) == n0 + 1
    assert np.linalg.norm(path.nodes[idx].p - np.array([280, 200])) < 2.0
    after = path.to_polygon(step=0.5)
    assert before.symmetric_difference(after).area < 0.002 * before.area
    assert path.delete(idx) and len(path) == n0


def test_smooth_handle_stays_collinear_and_alt_breaks_it():
    path = bz.fit_polygon(_circle(200, 200, 80), tol=1.0)
    nd = path.nodes[0]
    path.move_handle(0, "out", nd.h_out[0] + 30, nd.h_out[1] + 10)
    v_in, v_out = nd.h_in - nd.p, nd.h_out - nd.p
    cross = v_in[0] * v_out[1] - v_in[1] * v_out[0]
    assert abs(cross) / (np.linalg.norm(v_in) * np.linalg.norm(v_out)) < 1e-6
    assert np.dot(v_in, v_out) < 0
    old_in = nd.h_in.copy()
    path.move_handle(0, "out", nd.p[0], nd.p[1] - 40, break_smooth=True)
    assert not nd.smooth and np.allclose(nd.h_in, old_in)


def test_svg_export_writes_curves_with_few_points():
    from core.cutline_core import CutlineResult, OffsetSpec
    from core.svg_export import export_svg

    mp = MultiPolygon([_circle(300, 300, 150)])
    res = CutlineResult(dpi=300.0, width_px=640, height_px=640, design=mp, offsets={"cut": mp},
                        offset_mm=OffsetSpec(1, 2, 3))
    fd, path = tempfile.mkstemp(suffix=".svg")
    os.close(fd)
    try:
        export_svg(res, path)
        txt = open(path, encoding="utf-8").read()
        d = txt.split('<g id="cut"')[1].split('d="')[1].split('"')[0]
        assert " C " in d and " L " not in d
        assert d.count("C ") <= 16
    finally:
        os.remove(path)


class _Ev:
    def __init__(self, x, y, state=0):
        self.x, self.y, self.state = x, y, state


def test_path_editor_drag_node_apply_same_undo_and_delete():
    from core.cutline_core import CutlineResult, OffsetSpec
    from gui.app import CutLineApp

    app = CutLineApp()
    app.withdraw()
    if not hasattr(app, "preview_canvas"):
        app._build_layout()
    try:
        ring = os.path.join(SAMPLES, "ring.png")
        app.input_path.set(ring)
        from PIL import Image

        W, H = Image.open(ring).size
        a = _circle(W * 0.3, H * 0.5, min(W, H) * 0.15)
        b = _circle(W * 0.7, H * 0.5, min(W, H) * 0.15)   # a와 같은 모양(반복 칸)
        c = box(W * 0.1, H * 0.05, W * 0.2, H * 0.15)
        items = []
        for g in (a, b, c):
            mp = MultiPolygon([g])
            items.append(CutlineResult(dpi=300.0, width_px=W, height_px=H, design=mp, offsets={"cut": mp},
                                       offset_mm=OffsetSpec(1, 2, 3)))
        app._accumulated = items
        from core.accumulate import combine_results

        app._last_result = combine_results(items)
        app.update()
        app._pe_enter()
        app.update()
        assert app._pe_active and len(app._pe_items) == 3

        def ev(x, y, state=0):
            cx, cy = app._pe_to_canvas(x, y)
            return _Ev(cx - app.preview_canvas.canvasx(0), cy - app.preview_canvas.canvasy(0), state)

        # 칼선 a 클릭 -> 고름(기준점 보임)
        top_a = (a.centroid.x, a.bounds[1])
        app._pe_on_press(ev(*top_a))
        app._pe_on_release(ev(*top_a))
        assert app._pe_sel is not None and app._pe_path is not None and len(app._pe_path) >= 3
        # 기준점 하나를 바깥으로 끌기
        nd = app._pe_path.nodes[0]
        px, py = float(nd.p[0]), float(nd.p[1])
        cx, cy = a.centroid.x, a.centroid.y
        vx, vy = (px - cx), (py - cy)
        L = (vx * vx + vy * vy) ** 0.5
        tx, ty = px + vx / L * 25, py + vy / L * 25
        area_a0 = a.area
        app._pe_on_press(ev(px, py))
        app._pe_on_drag(ev((px + tx) / 2, (py + ty) / 2))
        app._pe_on_drag(ev(tx, ty))
        app._pe_on_release(ev(tx, ty))
        new_a = app._accumulated[0].offsets["cut"].geoms[0]
        new_b = app._accumulated[1].offsets["cut"].geoms[0]
        assert new_a.area > area_a0 * 1.01
        assert new_a.distance(Point(tx, ty)) < 2.0
        # 같은 그림 모두 적용: b도 같은 자리(옮긴 만큼)가 바뀜
        dx = b.centroid.x - a.centroid.x
        assert new_b.distance(Point(tx + dx, ty)) < 2.5
        assert app._accumulated[2].offsets["cut"].geoms[0].equals(c)  # 다른 모양은 그대로
        assert app._last_result.offsets["cut"].area > (a.area + b.area + c.area)
        # 되돌리기
        app._pe_undo_last()
        assert abs(app._accumulated[0].offsets["cut"].geoms[0].area - area_a0) < 1e-6
        assert abs(app._accumulated[1].offsets["cut"].geoms[0].area - b.area) < 1e-6
        # 선 위 더블클릭 -> 점 추가(작은 사각형이라 확대해서)
        app._preview_zoom = 4.0
        app._render_preview()
        app._pe_select(app._pe_items.index((2, 0)))
        n0 = len(app._pe_path)
        mid_top = ((c.bounds[0] + c.bounds[2]) / 2, c.bounds[1])
        app._pe_on_double(ev(*mid_top))
        assert len(app._pe_path) == n0 + 1
        # 점을 고르지 않고 Delete -> 칼선 전체 삭제(같은 모양 없음 -> 1개)
        app._pe_sel_node = None
        app._pe_delete_key()
        cut_c = app._accumulated[2].offsets["cut"]
        assert cut_c.is_empty
        app._pe_exit()
        assert not app._pe_active and app._pe_toolbar.winfo_manager() == ""
    finally:
        app.destroy()
