"""
2026-09-11(51차) 회귀 테스트: "똑같은 도안 여러 개일 때 하나는 섬세하게
작업하고 나머지는 복붙하라고 했는데 아예 각각 엉망으로 인식하고 있어",
"도안 인식이 엉망이고 사각형 칼선까지 생겨" 피드백.

50차(칸 안 낱개 요소 쪼개기)가 반복 칸 그룹핑과 충돌하던 버그를 재현하고
고쳤는지 확인한다: 완전히 똑같은 패널이 여러 번 반복되는 시트에서
- 대표 패널 하나에만 실제로 쪼개기(split_cell_into_sub_elements_px)가
  호출돼야 하고(반복 패널마다 따로 다시 계산하면 안 됨 -- 이게 "각각
  엉망으로/제각각 인식"의 원인이었음),
- 결과 그룹 개수 = 대표 패널에서 찾은 조각 개수와 같아야 하며,
- 각 그룹의 반복 인스턴스 쪽 조각 bbox가 실제로 그 패널의 같은 내용
  (같은 상대 위치) 위에 정확히 놓여야 한다.

전부 합성 이미지만 사용(순수 로직 검증 -- 실제 파일 확인은 재빌드 후
그녀가 직접 해야 함)."""

import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, "/root/cutline_studio")
os.environ.setdefault("CUTLINE_LICENSE_DEV_BYPASS", "1")

from PIL import Image, ImageDraw

import core.multi_design as md
from core.multi_design import detect_repeat_aware_sub_element_boxes_px


PANEL_W, PANEL_H = 400, 400
GAP_BETWEEN_PANELS = 100
N_PANELS = 3


def _draw_panel(d, ox, oy):
    """한 패널 안에 서로 뚜렷이 떨어진 3개 스티커(강아지 얼굴/사람/하트에
    해당) -- 50차 성공 케이스와 같은 배치."""
    d.ellipse((ox + 20, oy + 20, ox + 140, oy + 140), fill=(200, 150, 100))  # 강아지 얼굴 자리
    d.rectangle((ox + 260, oy + 20, ox + 380, oy + 180), fill=(120, 180, 220))  # 사람 자리
    d.polygon(
        [(ox + 160, oy + 300), (ox + 240, oy + 300), (ox + 200, oy + 380)],
        fill=(230, 60, 90),
    )  # 하트 자리


def _make_repeated_sheet(path):
    total_w = N_PANELS * PANEL_W + (N_PANELS - 1) * GAP_BETWEEN_PANELS
    img = Image.new("RGB", (total_w, PANEL_H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    cells = []
    for i in range(N_PANELS):
        ox = i * (PANEL_W + GAP_BETWEEN_PANELS)
        _draw_panel(d, ox, 0)
        cells.append((ox, 0, ox + PANEL_W, PANEL_H))
    img.save(path)
    return cells


def test_split_runs_only_once_per_repeat_group():
    """가장 중요한 확인: 실제로 비용이 드는 쪼개기(split_cell_into_sub_
    elements_px)가 패널이 3번 반복돼도 딱 1번만 호출돼야 한다(50차 버그는
    이게 패널마다, 즉 3번 따로 호출되면서 매번 다르게 나온 것)."""
    tmp = os.path.join(tempfile.gettempdir(), "_test_repeat_aware_sheet.png")
    cells = _make_repeated_sheet(tmp)
    call_count = {"n": 0}
    real_split = md.split_cell_into_sub_elements_px

    def counting_split(*args, **kwargs):
        call_count["n"] += 1
        return real_split(*args, **kwargs)

    try:
        with mock.patch.object(md, "split_cell_into_sub_elements_px", side_effect=counting_split):
            boxes, groups = detect_repeat_aware_sub_element_boxes_px(tmp, cells)
    finally:
        os.remove(tmp)

    print(f"split_cell_into_sub_elements_px 호출 횟수: {call_count['n']} (기대: 1, 패널 3개 중 대표만)")
    assert call_count["n"] == 1, "반복 패널인데도 쪼개기가 패널마다 따로 호출됨(51차 버그 재발)"
    print("[OK] 반복 패널 3개 중 대표 패널 1개에만 쪼개기가 실행됨")
    return boxes, groups


def test_group_count_matches_representative_split_and_all_repeated():
    tmp = os.path.join(tempfile.gettempdir(), "_test_repeat_aware_sheet2.png")
    cells = _make_repeated_sheet(tmp)
    try:
        boxes, groups = detect_repeat_aware_sub_element_boxes_px(tmp, cells)
    finally:
        os.remove(tmp)

    print(f"찾은 그룹(조각 종류) 개수: {len(groups)} (기대: 3 -- 강아지 얼굴/사람/하트)")
    assert len(groups) == 3, f"패널 하나 안 조각 3개가 정확히 3개 그룹으로 나오지 않음: {groups}"
    for g in groups:
        print(f"  그룹 크기: {len(g)} (기대: {N_PANELS} -- 패널마다 하나씩)")
        assert len(g) == N_PANELS, f"그룹 하나가 반복 패널 수({N_PANELS})만큼 채워지지 않음: {g}"
    print("[OK] 조각 3종류 x 반복 패널 3개 = 그룹 3개, 각 그룹마다 인스턴스 3개")


def test_repeated_instance_boxes_land_on_matching_content():
    """복제된(2번째/3번째 패널) 조각 bbox가 실제로 그 패널의 "같은" 스티커
    위에 놓이는지, 잘못된 곳(다른 스티커나 배경)을 가리키지 않는지 픽셀
    내용으로 확인 -- 축소본 평균 절대 차이가 충분히 작아야 함(같은
    스티커라면 두 패널이 완전히 동일한 그림이므로 차이가 거의 0에 가까워야
    함)."""
    import numpy as np
    import cv2

    tmp = os.path.join(tempfile.gettempdir(), "_test_repeat_aware_sheet3.png")
    cells = _make_repeated_sheet(tmp)
    try:
        boxes, groups = detect_repeat_aware_sub_element_boxes_px(tmp, cells)
        img = cv2.imread(tmp, cv2.IMREAD_COLOR)
    finally:
        os.remove(tmp)

    def thumb(box):
        x0, y0, x1, y1 = [int(round(v)) for v in box]
        crop = img[y0:y1, x0:x1]
        return cv2.resize(crop, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)

    max_diff = 0.0
    for g in groups:
        primary_thumb = thumb(boxes[g[0]])
        for other_i in g[1:]:
            diff = float(np.abs(primary_thumb - thumb(boxes[other_i])).mean())
            max_diff = max(max_diff, diff)
    print(f"대표 조각과 복제된 조각 간 축소본 최대 평균 차이: {max_diff:.2f} (기대: 6.0 이하 -- 사실상 동일한 그림)")
    assert max_diff <= 6.0, "복제된 조각이 실제로는 같은 스티커의 다른 위치/다른 스티커를 가리키고 있음"
    print("[OK] 복제된 모든 조각이 실제로 같은 내용의 스티커 위에 정확히 놓임")


def test_no_repeat_single_cell_still_splits_normally():
    """반복이 전혀 없는(패널 1개뿐인) 경우도 그대로 정상 동작하는지(회귀
    없음) -- 대표=자기 자신이라 그냥 한 번만 쪼개지고 끝나야 함."""
    tmp = os.path.join(tempfile.gettempdir(), "_test_repeat_aware_single.png")
    img = Image.new("RGB", (PANEL_W, PANEL_H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    _draw_panel(d, 0, 0)
    img.save(tmp)
    try:
        boxes, groups = detect_repeat_aware_sub_element_boxes_px(tmp, [(0, 0, PANEL_W, PANEL_H)])
    finally:
        os.remove(tmp)
    print(f"반복 없는 패널 1개 -- 그룹 개수: {len(groups)} (기대: 3), 각 그룹 크기: 1")
    assert len(groups) == 3
    assert all(len(g) == 1 for g in groups)
    print("[OK] 반복이 없는 단일 패널도 정상적으로 조각 3개로 쪼개짐(그룹마다 인스턴스 1개)")


def main():
    test_split_runs_only_once_per_repeat_group()
    test_group_count_matches_representative_split_and_all_repeated()
    test_repeated_instance_boxes_land_on_matching_content()
    test_no_repeat_single_cell_still_splits_normally()
    print("\nAll repeat-aware sub-element split checks passed.")


if __name__ == "__main__":
    main()
