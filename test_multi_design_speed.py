"""2026-10-25: 도안 자동 인식(①) 속도 개선(덩어리마다 시트 전체 대신 잘라낸 범위만 계산) -- 결과는 그대로여야 한다.
알고리즘 확인용 합성 이미지."""
import os
import tempfile
import time

import cv2
import numpy as np

from core.multi_design import detect_design_bboxes_px


def _sheet():
    rng = np.random.default_rng(7)
    h, w = 2400, 1800
    img = np.full((h, w, 3), 255, np.uint8)
    boxes = []
    for r in range(6):
        for c in range(5):
            x0, y0 = 80 + c * 340, 80 + r * 380
            bw, bh = int(rng.integers(160, 260)), int(rng.integers(160, 300))
            col = tuple(int(v) for v in rng.integers(20, 200, 3))
            if (r + c) % 3 == 0:
                cv2.ellipse(img, (x0 + bw // 2, y0 + bh // 2), (bw // 2, bh // 2), 0, 0, 360, col, -1)
            else:
                cv2.rectangle(img, (x0, y0), (x0 + bw, y0 + bh), col, -1)
            boxes.append((x0, y0, x0 + bw, y0 + bh))
    # 서로 닿은 두 도안(붙은 덩어리 나누기 경로)
    cv2.circle(img, (600, 2300), 70, (40, 90, 160), -1)
    cv2.circle(img, (735, 2300), 70, (160, 60, 40), -1)
    return img, boxes


def test_detect_boxes_finds_every_design_quickly():
    img, boxes = _sheet()
    d = tempfile.mkdtemp()
    p = os.path.join(d, "sheet.png")
    cv2.imwrite(p, img)
    t = time.time()
    found = detect_design_bboxes_px(p)
    took = time.time() - t
    assert took < 30
    for x0, y0, x1, y1 in boxes:
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        hit = [b for b in found if b[0] <= cx <= b[2] and b[1] <= cy <= b[3]]
        assert len(hit) == 1, (x0, y0, hit)
        b = hit[0]
        assert abs(b[0] - x0) < 25 and abs(b[2] - x1) < 25 and abs(b[1] - y0) < 25 and abs(b[3] - y1) < 25
    # 결과가 매번 같다
    assert [tuple(b) for b in detect_design_bboxes_px(p)] == [tuple(b) for b in found]
