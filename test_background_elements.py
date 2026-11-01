"""2026-11-01(멍푸: "배경요소는 칼선으로 지정하는거 선택할 수 있게 창을 띄워서 생성/미생성으로 구분지어"): 모서리 꺾쇠
같은 가는 장식선과 그림 뒤 한 색 배경 판(의자 등받이)을 찾고, 미생성이면 장식선 칼선은 빼고 배경 판은 판을 뺀 그림만
같은 간격으로 자른다. 보통 그림·카드(그림이 판 안에 다 들어간 것)는 건드리지 않는다."""
import cv2
import numpy as np
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.ops import unary_union

from core import background_elements as be
from core.cutline_core import CutlineResult, OffsetSpec

DPI = 300.0
MM = DPI / 25.4


def _px(v):
    return int(round(v * MM))


def _sheet():
    """연한 바탕에 (1) 가는 L 꺾쇠 (2) 청록 판 뒤에 선 주황 원(판 아래로 나옴) (3) 주황 원 하나 (4) 판 안에 다 들어간 카드."""
    h, w = _px(80), _px(150)
    img = np.full((h, w, 3), (245, 235, 220), np.uint8)
    t = max(2, _px(0.8))
    cv2.line(img, (_px(5), _px(5)), (_px(5), _px(17)), (20, 20, 20), t)          # 꺾쇠 세로
    cv2.line(img, (_px(5), _px(5)), (_px(15), _px(5)), (20, 20, 20), t)          # 꺾쇠 가로
    panel = (_px(30), _px(10), _px(60), _px(40))
    cv2.rectangle(img, panel[:2], panel[2:], (40, 168, 152), -1)                  # 청록 판
    cv2.circle(img, (_px(45), _px(45)), _px(14), (232, 168, 88), -1)              # 판 아래로 나온 그림
    cv2.circle(img, (_px(90), _px(40)), _px(12), (232, 168, 88), -1)              # 보통 그림
    cv2.rectangle(img, (_px(115), _px(15)), (_px(145), _px(60)), (40, 120, 200), -1)   # 카드
    cv2.circle(img, (_px(130), _px(37)), _px(8), (250, 250, 250), -1)             # 카드 안 그림
    return img


def _circle(cx, cy, r):
    return Polygon([(cx + r * np.cos(a), cy + r * np.sin(a)) for a in np.linspace(0, 2 * np.pi, 120)])


def _items():
    g = 1.2 * MM
    bracket = unary_union([box(_px(5) - 5, _px(5) - 5, _px(5) + 5, _px(17)), box(_px(5) - 5, _px(5) - 5, _px(15), _px(5) + 5)])
    fig = unary_union([box(_px(30), _px(10), _px(60), _px(40)), _circle(_px(45), _px(45), _px(14))])
    plain = _circle(_px(90), _px(40), _px(12))
    card = box(_px(115), _px(15), _px(145), _px(60))
    out = []
    for d in (bracket, fig, plain, card):
        out.append(CutlineResult(dpi=DPI, width_px=_px(150), height_px=_px(80), design=MultiPolygon([d] if d.geom_type == "Polygon" else list(d.geoms)),
                                 offsets={"cut": MultiPolygon([d.buffer(g)])}, offset_mm=OffsetSpec(1.2, 1.2, 1.2)))
    return out


def test_find_deco_and_panel_only():
    img, items = _sheet(), _items()
    found = be.find(items, img, DPI)
    kinds = {i: k for i, _j, k, _f in found}
    assert kinds == {0: be.DECO, 1: be.PANEL}, kinds
    assert be.counts(found) == (1, 1)


def test_apply_removes_deco_and_cuts_figure_without_panel():
    img, items = _sheet(), _items()
    old = items[1].offsets["cut"].geoms[0]
    n = be.apply(items, be.find(items, img, DPI))
    assert n == 2
    assert len(items[0].offsets["cut"].geoms) == 0                       # 장식선 칼선은 없음
    new = items[1].offsets["cut"].geoms[0]
    assert new.area < 0.8 * old.area                                      # 판을 뺐다
    assert new.bounds[1] > _px(10) + _px(5)                               # 판 윗부분은 칼선 밖
    assert new.contains(_circle(_px(45), _px(45), _px(14)).buffer(-_px(1)))  # 그림은 칼선 안
    assert len(items[2].offsets["cut"].geoms) == 1 and len(items[3].offsets["cut"].geoms) == 1   # 나머지는 그대로
