"""2026-10-27: 한 색 판(씰스티커 짙은 판) 위 그림 여러 개는 판 통째 네모가 아니라 판 위 그림마다
(알고리즘 확인용 합성 이미지 -- 칼선 품질은 실제 파일로 확인함).
  - 판 위 그림마다 모양(제목 글자 줄은 뺌), 장면 카드는 카드 모양 그대로.
  - 그림 둘레의 짙은 판 바탕을 외곽선(유테)으로 잘못 보지 않게 판 바탕 자리를 알려준다.
  - ① 칸도 판 위 그림마다.
  - 도무송 칸은 판 쪽으로 넓혀 그림을 감싸고, 카드는 그대로."""
import os
import tempfile

import cv2
import numpy as np
from PIL import Image

from core import white_layer as wl
from core.interactive_cutline import domusong_region_on_panel
from core.multi_design import _snap_boxes_to_solid_cards

DPI = 300.0
NAVY = (30, 38, 84)


def _panel(w=900, h=1600, title=True):
    """짙은 판 위에 그림 6개 + 작은 동그라미 3개(한 줄) + 세로 제목 글자 줄."""
    img = np.full((h, w, 3), NAVY, np.uint8)
    figs = [((250, 250), 140, (230, 160, 70)), ((620, 280), 120, (170, 210, 220)), ((280, 700), 150, (60, 170, 80)),
            ((640, 760), 130, (240, 210, 60)), ((260, 1200), 140, (230, 150, 60)), ((620, 1250), 150, (230, 200, 70))]
    for (cx, cy), r, col in figs:
        cv2.circle(img, (cx, cy), r, col, -1)
        cv2.circle(img, (cx - r // 3, cy - r // 4), r // 6, (20, 20, 20), -1)       # 눈
    for k, col in enumerate([(200, 60, 50), (230, 180, 50), (30, 80, 150)]):      # 동그라미 스티커 3개(1.3mm 간격)
        cv2.circle(img, (72, 470 + k * 100), 42, col, -1)
    if title:
        for k in range(8):                                                       # 세로 제목 글자(4mm 폭, 1.4mm 간격)
            cv2.rectangle(img, (835, 60 + k * 64), (880, 108 + k * 64), (240, 210, 60), -1)
            cv2.rectangle(img, (850, 75 + k * 64), (865, 95 + k * 64), NAVY, -1)
    return img, len(figs) + 3


def test_panel_figures_mask_cuts_each_figure_not_the_panel():
    img, n_fig = _panel()
    m = wl.panel_figures_mask(img, DPI)
    assert m is not None
    n = cv2.connectedComponents(m.astype(np.uint8), connectivity=8)[0] - 1
    assert n == n_fig                                                           # 제목 글자 줄은 빠짐
    assert not m[100, 857]                                                      # 제목 글자
    assert m[250, 250] and m[470, 72]                                          # 그림, 작은 동그라미
    assert not m[50, 50]                                                        # 판 바탕
    assert m.mean() < 0.6


def test_plain_card_with_one_figure_is_not_a_panel():
    img = np.full((600, 600, 3), NAVY, np.uint8)
    cv2.circle(img, (300, 300), 200, (230, 160, 70), -1)
    assert wl.panel_figures_mask(img, DPI) is None                              # 그림 1개 -- 카드 방식 그대로


def test_scene_card_is_not_a_panel():
    img = np.full((600, 600, 3), (240, 235, 220), np.uint8)
    img[350:] = (150, 40, 30)                                                   # 바닥
    cv2.rectangle(img, (30, 40), (220, 330), (60, 90, 200), -1)                 # 창문(카드 끝까지)
    img[:, :12] = (60, 90, 200)
    for x in range(80, 600, 110):
        cv2.circle(img, (x, 450), 40, (230, 160, 70), -1)
    assert wl.panel_figures_mask(img, DPI) is None


def test_replace_cards_marks_panel_background_for_outline_check():
    img, n_fig = _panel()
    sheet = np.zeros((1800, 1100, 3), np.uint8)                                 # 투명 시트(검정으로 보임)
    sheet[100:1700, 100:1000] = img
    mask = np.zeros(sheet.shape[:2], bool)
    mask[100:1700, 100:1000] = True
    info = {}
    new, n = wl.replace_cards_with_figures(sheet, mask, DPI, info=info)
    assert n == 1 and info["n_panels"] == 1
    assert cv2.connectedComponents(new.astype(np.uint8), connectivity=8)[0] - 1 == n_fig
    bg = info["panel_bg"]
    assert bg[150, 150] and not bg[350, 350]                                    # 판 바탕 / 그림 안쪽
    # 판 바탕을 흰색으로 보면 그림 가장자리에 외곽선이 없다(무테)
    rgb = sheet.copy()
    rgb[bg] = 255
    cuts, _sep, ny, nm, _thin = wl.auto_style_cuts(rgb, new, DPI, 1.2)
    assert ny == 0 and nm == n_fig


def test_detect_boxes_on_panel_are_per_figure():
    img, n_fig = _panel()
    sheet = np.full((1800, 1100, 3), 255, np.uint8)
    sheet[100:1700, 100:1000] = img
    boxes = [(100, 100, 1000, 1700)]                                            # 판 하나로 잡힌 칸
    boxes += [(300, 300, 500, 500), (600, 600, 800, 800), (300, 1100, 500, 1300), (650, 1150, 850, 1350)]
    out = _snap_boxes_to_solid_cards(sheet, boxes)
    assert len(out) == n_fig
    assert all((b[2] - b[0]) < 400 for b in out)


def test_domusong_region_grows_on_panel_but_not_on_card():
    d = tempfile.mkdtemp()
    # 판 위 그림 칸: 판 쪽으로 넓어진다
    img, _ = _panel(title=False)
    sheet = np.full((1800, 1100, 3), 255, np.uint8)
    sheet[100:1700, 100:1000] = img
    p1 = os.path.join(d, "_panel.png")
    Image.fromarray(sheet).save(p1)
    box = (210, 210, 490, 490)                                                  # 첫 그림(지름 280px)
    reg = domusong_region_on_panel(p1, box, (100, 100, 1000, 1700), DPI, 3.0)
    assert reg[0] < box[0] and reg[2] > box[2]
    # 흰 시트 위 카드 칸: 그대로
    card = np.full((1000, 1000, 3), 255, np.uint8)
    card[200:800, 200:800] = (200, 210, 100)
    p2 = os.path.join(d, "_card.png")
    Image.fromarray(card).save(p2)
    assert tuple(domusong_region_on_panel(p2, (198, 198, 802, 802), (0, 0, 1000, 1000), DPI, 3.0)) == (
        198, 198, 802, 802)


def test_domusong_region_unchanged_for_part_of_one_picture():
    # 한 장짜리 그림 안의 부분 칸(둘레가 여러 색) -- 넓히면 이웃 칸 도무송과 겹친다
    d = tempfile.mkdtemp()
    img = np.full((1000, 1000, 3), (150, 210, 210), np.uint8)
    cv2.circle(img, (500, 500), 380, (240, 170, 70), -1)
    cv2.rectangle(img, (380, 150), (620, 420), (0, 160, 200), -1)
    cv2.rectangle(img, (420, 560), (580, 760), (250, 250, 250), -1)
    p = os.path.join(d, "_one.png")
    Image.fromarray(img).save(p)
    box = (420, 200, 580, 380)
    assert tuple(domusong_region_on_panel(p, box, (0, 0, 1000, 1000), DPI, 3.0)) == box
