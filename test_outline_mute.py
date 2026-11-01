"""2026-11-01(멍푸: 투명 배경 스티커 한 장 -- "이건 칼선이 무테지만 외곽에 있어야해"): 투명 배경 스티커 한 장은
무테여도 그림 안쪽 1.2mm가 아니라 그림 외곽선 위로 자른다(가는 꼭지·귀 끝이 잘려 나가지 않게).
여러 장이 놓인 시트의 무테는 예전처럼 그림 안쪽."""
import cv2
import numpy as np

from core import white_layer as wl

DPI = 300.0


def _sticker(h=600, w=600):
    """둥근 몸통 + 가는 꼭지(폭 약 0.8mm) 한 덩어리."""
    m = np.zeros((h, w), np.uint8)
    cv2.circle(m, (300, 340), 180, 1, -1)
    cv2.line(m, (300, 170), (380, 60), 1, 10)
    return m.astype(bool)


def _fill(cuts, shape):
    out = np.zeros(shape, np.uint8)
    for g in cuts.geoms:
        cv2.fillPoly(out, [np.asarray(g.exterior.coords).round().astype(np.int32)], 1)
    return out.astype(bool)


def test_single_piece():
    m = _sticker()
    assert wl.is_single_piece(m)
    m2 = m.copy()
    m2[5:7, 5:7] = True                       # 티끌은 세지 않음
    assert wl.is_single_piece(m2)
    m3 = m.copy()
    cv2.circle(m3.view(np.uint8), (60, 540), 50, 1, -1)
    assert not wl.is_single_piece(m3)        # 따로 떨어진 그림이 있으면 한 장이 아님
    assert not wl.is_single_piece(np.zeros((10, 10), bool))


def test_outline_mute_keeps_whole_art():
    m = _sticker()
    rgb = np.full(m.shape + (3,), 255, np.uint8)
    rgb[m] = (230, 170, 90)
    cuts, _sep, ny, nm, _ = wl.auto_style_cuts(rgb, m, DPI, 1.2, outline_mute=True)
    assert nm == 1 and len(cuts.geoms) == 1
    inside = _fill(cuts, m.shape)
    assert (m & ~inside).sum() / m.sum() < 0.002          # 그림이 칼선 밖으로 거의 안 나감(꼭지 포함)
    assert (inside & ~m).sum() / m.sum() < 0.01           # 배경을 거의 안 먹음(외곽선 위)


def test_default_mute_is_still_inside():
    m = _sticker()
    rgb = np.full(m.shape + (3,), 255, np.uint8)
    rgb[m] = (230, 170, 90)
    cuts, _sep, _ny, nm, _ = wl.auto_style_cuts(rgb, m, DPI, 1.2)
    inside = _fill(cuts, m.shape)
    assert nm == 1
    assert (inside & ~m).sum() == 0                       # 예전 무테: 항상 그림 안쪽
    assert (m & ~inside).sum() / m.sum() > 0.03
