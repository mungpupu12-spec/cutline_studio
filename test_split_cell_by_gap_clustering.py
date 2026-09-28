"""
2026-09-10(50차) 회귀 테스트: 칸 안 낱개 요소 쪼개기 세 번째 시도
(`core.multi_design.split_cell_into_sub_elements_px`, 거리 기반 클러스터링).

앞선 두 번(11차 도입->되돌림, 37차 재도입->38차 재재확인 후 되돌림)은
"개수 + 크기 유사성"만으로 판단하다가 실패했다:
  - 11차 실패 패턴: 카드 한 장 + 테두리 장식 여러 개 -> 장식마다 낱개로
    쪼개짐.
  - 38차 실패 패턴: 캐릭터 하나 + 그 캐릭터에 딸린 장식 조각들(크기가
    서로 다름) -> 장식 조각들이 캐릭터에서 분리됨.
이번엔 "실제 배경 틈이 칸 크기 대비 얼마나 되는가"를 기준으로 삼아, 위 두
실패 패턴은 여전히 안 쪼개지고(장식은 항상 몸체에 거의 맞닿아 있으므로),
서로 뚜렷이 떨어진 진짜 별개 스티커들은 쪼개지는지 확인한다.

전부 합성 이미지만 사용(순수 로직 검증 -- 실제 파일 확인은 재빌드 후
그녀가 직접 해야 함)."""

import os
import sys
import tempfile

sys.path.insert(0, "/root/cutline_studio")
os.environ.setdefault("CUTLINE_LICENSE_DEV_BYPASS", "1")

from PIL import Image, ImageDraw

from core.multi_design import split_cell_into_sub_elements_px


CELL = (0, 0, 600, 600)


def _save(draw_fn):
    tmp = os.path.join(tempfile.gettempdir(), f"_test_split_cell_{id(draw_fn)}.png")
    img = Image.new("RGB", (600, 600), (255, 255, 255))
    d = ImageDraw.Draw(img)
    draw_fn(d)
    img.save(tmp)
    return tmp


def test_card_with_border_decorations_not_split():
    """실패 패턴 1(11차): 큰 카드 한 장 + 테두리를 두른 작은 장식 여러 개
    (장식이 카드 가장자리에 거의 맞닿음) -- 쪼개지면 안 됨."""

    def draw(d):
        d.rectangle((80, 80, 520, 520), outline=(50, 50, 200), width=4)
        # 카드 테두리에 거의 맞닿은 작은 장식 8개(코너/변 중앙)
        deco_centers = [
            (80, 80), (300, 78), (520, 80),
            (78, 300), (520, 300),
            (80, 520), (300, 522), (520, 520),
        ]
        for cx, cy in deco_centers:
            d.ellipse((cx - 14, cy - 14, cx + 14, cy + 14), fill=(200, 60, 60))

    tmp = _save(draw)
    try:
        result = split_cell_into_sub_elements_px(tmp, CELL)
    finally:
        os.remove(tmp)
    print(f"카드+테두리장식 결과 개수: {len(result)} (기대: 1)")
    assert len(result) == 1, f"카드+테두리 장식 패턴이 잘못 쪼개짐: {result}"
    print("[OK] 카드+테두리 장식 패턴은 쪼개지지 않음")


def test_character_with_decorative_parts_not_split():
    """실패 패턴 2(38차): 캐릭터 하나(큰 몸통) + 크기가 서로 다른 장식
    조각들(리본/배지/하트/달, 몸통에 거의 맞닿음) -- 쪼개지면 안 됨."""

    def draw(d):
        # 몸통(큰 타원)
        d.ellipse((150, 150, 450, 450), fill=(80, 160, 220))
        # 리본(몸통 위쪽에 거의 맞닿음, 중간 크기)
        d.polygon([(260, 148), (340, 148), (300, 100)], fill=(220, 80, 140))
        # 작은 배지 두 개(몸통 오른쪽 가장자리에 거의 맞닿음, 크기 다름)
        d.ellipse((445, 220, 470, 245), fill=(240, 200, 40))
        d.ellipse((448, 300, 462, 314), fill=(240, 200, 40))
        # 하트(몸통 아래쪽에 거의 맞닿음, 큰 조각)
        d.polygon([(280, 452), (320, 452), (300, 500)], fill=(230, 60, 90))
        # 초승달(몸통 왼쪽 위, 아주 작음)
        d.ellipse((140, 160, 155, 175), fill=(255, 230, 120))

    tmp = _save(draw)
    try:
        result = split_cell_into_sub_elements_px(tmp, CELL)
    finally:
        os.remove(tmp)
    print(f"캐릭터+장식조각 결과 개수: {len(result)} (기대: 1)")
    assert len(result) == 1, f"캐릭터+장식 조각 패턴이 잘못 쪼개짐: {result}"
    print("[OK] 캐릭터+장식 조각 패턴은 쪼개지지 않음")


def test_genuinely_separate_stickers_are_split():
    """성공 패턴: 진짜 서로 다른 스티커 4개(강아지 얼굴/사람/식물/하트에
    해당, 서로 뚜렷한 배경 공간을 두고 배치) -- 쪼개져야 함."""

    def draw(d):
        d.ellipse((40, 40, 160, 160), fill=(200, 150, 100))       # 강아지 얼굴 자리
        d.rectangle((400, 40, 520, 200), fill=(120, 180, 220))    # 사람 자리
        d.ellipse((40, 400, 180, 540), fill=(90, 180, 90))         # 식물 자리
        d.polygon([(440, 420), (500, 420), (470, 480)], fill=(230, 60, 90))  # 하트 자리

    tmp = _save(draw)
    try:
        result = split_cell_into_sub_elements_px(tmp, CELL)
    finally:
        os.remove(tmp)
    print(f"별개 스티커 4개 결과 개수: {len(result)} (기대: 4)")
    assert len(result) == 4, f"서로 뚜렷이 떨어진 스티커들이 하나로 뭉쳐짐: {result}"
    print("[OK] 서로 뚜렷이 떨어진 스티커 4개는 각각 낱개로 쪼개짐")


def test_single_plain_character_not_split():
    """가장 기본적인 회귀 확인: 장식도 없는 캐릭터 하나뿐인 칸은 그대로
    1개로 남아야 함(가장 흔한 경우, 절대 깨지면 안 됨)."""

    def draw(d):
        d.ellipse((150, 150, 450, 450), fill=(80, 160, 220), outline=(30, 30, 30), width=3)

    tmp = _save(draw)
    try:
        result = split_cell_into_sub_elements_px(tmp, CELL)
    finally:
        os.remove(tmp)
    print(f"캐릭터 하나뿐인 칸 결과 개수: {len(result)} (기대: 1)")
    assert len(result) == 1, f"장식 없는 캐릭터 하나가 잘못 쪼개짐: {result}"
    print("[OK] 캐릭터 하나뿐인 칸은 그대로 1개")


def main():
    test_card_with_border_decorations_not_split()
    test_character_with_decorative_parts_not_split()
    test_genuinely_separate_stickers_are_split()
    test_single_plain_character_not_split()
    print("\nAll split-by-gap-clustering checks passed.")


if __name__ == "__main__":
    main()
