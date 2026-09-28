"""
Logic/mechanics smoke test for core.multi_design.detect_sub_element_boxes_px
and gui.app.CutLineApp._expand_grid_cells_into_sub_elements -- NOT a cutline-
quality test. Uses small script-generated images with KNOWN, analytic blob
positions/count (same spirit as test_multi_design.py / test_fidelity.py)
purely to verify the detection ALGORITHM and its wiring are correct -- never
used to judge cutline quality on real artwork.

2026-09-08 피드백("칼선이 요소를 인식하지 못하고 있어. 대부분의 요소는
인물/도형/오브제/캐릭터로 구성 되어 있어 구분 기준을 만드는게 좋을것
같아", "칼선을 선/색의 경계를 기준으로 형태감을 보는게 좋을 것 같아")로
확인된 실제 문제: 재단선 격자가 있는 파일에서 자동 인식이 그 칸 전체를
통째로 GrabCut에 넘기다 보니, 칸 하나 안에 서로 떨어진 캐릭터/오브제/장식
여러 개가 있어도(실측: 실제 파일 하나에서 칸 하나에 너구리 모양 캐릭터 +
강아지 모양 캐릭터 + 꽃 5송이 + 잎 여러 개가 함께 놓인 사례) 결과가 칸
사각형 그대로 나와버리는 문제가 있었다. 이
파일은 그 진단과 수정(core.multi_design.detect_sub_element_boxes_px +
gui.app._expand_grid_cells_into_sub_elements)이 올바르게 동작하는지
합성/기지 형태로 검증한다.

2026-09-08(10차) 피드백("칼선 안쪽에 또 다른 칼선이 추가된 이중칼선
개선하고")으로 추가된 검증: 위 낱개 요소 검출이 실제로는 하나인 캐릭터를
(귀/머리와 몸통 사이의 우연한 잘록함 때문에) 두 블롭으로 잘못 나누는
경우가 실사용에서 확인됐다 -- 이 두 블롭은 bbox가 서로 겹치므로(진짜
서로 다른 요소는 배경 틈으로 떨어져 있어 bbox가 겹치지 않음),
core.multi_design._merge_overlapping_boxes가 이런 겹치는 bbox들을 미리
하나로 합쳐서, 겹치는 두 실루엣 추적(그 결과물이 "이중 칼선"으로
보였음)이 애초에 발생하지 않도록 한다.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("CUTLINE_LICENSE_DEV_BYPASS", "1")

from PIL import Image, ImageDraw

from core.multi_design import _merge_overlapping_boxes, detect_sub_element_boxes_px

W, H = 1200, 500
BG = (235, 235, 240, 255)


def check(label, cond):
    status = "OK" if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        raise SystemExit(1)


def _make_scene_sheet(path):
    """가로로 나란히 놓인 두 개의 '재단선 격자 칸'을 흉내:
      - 칸 A (x: 40..560): 너구리 흉내(서로 맞닿은 몸통+귀 두 부위, 즉
        하나의 캐릭터 = 하나의 연결된 블롭) + 별개로 떨어진 꽃 2송이 +
        잎 1개 -- 실제 파일에서 확인된 "칸 하나에 캐릭터+장식
        여러 개" 구성을 그대로 흉내낸 것. 기대값: 4개의 낱개 요소(캐릭터
        1 + 꽃 2 + 잎 1)가 각각 별도 박스로 검출되어야 함.
      - 칸 B (x: 640..1160): 캐릭터 하나만 있고 그 안의 두 부위(몸통+귀)가
        서로 맞닿아 있을 뿐, 별도로 떨어진 장식은 전혀 없음. 기대값: 이
        칸 안에서는 낱개 요소가 1개(또는 인식 실패 시 0개)만 나와야
        한다 -- 캐릭터 하나가 부위별로 쪼개지면 안 되고, 원래 없는 장식이
        생겨나서도 안 됨.
    """
    im = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(im)

    # --- 칸 A: 캐릭터(몸통+귀, 굵은 다리로 이어져 목이 없음 -- 진짜로
    # 하나로 붙은 모양. test_multi_design.py의 _one_connected_blob와 같은
    # 원리) ---
    d.ellipse([150, 260, 330, 440], fill=(90, 90, 95, 255))  # 몸통
    d.ellipse([260, 150, 380, 270], fill=(40, 40, 45, 255))  # 귀
    d.rectangle([220, 200, 320, 320], fill=(65, 65, 70, 255))  # 목(굵게 이어붙임)
    # 캐릭터와 배경 틈을 두고 떨어진 꽃 2송이 + 잎 1개
    flower1 = (410, 60, 470, 120)
    flower2 = (410, 140, 470, 200)
    leaf1 = (420, 380, 460, 440)
    d.ellipse(flower1, fill=(230, 60, 90, 255))
    d.ellipse(flower2, fill=(230, 60, 90, 255))
    d.ellipse(leaf1, fill=(60, 150, 70, 255))

    # --- 칸 B: 캐릭터 하나뿐(몸통+귀, 굵은 목으로 이어져 하나로 붙음),
    # 장식 없음 ---
    d.ellipse([750, 260, 930, 440], fill=(90, 90, 95, 255))  # 몸통
    d.ellipse([860, 150, 980, 270], fill=(40, 40, 45, 255))  # 귀
    d.rectangle([820, 200, 920, 320], fill=(65, 65, 70, 255))  # 목(굵게 이어붙임)

    im.save(path)
    return {
        "cell_a": (40, 20, 560, 460),
        "cell_b": (640, 20, 1160, 460),
        "expected_a_min_count": 4,  # 캐릭터 1 + 꽃 2 + 잎 1
        "expected_b_max_count": 1,  # 캐릭터 하나만, 쪼개지면 안 됨
    }


def test_cell_with_multiple_elements_is_split():
    tmp = "/tmp/_sub_element_scene.png"
    info = _make_scene_sheet(tmp)

    boxes_a = detect_sub_element_boxes_px(tmp, info["cell_a"])
    check(
        f"칸 A(캐릭터+꽃2+잎1)에서 낱개 요소가 최소 {info['expected_a_min_count']}개 검출됨 (실제: {len(boxes_a)}개)",
        len(boxes_a) >= info["expected_a_min_count"],
    )
    # 모든 박스가 region_px(칸 A) 범위 안쪽 좌표여야 함(원본 이미지 좌표계로
    # 정확히 되돌아왔는지 확인).
    rx0, ry0, rx1, ry1 = info["cell_a"]
    check(
        "칸 A에서 검출된 모든 박스가 원본 이미지 좌표(칸 범위 안)로 정확히 변환됨",
        all(rx0 <= x0 and y0 >= ry0 - 5 and x1 <= rx1 + 5 and y1 <= ry1 + 5 for (x0, y0, x1, y1) in boxes_a),
    )

    boxes_b = detect_sub_element_boxes_px(tmp, info["cell_b"])
    check(
        f"칸 B(캐릭터 하나뿐, 서로 맞닿은 몸통+귀)는 부위별로 쪼개지지 않고 "
        f"최대 {info['expected_b_max_count']}개만 검출됨 (실제: {len(boxes_b)}개)",
        len(boxes_b) <= info["expected_b_max_count"],
    )
    print("[OK] 격자 칸 안 개별 요소 검출: 장식이 여러 개인 칸은 낱개로, 캐릭터 하나뿐인 칸은 하나로 유지됨")


def test_repeat_aware_split_replaces_old_gui_wiring():
    """gui.app이 쓰던 이전 경로(칸을 먼저 각자 쪼갠 뒤 그 조각들을
    group_identical_boxes_px로 묶으려던 것, `_expand_grid_cells_into_sub_
    elements` + 별도 group_identical_boxes_px 호출)는 2026-09-11(51차)
    피드백("똑같은 도안 여러 개일 때... 아예 각각 엉망으로 인식")으로
    반복 패널과 정면 충돌하는 게 확인되어 통째로 걷어내고,
    `core.multi_design.detect_repeat_aware_sub_element_boxes_px`(반복 패널을
    먼저 묶고 대표만 쪼갠 뒤 나머지엔 옮겨 붙이는 순서로 고침, 자세한
    검증은 test_repeat_aware_sub_element_split.py)로 완전히 대체했다.
    gui.app에는 이제 이 함수를 감싸는 별도 메서드가 없고 두 호출 지점
    (`_run_mixed_detect`, `_run_auto_detect_and_add_all`)에서 직접 쓰므로,
    여기서는 그 함수가 실제로 import돼 있고(별도 래퍼 없이 바로 쓸 수
    있음을 확인) 칸 A/B 시나리오에서 기대대로 동작하는지만 다시 확인한다."""
    from core.multi_design import detect_repeat_aware_sub_element_boxes_px

    tmp = "/tmp/_sub_element_scene2.png"
    info = _make_scene_sheet(tmp)
    cell_boxes = [info["cell_a"], info["cell_b"]]

    boxes, groups = detect_repeat_aware_sub_element_boxes_px(tmp, cell_boxes)
    # 참고: 이 두 칸은 원래 detect_sub_element_boxes_px 단위 테스트용으로
    # 만들어진 장면이라(칸 B가 칸 A 속 캐릭터와 몸통이 완전히 같음), 축소본
    # 평균 차이 기준(group_identical_boxes_px)으로는 "반복"으로 묶일 수도
    # 있다 -- 그건 이 함수가 아니라 group_identical_boxes_px 자체의 기존
    # 판단 기준이라 여기서 강제하지 않는다. 이 테스트가 확인하려는 것은
    # 오직 "칸 안에 여러 조각이 있으면 boxes가 원래 칸 개수보다 늘어난다"는
    # 배선(wiring) 그 자체다.
    check(
        f"칸 A(캐릭터+뚜렷이 떨어진 장식)가 낱개로 쪼개져 전체 조각(boxes) 개수가 원래 칸 "
        f"2개보다 많아짐 (실제: {len(boxes)}개, 그룹 크기들: {[len(g) for g in groups]})",
        len(boxes) > len(cell_boxes),
    )
    print("[OK] detect_repeat_aware_sub_element_boxes_px가 gui.app 두 호출 지점을 대체함(51차)")


def test_merge_overlapping_boxes_unit():
    """core.multi_design._merge_overlapping_boxes 자체를 이미지 없이 순수
    좌표만으로 검증 -- 실제로 겹치는 bbox만 합쳐지고, 그냥 맞닿거나(면적
    겹침 없음) 완전히 떨어진 bbox는 그대로 유지되는지 확인."""
    overlapping = [(0, 0, 100, 100), (60, 60, 160, 160)]
    merged = _merge_overlapping_boxes(overlapping)
    check(
        f"실제로 겹치는 두 bbox는 하나(합집합)로 합쳐짐 (실제: {merged})",
        merged == [(0, 0, 160, 160)],
    )

    touching_only = [(0, 0, 100, 100), (100, 0, 200, 100)]
    merged2 = _merge_overlapping_boxes(touching_only)
    check(
        f"면적으로 겹치지 않고 변만 맞닿은 두 bbox는 합쳐지지 않고 그대로 유지됨 (실제: {merged2})",
        set(merged2) == set(touching_only),
    )

    far_apart = [(0, 0, 50, 50), (500, 500, 550, 550)]
    merged3 = _merge_overlapping_boxes(far_apart)
    check(
        f"완전히 떨어진 두 bbox는 그대로 유지됨 (실제: {merged3})",
        set(merged3) == set(far_apart),
    )

    chain = [(0, 0, 100, 100), (80, 0, 180, 100), (160, 0, 260, 100)]
    merged4 = _merge_overlapping_boxes(chain)
    check(
        f"연쇄적으로 겹치는 3개 bbox(A-B 겹침, B-C 겹침)는 전부 하나로 합쳐짐 (실제: {merged4})",
        merged4 == [(0, 0, 260, 100)],
    )
    print("[OK] _merge_overlapping_boxes: 실제 겹침만 병합하고, 단순 접촉/비겹침은 그대로 유지")


def _make_falsely_split_character(path):
    """워터쉐드 분리가 '진짜로는 하나인 캐릭터'를 잘못 둘로 나누는 실제
    상황(귀/머리 부분과 몸통 부분 사이에 우연히 뚜렷한 잘록함이 생기는
    경우)을 합성 이미지로 재현 -- test_multi_design.py의
    `_two_designs_touching_thin_neck`와 의도적으로 거의 같은 모양(둥근 두
    덩어리 + 가는 다리)을 쓰되, 여기서는 그게 실제로는 '서로 다른 두 도안'
    이 아니라 '캐릭터 하나'라는 실측 시나리오를 흉내낸 것. 이 정도로 잘록한
    허리는 _split_touching_blobs가 실제로 둘로 나누는 것으로 확인됨(고쳐지기
    전엔 detect_sub_element_boxes_px가 그대로 2개의 겹치는 bbox를 돌려줬고,
    그 각각을 따로 실루엣 추적하면 거의 같은 캐릭터가 두 번 그려져 '이중
    칼선'으로 보였음)."""
    im = Image.new("RGBA", (400, 300), (235, 235, 240, 255))
    d = ImageDraw.Draw(im)
    d.ellipse([60, 60, 200, 200], fill=(90, 90, 95, 255))  # 머리/귀 쪽 큰 덩어리
    d.ellipse([180, 140, 340, 260], fill=(90, 90, 95, 255))  # 몸통 쪽 큰 덩어리
    d.rectangle([170, 165, 220, 195], fill=(90, 90, 95, 255))  # 가는 목
    im.save(path)
    return (0, 0, 400, 300)


def test_overlap_merge_fixes_double_cutline_split():
    """실제로 확인된 이중 칼선 시나리오를 끝까지 재현: 워터쉐드가 캐릭터
    하나를 겹치는 bbox 2개로 잘못 나누더라도, detect_sub_element_boxes_px가
    최종적으로는 _merge_overlapping_boxes를 거쳐 다시 1개의 bbox로 돌려줘야
    한다(그래야 실루엣 추적도 한 번만 돌아가고, 이중 칼선이 생기지 않음)."""
    tmp = "/tmp/_falsely_split_character.png"
    region = _make_falsely_split_character(tmp)
    boxes = detect_sub_element_boxes_px(tmp, region)
    check(
        f"잘록한 허리 때문에 워터쉐드가 2개로 나눴어도, bbox가 겹치므로 최종적으로는 "
        f"1개로 다시 합쳐짐 (실제: {len(boxes)}개, {boxes})",
        len(boxes) == 1,
    )
    print("[OK] 이중 칼선 재현 시나리오: 잘못 나뉜 겹치는 bbox가 최종적으로 하나로 병합됨")


def main():
    test_cell_with_multiple_elements_is_split()
    test_repeat_aware_split_replaces_old_gui_wiring()
    test_merge_overlapping_boxes_unit()
    test_overlap_merge_fixes_double_cutline_split()
    print("\nAll sub-element-detection checks passed.")


if __name__ == "__main__":
    main()
