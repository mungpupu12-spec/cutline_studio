"""흰 옷이 배경으로 채워져 그림이 쪼개지던 문제(2026-11-01) -- core.multi_design._restore_white_bridges.

크림색 벽 -> 연분홍 흰 띠 -> 흰 셔츠로 이웃 색 차이가 작게 이어지면 배경 채우기가 셔츠까지 들어가
머리·팔·치마가 따로 잡혔다. 그림 조각 둘 이상에 닿은 순백 덩어리는 그림으로 되돌린다."""
import cv2
import numpy as np
from shapely.geometry import box

from core.multi_design import (
    detect_elements_by_background_flood_px,
    merge_boxes_of_one_art_piece_px,
)

CREAM = (221, 238, 249)    # BGR
BAND = (244, 244, 251)
FLOOR = (223, 221, 220)
WHITE = (255, 255, 255)
ORANGE = (90, 165, 230)
GREEN = (80, 130, 50)


def _scene(path):
    """벽·흰 띠·바닥 위에 머리(주황)-흰 셔츠-치마(초록)가 세로로 이어진 인물, 셔츠 양옆에 팔."""
    img = np.zeros((600, 600, 3), np.uint8)
    img[:, :] = CREAM
    img[290:350, :] = BAND      # 흰 띠(칸 양쪽 끝까지, 실제 시트처럼 칸 높이의 약 1/10)
    img[350:, :] = FLOOR
    cv2.circle(img, (300, 200), 110, ORANGE, -1)          # 머리
    cv2.rectangle(img, (220, 300), (380, 420), WHITE, -1)  # 흰 셔츠(띠에 닿음)
    cv2.ellipse(img, (200, 360), (22, 55), 0, 0, 360, ORANGE, -1)  # 왼팔
    cv2.ellipse(img, (400, 360), (22, 55), 0, 0, 360, ORANGE, -1)  # 오른팔
    cv2.rectangle(img, (225, 420), (375, 500), GREEN, -1)  # 치마
    cv2.imwrite(str(path), img)


def test_white_shirt_between_parts_is_not_background(tmp_path):
    p = tmp_path / "scene.png"
    _scene(p)
    objs = detect_elements_by_background_flood_px(str(p), (0, 0, 600, 600))
    shirt = box(240, 320, 360, 400)
    holder = [o for o in objs if o.intersection(shirt).area > 0.8 * shirt.area]
    assert holder, "흰 셔츠가 배경으로 채워졌다"
    fig = holder[0]
    assert fig.contains(box(280, 150, 320, 250).centroid)   # 머리
    assert fig.intersects(box(240, 440, 360, 490))          # 치마


def test_parts_split_by_white_shirt_are_merged(tmp_path):
    p = tmp_path / "scene.png"
    _scene(p)
    parts = [(190, 90, 410, 300), (178, 305, 222, 415), (378, 305, 422, 415), (225, 425, 375, 500)]
    merged = merge_boxes_of_one_art_piece_px(str(p), (0, 0, 600, 600), parts)
    assert len(merged) == 1
    x0, y0, x1, y1 = merged[0]
    assert x0 <= 180 and y0 <= 95 and x1 >= 420 and y1 >= 495


def test_white_part_touching_one_figure_stays_background(tmp_path):
    """흰 자리가 그림 하나에만 닿으면(그림 조각을 잇지 않으면) 예전처럼 배경으로 둔다."""
    img = np.zeros((600, 600, 3), np.uint8)
    img[:, :] = CREAM
    img[290:350, :] = BAND
    img[350:, :] = FLOOR
    cv2.circle(img, (300, 200), 110, ORANGE, -1)            # 머리만
    cv2.rectangle(img, (220, 300), (380, 420), WHITE, -1)   # 머리 하나에만 닿은 흰 자리
    p = tmp_path / "one.png"
    cv2.imwrite(str(p), img)
    objs = detect_elements_by_background_flood_px(str(p), (0, 0, 600, 600))
    patch = box(240, 330, 360, 410)
    assert objs
    assert all(o.intersection(patch).area < 0.2 * patch.area for o in objs)
