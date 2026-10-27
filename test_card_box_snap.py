"""2026-10-27: 꽉 찬 사각형 카드의 도안 칸은 카드 경계 하나로(알고리즘 확인용 합성 이미지).
실제 시트에서 왼쪽 끝 카드가 두 칸으로 쪼개지고 다른 카드 칸도 한쪽으로 밀려, 도무송 칼선이 그림 모양을 따라
작게 생기거나 카드와 어긋났다."""
import numpy as np
import cv2

from core.multi_design import _snap_boxes_to_solid_cards


def _sheet():
    h, w = 900, 1400
    img = np.full((h, w, 3), 255, np.uint8)
    cards = [(10, 20, 410, 420), (450, 20, 850, 420)]
    for x0, y0, x1, y1 in cards:
        img[y0:y1, x0:x1] = (190, 210, 90)
        for cx in range(x0 + 30, x1 - 10, 70):              # 카드 안 흰 꽃무늬(배경색과 같은 색)
            for cy in range(y0 + 30, y1 - 10, 70):
                cv2.circle(img, (cx, cy), 14, (255, 255, 255), -1)
    # 판(패널): 짙은 바탕 위 흰 테두리 스티커 여러 개
    img[480:880, 20:1380] = (40, 40, 90)
    stickers = []
    for i in range(6):
        x0 = 60 + i * 215
        cv2.rectangle(img, (x0, 540), (x0 + 150, 700), (255, 255, 255), -1)
        cv2.rectangle(img, (x0 + 15, 555), (x0 + 135, 685), (220, 80, 80), -1)
        stickers.append((x0 - 5, 535, x0 + 155, 705))
    return img, cards, stickers


def test_split_and_shifted_card_boxes_snap_to_card():
    img, cards, stickers = _sheet()
    boxes = [(0, 8, 110, 430), (106, 8, 420, 431),           # 카드 하나가 둘로 쪼개짐
             (462, 8, 865, 431)] + stickers                    # 한쪽으로 밀린 칸
    out = _snap_boxes_to_solid_cards(img, boxes, pad_px=2)
    for x0, y0, x1, y1 in cards:
        hit = [b for b in out if b[0] <= (x0 + x1) / 2 <= b[2] and b[1] <= (y0 + y1) / 2 <= b[3]]
        assert len(hit) == 1
        assert hit[0] == (x0 - 2, y0 - 2, x1 + 2, y1 + 2)
    for s in stickers:                                          # 판 위 스티커 칸은 그대로
        assert s in out
    assert len(out) == 2 + len(stickers)


def test_no_change_without_cards():
    img = np.full((400, 600, 3), 255, np.uint8)
    cv2.circle(img, (200, 200), 100, (30, 120, 200), -1)       # 사각형 카드가 아닌 그림
    boxes = [(95, 95, 305, 305)]
    assert _snap_boxes_to_solid_cards(img, boxes) == [(95, 95, 305, 305)]
