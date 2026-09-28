"""
Automatically detect multiple discrete sticker/design blobs within ONE
source image, so a full sheet (several separate characters laid out on a
shared background, with visible gaps between them) can get cutlines for
EVERY design in one pass instead of the artist dragging a rough box around
each one by hand.

2026-09-07 피드백: "스티커는 도안을 자동 인식해서 칼선을 생성해야지.
하나씩 선택하는 건 비효율적이야." -- 한 장의 시트 안에 서로 떨어져 있는
캐릭터 여러 개가 있을 때, 하나씩 드래그하지 않아도 전부 한 번에 찾아내기
위한 모듈.

This is plain "where is each separate design" detection, NOT a
silhouette-aware segmentation -- it only needs to answer that question, not
"what is its exact outline". Each bounding box this finds is meant to be fed
straight back into the normal per-design pipeline (core.image_style.
generate_style_cutline / the FULL_CUT/DOMUSONG paths) exactly as if the
artist had dragged that rectangle by hand; the real silhouette tracing
(GrabCut etc.) still happens per-design afterwards, same as always.

2026-09-07 알고리즘 전면 교체 -- 첫 버전(배경 "모서리" 색과의 거리로 전경/
배경을 가르는 방식)이 실제 인쇄용 시트 파일에서 실패하는 게 확인됨: "여러장의
도안이 배치된 ai 파일을 제대로 읽지 못하고... 단일 스티커 한장으로 인식".
원인으로 지목되는 두 가지 다:
  1) 시트 전체가 하나의 색(예: 청록색)으로 칠해진 배경 위에 도안이 얹혀
     있는 구성 -- 이 경우 "배경색과 다른 부분 = 전경"이라는 옛 가정에서,
     도안들 사이의 빈틈도 배경과 같은 색이라 모든 도안이 그 배경색 영역을
     통해 서로 이어져 하나로 뭉쳐버린다(구멍 여러 개 뚫린 판이 위상적으로는
     여전히 "하나로 연결된 판"인 것과 같은 이치).
  2) 인쇄용 원본은 재단선/가이드라인/여백(성명·수량 표기 칸 등)까지 포함한
     페이지 전체가 래스터화될 수 있어서, 이미지 "모서리" 색만으로 배경을
     추정하는 방식이 그 여백/마크에 쉽게 속는다.
두 문제 모두 "배경이 무슨 색이냐"를 추정하는 대신 "실제 그림(선/색 경계)이
있는 곳이 어디냐"를 직접 찾는 방식으로 바꾸면 자연스럽게 해결된다 -- 배경이
흰색이든 청록색이든, 여백에 크롭마크가 있든 없든, 도안이 그려진 자리에는
반드시 색 경계(엣지)가 있고 빈 배경/여백에는 없기 때문. 그래서 색 거리 대신
Canny 엣지 검출 + (엣지로 둘러싸인 안쪽을 전경으로 채우는) hole-filling으로
바꿈.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

try:
    from skimage.feature import peak_local_max as _peak_local_max
    from skimage.segmentation import watershed as _ski_watershed
except Exception:  # noqa: BLE001
    # 2026-09-15: skimage가 없는 환경(구버전 설치본 등)에서도 최소한 기존
    # 방식(블롭 전체 최댓값 * peak_ratio + cv2.watershed)으로는 계속
    # 동작해야 하므로, 이 함수들 자체가 없을 때는 아래 _split_touching_blobs가
    # 알아서 예전 방식으로 폴백한다(_PEAK_LOCAL_MAX_AVAILABLE 참고).
    _peak_local_max = None
    _ski_watershed = None
_PEAK_LOCAL_MAX_AVAILABLE = _peak_local_max is not None and _ski_watershed is not None

# _fill_enclosed_regions(split_multi_component_holes=True)에서, 이보다
# 가로/세로 둘 다 작은 구멍은 "여러 캐릭터를 잇는 배경 틈"일 수 없어(캐릭터
# 하나보다 작을 수 없는 크기) 다중-성분 판정 대상에서 미리 제외한다.
# 2026-09-28(실측으로 40px가 너무 작았음을 확인): 40px는 캐릭터 하나의
# 눈/코처럼 얼굴 안 디테일 하나의 크기와도 겹쳐서, 얼굴 하나(원래 한
# 캐릭터)의 눈-코 사이 틈까지 "다른 캐릭터 여러 개가 맞닿은 틈"으로
# 잘못 판정해 눈/코가 얼굴에서 따로 떨어진 별도 요소로 쪼개지는 부작용이
# 실제 파일(편의상 C로 부름)로 확인됐다. 실제 병합 버그(B/C/D)의 진짜 틈은
# 캐릭터 몸통 크기(수백 px)인 반면 눈/코 디테일은 이보다 훨씬 작으므로,
# 문턱값을 훨씬 크게 올려 얼굴 디테일은 걸리지 않고 실제 캐릭터 사이
# 틈만 걸리도록 한다.
_MIN_SPLIT_HOLE_DIM_PX = 150


def _odd(n) -> int:
    n = int(round(n))
    return n + 1 if n % 2 == 0 else n


def _fill_enclosed_regions(
    mask_u8: np.ndarray,
    border_margin_px: int = 0,
    split_multi_component_holes: bool = False,
) -> np.ndarray:
    """mask_u8(255=전경/엣지, 0=배경)에서, 이미지 가장자리와 연결되지 않은
    "배경" 영역(즉 전경으로 완전히 둘러싸인 안쪽)을 전경으로 채워 돌려준다.
    도안의 외곽선(엣지)만으로는 속이 빈 고리 모양이 되는데, 실제로는 그
    안쪽 전체가 도안이므로 하나의 꽉 찬 덩어리로 만들어줘야 도안 하나당
    블롭 하나가 된다.

    가짜 배경 테두리 1px를 이미지 둘레에 둘러 채운 뒤(cv2.copyMakeBorder),
    그 확실히 배경인 테두리에서부터 flood-fill을 시작한다 -- 실제 이미지의
    네 모서리 자체가 크롭마크 등으로 전경(엣지)일 수 있는 경우까지 대비한
    것(모서리 딱 4점만 시도하던 이전 방식은 네 모서리가 전부 전경이면
    flood가 아예 시작을 못 해 이미지 전체가 "둘러싸인 영역"으로 잘못
    채워지는 버그가 있었음).

    2026-09-14(90도 회전 인식 실패 진단, 실제 파일로 확인, 1차): 시작점을
    모서리 한 점(0,0)에서만 flood하던 이전 방식은, 배경 자체가 세로/가로로
    색이 나뉜 줄무늬(예: 밤하늘 장면 배경을 파란색/크림색/회색/노란색
    세로 띠로 나눠 그린 실제 파일)일 때 심각하게 깨졌다 -- 각 띠의
    경계선 자체가 Canny 엣지로 잡혀 위아래 끝까지 이어지는 "벽"이 되면서,
    배경이 사실은 여러 개의 서로 통하지 않는 칸(각 띠)으로 쪼개져 있는데
    (0,0)이 속한 첫 번째 띠에서만 flood가 퍼지고 나머지 띠들은 전혀
    도달하지 못했다. 그래서 모서리 한 점이 아니라 네 변의 모든 배경 픽셀
    각각을 flood 시작점 후보로 삼도록 고쳤다(이미 flood된 픽셀은 다시
    시작하지 않으므로 실질 비용은 여전히 이미지 전체를 한 번 훑는 정도).

    2026-09-14(같은 진단, 2차 -- 1차 수정 후에도 같은 실제 파일에서 재확인):
    네 변 전부를 시작점으로 삼아도 여전히 실패하는 경우가 남아있었다 --
    이 함수를 부르는 쪽(`_content_mask_from_gray`)이 엣지를 미리 팽창/닫기
    (dilate+close, 반경 `close_px`)해서 넘기는데, 크롭 경계에서 딱
    `close_px`보다 좁게(실측: 5px, close_px=7) 떨어진 곳에 원래는 진짜
    바깥과 이어져 있던 배경 한 덩어리(실측: 82,970px, 칸 오른쪽 2/3을
    덮는 배경 전체)가 있으면, 그 좁은 틈이 우리가 스스로 적용한
    닫기(close) 연산 자체 때문에 완전히 막혀버려 "이미지 경계에 어떻게든
    닿아있음"이라는 기준을 (아주 근소하게) 통과 못 했다 -- 원인이 진짜
    도안의 닫힌 형태가 아니라 우리 쪽 팽창/닫기 연산의 부작용이므로, 그
    구멍을 다시 열어줘야 한다.

    그래서 `border_margin_px`(호출하는 쪽이 자신이 쓴 `close_px`를 그대로
    넘겨줌)만큼 진짜 이미지 가장자리에 가까운 배경 픽셀은, 실제 경계까지
    flood가 도달했는지와 무관하게 전부 시작점 후보로 추가한다 -- 우리
    자신의 닫기 연산이 만들어낼 수 있는 벽의 두께는 최대 `close_px` 정도
    뿐이므로, 그보다 좁은 틈으로만 막힌 배경은 우리가 만든 인공적인 벽일
    뿐 실제 도안의 닫힌 안쪽이 아니라고 보는 것이 안전하다(실제 캐릭터의
    속이 빈 부분이 크롭 경계에서 `close_px`(보통 5~21px) 안쪽에 딱 붙어
    있는 경우는 실무상 사실상 없음).

    올바른 판정 기준은 "이미지 경계에 어떻게든 닿아 있거나, 우리 쪽 닫기
    연산이 만들 수 있는 정도의 좁은 틈만으로 경계와 떨어진 배경"은 전부
    배경이고, 그중 진짜로(그보다 훨씬 넓게) 전경으로 완전히 막힌 부분만
    진짜 "둘러싸인 영역"이라는 것이다.

    2026-09-26(실제 파일 A의 나무 장식이 인쇄 시트 가장자리에서 그대로
    잘려나간 문제를 색 기반 보조 판정으로 고쳐보려고 시도했다가 되돌림):
    "가장자리 씨앗이 배경과 색까지 이어져야 함"이라는 조건을 한 번
    추가해봤으나(_reached_from_border_color_aware), 실제 파일로 검증해보니
    이 나무는 배경이 엣지 약한 틈으로 새어 들어가 잠식된 경우가 아니라
    캔버스 가장자리 자체가 나무 자신의 색이라 색 조건을 아무리 엄격히
    해도 도움이 안 됐다(정확히 0픽셀 차이). 게다가 이 씨앗 후보를 전부
    순수 파이썬 루프로 훑는 방식이 실제 인쇄용 대형 시트(수천x수천px)에서
    체감될 만큼 느려지는 부작용만 남겼다 -- 효과 없이 무거워지기만
    했으므로 전부 되돌림. 이 문제(가장자리에서 잘려나간 도안 구별)를 다시
    시도한다면 이 함수 하나가 아니라 시트 전체의 배경 색/그라디언트 법칙을
    먼저 추정하는 훨씬 큰 작업이 필요하다.

    2026-09-27/28(여백 없이 가장자리까지 꽉 채워 인쇄된 시트에서 서로 다른
    캐릭터 여러 개가 하나의 칼선으로 잘못 합쳐지는 문제, 실제 파일 3개
    (편의상 B/C/D로 부름)로 확인, C는 이 수정으로 완전히 해결/D는 부분
    개선/B는 이 방법만으로는 해결 안 됨 -- 아래 참고): 이런 시트는
    캐릭터 사이사이의 진짜 배경 틈들이 격자 눈금/장식 틈을 타고 서로
    이어져, 시트 대부분을 덮는 거대한 "배경 그물망"을 이룬다. 이 그물망이
    시트 실제 가장자리까지 어디서도 못 닿으면 통째로 "둘러싸인 영역"으로
    오판돼 전부 채워지고, 그 그물망에 걸린 모든 캐릭터가 한 덩어리로
    인식된다(실측: B 73%, C 64%, D 87%가 각각 캐릭터 여러 개를 한
    박스로 병합).

    면적 비율로 자르는 방식은 이미 시도했다가 되돌렸다(캐릭터 하나 자신의
    정당하게 넓은 내부 구멍과 크기만으로 구분 불가). 대신 "이 구멍을
    채우면 원래 서로 안 이어져 있던 윤곽선(전경) 두 덩어리 이상이 하나로
    붙어버리는가"를 직접 확인한다: 채우기 전 mask_u8에서 이 구멍 바로
    바깥에 맞닿은 서로 다른 연결 성분이 몇 개인지 센다. 1개뿐이면(정상적인
    자기 자신의 홀) 채우고, 2개 이상이면(서로 다른 캐릭터 윤곽선이 이
    구멍을 사이에 두고 맞닿음) 채우지 않는다.

    실측 결과(2026-09-28, B/C/D 실제 파일): C는 병합 박스가
    64%->5.5%로 완전히 해소됨. D는 87%->30%로 크게 개선됐지만 일부 남음.
    B는 전혀 개선 안 됨(73%->73%, 그대로) -- 원인을 실측으로 확인:
    B는 캐릭터 윤곽선들이 (severing 이후에도) 애초에 Canny+팽창/닫기
    단계에서부터 얇은 장식선/격자선으로 시트 전체가 이미 "연결 성분 1개"
    로 하나로 이어져 있어서(실측: 전경 픽셀의 99% 이상이 단일 연결 성분),
    이 구멍-경계 판정 자체가 적용될 지점이 없다(어디를 봐도 "다른 성분
    2개"가 아니라 "성분 1개"만 보임). 이건 이 함수 하나로 풀 수 있는
    문제가 아니라, 엣지 검출/장식선 자체를 다르게 다뤄야 하는 훨씬 큰
    작업이 필요하다 -- B 같은 경우는 다음에 별도로 다시 시도해야 한다.

    그래도 C를 완전히, D를 부분적으로 고치고 나머지 파일들은 전혀
    건드리지 않는(실측: 회귀 테스트 67개 그대로 통과, 나머지 파일들 박스
    결과 동일) 순수한 개선이라 적용한다.

    성능(실측 후 최적화): 구멍마다 "이미지 전체 크기" 배열을 다루면 실제
    시트의 수백 개 작은 구멍 때문에 느려지므로, 각 구멍의 실제 bbox
    주변 작은 영역만 잘라내 그 안에서만 dilate/판정한다. 추가로, 이
    판정으로 걸러낼 수 있는 구멍은 애초에 "캐릭터 하나보다 작을 수 없는
    크기"여야 하므로, `_MIN_SPLIT_HOLE_DIM_PX`보다 작은(가로/세로 모두)
    구멍은 이 비교적 비싼 판정 자체를 건너뛰고 바로 채운다(대부분의 구멍은
    이 정도로 작은 디테일(캐릭터 자신의 무늬/점 등)이라 이 스킵만으로도
    비용 대부분이 사라진다).

    `detect_design_bboxes_px`가 시트 전체를 훑을 때만 이 판정을 켜고
    (`split_multi_component_holes=True`), 그 외 모든 호출부(`detect_sub_
    element_boxes_px`의 칸 하나 크롭, `cell_has_content_px`, `core.
    image_style`의 무테 개별 실루엣 추적 등)는 기본값 False로 예전과 완전히
    동일하게 동작한다."""
    background = cv2.bitwise_not(mask_u8)  # 255 where NOT edge/foreground
    padded = cv2.copyMakeBorder(background, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=255)
    flood_mask = np.zeros((padded.shape[0] + 2, padded.shape[1] + 2), np.uint8)
    reached = padded.copy()
    ph, pw = padded.shape[:2]
    border_xy = set()
    for x in range(pw):
        border_xy.add((x, 0))
        border_xy.add((x, ph - 1))
    for y in range(ph):
        border_xy.add((0, y))
        border_xy.add((pw - 1, y))
    if border_margin_px > 0:
        h0, w0 = background.shape[:2]
        margin = min(border_margin_px, h0, w0)
        cols = list(range(0, margin)) + list(range(max(0, w0 - margin), w0))
        rows = list(range(0, margin)) + list(range(max(0, h0 - margin), h0))
        for y in range(h0):
            for x in cols:
                border_xy.add((x + 1, y + 1))  # +1: copyMakeBorder 오프셋
        for x in range(w0):
            for y in rows:
                border_xy.add((x + 1, y + 1))
    for bx, by in border_xy:
        if reached[by, bx] == 255:  # 아직 어떤 flood에도 닿지 않은 배경
            cv2.floodFill(reached, flood_mask, (bx, by), 128)
    reached = reached[1:-1, 1:-1]  # 앞서 둘렀던 가짜 테두리 1px를 다시 잘라냄
    enclosed = (background == 255) & (reached != 128)

    if split_multi_component_holes and enclosed.any():
        # 위 docstring(2026-09-27/28) 참고. 성능(실측 후 재작성, 2026-09-28
        # "프로그램 전체적으로 느려지고 있어 계산 다 삭제" 피드백 대응): 첫
        # 버전은 구멍마다 파이썬 루프를 돌며 각각 cv2.dilate를 불렀는데,
        # 구멍이 실제 시트에서 수백 개(예: 실측 358개)라 그 호출 수 자체의
        # 파이썬/OpenCV 오버헤드가 쌓여 여러 파일에서 체감될 만큼(실측:
        # 최대 +188%) 느려졌다. 파이썬 루프 대신 이미지 전체를 8방향으로
        # "밀어서"(np.roll) 한 번에 비교하는 벡터 연산으로 다시 짜서, 구멍
        # 개수와 무관하게 이미지 크기에만 비례하는 고정된 소수의 배열 연산
        # 몇 번으로 끝낸다(실측: 최대였던 파일도 원래 속도로 복귀).
        fg_n, fg_labels = cv2.connectedComponents((mask_u8 > 0).astype(np.uint8), connectivity=8)
        hole_n, hole_labels, hole_stats, _ = cv2.connectedComponentsWithStats(
            enclosed.astype(np.uint8), connectivity=8
        )
        if hole_n > 1 and fg_n > 1:
            # 위 docstring 참고: 캐릭터 하나보다 작을 수 없는 크기의 구멍만
            # 이 판정 대상 -- 대부분인 작은 구멍(자기 자신의 디테일)은
            # 애초에 이 비교 자체에서 제외해 작업량을 크게 줄인다.
            eligible = (hole_stats[:, 2] >= _MIN_SPLIT_HOLE_DIM_PX) | (hole_stats[:, 3] >= _MIN_SPLIT_HOLE_DIM_PX)
            eligible[0] = False  # 라벨 0(배경)은 구멍이 아님
            if eligible.any():
                hole_labels_elig = np.where(eligible[hole_labels], hole_labels, 0).astype(np.int64)
                fg_labels64 = fg_labels.astype(np.int64)
                fg_max = int(fg_labels64.max())
                key_mult = fg_max + 1
                pair_key_chunks = []
                for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)):
                    shifted_fg = np.roll(fg_labels64, (dy, dx), axis=(0, 1))
                    if dy == -1:
                        shifted_fg[-1, :] = 0
                    elif dy == 1:
                        shifted_fg[0, :] = 0
                    if dx == -1:
                        shifted_fg[:, -1] = 0
                    elif dx == 1:
                        shifted_fg[:, 0] = 0
                    valid = (hole_labels_elig > 0) & (shifted_fg > 0)
                    if valid.any():
                        pair_key_chunks.append(np.unique(hole_labels_elig[valid] * key_mult + shifted_fg[valid]))
                if pair_key_chunks:
                    all_pair_keys = np.unique(np.concatenate(pair_key_chunks))
                    hole_ids_of_pairs = all_pair_keys // key_mult
                    uniq_hole_ids, distinct_fg_counts = np.unique(hole_ids_of_pairs, return_counts=True)
                    exclude_hole_ids = uniq_hole_ids[distinct_fg_counts >= 2]
                    if exclude_hole_ids.size:
                        enclosed[np.isin(hole_labels, exclude_hole_ids)] = False

    result = mask_u8.copy()
    result[enclosed] = 255
    return result


def _split_touching_blobs(mask_u8: np.ndarray, min_area: int, peak_ratio: float = 0.5):
    """맞닿아 하나로 합쳐진 둥근 도안들(예: 인쇄 시트에서 서로 거의 맞닿게
    배치된 원형/타원형 스티커들)을 다시 나눈다.

    2026-09-07 실제 사용자 파일로 재현/확인된 버그: 시트
    안의 10개 개별 스티커 중 여러 쌍이 인쇄 여백을 아끼려고 서로 거의
    맞닿게 배치돼 있어서, 흰 테두리끼리 실제로 맞닿거나 1px도 안 되게만
    떨어진 지점이 생김 -- 그 지점엔 청록 배경이 거의/전혀 안 보여서 Canny
    엣지+hole-fill만으로는 두 스티커가 하나의 연결된 덩어리로 합쳐져
    버림(실측: 원래 10개여야 할 스티커가 5개 블롭으로, 그 중 다수가 서로
    다른 스티커 2~3개를 한 번에 담고 있었음 -- "여러장의 스티커가 배열된
    걸 한 장으로 인식" 피드백의 실제 원인).

    고전적인 "붙어있는 둥근 물체 분리" 기법(거리 변환 + 워터쉐드)을 적용:
    각 블롭 내부에서 배경까지의 거리가 가장 먼 지점(그 블롭의 '중심부')을
    자체 최대값의 peak_ratio 이상인 곳으로 근사해서 씨앗으로 삼고, 한
    블롭 안에서 씨앗이 여러 덩어리로 나뉘어 나오면(=사실은 서로 다른 도안
    여러 개가 맞닿아 있던 것) 그 개수만큼 라벨을 나눠 워터쉐드로 실제
    경계선을 그린다. 씨앗이 하나만 나오면(=원래도 정말 하나였던 도안)
    그대로 하나의 라벨을 유지한다.

    일부러 굵은 막대로 이어붙인 것처럼 "진짜 하나로 봐야 하는" 모양은
    이어지는 부분의 거리 변환 값도 여전히 높게 유지되므로(잘록해지는 정도가
    약함) peak_ratio 임계값을 넘는 두 번째 씨앗이 따로 생기지 않아 계속
    하나로 유지된다 -- test_multi_design.py의 `_one_connected_blob` 케이스로
    검증됨.

    2026-09-14(4차, 실제 세트5 파일로 발견 -- "장식 하나가 옆 큰 캐릭터에
    통째로 흡수됨"): 크기 차이가 큰 두 도안이 헤일로끼리만 살짝 맞닿아 같은
    블롭이 되면, 작은 쪽의 자체 최대 거리값이 큰 쪽 최대값의 peak_ratio(0.5)
    에 못 미쳐 씨앗이 안 생기고 큰 쪽에 흡수되는 문제를 발견했다. "블롭
    전체 최댓값 대비 비율"이 아니라 "지역 극대값(local maxima, 자기 주변
    작은 창 안에서의 최댓값)"을 씨앗 기준으로 바꾸는 시도를 해봤으나, 실제
    파일로 재검증하니 오히려 셀 전체가 박스 하나로 뭉개지는 훨씬 심각한
    회귀가 발생함(실측: 같은 셀에서 박스 수가 11개→6개로 줄고, 그 중
    하나가 셀 전체 크기(1773x948)를 그대로 차지함 -- 정확한 원인은 아직
    특정하지 못함, 아마도 이 창 비교 자체가 진짜 하나인 넓은 배경성/윤곽선
    요소 등 다른 블롭에도 예상 못 한 방식으로 영향을 준 것으로 추정).
    위험이 너무 커서 이 시도는 전부 되돌리고, 원래의 peak_ratio 기준
    그대로 유지한다. 크기 차이 나는 두 도안이 맞닿는 문제는 다음에 더
    안전한 방법(예: 워터쉐드 자체가 아니라 후처리 단계에서 별도 검출)으로
    다시 시도해야 한다."""
    dist = cv2.distanceTransform(mask_u8, cv2.DIST_L2, 5)
    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask_u8, connectivity=8)

    markers = np.zeros(mask_u8.shape, dtype=np.int32)
    next_marker = 2  # 1은 확실한 배경(관례), 0은 아직 안 정해진 영역
    # 2026-09-13 추가(원래 블롭 출처 추적): 아래에서 반환하는 marker_origin은
    # "이 최종 라벨이 원래(워터쉐드 적용 전, cv2.connectedComponentsWithStats
    # 기준) 몇 번 블롭에서 나왔는가"를 기록한다. 왜 필요한가는
    # `_merge_overlapping_boxes` 호출부(주석 참고) -- 서로 원래부터 별개였던
    # (배경으로 이미 완전히 분리돼 있던) 블롭 두 개가, 삐죽삐죽한/복슬복슬한
    # 실루엣 때문에 우연히 bbox만 겹치는 경우와, 원래 하나였던 블롭이
    # 워터쉐드로 (잘못) 두 개로 나뉜 경우를 구분하는 데 쓰인다.
    marker_origin = {1: -1}  # 1(확정 배경)은 원래 블롭이 없음 -- 편의상 -1

    # 2026-09-15(7차, 실측으로 원인 확정 -- "고양이+초록 장식이 계속 하나로
    # 흡수됨", idx11 IoU 0.06): 6차까지 시도한 "오프닝으로 얇은 다리만
    # 끊기" 방식은 이 케이스에 전혀 안 통했다 -- 실측으로 커널을 75px까지
    # 키워도 둘이 맞닿는 지점이 전혀 얇아지지 않았다(두 실루엣이 꽤 두툼하게
    # 맞닿아 있음, "얇은 다리"가 아니라 "완만하게 이어지는 굴곡"). 이런
    # 경우는 오프닝(모폴로지)이 아니라 애초에 거리 변환의 진짜 국소
    # 극대값(local maxima)을 찾아야만 풀 수 있다 -- 다만 4차 시도(이 문서
    # 위쪽 참고)에서 "블롭 전체 최댓값 대비 비율"을 순진하게 "각자 이웃
    # 창 안에서 극대값"으로만 바꿨다가 셀 하나가 통째로 박스 하나로
    # 뭉개지는 심각한 회귀가 났었다 -- 그 원인은 비최대억제(non-maximum
    # suppression)가 없어서, 완만하게 굴곡지는 능선을 따라 서로 몇 px밖에
    # 안 떨어진 "가짜 극대값"이 수십~수백 개 동시에 씨앗으로 뽑혀
    # 워터쉐드가 예측 불가능하게 동작했기 때문으로 추정된다.
    #
    # 그래서 이번엔 직접 구현하는 대신, 정확히 이 문제(서로 다른 크기의
    # 극대값들을 최소 간격 보장하며 찾기)를 위해 설계된 검증된 라이브러리
    # 함수(skimage.feature.peak_local_max, min_distance로 비최대억제를
    # 내장)를 쓴다. `min_distance`는 min_area(이 칸에서 "이 정도는 돼야
    # 진짜 도안"이라고 이미 정해둔 척도)에 비례시켜서, 실제 세트5 파일로
    # 여러 값을 직접 실측 비교해 고른 안전 범위(장식 하나(고양이+장식,
    # 겨우 76.3의 거리값)는 정확히 찾아내면서, 이미 낱개로 잘 잡히던
    # 다른 도안(체크무늬 방석 장식)은 잘못 둘로 쪼개지 않는 값)를 쓴다 --
    # 이 실측 검증 없이는 값 하나 고르는 것도 못 믿는다.
    #
    # 2026-09-15(8차, 실측으로 원인 확정 -- "잎/클로버 모양 장식 하나가
    # 좌우(또는 위아래) 두 조각으로 쪼개짐", 다른 실제 세트 파일): 잎
    # 모양처럼 양쪽으로 둥근 두 갈래(lobe)를 가진 장식 하나는, 거리
    # 변환값이 각 갈래 중심에서 따로 국소 극대값을 가져서 1.4배 배율로는
    # peak_local_max가 진짜 하나인 도안을 두 씨앗으로 잘못 나눴다(실측:
    # 두 씨앗 좌표가 제자리 그대로 유지된 채 배율만 1.4->2.0으로 올리니
    # 정확히 1개로 합쳐짐). 배율을 2.0으로 올려서 재현: 전체 7개 실제
    # 파일 스윕(515개 실측 칼선 비교)으로 회귀 여부를 확인했고, 이
    # 장식이 있던 파일에서만 낮은 IoU 사례 7건이 개선(median 그대로,
    # IoU<0.5 15/57 -> 8/57)됐을 뿐 나머지 6개 파일(특히 4차/6차/7차
    # 시도가 다뤘던, 서로 다른 크기 도안이 맞닿는 케이스가 있는 파일)은
    # 숫자가 소수점까지 완전히 동일해 회귀가 전혀 없음을 확인했다.
    min_distance = max(30.0, (min_area ** 0.5) * 2.0)
    min_peak_dist = max(1.5, (min_area ** 0.5) * 0.3)

    # 2026-09-15(7차, 합성 회귀 테스트로 발견): 사각형처럼 각진(둥글지 않은)
    # 실제 도안 하나는 거리 변환값이 중심축을 따라 "평평한 능선(고원)"을
    # 이루는데, peak_local_max가 그 평평한 고원 위 서로 다른 두 지점을
    # (min_distance만 넘으면) 별개의 극대값으로 착각해 멀쩡한 도안 하나를
    # 위아래/좌우 두 조각으로 잘못 쪼개는 회귀가 실측(합성 테스트)으로
    # 확인됨(예: 세로로 긴 사각형 하나 -> 위/아래 절반 두 조각). 거리
    # 변환에 살짝 가우시안 블러를 줘서 이런 평평한 고원에 아주 미세한
    # 기울기를 만들어주면(전체 모양 판단에는 영향 없을 만큼 작은 정도),
    # 고원 전체에서 진짜 정점 단 하나만 살아남는다 -- 실제 세트5 파일의
    # 고양이+장식 케이스(둥글지 않은 평평한 부분이 없음)는 블러를 줘도
    # 여전히 정확히 3개(각 도안 1개씩)를 찾아냄을 실측으로 확인했다.
    # 블러는 씨앗을 "찾는" 용도로만 쓰고, 실제 워터쉐드가 경계를 그리는
    # 지형(아래 -dist)에는 쓰지 않는다(경계 정확도에 영향 주지 않도록).
    blur_sigma = max(3.0, (min_area ** 0.5) * 0.15)
    blur_ksize = int(blur_sigma * 6) | 1  # 홀수로 보정
    dist_for_peaks = cv2.GaussianBlur(dist, (blur_ksize, blur_ksize), blur_sigma)

    for i in range(1, n_labels):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        blob_mask = labels == i
        blob_dist = np.where(blob_mask, dist, 0)
        local_max = float(blob_dist.max())
        if local_max <= 0:
            continue

        seed_components = []  # 각 원소: 이 블롭 안에서 찾은 씨앗 하나의 boolean 마스크
        if _PEAK_LOCAL_MAX_AVAILABLE:
            try:
                coords = _peak_local_max(
                    dist_for_peaks,
                    min_distance=int(round(min_distance)),
                    labels=blob_mask.astype(np.int32),
                    threshold_abs=min_peak_dist,
                    exclude_border=False,
                )
            except Exception:  # noqa: BLE001
                coords = None
            if coords is not None and len(coords) > 0:
                for y, x in coords:
                    m = np.zeros(mask_u8.shape, dtype=bool)
                    m[int(y), int(x)] = True
                    seed_components.append(m)

        if not seed_components:
            # skimage를 못 쓰거나(설치 안 됨) 극대값을 하나도 못 찾은 경우
            # (이 블롭 전체가 min_peak_dist 미만으로 얇음 등) -- 예전
            # 방식(블롭 전체 최댓값 * peak_ratio) 그대로 폴백한다. 이 경로는
            # 지금까지 실측으로 검증돼온 기존 동작을 그대로 보존하므로
            # 회귀 위험이 없다.
            seed_mask = (blob_dist >= local_max * peak_ratio).astype(np.uint8)
            n_seed, seed_labels = cv2.connectedComponents(seed_mask, connectivity=8)
            for seed_i in range(1, n_seed):
                seed_components.append(seed_labels == seed_i)

        for comp in seed_components:
            markers[comp] = next_marker
            marker_origin[next_marker] = i
            next_marker += 1

    if next_marker <= 2:
        # 씨앗이 하나도 안 나온 예외적인 경우(예: 남은 블롭이 전부
        # min_area 미만) -- 워터쉐드 자체를 건너뛰고 원래 라벨을 그대로
        # 돌려준다(안전장치). 이 경로는 애초에 워터쉐드로 나뉜 적이 없으므로
        # 각 라벨이 곧 자기 자신의 원래 블롭이다.
        origin = {int(lbl): int(lbl) for lbl in np.unique(labels) if lbl != 0}
        return labels, origin

    markers[mask_u8 == 0] = 1  # 배경은 확실한 배경으로 고정

    # 워터쉐드는 "높은 곳(능선)"을 경계로 삼으므로, 각 도안의 중심(거리
    # 변환값이 큰 곳)이 낮고 맞닿는 지점(거리 변환값이 작은 곳)이 높도록
    # 뒤집은 이미지를 입력으로 준다 -- 붙어있는 두 도안 사이의 잘록한
    # 지점이 바로 그 능선이 되어 정확히 그 자리에서 갈라진다.
    #
    # 2026-09-13 발견/수정(둥근 도안 bbox 수축 버그): cv2.watershed는 "값
    # 기준 단순 침수"가 아니라 "이미 라벨된 이웃과의 값 차이(비용)"를
    # 누적해서 채우는 방식이라, 배경(마스크 바깥) 전체가 완전히 균일한
    # 255였고 도안 가장자리(거리값 0에 가까움)도 정규화 후 배경과 거의
    # 같은 값이 되어(둘 다 255 근처) 배경이 "한 칸 건너오는 비용"과 도안
    # 내부를 한 칸씩 파고드는 비용이 거의 같아지는 문제가 있었다. 그 결과
    # 배경이 둥근/볼록한 도안 중심부 근처까지 잠식해 들어와 최종 라벨
    # 영역(및 그 bbox)이 실제 도안보다 훨씬 작게 나옴 -- 서로 붙은 도안을
    # 나누는 경우가 아니라 완전히 고립된 도안 하나뿐일 때도 재현됨(반지름
    # 90짜리 원이 반지름 45 안팎으로 수축, 원본 대비 bbox 폭이 약 30%나
    # 줄어드는 걸 합성 테스트로 직접 확인). "이미지가 조각나서 인식된다"는
    # 실제 피드백과 정확히 들어맞는 원인.
    #
    # 고침: 도안 내부의 거리값은 여전히 0~interior_cap 범위로 정규화해서
    # 쓰되(그래야 서로 다른 도안이 맞닿은 잘록한 목 부분은 계속 낮은
    # 높이=능선으로 남아 정확히 그 자리에서 갈라짐), 배경만은 정규화된
    # 범위와 확실히 동떨어진 별도의 고정값(255)으로 강제한다. 배경에서
    # 도안 경계로 "건너오는" 단 한 칸의 비용이 도안 내부를 한 칸씩
    # 파고드는 비용보다 압도적으로 커지도록 만들어서, 워터쉐드가 배경을
    # 실제 도안 가장자리에서 멈추게 하면서도 진짜 목(잘록한 지점)은
    # 여전히 정확히 갈라낸다.
    #
    # 주의: 이 수정으로 도안 bbox가 정상 크기로 커지면서, 아래
    # _merge_overlapping_boxes의 겹침 임계값(예전의 축소된 박스 크기를
    # 기준으로 튜닝돼 있던 값)이 같이 재조정되어야 한다(별도로 수정함).
    # 2026-09-15(7차): 위에서 skimage.feature.peak_local_max로 점 하나짜리
    # 씨앗을 찾게 되면서, 실제 파일로 검증해보니 cv2.watershed는 이런
    # "점" 씨앗을 잘 못 다뤘다(실측: 고양이+초록 장식 케이스에서, 각자
    # 정확한 위치에 씨앗을 찾았는데도 cv2.watershed 결과는 실제 두 도안
    # 모양과 전혀 안 맞는 이상한 3조각으로 갈라짐 -- 아마 이웃 비용 누적
    # 방식이 점 씨앗 하나의 "세력"을 제대로 못 키우는 것으로 추정). 반면
    # skimage.segmentation.watershed(점 씨앗을 기본으로 상정하고 설계된
    # 함수)로 똑같은 씨앗을 흘려보내니 정확히 두 캐릭터 + 장식 각각의
    # 실제 모양과 거의 일치하는 결과가 나왔다(실측: 장식 bbox가 실제
    # 칼선 bbox와 각 변 20px 안쪽으로 거의 일치). 그래서 씨앗을 점으로
    # 바꾼 이상 흘려보내는 방식도 그 점에 맞게 skimage 쪽으로 같이
    # 바꾼다 -- `mask=mask_u8`로 배경을 아예 흘림 대상에서 제외하므로,
    # 위 2026-09-13에 고쳤던 "배경이 도안을 잠식"하는 문제도 별도의
    # elevation 트릭 없이 원천적으로 안 생긴다.
    #
    # skimage를 못 쓰는 환경(설치가 안 됐거나 이 함수 자체가 없는 경우)
    # 에서는 예전에 이미 실측으로 검증해둔 cv2.watershed + elevation
    # 트릭 경로를 그대로 쓴다(회귀 위험 없는 안전한 폴백).
    if _PEAK_LOCAL_MAX_AVAILABLE:
        result = _ski_watershed(-dist, markers=markers, mask=mask_u8.astype(bool))
        return result.astype(np.int32), marker_origin

    interior_cap = 200
    norm_dist = cv2.normalize(dist, None, 0, interior_cap, cv2.NORM_MINMAX)
    elevation_gray = (interior_cap - norm_dist).astype(np.uint8)
    elevation_gray[mask_u8 == 0] = 255
    elevation = cv2.cvtColor(elevation_gray, cv2.COLOR_GRAY2BGR)
    cv2.watershed(elevation, markers)
    return markers, marker_origin


def _content_mask_from_gray(
    gray: np.ndarray,
    close_ratio: float,
    edge_low: int,
    edge_high: int,
    split_multi_component_holes: bool = False,
):
    """detect_design_bboxes_px/detect_sub_element_boxes_px(박스 추출용)와
    core.image_style의 무테(BORDERLESS) 실루엣 추적(2026-09-08(11차) 피드백:
    "무테 칼선은 요소의 외곽 색을 기준으로 라인을 생성하고 생성한 라인을
    축소 및 재배치해 칼선을 생성해")이 공통으로 쓰는 가장 기초적인 단계만
    떼어낸 것 -- Canny 엣지 -> 살짝 팽창/닫기로 끊어진 선 잇기 -> 그 안쪽을
    채우기(_fill_enclosed_regions). 워터쉐드로 붙어있는 덩어리를 나누는 것과
    bbox를 추출하는 것은 이 함수의 책임이 아니다(호출하는 쪽이 필요하면
    직접 함). 결과는 255=내용물/0=배경인 흑백 마스크, gray와 같은 크기.

    2026-09-14(좋은 칼선 기준 맞추기 작업 중 시도했다가 되돌림): 한때 이
    함수 반환 직전에 "코어 마스크 주변 저대비 헤일로까지 편입"하는 전역
    확장을 넣어봤으나(_grow_into_low_contrast_halo였던 이름), 실제 파일로
    검증하니 이 함수는 `_detect_boxes_from_gray`의 "몇 개의 별개 도안이
    있는가"(박스 개수/분리) 판단에도 그대로 쓰이는데, 그 판단에 영향을
    주는 마스크 자체를 넓히면 원래 서로 분리돼 있던 도안들이 좁은 배경
    틈을 두고 붙어있을 때 그 틈까지 편입해버려 잘못 합쳐지는 회귀가
    실제로 재현됐다(실측: file1 셀9가 8개 -> 1개로 붕괴). 헤일로 편입은
    "이미 확정된 박스 하나하나를 살짝 넓히는" `_expand_boxes_for_halo`
    쪽으로 옮겨서, 개수/분리 판단에는 전혀 손대지 않고 박스 좌표만
    넓히도록 다시 설계함(아래 함수 참고)."""
    edges = cv2.Canny(gray, edge_low, edge_high)
    h, w = gray.shape[:2]
    short_side = min(w, h)
    close_px = _odd(max(3, min(21, short_side * close_ratio)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_px, close_px))
    mask_u8 = cv2.dilate(edges, kernel, iterations=1)
    mask_u8 = cv2.morphologyEx(mask_u8, cv2.MORPH_CLOSE, kernel)
    # dilate 한 번 + close(내부적으로 dilate+erode) 한 번, 도합 팽창이 두 번
    # 겹치므로 우리 쪽 연산이 만들 수 있는 벽의 실제 두께는 close_px 한 번
    # 몫보다 넉넉히 더 크다 -- 실제 파일로 재측정: close_px=7인데도 진짜
    # 배경 한 덩어리가 경계에서 7px 떨어진 곳에서 막혀 있었음(딱 1px 차이로
    # 안 열림). 여유를 둬서 2배로 넉넉히 잡는다.
    return _fill_enclosed_regions(
        mask_u8,
        border_margin_px=close_px * 2,
        split_multi_component_holes=split_multi_component_holes,
    )


def _bbox_gap_px(a: tuple, b: tuple) -> float:
    """두 박스(x0,y0,x1,y1) 사이의 최단 거리(겹치면 0)."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    dx = max(ax0 - bx1, bx0 - ax1, 0)
    dy = max(ay0 - by1, by0 - ay1, 0)
    return float((dx * dx + dy * dy) ** 0.5)


def _expand_one_box_for_halo(
    gray_full: np.ndarray,
    box: tuple,
    cap_px: int,
    clip_px: tuple,
) -> tuple:
    """박스 하나를, 바로 바깥의 배경과 뚜렷이 다른 색이 이어지는 만큼만
    (최대 `cap_px`까지, `clip_px` 영역을 벗어나지 않는 선에서) 네 방향
    독립적으로 넓힌다 -- Canny로는 못 잡는 저대비 헤일로/번짐 테두리를
    박스 안에 포함시키기 위함(2026-09-14, 좋은 칼선 기준 맞추기).

    한 줄(row)/한 칸(col)씩 바깥으로 이동하며, 그 줄 전체 중 배경과
    뚜렷이 다른 픽셀의 비율이 30% 미만이면(=거의 다 배경) 그 자리에서
    멈춘다 -- 진짜 배경에 닿는 순간 정확히 멈추므로 열린 배경 공간을
    무한정 잠식하지 않는다. `cap_px`는 호출하는 쪽이 이웃 박스와의 실제
    간격의 절반으로 미리 제한해서 넘겨주므로, 이 함수 자체는 그 한도
    안에서만 움직이면 서로 다른 도안끼리 절대 겹치지 않는다."""
    if cap_px <= 0:
        return box
    x0, y0, x1, y1 = box
    cx0, cy0, cx1, cy1 = clip_px
    h, w = gray_full.shape[:2]
    cx0, cy0 = max(0, cx0), max(0, cy0)
    cx1, cy1 = min(w, cx1), min(h, cy1)

    ring = 4
    bx0, by0 = max(cx0, x0 - ring), max(cy0, y0 - ring)
    bx1, by1 = min(cx1, x1 + ring), min(cy1, y1 + ring)
    if bx1 <= bx0 or by1 <= by0:
        return box
    outer = gray_full[by0:by1, bx0:bx1]
    inner_mask = np.zeros(outer.shape, dtype=bool)
    iy0, ix0 = max(0, y0 - by0), max(0, x0 - bx0)
    iy1, ix1 = min(outer.shape[0], y1 - by0), min(outer.shape[1], x1 - bx0)
    if iy1 > iy0 and ix1 > ix0:
        inner_mask[iy0:iy1, ix0:ix1] = True
    ring_px = outer[~inner_mask]
    if ring_px.size < 20:
        return box

    bg_mean = float(ring_px.mean())
    bg_std = float(ring_px.std())
    thresh = max(8.0, 3.0 * bg_std)

    new_x0, new_y0, new_x1, new_y1 = x0, y0, x1, y1
    for d in range(1, cap_px + 1):
        cx = x0 - d
        if cx < cx0:
            break
        col = gray_full[y0:y1, cx]
        if col.size == 0 or np.mean(np.abs(col.astype(np.float32) - bg_mean) > thresh) < 0.3:
            break
        new_x0 = cx
    for d in range(1, cap_px + 1):
        cx = x1 - 1 + d
        if cx >= cx1:
            break
        col = gray_full[y0:y1, cx]
        if col.size == 0 or np.mean(np.abs(col.astype(np.float32) - bg_mean) > thresh) < 0.3:
            break
        new_x1 = cx + 1
    for d in range(1, cap_px + 1):
        cy = y0 - d
        if cy < cy0:
            break
        row = gray_full[cy, x0:x1]
        if row.size == 0 or np.mean(np.abs(row.astype(np.float32) - bg_mean) > thresh) < 0.3:
            break
        new_y0 = cy
    for d in range(1, cap_px + 1):
        cy = y1 - 1 + d
        if cy >= cy1:
            break
        row = gray_full[cy, x0:x1]
        if row.size == 0 or np.mean(np.abs(row.astype(np.float32) - bg_mean) > thresh) < 0.3:
            break
        new_y1 = cy + 1
    return (new_x0, new_y0, new_x1, new_y1)


def _expand_boxes_for_halo(
    gray_full: np.ndarray,
    boxes: list,
    clip_px: tuple,
    expand_ratio: float = 0.1,
    min_expand_px: int = 15,
    max_expand_px: int = 60,
) -> list:
    """`boxes`(이미 개수/분리가 다 확정된 최종 결과)를 하나하나 독립적으로
    넓힌다. 각 박스가 넓혀질 수 있는 한도는 (a) 그 박스 자신의 짧은 변
    기준 비율(`expand_ratio`, min/max_expand_px로 클램프 -- 실측: 캐릭터
    번짐 폭이 캐릭터 자체 크기의 약 10% 안팎이었음)과 (b) 그 박스와 가장
    가까운 "다른" 박스까지 실제 간격의 절반 중 더 작은 쪽으로 미리
    제한한다 -- 그래서 이 함수는 몇 개의 박스가 있는지, 어느 박스가 어느
    박스와 합쳐져야 하는지에는 전혀 관여하지 않고(그 판단은 이미 끝난 뒤
    호출됨) 오직 각 박스의 좌표만, 서로 겹치지 않는 한도 안에서 살짝
    넓혀 저대비 헤일로/번짐을 포함시킨다."""
    if len(boxes) == 0:
        return boxes
    result = []
    for i, box in enumerate(boxes):
        x0, y0, x1, y1 = box
        short_side = min(x1 - x0, y1 - y0)
        own_cap = max(min_expand_px, min(max_expand_px, int(round(short_side * expand_ratio))))
        nearest_gap = min(
            (_bbox_gap_px(box, boxes[j]) for j in range(len(boxes)) if j != i),
            default=None,
        )
        # 다른 박스가 하나도 없으면(이 칸/시트에 도안이 이거 하나뿐) 이웃과의
        # 간격 제한 자체가 의미 없으므로 own_cap만 적용한다 -- float('inf')
        # 를 그대로 쓰면 inf//2가 nan이 되어 int() 변환이 터짐(실제 재현:
        # 요소가 1개뿐인 칸에서 크래시).
        cap = own_cap if nearest_gap is None else min(own_cap, int(nearest_gap // 2))
        result.append(_expand_one_box_for_halo(gray_full, box, cap, clip_px))
    return result


def _sever_thin_bridges_for_split(mask_u8: np.ndarray) -> np.ndarray:
    """2026-09-14(6차, 실제 세트5 파일의 실제 칼선레이어와 대조해 발견 --
    "칼선이 여러 캐릭터를 하나로 크게 휘감는다", 실측 IoU 0.272): 배경에
    그려진 얇은 장식선(예: 캐릭터들이 올라앉은 "선반/바닥선")이 그 위에
    앉은 여러 캐릭터의 헤일로에 살짝씩 닿으면서, 서로는 전혀 안 닿는
    캐릭터 여러 개를(실측: 캐릭터 4개 + 장식 2개, 시트 절반 가까운 면적)
    Canny+hole-fill 단계에서 이미 하나의 거대한 블롭으로 묶어버리는 문제가
    실측으로 확인됐다. 이 블롭은 bbox 대비 채움 비율이 이미 충분히
    높아서(실측 48.8%) 기존의 "채움 비율이 낮으면 오프닝 재시도" 경로가
    아예 발동하지 않고, 그 뒤 워터쉐드도 이런 대규모 다중 병합까지는
    다 못 갈라서 최종 박스 하나가 실제 캐릭터 하나의 3~4배 면적을 그대로
    차지해버렸다(실측: 실제 칼선과 겹침 IoU 0.272 -- 캐릭터 몸통은
    맞는데 옆 캐릭터/장식/빈 배경까지 통째로 딸려 나옴).

    이 얇은 선 자체는 실측으로 두께가 6~7px 수준(캐릭터 몸통은 수백px)
    이라, 그보다 확실히 큰 오프닝(모폴로지 열기)이면 선만 끊어내고 진짜
    캐릭터 몸통은 거의 그대로 남는다. 다만 이 오프닝을 무조건 적용하면
    (a) 이 문제가 없는 평범한 칸에서도 실루엣이 자잘하게 깎여나가거나
    (b) 원래 진짜로 얇은 장식(가는 잎맥 무늬 등)이 사라질 위험이 있으므로,
    반드시 두 안전장치를 같이 둔다: 오프닝 후 남은 픽셀 수가 원래의 90%
    미만으로 줄면(=선이 아니라 진짜 내용까지 깎아냈다는 뜻) 오프닝 이전
    마스크를 그대로 쓰고, 오프닝으로 연결성분 개수가 실제로 늘지 않았으면
    (=애초에 쪼갤 다리가 없었다는 뜻, 회귀 위험만 있고 얻는 게 없음) 역시
    오프닝 이전 마스크를 그대로 쓴다. 이 두 조건을 다 통과했을 때만(=
    확실히 "가는 다리만 지워서 쪼개졌다"는 뜻일 때만) 오프닝 결과를
    채택한다 -- 이 함수는 박스 개수/분리 판정에만 쓰이고(`_detect_boxes_
    from_gray`), 실제 칼선 실루엣을 그리는 `_content_mask_from_gray`의
    다른 호출부(무테 실루엣 추적)에는 전혀 영향을 주지 않는다(이 함수가
    그 공유 함수 내부가 아니라 여기, `_detect_boxes_from_gray`에서만
    호출되기 때문)."""
    orig_area = int((mask_u8 > 0).sum())
    if orig_area == 0:
        return mask_u8
    short_side = min(mask_u8.shape[:2])
    kernel_px = _odd(max(5, min(25, short_side * 0.01)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_px, kernel_px))
    opened = cv2.morphologyEx(mask_u8, cv2.MORPH_OPEN, kernel)
    kept_ratio = int((opened > 0).sum()) / orig_area
    if kept_ratio < 0.9:
        return mask_u8
    n0, _, _, _ = cv2.connectedComponentsWithStats(mask_u8, connectivity=8)
    n1, _, _, _ = cv2.connectedComponentsWithStats(opened, connectivity=8)
    if n1 <= n0:
        return mask_u8
    return opened


def _detect_boxes_from_gray(
    gray: np.ndarray,
    min_area_ratio: float,
    close_ratio: float,
    edge_low: int,
    edge_high: int,
    pad_px: int,
    split_multi_component_holes: bool = False,
    suspicious_regions: list = None,
):
    """detect_design_bboxes_px와 detect_sub_element_boxes_px가 공유하는
    핵심 로직(Canny 엣지 -> hole-fill -> 붙어있는 덩어리 재분리 -> bbox
    추출). 둘의 차이는 오직 "어느 이미지(전체 시트 vs 격자 한 칸 크롭)를
    넘겨주느냐" 뿐이라서, 그 부분만 호출하는 쪽에서 각자 준비하고 이 함수는
    순수하게 gray 배열 하나만 갖고 동작한다(좌표는 그 gray 배열 기준
    로컬 좌표로 돌려줌 -- 원본 이미지 좌표로 옮기는 것은 호출하는 쪽 책임).

    `split_multi_component_holes`는 `_fill_enclosed_regions`로 그대로
    전달되는 옵션(기본 False=예전과 동일)이다 -- 2026-09-27/28 여백 없는
    시트 캐릭터 병합 문제 대응, 자세한 이유는 그 함수 문서 참고.

    2026-09-28(파스텔색 캐릭터가 저대비 배경과 만나면 몸통 실루엣이 통째로
    빠지는 문제, 실제 파일 3개(C, 그리고 다른 한 파일의 판다/토끼 캐릭터)로
    확인 -- 자세한 원인 분석은 세션 기록 참고): 이 버그를 "자동으로 고치는"
    시도(잔여 영역 병합, 배경 인페인팅 기반 재구성 등)는 전부 실측 결과
    회귀 위험이 있어 보류함
    (다른 캐릭터/장식을 하나로 잘못 합치는 사례가 실제로 재현됨). 대신
    훨씬 안전한 "의심 표시"만 추가한다 -- 아래 두 continue 지점(면적/색
    필터는 통과했지만 채움 비율이 끝까지 낮아 완전히 버려지는 컴포넌트)은
    바로 이 버그의 실제 발생 지점과 정확히 일치하므로, `suspicious_regions`
    가 주어지면(기본 None=완전히 기존과 동일, 아무 동작 변화 없음) 버려지는
    컴포넌트의 bbox를 그 칸 면적 대비 일정 비율 이상(작은 잡음/장식은
    제외)일 때만 거기에 기록한다. 이 리스트는 박스 목록/칼선 생성 어디에도
    영향을 주지 않는 순수 진단 정보이고, 호출하는 쪽(gui.app)이 나중에
    "이 부분 확인해 보세요" 안내에만 쓴다."""
    h, w = gray.shape[:2]
    mask_u8 = _content_mask_from_gray(
        gray, close_ratio, edge_low, edge_high,
        split_multi_component_holes=split_multi_component_holes,
    )

    # 2026-09-26(실제 파일 "강아지+쿠션" 칼선이 둘로 쪼개지는 문제 대응):
    # 절단(_sever_thin_bridges_for_split) 전의 마스크를 남겨둔다. 이 절단은
    # "서로 다른 캐릭터 여러 개를 우연히 이어주는 얇은 배경 장식선"을 끊어
    # 내라고 만든 것인데, 실제로는 하나의 캐릭터 자신의 얇은 부위(다리/끈 등)
    # 까지 같은 두께라는 이유로 같이 끊어버릴 수 있다(실측: 강아지 상반신과
    # 깔고 앉은 쿠션이 진짜 하나의 손그림 칼선인데, 절단 후 둘로 갈라짐 --
    # 아래 별개 절 참고). 아래에서 절단 전/후를 비교해 "절단이 실제로 무언가
    # 를 갈랐는지", 갈랐다면 "그 전에는 어느 블롭 하나였는지"를 표시해 두고,
    # 이 정보로 병합 판단(아래 min_contact_ratio 루프)에서 필요하면 다시
    # 다리를 이어붙일 수 있게 한다.
    mask_before_sever = mask_u8
    mask_u8 = _sever_thin_bridges_for_split(mask_u8)
    sever_changed = not np.array_equal(mask_before_sever > 0, mask_u8 > 0)
    pre_sever_labels = None
    if sever_changed:
        _, pre_sever_labels = cv2.connectedComponents(
            (mask_before_sever > 0).astype(np.uint8), connectivity=8
        )
    # 절단에 실제로 쓰인 것과 똑같은 커널 크기(_sever_thin_bridges_for_split
    # 내부 공식과 동일) -- "절단으로 생긴 틈을 다시 다리로 잇는" 판단에서,
    # 그 틈이 정말 이 절단 때문에 생긴 정도(대략 이 커널 폭 안쪽)인지 가늠하는
    # 데 쓴다. 함수를 새로 고치지 않고 여기서 같은 공식을 그대로 재사용한다.
    bridge_kernel_px = _odd(max(5, min(25, min(h, w) * 0.01)))
    bridge_dilate_iters = max(2, -(-bridge_kernel_px // 2) + 3)  # -(-a//b) == ceil(a/b), 정수 나눗셈만으로 올림 계산

    # 2026-09-12(54차 시도, 되돌림): 박스를 캐릭터 실제 픽셀에 더 타이트하게
    # 맞춰보려고 "팽창 전 원본 Canny 엣지 픽셀만으로 bbox 극값을 다시 재는"
    # 방식을 시도했었다. 합성 테스트는 전부 통과했지만, 실제 파일(파일1)의
    # 큰 배경 요소(화면 모서리에 걸쳐 잘린 벚꽃 잎 뭉치)로 직접 검증해보니
    # 그 블롭의 진짜 색 경계 엣지 픽셀이 한쪽에 몰려 있어서 bbox가 실제
    # 내용의 극히 일부(세로 2px짜리)로 붕괴하는 심각한 회귀가 눈으로 직접
    # 확인됨 -- 그래서 이 시도는 되돌리고, 아래는 원래(팽창/닫기/채우기
    # 끝난 마스크 자체의 픽셀 극값을 그대로 쓰는) 방식으로 유지한다. 다음에
    # 다시 시도한다면 "타이트해진 박스가 원래 박스 대비 지나치게(예: 60%
    # 이상) 줄어들면 못 믿고 원래 박스로 폴백"하는 안전장치를 반드시 같이
    # 넣어야 한다.
    min_area = max(1, int(min_area_ratio * w * h))

    # 2026-09-07: 실제 배경 틈이 없거나 아주 좁아서(서로 맞닿을 만큼 촘촘히
    # 배치된 스티커들) 하나로 합쳐진 블롭을, 거리 변환+워터쉐드로 다시
    # 나눠본다(_split_touching_blobs 참고). 원래부터 정말 하나였던 도안은
    # 그대로 하나의 라벨로 남는다.
    markers, marker_origin = _split_touching_blobs(mask_u8, min_area)

    # 2026-09-10(49차) 피드백("빈 슬롯에 칼선 생김" -- "자리 표시용" 빈
    # 테두리가 hole-fill로 꽉 찬 하나의 덩어리가 되어 진짜 도안처럼 박스로
    # 잡히던 문제, core.multi_design.cell_has_content_px에 적용한 것과 같은
    # 원인): 진짜 도안은 배경과 색이 달라야만 엣지로 잡히므로, 덩어리의
    # 원본 회색조 평균이 배경 평균과 사실상 같으면(테두리 선만 다르고
    # 안쪽은 배경 그대로인 빈 테두리) 박스로 만들지 않는다.
    background_pixels = gray[mask_u8 == 0]
    bg_mean = float(background_pixels.mean()) if background_pixels.size else float(gray.mean())
    max_bg_color_diff = 8.0  # 0~255 스케일

    # 2026-09-08(10차) 피드백("이중 칼선 개선") 관련 주의사항: 겹치는
    # bbox를 합치는 판단(_merge_overlapping_boxes)은 반드시 pad_px를
    # 더하기 "전"의 꼭 맞는(tight) bbox로 해야 한다 -- 서로 정말 다른
    # 도안이 가는 목에서 정확히 갈라진 경우, 딱 맞는 bbox끼리는 겹치지
    # 않고 그냥 한 점에서 맞닿을 뿐인데(실측: 겹침 면적 0), 각자에 pad_px
    # 여백을 먼저 더해버리면 그 여백만큼 서로 침범해 몇 픽셀짜리 가짜
    # 겹침이 생겨서(0이 아니게 됨) 서로 다른 두 도안이 도로 하나로 잘못
    # 합쳐질 수 있다. 그래서 먼저 tight box로 병합 여부를 정하고, pad_px는
    # 병합이 끝난 뒤 최종 박스에만 적용한다.
    # 2026-09-13 추가(채움 비율 필터): `_split_touching_blobs`의 bbox 수축
    # 버그를 고치고(그 함수 문서 참고) 실제 파일로 재검증하다가 새로 확인된
    # 문제 -- 밤하늘 그러데이션 배경(여러 조각으로 흩어진 별/구름 무늬가
    # Canny+hole-fill로 하나의 덩어리처럼 이어짐)이나 장식용 테두리의 바깥
    # 윤곽선(속이 빈 얇은 곡선)처럼, 칸 전체(또는 그에 가까운) 크기의 bbox를
    # 차지하면서도 정작 자기 bbox 안을 거의 안 채우는(듬성듬성하거나 속이
    # 빈) 덩어리가 실제 도안과 똑같이 "배경과 색이 다르다"는 이유만으로
    # tight_boxes 후보에 섞여 들어왔다. 이런 덩어리가 하나라도 섞이면
    # `_merge_overlapping_boxes`가(그 bbox 안에 놓인 작고 진짜인 도안들과
    # 필연적으로 겹치므로) 칸 안의 서로 다른 도안 전부를 그 배경/테두리
    # 하나로 뭉개버렸다(실측: 칸 하나에서 14개였어야 할 결과가 1개로 붕괴).
    #
    # 진짜 도안(캐릭터 몸통, 작은 반짝임 장식 등)은 아무리 작거나 뾰족한
    # 모양이어도 자기 bbox 안을 최소 절반 안팎은 채운다(실측 0.5~0.79),
    # 반면 배경 그러데이션/얇은 테두리 윤곽선은 실측 0.008~0.12로 확연히
    # 낮다 -- 그래서 자기 bbox 대비 실제 채워진 픽셀 비율이
    # min_fill_ratio(기본 20%) 미만이면 "속이 빈/듬성듬성한 배경 요소"로
    # 보고 애초에 tight_boxes 후보에서 제외한다.
    min_fill_ratio = 0.2

    # 2026-09-13(3차, "칼선이 붙어 있고" 진단 중 새로 발견): 위 채움 비율
    # 필터를 실제 곰 파일로 검증하다가, 진짜 캐릭터인데도 오히려 이 필터에
    # 걸려 통째로 사라지는 새로운 오탐이 확인됨 -- 캐릭터 몸통 자체는 한
    # 구석에 멀쩡히 뭉쳐 있는데, 화면 반대편까지 이어지는 아주 가느다란
    # (세로 몇 픽셀 안 되는) 잡음/장식 흔적이 워터쉐드 라벨 하나에 같이
    # 붙어버려서, bbox가 캐릭터 실제 크기의 몇 배로 부풀려지고 그만큼
    # 채움 비율이 뚝 떨어졌다(실측: 진짜 캐릭터 면적은 그대로인데 bbox가
    # 칸 전체 폭까지 늘어나 fill_ratio 0.10~0.13으로 배경 수준까지 떨어짐).
    #
    # 그렇다고 필터 자체를 없애거나 완화하면 정작 걸러야 할 진짜 배경
    # 덩어리(그러데이션/빈 테두리)도 다시 통과시키게 되므로, 필터를
    # 통과 못 한 라벨에 한해서만(이미 정상적으로 통과하는 라벨의 bbox는
    # 전혀 건드리지 않음-- 정상 케이스에 대한 회귀 위험을 원천 차단) "가는
    # 잡음만 제거하는" 작은 모폴로지 오프닝을 적용해 재시도한다. 오프닝은
    # 몇 픽셀 두께의 가느다란 잡음/실선은 지워도 실제 캐릭터 몸통처럼 어느
    # 정도 두께가 있는 덩어리는 거의 그대로 남기므로, 오프닝 후 남은 가장
    # 큰 연결 성분 하나의 bbox로 다시 채움 비율을 재보면 진짜 캐릭터는
    # 이제 정상 범위로 회복되고(실측 0.63~0.67), 진짜 배경/빈 테두리는
    # 오프닝을 해도 여전히 낮게 남는다. 그래도 기준을 못 넘으면 원래대로
    # 제외한다.
    short_side_for_open = min(w, h)
    open_px = _odd(max(5, min(25, short_side_for_open * 0.01)))
    open_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (open_px, open_px))

    tight_boxes = []
    tight_box_labels = []
    tight_box_areas = []
    # 2026-09-14(실제 파일로 발견, "칼선이 아예 없다"): 아래 오프닝-재시도
    # 경로가 여러 진짜 요소로 이미 갈라지는 경우(다음 설명 참고)에도, 항상
    # "가장 큰 조각 하나만" 남기고 나머지는 통째로 버려왔다 -- 그 나머지
    # 조각 각각의 tight_box를 만들 때는 기본값 None(= 아래 병합 판단에서
    # markers==lbl, 즉 원래 블롭 전체를 그대로 씀, 기존 동작과 100% 동일)을
    # 쓰지만, 이렇게 새로 여러 개로 쪼갠 조각들끼리는 그 판단에 원래 블롭
    # 전체가 아니라 각자의(이미 분리된) 마스크만 써야 한다 -- 자세한 이유는
    # 아래 그 조각을 실제로 만드는 자리의 주석 참고.
    tight_box_masks: list = []
    for lbl in sorted(set(np.unique(markers)) - {0, 1, -1}):
        comp_mask_full = markers == lbl
        ys, xs = np.where(comp_mask_full)
        area = xs.size
        if area < min_area:
            continue
        component_mean = float(gray[comp_mask_full].mean())
        color_matches_bg = abs(component_mean - bg_mean) <= max_bg_color_diff
        # 2026-09-14(90도 회전 진단 중 실제 파일로 새로 확인): bg_mean은 칸
        # 전체 배경 픽셀의 "전역 평균 하나"라서, 배경이 여러 색 띠/그러데이션
        # 으로 나뉜 실제 파일에서는 그 전역 평균이 특정 색 띠 쪽으로 쏠릴 수
        # 있다 -- 이번에 위 hole-fill 배경 인식 자체를 고치면서(같은 파일,
        # 같은 칸) 전역 평균이 이동했고, 하필 그 새 평균이 실제 캐릭터(연한
        # 크림톤 캐릭터, 실측 component_mean=179.0)와 겨우 3.45 차이로
        # 가까워져 진짜 캐릭터가 "빈 테두리"로 잘못 제외됨(실측 area=164783,
        # 정상적으로 fill_ratio=0.771까지 통과했었는데 색 필터에서만 걸림).
        # cell_has_content_px가 이미 같은 문제를 표준편차로 보강한 것과
        # 똑같은 안전장치를 여기도 추가한다 -- 진짜 빈 테두리 안쪽은 무늬가
        # 없어 표준편차가 낮은 반면(실측 기준 24.0 미만), 진짜 캐릭터는
        # 눈/옷 등 내부 디테일이 있어 표준편차가 훨씬 높다(이번 캐릭터
        # 실측 38.65) -- 색만 같고 내부까지 밋밋할 때만 제외한다.
        if color_matches_bg:
            is_flat_interior = float(gray[comp_mask_full].std()) <= 24.0
            if is_flat_interior:
                continue  # 배경과 색이 같고 내부에 디테일도 없음 -- 빈 테두리 등으로 보고 제외
        x0 = int(xs.min())
        y0 = int(ys.min())
        x1 = int(xs.max()) + 1
        y1 = int(ys.max()) + 1
        bbox_area = (x1 - x0) * (y1 - y0)
        if bbox_area > 0 and (area / bbox_area) < min_fill_ratio:
            opened = cv2.morphologyEx(comp_mask_full.astype(np.uint8), cv2.MORPH_OPEN, open_kernel)
            n_open, open_labels, open_stats, _ = cv2.connectedComponentsWithStats(opened, connectivity=8)
            if n_open <= 1:
                if suspicious_regions is not None and bbox_area >= 0.05 * h * w:
                    suspicious_regions.append((x0, y0, x1, y1))
                continue  # 오프닝으로도 남는 게 없음 -- 진짜 잡음/배경으로 보고 제외

            # 2026-09-14(실제 파일로 발견, "칼선이 아예 없다"): 오프닝 후
            # 살아남은 조각이 "실제 도안 같은" 조건(자기 bbox 대비 채움
            # 비율이 min_fill_ratio 이상)을 만족하는 게 여러 개일 수 있다 --
            # 이건 애초에 "진짜 캐릭터 하나 + 화면을 가로지르는 가는 잡음
            # 하나"가 아니라, "서로 다른 진짜 요소 여러 개가 hole-fill/
            # 모폴로지 닫기 과정에서 가는 다리로 우연히 이어져 하나의
            # 블롭이 돼버린" 경우다(실측: 세트5 파일, 꽃 장식 하나가 옆의
            # 큰 캐릭터와 이렇게 이어져 있었음 -- 오프닝하면 큰 캐릭터
            # 조각과 꽃 조각이 분명히 갈라지는데, 예전 코드는 "가장 큰
            # 조각 하나만" 남기고 꽃 조각은 통째로 버렸다. 그 결과 꽃
            # 장식은 시트 전체에서 단 하나의 칼선도 안 만들어짐 -- 실제
            # 손그린 칼선레이어와 대조해 확인).
            #
            # 그래서 조건을 만족하는 조각을 "가장 큰 것 하나만"이 아니라
            # 전부 각자의 tight_box로 만든다. 다만 이 조각들의 병합 여부
            # 판단(아래 min_contact_ratio 루프)에는 원래 블롭 전체
            # (markers==lbl, 즉 잡음 다리로 이어진 상태 그대로)가 아니라
            # 이미 갈라진 각자의 마스크를 써야 한다 -- 그렇지 않으면 그
            # 판단이 "원래 블롭 전체끼리 겹치는가"를 보게 되어(둘 다 같은
            # lbl이라 사실상 자기 자신과 비교하는 셈) 다시 하나로 합쳐져
            # 버린다. 조각이 정확히 하나뿐이면(기존에 이미 검증된, "진짜
            # 캐릭터 하나 + 가는 잡음" 케이스) 예전과 완전히 동일하게
            # 동작하도록 tight_box_masks에 None(= markers==lbl 그대로 씀)을
            # 넣어 회귀 위험을 원천 차단한다.
            candidates = []
            for oi in range(1, n_open):
                st = open_stats[oi]
                ow, oh = int(st[cv2.CC_STAT_WIDTH]), int(st[cv2.CC_STAT_HEIGHT])
                oarea = int(st[cv2.CC_STAT_AREA])
                obbox_area = ow * oh
                if obbox_area <= 0 or oarea < min_area or (oarea / obbox_area) < min_fill_ratio:
                    continue
                ox0, oy0 = int(st[cv2.CC_STAT_LEFT]), int(st[cv2.CC_STAT_TOP])
                ox1, oy1 = ox0 + ow, oy0 + oh
                # 가는 잡음만 지운 것이지 실제 캐릭터 몸통은 아니므로, 최종
                # bbox는 (오프닝으로 깎여나가지 않은) 원본 마스크 기준으로 이
                # 핵심 영역 범위만 다시 재확인해 쓴다 -- 오프닝 자체의 침식으로
                # 캐릭터 가장자리 디테일이 손실되는 것을 막기 위함.
                core_mask = comp_mask_full & (
                    (np.arange(comp_mask_full.shape[0])[:, None] >= oy0)
                    & (np.arange(comp_mask_full.shape[0])[:, None] < oy1)
                    & (np.arange(comp_mask_full.shape[1])[None, :] >= ox0)
                    & (np.arange(comp_mask_full.shape[1])[None, :] < ox1)
                )
                cys, cxs = np.where(core_mask)
                if cxs.size == 0:
                    continue
                # 접촉 판정(아래 min_contact_ratio 루프)에는 core_mask를 쓰면
                # 안 된다 -- core_mask는 "bbox로 자른 것일 뿐"이라 서로 다른
                # 조각의 오프닝-후 bbox가 겹치면(실측: 세트5, 꽃 장식과 큰
                # 캐릭터의 오프닝 후 bbox가 75x120px 정도 겹쳤음) 그 겹친
                # 영역 안의 (오프닝 전) comp_mask_full 픽셀을 두 조각이
                # 동시에 나눠 가지게 되어 실제로는 서로소가 아니게 된다.
                # 그 결과 접촉 길이가 부풀려져 다시 하나로 합쳐지는 버그가
                # 실측으로 확인됨. open_labels==oi는 connectedComponentsWithStats가
                # 직접 만든 라벨이라 정의상 다른 oi끼리 절대 겹치지 않는
                # 진짜 서로소 마스크이므로, 접촉 판정에는 이것만 쓴다.
                touch_mask = open_labels == oi
                candidates.append((
                    int(cxs.min()), int(cys.min()), int(cxs.max()) + 1, int(cys.max()) + 1,
                    cxs.size, touch_mask,
                ))
            if not candidates:
                if suspicious_regions is not None and bbox_area >= 0.05 * h * w:
                    suspicious_regions.append((x0, y0, x1, y1))
                continue  # 가는 잡음을 지워도 여전히 듬성듬성함 -- 배경 그러데이션/테두리 윤곽선 등으로 보고 제외
            if len(candidates) == 1:
                x0, y0, x1, y1, area, _touch_mask = candidates[0]
                tight_boxes.append((x0, y0, x1, y1))
                tight_box_labels.append(int(lbl))
                tight_box_areas.append(area)
                tight_box_masks.append(None)
            else:
                for cx0, cy0, cx1, cy1, carea, touch_mask in candidates:
                    tight_boxes.append((cx0, cy0, cx1, cy1))
                    tight_box_labels.append(int(lbl))
                    tight_box_areas.append(carea)
                    tight_box_masks.append(touch_mask)
            continue
        tight_boxes.append((x0, y0, x1, y1))
        tight_box_labels.append(int(lbl))
        tight_box_areas.append(area)
        tight_box_masks.append(None)

    # 2026-09-13(2차, "칼선이 붙어 있고" 피드백): 원래 서로 다른(별개의)
    # 워터쉐드 이전 블롭에서 나온 조각은 애초에 절대 합치지 않는다(위
    # `_merge_overlapping_boxes` 문서 참고). 문제는 "같은 원래 블롭"에서
    # 나온 조각들 사이에서도 여전히 남는다 -- 복슬복슬한 캐릭터 8마리가
    # 실제로는 배경으로 완전히 분리돼 있었는데도, 털 몇 가닥이 어쩌다
    # 화면 다른 지점에서 스치듯 이어져 있으면(예: 꼬리 끝과 다른 캐릭터의
    # 발끝) canny+hole-fill 상으로는 "하나의 커다란 원래 블롭"이 돼버린다.
    # 이 경우 peak_ratio 씨앗 분리는 8개의 진짜 캐릭터로 정확히 나누지만,
    # 그 8개는 "같은 원래 블롭 출신"이라는 이유만으로는 병합 후보에서
    # 배제되지 않는다.
    #
    # 실제 원본.칼선과 겹쳐서 비교해 확인된 사실: 정말로 하나였던 캐릭터가
    # 잘록한 허리에서 (잘못) 둘로 나뉜 경우, 두 조각이 맞닿는 경계선의
    # 길이(실측: 합성 재현 시나리오에서 88px, 두 조각 중 작은 쪽 면적의
    # 제곱근 대비 비율 0.79)는 매우 넓다 -- 원래 하나의 두꺼운 몸통이었던
    # 만큼 잘록해진 지점도 여전히 상당한 두께를 갖기 때문. 반면 실제로는
    # 서로 다른 캐릭터인데 털 몇 가닥으로만 스치듯 이어진 경우(실측: 실제
    # 곰 캐릭터 8마리 조합에서 나온 모든 쌍이 이 비율 0.17 이하, 대부분
    # 0.02 미만)는 그 경계선이 아주 짧다. 그래서 같은 원래 블롭 출신이라도
    # 두 조각이 맞닿는 실제 경계선 길이가(작은 쪽 면적의 제곱근 대비)
    # min_contact_ratio(기본 30%) 미만이면 "우연히 살짝 스친 별개의 캐릭터"
    # 로 보고 병합 후보에서 제외한다. 두 기준(합성 재현 0.79 vs 실제 곰 파일
    # 최댓값 0.17) 사이에 충분한 여유를 두고 고른 값.
    min_contact_ratio = 0.3
    dilate_kernel = np.ones((3, 3), np.uint8)
    n_tight = len(tight_box_labels)
    merge_group = list(range(n_tight))  # union-find 대신 간단히: 같은 그룹이면 같은 정수

    def _find(idx):
        while merge_group[idx] != idx:
            merge_group[idx] = merge_group[merge_group[idx]]
            idx = merge_group[idx]
        return idx

    def _union(idx_a, idx_b):
        ra, rb = _find(idx_a), _find(idx_b)
        if ra != rb:
            merge_group[rb] = ra

    # 2026-09-26(성능 회귀 대응, 실측: 실제 파일 하나에 125초 걸리던 것이
    # 이 캐시 추가만으로 근본 원인임이 확인됨 -- "파일 불러오니까 무거워
    # 지면서 렉걸렸어" 피드백): 아래 이중 루프(idx_i, idx_j 쌍 전부를
    # 비교)가 매 반복마다 `_touch_mask`/`_pre_sever_origin`/`_fill_ratio`/
    # `_aspect_ratio`를 다시 호출하는데, 이 함수들은 전부 `idx` 하나에만
    # 좌우되는 순수 함수(같은 idx면 항상 같은 결과)이면서도 그 안에서
    # 시트 전체 크기의 배열(`markers == label`, `np.where(mask)`)을 매번
    # 처음부터 다시 계산했다. 실측(cProfile)으로 확인된 실제 파일(10칸
    # 시트, 63개 최종 박스로 병합되기 전 후보 도안 1000개+ 규모)에서
    # 이 네 함수만으로 총 125초 중 약 108초를 차지 -- 후보 개수의 제곱에
    # 비례해 같은 전체-이미지 배열을 반복 재계산한 것이 원인이었다.
    # idx별로 한 번만 계산해 캐싱하면(아래 각 함수) 결과는 완전히 동일하게
    # 유지하면서 반복 재계산만 없앨 수 있다(순수 함수라 캐싱이 동작을
    # 바꾸지 않음, 회귀 없음 -- 실제 파일로 결과 동일함을 재검증함).
    _touch_mask_cache: dict = {}

    def _touch_mask(idx):
        cached = _touch_mask_cache.get(idx)
        if cached is not None:
            return cached
        # tight_box_masks[idx]가 있으면(오프닝으로 한 원래 블롭에서 여러
        # 진짜 요소를 갈라낸 경우) 그 조각 자신의 마스크를, 없으면(기존
        # 동작 그대로) markers==lbl을 쓴다 -- 위 tight_boxes 작성부 주석
        # 참고.
        explicit = tight_box_masks[idx]
        result = explicit if explicit is not None else (markers == tight_box_labels[idx])
        _touch_mask_cache[idx] = result
        return result

    # 2026-09-15(7차, 실측으로 발견 -- "고양이+초록 장식이 계속 하나로
    # 흡수됨"): 위 min_contact_ratio 하나만으로는 "크기가 많이 다른 두
    # 개체가 진짜로 살짝 맞닿은" 경우를 못 걸러낸다는 게 실측으로 확인됐다
    # -- 큰 고양이(면적 147420)와 훨씬 작은 장식(면적 22389, 약 6.6배 차이)
    # 이 실제로 서로 다른 개체인데도 접촉 비율이 0.535까지 나왔다(작은 쪽
    # 면적 제곱근을 분모로 쓰다 보니, 작은 쪽 입장에서는 그 접촉이 상대적
    # 으로 크게 보임). 반면 이 병합 로직이 원래 잡아야 하는 "진짜 하나였던
    # 캐릭터가 잘록한 허리에서 나뉜" 경우(위 테스트 참고)는 나뉜 두 조각의
    # 크기가 서로 비슷하다(실측 비율 약 0.98) -- 진짜 허리 분리는 애초에
    # "굵은 몸통 하나가 잘록해진 것"이라 양쪽 다 어느 정도 부피가 있어야
    # 하기 때문. 그래서 접촉 비율 조건에 더해, 두 조각의 면적 비율(작은
    # 쪽/큰 쪽)이 min_size_ratio 이상일 때만(=진짜 "비슷한 크기 둘로
    # 나뉜" 모양일 때만) 병합 후보로 삼는다 -- 크기가 많이 다른 쌍은 접촉
    # 비율이 아무리 높아도 "큰 개체 옆에 작은 개체가 살짝 닿은 것"으로 보고
    # 애초에 병합 후보에서 제외한다.
    min_size_ratio = 0.25

    # 2026-09-26(실제 파일 "강아지+쿠션" 칼선 분할 대응, 신규): 워터쉐드
    # 이전부터 이미 서로 다른 블롭이었던 두 조각도, 그보다 더 앞선 단계인
    # "얇은 다리 절단"(_sever_thin_bridges_for_split) 전에는 하나의 블롭
    # 이었다면 -- 즉 그 절단이 진짜로 만들어낸 틈이라면 -- 다시 다리를 놓아
    # 이어붙일 후보로 검토한다(실측 근거: 강아지 상반신 블롭과 쿠션 블롭이
    # 절단 전에는 시트 한 칸 전체를 덮는 하나의 블롭이었고, 실제 손그림
    # 칼선 레이어에서도 이 둘이 하나의 칼선이었음을 확인함). 자세한 안전
    # 장치(고정 팽창 폭, 채움 비율 가드)는 아래 각 조건 옆 주석 참고 --
    # 실제 파일 7종(작가의 여러 실제 작업 파일) 전체를 대상으로 검증해서
    # 결정한 값들이다.
    min_fill_ratio_for_bridge = 0.3
    # 아래에서 "다리 재연결"로 병합을 결정한 (idx_i, idx_j) 쌍을 따로
    # 기록해 둔다 -- 이 둘은 실제 픽셀 사이에 진짜 틈(수~십수 px)이 있어서
    # tight box끼리 겹치지 않으므로, 워터쉐드-허리분리 케이스만 염두에 두고
    # "bbox가 겹쳐야만 합친다"고 짠 아래 `_merge_overlapping_boxes`(min_overlap_
    # ratio 게이트)를 그대로 통과하지 못한다. 그래서 이 쌍만 별도로 표시해
    # 두고, `_merge_overlapping_boxes`를 부르기 직전에 이 두 tight box를
    # 미리 하나의 합집합 bbox로 늘려서(둘 다 똑같은 확장된 bbox를 갖게 됨 --
    # 그러면 자연히 100% 겹치므로) 그 겹침 게이트를 안전하게 통과하게 한다.
    bridge_pairs: list = []

    _pre_sever_origin_cache: dict = {}

    def _pre_sever_origin(idx):
        if pre_sever_labels is None:
            return None
        if idx in _pre_sever_origin_cache:
            return _pre_sever_origin_cache[idx]
        mask = _touch_mask(idx)
        ys, xs = np.where(mask)
        result = None if ys.size == 0 else int(pre_sever_labels[ys[0], xs[0]])
        _pre_sever_origin_cache[idx] = result
        return result

    def _fill_ratio(mask):
        ys, xs = np.where(mask)
        if xs.size == 0:
            return 0.0
        bbox_area = (xs.max() - xs.min() + 1) * (ys.max() - ys.min() + 1)
        return xs.size / bbox_area if bbox_area else 0.0

    _fill_ratio_cache: dict = {}

    def _fill_ratio_by_idx(idx):
        if idx not in _fill_ratio_cache:
            _fill_ratio_cache[idx] = _fill_ratio(_touch_mask(idx))
        return _fill_ratio_cache[idx]

    max_aspect_ratio_for_bridge = 3.0

    def _aspect_ratio(mask):
        ys, xs = np.where(mask)
        if xs.size == 0:
            return 1.0
        bw = xs.max() - xs.min() + 1
        bh = ys.max() - ys.min() + 1
        return max(bw, bh) / max(1, min(bw, bh))

    _aspect_ratio_cache: dict = {}

    def _aspect_ratio_by_idx(idx):
        if idx not in _aspect_ratio_cache:
            _aspect_ratio_cache[idx] = _aspect_ratio(_touch_mask(idx))
        return _aspect_ratio_cache[idx]

    for idx_i in range(n_tight):
        lbl_i = tight_box_labels[idx_i]
        origin_i = marker_origin.get(lbl_i, lbl_i)
        touch_i = _touch_mask(idx_i)
        dil_i = cv2.dilate(touch_i.astype(np.uint8), dilate_kernel, iterations=1)
        bridge_origin_i = _pre_sever_origin(idx_i) if sever_changed else None
        bridge_dil_i = None
        fill_i = None
        if bridge_origin_i is not None:
            bridge_dil_i = cv2.dilate(touch_i.astype(np.uint8), dilate_kernel, iterations=bridge_dilate_iters)
            fill_i = _fill_ratio_by_idx(idx_i)
        for idx_j in range(idx_i + 1, n_tight):
            lbl_j = tight_box_labels[idx_j]
            area_i, area_j = tight_box_areas[idx_i], tight_box_areas[idx_j]
            size_ok = (
                max(area_i, area_j) > 0
                and (min(area_i, area_j) / max(area_i, area_j)) >= min_size_ratio
            )
            if marker_origin.get(lbl_j, lbl_j) == origin_i:
                if not size_ok:
                    continue  # 크기가 너무 많이 다름 -- 진짜 허리 분리가 아니라 "작은 개체가 옆에 닿은 것"
                contact_len = int((dil_i.astype(bool) & _touch_mask(idx_j)).sum())
                min_dim = min(area_i, area_j) ** 0.5
                if min_dim > 0 and (contact_len / min_dim) >= min_contact_ratio:
                    _union(idx_i, idx_j)
                continue
            # 원래부터(워터쉐드 이전에도) 서로 다른 블롭 -- 위 "절단 다리
            # 다시 잇기" 후보인지만 마지막으로 확인한다. 아래 조건 중 하나라도
            # 안 맞으면 기존 그대로(회귀 없음) 병합하지 않는다.
            if not size_ok or bridge_origin_i is None:
                continue
            if _pre_sever_origin(idx_j) != bridge_origin_i:
                continue  # 절단 이전에도 이미 서로 다른 블롭이었음 -- 절단과 무관
            touch_j = _touch_mask(idx_j)
            # 장식용 배경선처럼 길쭉하고 속이 빈 모양이 우연히 진짜 캐릭터와
            # 가까워서 걸리는 경우를 걸러낸다(실제 파일 실측으로 발견 --
            # 그런 모양은 자기 bbox 채움 비율이 1~25% 수준으로 뚜렷하게 낮음,
            # 반면 진짜로 갈라진 캐릭터 두 조각은 65~99% 수준).
            if fill_i < min_fill_ratio_for_bridge or _fill_ratio_by_idx(idx_j) < min_fill_ratio_for_bridge:
                continue
            # 2026-09-26(실제 파일로 추가 발견 -- 채움 비율 가드만으로는 부족함):
            # 점선/구슬 모양으로 이어진 얇고 긴 장식선의 일부 조각은, 그
            # 조각 자체의 bbox 채움 비율이 우연히 30%를 넘을 수 있다(예:
            # 실측 세로 925px짜리 가늘고 긴 조각이 채움 비율 0.321로 위
            # 가드를 통과함) -- 다만 이런 조각은 예외 없이 한쪽으로 아주
            # 길쭉하다(실측 가로세로 비율 약 54:1, 진짜로 갈라진 캐릭터
            # 두 조각은 실측 1.0~1.9 수준). 그래서 자기 bbox의 긴 변/짧은
            # 변 비율이 max_aspect_ratio_for_bridge를 넘으면(장식선처럼
            # 길쭉한 모양) 후보에서 제외한다.
            if _aspect_ratio_by_idx(idx_i) > max_aspect_ratio_for_bridge or _aspect_ratio_by_idx(idx_j) > max_aspect_ratio_for_bridge:
                continue
            # 절단에 실제로 쓰인 커널 폭만큼만(bridge_dilate_iters, 위에서
            # 계산) 고정으로 팽창시켜 다시 닿는지 본다 -- 이 폭을 넘어서는
            # (즉 절단과 무관하게 원래 멀리 떨어져 있던) 서로 다른 캐릭터는
            # 아무리 늘려도 안 닿으므로 자연히 후보에서 빠진다.
            bridge_dil_j = cv2.dilate(touch_j.astype(np.uint8), dilate_kernel, iterations=bridge_dilate_iters)
            bridge_contact = int((bridge_dil_i.astype(bool) & bridge_dil_j.astype(bool)).sum())
            min_dim = min(area_i, area_j) ** 0.5
            if min_dim > 0 and (bridge_contact / min_dim) >= min_contact_ratio:
                _union(idx_i, idx_j)
                bridge_pairs.append((idx_i, idx_j))

    # 위 주석 참고: "다리 재연결"로 합쳐진 쌍은 실제로 tight box끼리 떨어져
    # 있으므로, 아래 `_merge_overlapping_boxes`의 겹침 비율 게이트를 통과할
    # 수 있게 미리 두 box를 합집합 bbox로 늘려둔다(둘 다 같은 확장된 bbox를
    # 갖게 되어 100% 겹침이 되므로 안전하게 통과함).
    for bi, bj in bridge_pairs:
        ux0 = min(tight_boxes[bi][0], tight_boxes[bj][0])
        uy0 = min(tight_boxes[bi][1], tight_boxes[bj][1])
        ux1 = max(tight_boxes[bi][2], tight_boxes[bj][2])
        uy1 = max(tight_boxes[bi][3], tight_boxes[bj][3])
        tight_boxes[bi] = (ux0, uy0, ux1, uy1)
        tight_boxes[bj] = (ux0, uy0, ux1, uy1)

    tight_box_groups = [_find(idx) for idx in range(n_tight)]
    merged_tight = _merge_overlapping_boxes(tight_boxes, origins=tight_box_groups)
    boxes = []
    for x0, y0, x1, y1 in merged_tight:
        boxes.append(
            (max(0, x0 - pad_px), max(0, y0 - pad_px), min(w, x1 + pad_px), min(h, y1 + pad_px))
        )
    return boxes


def cell_has_content_px(
    image_path: str,
    cell_px: tuple,
    min_content_ratio: float = 0.01,
    close_ratio: float = 0.006,
    edge_low: int = 25,
    edge_high: int = 80,
) -> bool:
    """2026-09-10(44차) 피드백("아래의 두칸은 왜 인식이 되는거야", "불필요한
    부분이 있어" -- 실제 시트 캡처로 두 번 재현 확인된, 빈 칸이 도안으로
    잘못 처리되던 문제): `cell_px`(재단선 격자 한 칸) 안에 실제로 인쇄될
    내용이 있는지, 아니면 비어 있는 슬롯인지를 가볍게 확인한다.

    작가 본인이 이미 그려 놓은 진짜 재단선 격자(core.ai_cutline_reader.
    load_real_grid_cells)는 "여기가 스티커 한 장이 놓일 자리"라는 배치
    정보일 뿐, 그 자리에 실제로 그림이 그려져 있는지는 보장하지 않는다
    (인쇄 발주 시트 템플릿에 아직 안 채운 빈 슬롯이 남아 있는 경우가
    실측으로 확인됨). 반면 `detect_design_bboxes_px`(픽셀에서 직접 도안을
    찾는 경로)는 애초에 Canny 엣지+최소 면적 기준을 통과한 곳만 박스로
    만들기 때문에 이런 빈 슬롯을 스스로 걸러낸다 -- 문제는 진짜 격자를
    그대로 믿고 쓰는 경로(`gui.app._expand_grid_cells_into_sub_elements`)에는
    이 확인이 아예 없어서, 빈 슬롯도 "도안 하나"로 그대로 넘어가 칼선까지
    만들어졌다는 것.

    같은 Canny 엣지 기반 내용 마스크(`_content_mask_from_gray`, 이 칸 크기
    기준으로 새로 계산 -- detect_design_bboxes_px가 시트 전체를 기준으로
    already 계산한 마스크를 재사용하지 않는 이유: 격자 칸 하나하나의 실제
    크기가 시트 전체보다 훨씬 작아서, close_ratio 등 크기에 비례한 파라미터가
    시트 전체 기준일 때와 다르게 작동해야 정확함 -- detect_sub_element_boxes_px
    가 이미 같은 이유로 자기만의 크롭을 새로 만드는 것과 동일한 원리)를 써서,
    그 칸 안에서 마스크가 차지하는 비율이 `min_content_ratio`(기본 1%) 미만이면
    "빈 칸"으로 본다. 진짜 도안은 아무리 작아도 이보다는 훨씬 크게 색/선
    경계를 남기므로(캐릭터 하나만 있어도 최소 수십 퍼센트), 1%는 오탐(진짜
    도안을 빈 칸으로 잘못 판단) 위험 없이 진짜 빈 슬롯만 걸러내기에 넉넉한
    여유를 둔 값이다.

    2026-09-10(48차) 피드백("아래의 두칸은 왜 인식이 되는거야" 재발생):
    시트 전체가 하나의 배경 패턴(줄무늬/그러데이션 경계, 칸 구분선 등)을
    공유하는 경우, 실제 캐릭터가 없는 "진짜 빈 칸"도 그 배경 패턴 자체의
    색 경계 때문에 Canny 엣지가 1%를 넘을 수 있다는 게 이번에 새로 확인된
    한계 -- 44차의 단순 "전체 엣지 비율"만으로는 "배경이 그대로 이어지는
    빈 칸"과 "실제 캐릭터가 있는 칸"을 구분하지 못했다.

    그래서 마스크를 연결 요소(connected components)로 나눈 뒤, 칸 폭/높이의
    대부분(`max_span_ratio_for_band`, 기본 92%)을 가로지르면서도 두께는
    아주 얇은(`max_thin_ratio_for_band`, 기본 12%) 조각은 "줄무늬/구분선"으로
    보고 내용 비율 계산에서 제외한다 -- 실제 캐릭터는 아무리 작아도 이렇게
    칸 전체를 가로지르는 얇은 띠 모양이 되는 경우가 사실상 없어서(원이든
    사각형이든 폭/높이 양쪽 다 어느 정도 두께를 가짐), 진짜 도안을 잘못
    걸러낼 위험 없이 반복되는 배경 무늬만 정확히 골라낸다.

    2026-09-10(49차) 피드백("두번째/세번째 아래 공백에 칼선 생김" -- 48차
    수정 후에도 재발): 빈 슬롯 자리에 아직 그림은 안 채웠지만 "여기가 자리
    표시" 용 테두리/프레임만 얇게 그려져 있는 실제 경우가 새로 확인됨.
    `_content_mask_from_gray`의 hole-fill 단계는 닫힌 테두리의 안쪽을
    무조건 채우므로(캐릭터든 빈 테두리든 구분 안 함), 안이 완전히 빈
    테두리도 큰 덩어리 하나로 잡혀 48차의 "얇은 띠" 필터를 통과해버렸다.
    (처음엔 erode로 테두리 선만 벗겨내려 했으나, hole-fill이 이미 내부
    전체를 하나의 꽉 찬 덩어리로 채워놔서 erode로는 거의 안 줄어든다는
    것을 합성 테스트로 확인 -- 그래서 "모양"이 아니라 "색"으로 판단하는
    아래 방식으로 교체함.)

    구분법: 진짜 도안은 배경과 색이 달라야만 애초에 엣지로 잡힌다(그
    색 차이가 바로 캐릭터가 눈에 보이는 이유) -- 반면 빈 테두리의 안쪽은
    테두리 선만 배경과 다를 뿐, 그 안쪽 면적은 여전히 배경과 완전히 같은
    색이다. 그래서 각 덩어리의 "원본 회색조 평균값"을, 그 칸 안에서
    덩어리가 아닌 부분(배경으로 남은 부분)의 평균값과 비교해 거의 같으면
    (차이가 매우 작으면) 그 덩어리는 내용으로 세지 않는다 -- 배경 자체가
    다시 칠해진 것일 뿐 실제 그림이 아니라고 보는 것."""
    x0, y0, x1, y1 = [int(round(v)) for v in cell_px]
    with Image.open(image_path) as im:
        im = im.convert("RGB")
        w, h = im.size
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w, x1), min(h, y1)
        if x1 <= x0 or y1 <= y0:
            return False
        gray = np.array(im.crop((x0, y0, x1, y1)).convert("L"))
    mask = _content_mask_from_gray(gray, close_ratio, edge_low, edge_high)
    if mask.size == 0:
        return False
    ch, cw = mask.shape[:2]
    max_span_ratio_for_band = 0.92
    max_thin_ratio_for_band = 0.12
    max_bg_color_diff = 8.0  # 0~255 스케일 -- 배경과 이 정도밖에 안 다르면 "같은 색"으로 봄
    # 2026-09-12(59차) 피드백("동일 도안은 1개를 정확히 추적... 적용하기로
    # 했는데 추적이 각기 달라"): 실제 반복 시트(하늘/배경이 시트 전체에
    # 걸쳐 이어지는 연속 그러데이션인 파일)로 재현/확인된 새 실패 사례 --
    # 배경이 단색이 아니라 그러데이션이면, 같은 캐릭터가 반복되는 칸이라도
    # 칸마다 "배경이 이어지는 위치"가 달라 남는 배경 픽셀의 평균이 미묘하게
    # 다르다. 그중 한 칸에서는 우연히 그 배경 평균이 캐릭터 덩어리 자체의
    # 평균과 8.0 이내로 가까워져(실측: 진짜 빈 테두리 사례 diff=4.71인데
    # 이 칸은 diff=7.80으로 그보다 빈 쪽에 더 가까웠음), 색만으로는 "빈
    # 테두리"와 구분이 안 됐다 -- 그 결과 이 칸 하나만 "빈 칸"으로 잘못
    # 걸러져 반복 그룹에서 빠지고, 그룹 전체가 공유해야 할 대표 칼선 대신
    # 따로 재추적되어(그래서 "추적이 각기 달라") 시트 안에서 유독 한 칸만
    # 결과가 달라 보였다.
    #
    # 색만으로 안 될 때, 실제 빈 테두리(자리 표시용)와 진짜 그려진 캐릭터를
    # 가르는 또 다른 확실한 신호가 있다: 빈 테두리의 안쪽은 hole-fill로
    # 다 채워져도 그 안이 실제로는 거의 한 가지 색(내부에 그림 디테일이
    # 전혀 없음)이라 표준편차가 아주 낮은 반면, 실제 캐릭터는 눈/옷/무늬 등
    # 내부 디테일이 있어 표준편차가 뚜렷하게 높다(실측: 진짜 빈 테두리
    # std=20.05, 위 실패 사례 캐릭터 칸 std=57.41 -- 완전히 다른 구간).
    # 작은 단색 캐릭터(예: 동그라미 하나만 채운 경우, 회귀 테스트
    # test_blank_cell_hollow_frame 참고)까지 감안해도 std=27.91로 여전히
    # 빈 테두리보다 뚜렷이 높았다 -- 그 사이(24.0)를 기준으로 삼는다. 색
    # 기준(맞고 다름)은 그대로 두고, "색도 배경과 같고 + 내부도 무늬 없이
    # 밋밋함" 둘 다 만족할 때만 빈 테두리로 판정하도록 안전장치를 하나 더
    # 추가한 것이라, 기존에 이미 검증된 회귀 테스트(순수 단색 빈 테두리,
    # 줄무늬 배경)는 전혀 건드리지 않는다.
    max_flat_interior_std = 24.0
    n_labels, labels, stats, _centroids = cv2.connectedComponentsWithStats(
        (mask > 0).astype(np.uint8), connectivity=8
    )
    background_pixels = gray[mask == 0]
    bg_mean = float(background_pixels.mean()) if background_pixels.size else float(gray.mean())
    real_area = 0
    for label in range(1, n_labels):  # 0번 라벨은 배경
        area = int(stats[label, cv2.CC_STAT_AREA])
        bw = int(stats[label, cv2.CC_STAT_WIDTH])
        bh = int(stats[label, cv2.CC_STAT_HEIGHT])
        spans_full_width = bw >= cw * max_span_ratio_for_band
        spans_full_height = bh >= ch * max_span_ratio_for_band
        is_thin_band = (
            (spans_full_width and bh <= ch * max_thin_ratio_for_band)
            or (spans_full_height and bw <= cw * max_thin_ratio_for_band)
        )
        if is_thin_band:
            continue
        component_pixels = gray[labels == label]
        component_mean = float(component_pixels.mean())
        color_matches_bg = abs(component_mean - bg_mean) <= max_bg_color_diff
        is_flat_interior = float(component_pixels.std()) <= max_flat_interior_std
        if color_matches_bg and is_flat_interior:
            continue  # 배경과 색이 같고 내부에 디테일도 없음 -- 빈 테두리 등으로 보고 제외
        real_area += area
    ratio = float(real_area) / float(mask.size)
    return ratio >= min_content_ratio


def expand_box_to_neighbor_midpoint_px(
    box_px: tuple, other_boxes_px, image_size_px: tuple
) -> tuple:
    """
    2026-09-10(46차) 피드백("도무송 칼선이 자꾸 사라져... 안쪽에 생성 안
    됬어"): 도무송/완칼은 옆 도안을 침범하지 못하도록 "지금 칸 자체"를
    bounds_px로 넘겨왔다(7차). 그런데 도무송은 그와 별개로 도안에서 최소
    15mm는 떨어져야 한다는 최소 여유 규칙도 있다(40차, MIN_DOMUSONG_GAP_MM --
    53차에 실제 인쇄소 가이드 파일 실측을 근거로 2.0mm로 낮아졌지만, 아래
    설명은 이 함수가 만들어진 당시 상황 그대로 남겨둔다). 시트 안 칸들이
    서로 딱 붙어 있거나 겹쳐 있으면(실제 스티커 시트에서 흔함) 이 둘이
    정면으로 충돌한다: 15mm 밖으로 밀려던 칼선이 곧바로
    "지금 칸" 경계에 다시 잘려나가, 도안 경계선과 완전히 겹쳐버려 화면에서
    "칼선이 사라진 것처럼" 보인다.

    이 함수는 "지금 칸"을 곧이곧대로 쓰는 대신, 실제 이웃 도안까지의 거리를
    직접 재서 그 중간 지점까지만 넓혀준다 -- 이웃이 있는 방향은 딱 그
    중간까지만(그래야 이웃 쪽도 반대로 넓혀도 서로 겹칠 일이 없음), 이웃이
    없는 방향은 시트 가장자리까지 자유롭게 넓힌다. 실제 칼선 두께는 이
    함수가 아니라 offset_mm/최소 간격 규칙이 정하므로, 넓혀준 만큼 칼선이
    커지는 게 아니라 "그만큼 여유 공간이 있다"는 사실만 알려주는 것 --
    이웃이 없는데 굳이 예전처럼 좁게 자를 이유가 없다.

    `box_px`: 지금 처리 중인 도안의 원래(좁은) 칸 (x0,y0,x1,y1).
    `other_boxes_px`: 같은 시트의 나머지 도안 칸들(자기 자신이 섞여 있어도
    무방 -- 좌표가 같으면 자동으로 건너뜀).
    `image_size_px`: (width, height) -- 이웃이 없는 방향의 상한.
    """
    x0, y0, x1, y1 = box_px
    w, h = image_size_px
    left, top, right, bottom = 0.0, 0.0, float(w), float(h)
    for other in other_boxes_px or []:
        ox0, oy0, ox1, oy1 = other
        if (ox0, oy0, ox1, oy1) == (x0, y0, x1, y1):
            continue
        vert_overlap = min(y1, oy1) - max(y0, oy0)
        if vert_overlap > 0:
            if ox1 <= x0:
                left = max(left, (ox1 + x0) / 2.0)
            if ox0 >= x1:
                right = min(right, (x1 + ox0) / 2.0)
        horiz_overlap = min(x1, ox1) - max(x0, ox0)
        if horiz_overlap > 0:
            if oy1 <= y0:
                top = max(top, (oy1 + y0) / 2.0)
            if oy0 >= y1:
                bottom = min(bottom, (y1 + oy0) / 2.0)
    # 원래 칸 자체보다 좁아지는 일은 절대 없어야 한다(안전장치).
    left = min(left, x0)
    top = min(top, y0)
    right = max(right, x1)
    bottom = max(bottom, y1)
    return (left, top, right, bottom)


def _box_overlap_ratio(a: tuple, b: tuple) -> float:
    """겹치는 면적을, 둘 중 더 작은 bbox 자신의 면적에 대한 비율로 돌려준다
    (겹치지 않으면 0.0). 절대 픽셀 수가 아니라 비율을 쓰는 이유: 시트/칸의
    해상도에 따라 '몇 픽셀 겹침'의 의미가 달라지기 때문."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(1, (ax1 - ax0) * (ay1 - ay0))
    area_b = max(1, (bx1 - bx0) * (by1 - by0))
    return inter / min(area_a, area_b)


def _merge_overlapping_boxes(boxes: list, min_overlap_ratio: float = 0.05, origins: list = None) -> list:
    """2026-09-08(10차) 피드백("칼선 안쪽에 또 다른 칼선이 추가된 이중칼선
    개선"): _split_touching_blobs의 거리 변환+워터쉐드 분리가, 진짜로는
    하나인 캐릭터(예: 귀/머리 부분과 몸통 부분 사이에 우연히 뚜렷한 '잘록한
    허리'가 생기는 경우)를 두 블롭으로 잘못 나눠버릴 때가 있다. 이 경우 두
    블롭의 bbox가 서로 상당히(면적 기준 실측 약 25%) 겹치는데, 그대로 각각
    실루엣 추적을 돌리면 둘 다 margin_px만큼 바깥으로 확장된 크롭을 쓰기
    때문에 서로의 영역을 침범해서 거의 같은 캐릭터를 살짝 다른 모양으로
    두 번 그리게 되고, 그 결과가 "칼선 안쪽에 또 다른 칼선이 있는" 이중
    칼선으로 보인다.

    그래서 bbox가 min_overlap_ratio(기본 5%) 이상 겹치는 블롭들은 여기서
    미리 하나의 bbox(합집합)로 합쳐서, 애초에 겹치는 두 개의 실루엣 추적이
    발생하지 않도록 한다.

    임계값을 단순 "겹치면 무조건"(0 초과)이 아니라 5%로 잡은 이유: 서로
    진짜 다른 두 도안이 가는 목에서 정확히 갈라진 경우(test_multi_design.py
    의 `_two_designs_touching_thin_neck`), 워터쉐드 경계선이 완벽한 직선이
    아니라서 둘의 bbox가 갈라진 지점에서 아주 살짝(실측 약 0.7%) 겹칠 수
    있다 -- 이건 "같은 도안이 잘못 나뉜 것"이 아니라 "다른 도안이 제대로
    나뉜 것"인데, 겹침이 조금이라도 있으면 무조건 합친다면 이런 진짜로
    분리돼야 할 경우까지 도로 하나로 합쳐버리는 회귀가 생긴다(실제로 처음
    구현에서 이 문제가 나서 5%로 조정함). 5%는 실측된 두 경우(진짜 분리
    0.7% vs 잘못 나뉜 캐릭터 25%) 사이에 넉넉한 여유를 두고 고른 값.

    2026-09-13: `_split_touching_blobs`의 bbox 수축 버그를 고치고 실제
    파일로 재검증하다가, 이 함수에 "크기가 아주 다른 두 bbox가 겹치면
    합치지 말라"는 안전장치를 먼저 시도했었다 -- 그런데 그건 "장식이
    본체보다 훨씬 작아도 거의 맞닿아 있으면 합쳐져야 하는" 정상 케이스
    (예: 토끼 몸통 + 작은 꽃 장식들)까지 막아버리는 회귀를 만들어서
    되돌렸다. 실제 원인은 이 함수가 아니라 애초에 "듬성듬성한/속이 빈
    모양(배경 그러데이션이나 테두리 장식선처럼 자기 bbox 안을 거의 안
    채우는 덩어리)"이 tight_boxes 후보 자체에 섞여 들어온 것이었다 --
    그래서 진짜 수정은 `_detect_boxes_from_gray`의 tight_boxes 필터에
    있다(그 함수의 채움비율 필터 설명 참고).

    2026-09-13(2차, "칼선이 붙어 있고. 하나의 개체로 생성이 안되고 있잖아"
    피드백): 실제 칼선을 원본과 겹쳐서 직접 비교해보고서야 드러난 문제 --
    이 함수는 bbox "사각형" 겹침 비율만 보는데, 복슬복슬한 털/삐죽삐죽한
    윤곽(예: 곰 캐릭터들)은 실제 그림(픽셀)은 서로 전혀 안 닿아 있어도
    (직접 측정: 실제 최단 거리 1.4px, 픽셀 교집합 0) 삐져나온 부분끼리
    bbox 사각형만 크게 겹칠 수 있다(실측 최대 21.7% -- 기존 5% 기준을
    가볍게 넘음). 이 경우 서로 완전히 다른 두 캐릭터가 하나의 칼선으로
    합쳐져 버렸다("칼선이 붙어있다"의 직접적인 원인).

    이 함수가 원래 합치려고 만든 경우("원래 하나였던 도안이 워터쉐드로
    잘못 둘로 나뉨", 위 문서 참고)와, 지금 새로 발견된 경우("원래부터
    배경으로 완전히 분리돼 있던 서로 다른 두 도안의 bbox가 삐죽한 모양
    때문에 우연히 겹침")를 bbox 겹침 비율만으로는 구분할 수 없다 -- 그래서
    호출하는 쪽(`_detect_boxes_from_gray`)이 각 bbox가 원래 어느 워터쉐드
    이전 블롭(`_split_touching_blobs`가 돌려주는 origin id)에서 나왔는지를
    `origins`로 같이 넘겨주면, "같은 원래 블롭에서 나온 조각끼리"만 이
    함수의 bbox 겹침 기준으로 합치고, "원래부터 서로 다른(배경으로 이미
    분리돼 있던) 블롭"끼리는 bbox가 아무리 많이 겹쳐도 합치지 않는다.
    같은 원래 블롭에서 나온 조각들은 애초에 서로 맞닿아 있던 하나의
    덩어리였으므로(그래서 워터쉐드가 필요했음) 이 구분이 정확히 원래
    의도한 두 경우를 갈라낸다. `origins`를 안 넘기면(다른 호출부/테스트
    호환) 원래 동작 그대로 모든 쌍을 검사한다."""
    if len(boxes) <= 1:
        return boxes
    merged = list(boxes)
    merged_origins = list(origins) if origins is not None else None
    changed = True
    while changed:
        changed = False
        for i in range(len(merged)):
            for j in range(i + 1, len(merged)):
                if merged_origins is not None and merged_origins[i] != merged_origins[j]:
                    continue  # 원래부터 서로 다른(별개의) 블롭 -- bbox만 겹쳐도 합치지 않는다
                if _box_overlap_ratio(merged[i], merged[j]) >= min_overlap_ratio:
                    ax0, ay0, ax1, ay1 = merged[i]
                    bx0, by0, bx1, by1 = merged[j]
                    merged[i] = (min(ax0, bx0), min(ay0, by0), max(ax1, bx1), max(ay1, by1))
                    del merged[j]
                    if merged_origins is not None:
                        del merged_origins[j]
                    changed = True
                break
            if changed:
                break
    return merged


def _box_gap_px(a: tuple, b: tuple) -> float:
    """두 bbox 사이의 실제 빈틈 거리(테두리와 테두리 사이, 픽셀 단위) --
    겹치거나 맞닿으면 0.0. x축/y축 간격을 각각 구해(겹치는 축은 0) 유클리드
    거리로 합쳐서, 대각선 방향으로 떨어진 경우도 정확히 잰다."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    dx = max(ax0 - bx1, bx0 - ax1, 0.0)
    dy = max(ay0 - by1, by0 - ay1, 0.0)
    return float((dx * dx + dy * dy) ** 0.5)


def cluster_boxes_by_gap_px(boxes: list, gap_px: float) -> list:
    """2026-09-10(50차): 칸 안에서 찾은 낱개 요소(boxes)를, 서로 gap_px
    이내로 가까운 것끼리 묶어 그 묶음의 합집합 bbox 리스트로 돌려준다
    (core.cutline_core.merge_close_elements가 폴리곤을 gap 기준으로 묶던
    것과 같은 원리를 bbox에 적용한 것).

    38차까지 썼던 "개수 + 크기 유사성"(is_decorative_frame_cluster) 기준은
    "캐릭터 하나에 딸린 장식 조각(얼굴/리본/작은 배지들/하트/달 장식 등)"이
    서로 크기가 다르면 통과하지 못하는 근본적인 한계가 있었다(38차 재발
    원인). 반면 장식 조각은 크기와 무관하게 거의 항상 원래 캐릭터 몸체에
    맞닿거나 아주 가깝게 붙어 있고(진짜 배경 틈이 사실상 없음), 반대로
    정말 서로 다른 캐릭터/스티커는 그 사이에 눈에 보이는 배경 공간을
    의도적으로 둔다 -- 이 "실제 거리" 차이는 크기/개수와 무관하게 성립하는
    더 근본적인 구별 기준이라고 보고 새로 도입.

    2026-09-12(60차) 피드백("얼굴만 추적됨 / 작은 도형 누락 / 옆 도형과
    뭉쳐서 과대추적" 3가지 실제 사례를 실제 칼선 레이어와 대조해 확인)에서,
    50차 구현이 union-find로 "A-B가 가깝고 B-C가 가까우면 A-B-C를 통째로
    묶는" 단일 연결(single-linkage) 방식이었다는 게 근본 원인으로 드러남 --
    실제 10칸 시트 파일 하나에서, 서로 다른 캐릭터/장식 6개가 각각은 이웃과
    51~69px 간격으로 놓여 있었는데(칸이 커서 gap_px 자체가 75.5px로 계산됨),
    사슬처럼 하나씩 이어지며 결국 폭 1006px짜리 박스 하나로 전부 뭉쳐버렸다
    (실제로 그 중 양 끝 요소끼리는 서로 680px 넘게 떨어져 있어 도저히 "하나의
    도안"일 수 없는데도). 그 결과 그 박스 하나에 GrabCut을 돌리면 일부 요소만
    잡히거나(나머지는 아예 누락) 엉뚱하게 큰 하나의 덩어리로 나왔다(사례 재현:
    qa_repro_pattern1_isolate.py, qa_check_boxes_idx3_idx6.py -- 스크래치패드).

    고친 방식(완전 연결, complete-linkage): 묶음 "안의 모든 쌍"이 gap_px
    이내일 때만 하나로 묶는다 -- 사슬의 중간만 가까운 경우는 묶지 않는다.
    이 방식은 수학적으로 기존 단일 연결 결과의 "부분집합"만 만들 수 있다
    (완전 연결로 묶이는 그룹은 반드시 단일 연결로도 같은 그룹에 속하므로,
    새로 과도하게 뭉치는 경우는 논리적으로 생길 수 없다 -- 오직 "덜 뭉치거나
    같거나"만 가능). 그래서 이미 실제 파일로 검증된, 정확히 2개짜리 장식
    조각 쌍(예: 얼굴 안 꽃 장식+코, 47~51px 간격으로 여전히 같은 캐릭터인
    사례, qa_calibrate_gap_threshold*.py로 확인)에는 전혀 영향이 없고
    (2개짜리 쌍은 단일/완전 연결이 항상 같은 결과), 3개 이상이 사슬로 잘못
    엮이는 경우만 바로잡는다. 실제 파일1 재현 데이터로 검증: 기존 방식은
    6개 요소를 전부 하나로 묶었지만, 이 방식은 [idx3+idx4]/[중간 요소+하트]/
    [idx7+나머지] 3개의 훨씬 작고 정확한 묶음으로 나눈다(정확히 6개 낱개로
    쪼개지진 않는데, 그중 몇 쌍은 여전히 51px 이내라 완전 연결 기준으로도
    묶이기 때문 -- 이 잔여 오탐은 순수 거리 기반 방식의 알려진 한계로 남아
    있고, 색 기반 보완도 같은 파일로 시도했으나 배경이 그라데이션이거나 큰
    칸 안에서 지역 배경 추정이 캐릭터 자체 색에 오염되는 문제로 아직
    안전하게 못 씀 -- 정직하게 미해결로 남겨둠, qa_test_content_based_gap.py/
    qa_test_local_bg_gap.py 참고)."""
    n = len(boxes)
    if n <= 1:
        return list(boxes)

    gap_cache: dict = {}

    def gap(i, j):
        key = (i, j) if i < j else (j, i)
        if key not in gap_cache:
            gap_cache[key] = _box_gap_px(boxes[i], boxes[j])
        return gap_cache[key]

    clusters = [[i] for i in range(n)]
    while True:
        best_pair = None
        best_dist = None
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                # 완전 연결 거리 = 두 묶음 사이 "가장 먼" 쌍의 거리 --
                # 이 값이 gap_px 이내여야 그 둘 안의 모든 요소쌍이 서로
                # gap_px 이내라고 보장된다.
                complete_dist = max(
                    gap(i, j) for i in clusters[a] for j in clusters[b]
                )
                if complete_dist <= gap_px and (best_dist is None or complete_dist < best_dist):
                    best_dist = complete_dist
                    best_pair = (a, b)
        if best_pair is None:
            break
        a, b = best_pair
        clusters[a] = clusters[a] + clusters[b]
        del clusters[b]

    clustered = []
    for group in clusters:
        group_boxes = [boxes[i] for i in group]
        x0 = min(b[0] for b in group_boxes)
        y0 = min(b[1] for b in group_boxes)
        x1 = max(b[2] for b in group_boxes)
        y1 = max(b[3] for b in group_boxes)
        clustered.append((x0, y0, x1, y1))
    return clustered


def is_decorative_frame_cluster(
    boxes: list,
    small_area_ratio: float = 0.15,
    small_cluster_max_ratio: float = 2.0,
    min_small_count: int = 3,
    min_small_fraction: float = 0.7,
) -> bool:
    """2026-09-10 피드백("칼선이 옆 칼선이랑 붙어 있거나 아예 인식했던
    요소를 인지하지 못해")으로 실제 파일 두 개(야경 배경 시트, 낱장 카드
    시트)를 재현해서 확인한 패턴: `detect_sub_element_boxes_px`가 한 칸/한
    영역 안에서 낱개 요소를 찾을 때, "카드형 도안 하나 + 그 테두리를 두른
    작은 장식(코너 꽃/잎 마크 등) 여러 개"인 경우를 실제로는 하나로 잘라야
    하는데 장식 마크 하나하나를 별도 도안으로 오인하는 경우가 실측으로
    확인됐다(실제 카드 한 장, 테두리에 거의 같은 크기의 잎 장식 7개 +
    중앙에 훨씬 큰 캐릭터 1개 -> 8개로 쪼개짐, 실제 칼선은 1개뿐).

    반면 실제로 여러 캐릭터가 나란히 있는 정상적인 경우(예: 밤하늘 배경에
    캐릭터/장식이 흩어져 있는 시트)는 크기가 이렇게 극단적으로 쏠리지
    않는다 -- 작은 것과 큰 것 사이에 자연스러운 분포가 있다(실측 비교:
    정상 케이스는 "작은 것들" 그룹 안에서도 크기가 최대 2배 가까이
    벌어지고, 전체의 절반 이하만 그 그룹에 속함).

    그래서 "가장 큰 박스 면적의 15% 미만인 작은 박스"가 (a) 전체의 70%
    이상을 차지하고 (b) 그 작은 박스들끼리도 크기가 서로 2배 이내로
    거의 똑같으면(장식 마크는 보통 다 같은 크기로 찍어내므로) -- 이건
    "카드 + 테두리 장식" 패턴으로 보고 True를 돌려준다(호출하는 쪽이 이
    경우 낱개로 쪼개지 않고 원래 영역 전체를 그대로 쓰도록)."""
    if len(boxes) < min_small_count + 1:
        return False
    areas = sorted((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in boxes)
    max_area = areas[-1]
    if max_area <= 0:
        return False
    small = [a for a in areas if a < small_area_ratio * max_area]
    if len(small) < min_small_count:
        return False
    if (len(small) / len(areas)) < min_small_fraction:
        return False
    if small[-1] > small[0] * small_cluster_max_ratio:
        return False
    return True


def _reading_order_sort(boxes: list) -> list:
    """자연스러운 읽기 순서(위 -> 아래, 왼쪽 -> 오른쪽)로 정렬 -- 세로
    위치를 대략적인 "행" 단위로 묶은 뒤, 같은 행 안에서는 가로 위치로
    정렬한다."""
    if not boxes:
        return boxes
    heights = [y1 - y0 for (_, y0, _, y1) in boxes]
    row_bucket = max(1, int(np.median(heights) * 0.6))
    return sorted(boxes, key=lambda b: (round(b[1] / row_bucket), b[0]))


def _is_gradient_sliver_box(
    box: tuple,
    other_boxes: list,
    aspect_ratio_min: float = 4.0,
    max_gap_px: float = 40.0,
    min_neighbor_area_ratio: float = 2.5,
    min_overlap_frac: float = 0.35,
) -> bool:
    """2026-09-26 실제 파일로 발견("자다니는 강아지+이불" 패널 위에 얇고
    긴 유령 박스 하나가 따로 잡혀, 그 옆 진짜 패널의 칼선과 겹쳐 보이던
    문제 -- 멍푸가 스크린샷으로 보여준 "칼선이 서로 침범한다" 증상의 실제
    원인 중 하나): 배경 자체가 부드러운 색 그러데이션(예: 밤하늘 -> 이불
    색으로 서서히 변하는 부분)이면, 그 그러데이션의 색이 갈라지는 지점을
    Canny가 진짜 경계선처럼 잡아서, 실제로는 아무 그림도 없는 좁고 긴
    "유령 조각"이 하나의 박스로 만들어질 수 있다(실측: 폭 320 x 높이 47px
    짜리, 안이 꽉 찬 것처럼 보여 기존 "속이 빈 테두리" 필터에도 안 걸림).

    실제로 확인해보니 이런 유령 조각은 항상 (1) 가로세로비가 매우
    극단적이고(실측 5~11:1), (2) 진짜 도안이 담긴 훨씬 더 큰 박스와 거의
    맞닿아 있다(실측 간격 9~11px)는 공통점이 있었다 -- 진짜 도안(캐릭터,
    소품, 배경 장식)은 아무리 작아도 이 정도로 한쪽만 극단적으로 길지
    않고, 설령 길쭉한 진짜 장식(리본 등)이라도 훨씬 더 큰 이웃과 거의
    안 떨어진 채 나란히 붙어 있는 경우는 실측 범위 안에서 확인되지 않았다.

    그래서 이 셋 다 만족할 때만("아주 길쭉함" + "훨씬 큰 이웃과 거의
    맞닿음" + "그 이웃과 같은 방향으로 상당 부분 겹침") 유령 조각으로 보고
    제외한다 -- 셋 중 하나라도 안 맞으면(예: 길쭉하지만 이웃과 멀리
    떨어진 진짜 장식) 절대 건드리지 않아 회귀 위험을 최소화했다. 시트
    전체 단위 감지(detect_design_bboxes_px)에서만 쓴다 -- 격자 한 칸 안의
    낱개 요소 분리(detect_sub_element_boxes_px)는 칸 경계에 딱 붙은 요소가
    흔하고 정상이라(예: 귀가 칸 가장자리에 닿는 경우), 거기서는 이 필터를
    쓰지 않는다."""
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    short_side, long_side = min(bw, bh), max(bw, bh)
    if short_side <= 0 or (long_side / short_side) < aspect_ratio_min:
        return False
    area = bw * bh
    horizontal = bw >= bh  # 가로로 긴 형태인지(아니면 세로로 긴 형태)

    for ob in other_boxes:
        if ob == box:
            continue
        ox0, oy0, ox1, oy1 = ob
        o_area = (ox1 - ox0) * (oy1 - oy0)
        if o_area < area * min_neighbor_area_ratio:
            continue
        if _bbox_gap_px(box, ob) > max_gap_px:
            continue
        if horizontal:
            overlap = max(0.0, min(x1, ox1) - max(x0, ox0))
            frac = overlap / bw if bw > 0 else 0.0
        else:
            overlap = max(0.0, min(y1, oy1) - max(y0, oy0))
            frac = overlap / bh if bh > 0 else 0.0
        if frac >= min_overlap_frac:
            return True
    return False


def detect_design_bboxes_px(
    image_path: str,
    min_area_ratio: float = 0.0004,
    close_ratio: float = 0.006,
    edge_low: int = 25,
    edge_high: int = 80,
    pad_px: int = 2,
):
    """Return a list of (x0, y0, x1, y1) bounding boxes in ORIGINAL image
    pixel coordinates, one per detected design, in a natural top-to-bottom,
    left-to-right reading order.

    Finds designs by their actual line/color-boundary content (Canny edges),
    not by comparing to an assumed background color -- this works the same
    whether the sheet's background is white, a solid color, or has stray
    print marks near the edges. Each design's outline encloses its own
    interior, which is filled in (see `_fill_enclosed_regions`) so a design
    becomes one solid blob rather than a hollow ring; a small morphological
    closing (`close_ratio`, capped so it can't grow unboundedly on very
    high-resolution sheets) bridges tiny gaps in an otherwise-closed outline
    (e.g. from anti-aliasing or a thin unclosed line), WITHOUT bridging the
    real empty gaps between separate designs.

    `min_area_ratio` filters out specks/noise smaller than that fraction of
    the total image area (crop marks, dust, JPEG artifacts) -- real designs
    are assumed to be meaningfully larger than that. Returns an empty list
    if nothing looks like a separate design (e.g. a blank image).
    """
    with Image.open(image_path) as im:
        im = im.convert("RGB")
        w, h = im.size
        gray = np.array(im.convert("L"))

    boxes = _detect_boxes_from_gray(
        gray, min_area_ratio, close_ratio, edge_low, edge_high, pad_px,
        split_multi_component_holes=True,
    )
    # 2026-09-26: 배경 그러데이션 경계가 만드는 "유령 조각"(위
    # _is_gradient_sliver_box 문서 참고)을 halo 확장 전에 제거한다 --
    # halo 확장은 이미 확정된 박스를 살짝 넓히기만 할 뿐이라, 이 시점에
    # 걸러야 이후 단계(칼선 생성)까지 그 유령 조각이 넘어가지 않는다.
    boxes = [b for b in boxes if not _is_gradient_sliver_box(b, boxes)]
    # 2026-09-14(좋은 칼선 기준 맞추기): 개수/분리는 위에서 이미 다 확정됐고,
    # 이제 그 최종 박스 하나하나만 저대비 헤일로만큼 살짝 넓힌다(자세한
    # 이유는 _expand_boxes_for_halo/_content_mask_from_gray 문서 참고).
    boxes = _expand_boxes_for_halo(gray, boxes, clip_px=(0, 0, w, h))
    return _reading_order_sort(boxes)


def detect_sub_element_boxes_px(
    image_path: str,
    region_px: tuple,
    min_area_ratio: float = 0.002,
    close_ratio: float = 0.006,
    edge_low: int = 25,
    edge_high: int = 80,
    pad_px: int = 2,
    suspicious_regions: list = None,
):
    """이미 "여기가 스티커 한 세트가 놓인 자리"라고 알고 있는 한 영역
    (region_px, 원본 이미지 픽셀 좌표 -- 실제 재단선 격자 한 칸 등) 안에서,
    그 안에 같이 그려진 개별 요소를 낱개로 찾아 각각의 bbox를 원본 이미지
    좌표로 돌려준다.

    2026-09-08 피드백("칼선이 요소를 인식하지 못하고 있어. 대부분의 요소는
    인물/도형/오브제/캐릭터로 구성 되어 있어 구분 기준을 만드는게 좋을것
    같아", "칼선을 선/색의 경계를 기준으로 형태감을 보는게 좋을 것 같아")로
    확인된 문제: 실제 시트를 보면 재단선 격자 한 칸 안에 캐릭터 여러 마리와
    꽃/잎 같은 장식이 서로 배경 틈을 두고 따로따로 놓여 있는 구성이 흔하다
    (실측: 실제 파일 하나에서 칸 하나에 너구리 모양 캐릭터 1마리 + 강아지
    모양 캐릭터 1마리 + 꽃 5송이 + 잎 여러 개 + 병아리/열매 모양 장식 여러
    개가 함께 놓여 있었는데, 작가 본인이 그려둔 실제 칼선 레이어를 보면 이
    하나하나가 전부 따로 닫힌 칼선을 갖고 있음). 그런데 기존 자동 인식은 재단선 격자가 있으면 그 칸 전체를
    통째로 GrabCut에 넘겨 "칸 = 도안 하나"로 잘라버렸다 -- 배경(하늘/잔디
    그라데이션 등)이 칸 가장자리까지 꽉 채워져 있어 칸 내부에 GrabCut이
    가를 만한 색 차이가 거의 없다 보니, 결과가 사실상 칸 사각형 그대로
    나와 버려서 실제 작가의 칼선과 전혀 다른 모양이 됐다.

    detect_design_bboxes_px와 완전히 같은 원리(색이 아니라 선/색 경계 자체를
    Canny로 찾고, 그 경계로 둘러싸인 안쪽을 채워 하나의 덩어리로 보는 방식)
    를 재사용하되, 이미지 전체가 아니라 이 region_px 안쪽만 잘라서 그 안
    에서만 돌린다 -- 서로 배경 틈으로 떨어진 요소는 각각 다른 블롭으로
    (예: 너구리와 강아지가 따로), 하나의 캐릭터 안에서 서로 맞닿은 색 부위
    (귀/몸통 등)는 여전히 하나의 블롭으로 남는다(캐릭터가 부위별로 쪼개져
    나오지 않도록).

    호출하는 쪽(gui.app)이 이 결과가 2개 이상이면 그 낱개 요소들을 각각
    독립된 "도안"으로 취급해 개별 실루엣을 추적하고, 1개 이하(전체가 서로
    맞닿은 캐릭터 하나뿐이거나 색 경계를 전혀 못 찾은 평범한 칸)면 안전하게
    이전처럼 region_px 전체를 그대로 하나의 도안으로 쓰도록 설계됨(회귀
    없음 -- 이 함수 자체는 항상 "찾은 것"만 보고하고 대체 여부는 판단하지
    않는다).

    `min_area_ratio`의 기본값이 detect_design_bboxes_px보다 큰 것(0.002 vs
    0.0004)은, 여긴 기준 면적이 전체 시트가 아니라 이미 훨씬 작은 칸 하나
    라서 같은 비율이면 절대 면적이 지나치게 작아져(예: 640x340px 칸 기준
    약 9x9px) 잔선/노이즈까지 요소로 잡을 위험이 커지기 때문.

    `suspicious_regions`: 2026-09-28(파스텔색 캐릭터 몸통 실루엣이 통째로
    빠지는 문제, 자세한 내용은 `_detect_boxes_from_gray` 문서 참고) --
    기본 None이면 이전과 완전히 동일(아무 동작 변화 없음). 리스트를 넘기면
    이 칸 안에서 "내용은 있는데 끝내 박스로 확정 못 한" 큰 영역의 bbox를
    원본 이미지 좌표로 그 리스트에 추가만 한다 -- 반환하는 boxes 자체에는
    전혀 영향 없는 순수 진단 정보."""
    rx0, ry0, rx1, ry1 = [int(round(v)) for v in region_px]
    with Image.open(image_path) as im:
        im = im.convert("RGB")
        w, h = im.size
        rx0, ry0 = max(0, rx0), max(0, ry0)
        rx1, ry1 = min(w, rx1), min(h, ry1)
        if rx1 <= rx0 or ry1 <= ry0:
            return []
        full_gray = np.array(im.convert("L"))
        gray = full_gray[ry0:ry1, rx0:rx1]

    local_suspicious = None if suspicious_regions is None else []
    boxes = _detect_boxes_from_gray(
        gray, min_area_ratio, close_ratio, edge_low, edge_high, pad_px,
        suspicious_regions=local_suspicious,
    )
    if suspicious_regions is not None:
        for (sx0, sy0, sx1, sy1) in local_suspicious:
            suspicious_regions.append((sx0 + rx0, sy0 + ry0, sx1 + rx0, sy1 + ry0))
    boxes = [(x0 + rx0, y0 + ry0, x1 + rx0, y1 + ry0) for (x0, y0, x1, y1) in boxes]
    # 2026-09-14(좋은 칼선 기준 맞추기): 개수/분리는 위에서 이미 다 확정됐고,
    # 이제 그 최종 박스 하나하나만 저대비 헤일로만큼 살짝 넓힌다 -- region_px
    # (이 칸) 경계는 넘지 않는다(다른 칸의 내용까지 침범하지 않도록).
    boxes = _expand_boxes_for_halo(full_gray, boxes, clip_px=(rx0, ry0, rx1, ry1))
    return _reading_order_sort(boxes)


def split_cell_into_sub_elements_px(
    image_path: str,
    cell_px: tuple,
    cluster_gap_ratio: float = 0.08,
    min_clusters: int = 2,
    suspicious_regions: list = None,
) -> list:
    """2026-09-10(50차) -- "강아지 배경의 스티커는 스티커마다 칼선이 제대로
    생성 안 됐다"(패널 하나 안 개별 스티커 여러 개가 하나로 뭉뚱그려짐)
    피드백에 대한, 이 기능의 세 번째 시도.

    앞선 두 번의 시도(gui.app._expand_grid_cells_into_sub_elements 문서
    참고)는 둘 다 "개수 + 크기"만으로 "칸 안 서로 다른 캐릭터 여러 개"와
    "캐릭터 하나에 딸린 장식 조각들"을 가르려다 실패했다(11차: 장식 하나
    하나를 낱개로 오인, 38차: 장식 조각 크기가 들쭉날쭉해서 안전장치를
    그냥 통과해버림). 이번엔 기준 자체를 바꿔서, `detect_sub_element_boxes_px`
    로 일단 색 경계 기준 낱개 요소를 다 찾은 뒤 `cluster_boxes_by_gap_px`로
    "실제 배경 틈이 이 칸 크기 대비 얼마나 되는가"를 기준으로 다시 묶는다
    -- 장식 조각은 크기와 무관하게 원래 몸체와 거의 맞닿아 있고(틈이 사실상
    0), 진짜 서로 다른 스티커는 그 사이에 뚜렷한 배경 공간을 의도적으로
    두기 때문에(구성 상 당연히 그래야 실물 스티커로 낱개 절단이 가능함),
    이 "칸 크기 대비 상대 거리"가 크기/개수보다 더 근본적인 구별 기준이라고
    보고 새로 설계함.

    묶은 뒤에도 여전히 클러스터가 `min_clusters`(기본 2) 미만이면(전부
    하나로 뭉쳐졌다 = 원래 하나의 캐릭터였다는 뜻) 원래 cell_px 하나만
    돌려주고, 혹시 클러스터링 후에도 "큰 덩어리 하나 + 작고 비슷한 크기의
    조각 여럿" 패턴(is_decorative_frame_cluster)이 남아 있으면 이중 안전
    장치로 역시 쪼개지 않는다(카드+테두리 장식 패턴에 대한 재확인).

    `cluster_gap_ratio`(칸의 짧은 변 기준 비율, 기본 8%)는 실제 캡처가
    아니라 합성 이미지로 설계/검증한 값이라, 이 함수가 코드/합성 테스트로는
    통과해도 그녀의 실제 파일에서 정확히 원하는 대로 나눠지는지는 재빌드
    후 실제 확인이 필요함 -- 여기서 "고쳤다"고 단정하지 않는다.

    2026-09-14 피드백("여전히 하나의 반복도안을 동일하게 처리 못함 / 부분적으로
    덜 인식되거나 한 요소에 여러 칼선이 중첩")으로 실제 파일 2개(곰 시트,
    너구리+강아지+꽃 시트)를 직접 재현해서 확인된 문제: 위 `cluster_boxes_
    by_gap_px`가 "칸 크기 대비 8% 이내 거리"라는 하나의 기준만 보는데, 이게
    실제 파일에서는 서로 완전히 다른 캐릭터/장식인데도 그 정도 거리 안에
    놓이는 경우가 흔했다(실측: 강아지 캐릭터와 옆에 있는 꽃 장식이 75px
    이내라는 이유만으로 하나로 합쳐짐 -- 실제로는 꽃이 강아지 몸통과
    맞닿는 지점의 "접촉 길이"가 자기 크기 제곱근 대비 10% 남짓밖에 안 되는
    남남 관계였음). 그 결과 한 요소는 GrabCut이 그 중 한쪽만 잡아 칼선이
    통째로 빠지고, 다른 칸에서는 두 요소가 한 덩어리로 뭉개진 이상한
    칼선이 나왔다 -- "부분적으로 덜 인식" + "한 요소에 여러 칼선 중첩"
    두 증상이 사실 같은 원인.

    반면 `detect_sub_element_boxes_px`는 이미 (2026-09-13 수정으로) 순수
    "칸 크기 대비 거리"가 아니라 "원래 같은 덩어리에서 나왔는지 + 실제로
    맞닿는 경계선이 자기 크기 대비 충분히 넓은지"(접촉 길이 비율)를 보고
    병합을 판단한다 -- 바로 위 `_merge_overlapping_boxes` 호출부 문서 참고.
    이 기준으로 직접 재확인해보니, 이 함수가 원래 지키려던 두 케이스(카드+
    테두리 장식, 캐릭터+장식 조각들)도 `cluster_boxes_by_gap_px` 없이
    `detect_sub_element_boxes_px` 결과만으로 이미 정확히 1개로 남고, "진짜
    서로 다른 스티커 4개" 케이스도 정확히 4개로 유지됐다(합성 테스트로
    확인) -- 즉 접촉 길이 기준이 거리 기준의 역할을 이미 포함하면서도 더
    정확하다. 그래서 이중으로 판단하던 거리 기반 재군집 단계를 걷어내고,
    `detect_sub_element_boxes_px`가 이미 내린 판단을 그대로 신뢰한다(중복
    제거이자 실제 버그 수정).

    같은 이유로 `is_decorative_frame_cluster`(개수+크기 비율만 보는 이중
    안전장치, 11차/38차 당시 기준)도 더 이상 여기서 쓰지 않는다 -- 실제로
    "캐릭터 하나 + 뚜렷이 떨어진 장식 여러 개"처럼 장식 개수가 많고 크기가
    서로 비슷하면(진짜로 다른 도안인데도) 이 개수+크기 기준에 우연히
    걸려서 다시 1개로 합쳐버리는 회귀가 재현됨(2026-09-14, 회귀 테스트
    test_repeat_aware_split_replaces_old_gui_wiring). `detect_sub_element_
    boxes_px`가 이미 접촉 길이 기준으로 훨씬 정확하게 판단했으므로, 그
    판단을 개수+크기라는 더 거친 기준으로 다시 뒤집을 필요가 없다.

    `suspicious_regions`: `detect_sub_element_boxes_px`와 동일(기본 None=
    동작 변화 없음, 진단용 리스트를 넘기면 그대로 이어받아 기록)."""
    raw_boxes = detect_sub_element_boxes_px(image_path, cell_px, suspicious_regions=suspicious_regions)
    raw_boxes = [b for b in raw_boxes if not _is_uncuttable_sliver_box(b, cell_px)]
    if len(raw_boxes) < min_clusters:
        return [tuple(cell_px)]
    return _reading_order_sort(raw_boxes)


def _is_uncuttable_sliver_box(
    box_px: tuple,
    cell_px: tuple,
    min_short_side_ratio: float = 0.02,
    min_short_side_px: float = 12.0,
    min_aspect_ratio: float = 4.0,
) -> bool:
    """2026-09-28(멍푸 피드백 "이중 칼선에 배경까지 칼선이 들어간게 왜
    해결이지", "무테는 이미지 안쪽에 칼선이 들어간다고 수 회 말했어.
    배경이미지를 같이 자르면 상품 가치가 없어"): 실제 10칸 시트 파일에서
    두 칸 사이의 얇은 배경 틈(실측 폭 7px x 높이 745px, 가로세로비 약
    106:1)이 칸 안 낱개 요소 분리 단계에서 "요소 하나"로 잡혀, 그 위에
    배경만 자르는 길쭉한 알약 모양 칼선이 생긴 것을 확인했다.

    `_is_gradient_sliver_box`(시트 전체용)는 "훨씬 큰 이웃과 맞닿음" 조건이
    필요하고 칸 가장자리에 붙은 정상 요소 때문에 낱개 분리 경로에서는
    의도적으로 안 쓰므로, 여기서는 그와 별개로 "물리적으로 따로 잘라낼 수
    없을 만큼 얇은가"만 본다: 짧은 변이 칸 짧은 변의 2%(실측 칸 945px
    기준 약 19px, 300dpi에서 약 1.6mm -- 최소 칼선 간격 2mm보다도 좁아
    독립된 스티커 조각이 될 수 없음) 미만 *이면서* 가로세로비가 4:1
    이상일 때만 버린다. 작은 점/원 같은 진짜 작은 요소는 가로세로비
    조건에 안 걸리고(게다가 이미 면적 기준 필터가 따로 있음), 길쭉하더라도
    폭이 충분한 진짜 장식(리본 등)은 짧은 변 조건에 안 걸린다."""
    x0, y0, x1, y1 = box_px
    bw, bh = float(x1 - x0), float(y1 - y0)
    short_side, long_side = min(bw, bh), max(bw, bh)
    if short_side <= 0:
        return True
    cx0, cy0, cx1, cy1 = cell_px
    cell_short = float(min(cx1 - cx0, cy1 - cy0))
    threshold = max(min_short_side_px, min_short_side_ratio * cell_short)
    return short_side < threshold and (long_side / short_side) >= min_aspect_ratio


def find_design_bbox_at_point_px(image_path: str, point_px, boxes_px=None, **kwargs):
    """2026-09-07 피드백("드래그로 이미지 선택하는게 어려워서 커서를 만들고
    원하는 위치에 두면 자동으로 인식하게 설정"): 드래그로 영역을 직접
    지정하는 대신, 화면에서 클릭 한 번 한 지점에 있는 도안을 자동으로
    찾아 그 도안 전체의 bbox를 돌려준다.

    point_px = (x, y) 원본 이미지 픽셀 좌표. 클릭한 지점이 detect_design_
    bboxes_px가 찾은 어느 박스 안에도 없으면(배경을 클릭했거나, 도안 경계에
    살짝 못 미친 경우), 가장 가까운 박스를 찾아보되 그 박스 자기 크기의
    절반보다 멀면 포기하고 None을 돌려준다(엉뚱한 도안이 선택되는 것을
    막기 위함). 도안이 아예 없으면 None.

    boxes_px: 2026-09-26 피드백(마우스를 가져다 대기만 해도 미리보기를
    보여주는 "호버 미리보기") 대응 -- 마우스가 움직일 때마다 이 함수를
    부르는데, 매번 detect_design_bboxes_px(시트 전체를 다시 훑는 무거운
    작업)를 다시 돌리면 화면이 멈춘다. 호출하는 쪽에서 이미 계산해 캐시해둔
    박스 목록이 있으면 이 인자로 넘겨서 재계산을 건너뛴다. 안 넘기면
    (기존 호출부는 그대로 동작) 예전처럼 여기서 직접 계산."""
    x, y = point_px
    boxes = boxes_px if boxes_px is not None else detect_design_bboxes_px(image_path, **kwargs)
    if not boxes:
        return None
    for box in boxes:
        x0, y0, x1, y1 = box
        if x0 <= x <= x1 and y0 <= y <= y1:
            return box

    def _dist(box):
        x0, y0, x1, y1 = box
        dx = max(x0 - x, 0, x - x1)
        dy = max(y0 - y, 0, y - y1)
        return (dx ** 2 + dy ** 2) ** 0.5

    nearest = min(boxes, key=_dist)
    x0, y0, x1, y1 = nearest
    max_dim = max(x1 - x0, y1 - y0)
    if _dist(nearest) <= max_dim * 0.5:
        return nearest
    return None


def group_identical_boxes_px(
    image_path: str,
    boxes_px: list,
    thumb_size: int = 48,
    max_mean_diff: float = 6.0,
    max_size_diff_px: float = 8.0,
) -> list:
    """
    2026-09-07(6차) 피드백("반복되는 스티커는 한개의 칼선을 먼저 완성하고
    나머지에 그대로 적용해"): 시트 한 장에 완전히 같은 도안이 여러 번
    반복되는 경우(흔한 구성 -- 같은 캐릭터가 격자로 몇 번씩 찍혀 있는 시트),
    detect_design_bboxes_px/실제 재단선 격자가 찾아낸 박스들 중 어느 것들이
    "진짜 같은 그림의 반복"인지 픽셀 내용으로 직접 확인해서 묶어준다.

    호출하는 쪽(gui.app._run_auto_detect_and_add_all)은 이 결과를 이용해
    그룹당 실루엣 추적(GrabCut)을 딱 한 번만 실제로 실행하고, 같은 그룹의
    나머지 박스에는 그 결과를 평행이동만 해서 그대로 복제한다 -- 그러면
    (1) 완전히 같은 인쇄물인데 반복마다 GrabCut이 조금씩 다르게 잡는 문제가
    사라지고(항상 정확히 같은 모양), (2) 반복이 많은 시트일수록 실제
    세그멘테이션 실행 횟수가 크게 줄어 자동 인식 전체 속도도 빨라진다.

    비교는 각 박스를 이미지에서 잘라 작은 정사각 축소본(thumb_size)으로
    만든 뒤 픽셀 평균 절대 차이(mean absolute difference)로 판단한다 --
    디지털로 찍어낸 완전히 같은 인쇄물은 축소본이 사실상 동일하고(수 단위
    이하), 진짜 다른 그림은 이보다 훨씬 크게 차이 나므로 엄격한 기본
    임계값(6.0, 0~255 스케일)으로도 오탐 없이 잘 갈린다. 크기가
    `max_size_diff_px`보다 다른 두 박스는 내용이 비슷해 보여도 절대 같은
    그룹으로 묶지 않는다 -- 한 박스에서 추적한 칼선을 그대로 평행이동해
    다른 크기의 박스 위에 얹으면 어긋날 수 있기 때문(안전 우선).

    2026-09-09(29차) 피드백("잘 생성된 칼선을 복제해서 재배치 하면 된다고
    했는데 왜 새로 생성해서 이중 칼선을 만들었어")로 실제 10칸 재단선
    격자 파일에서 재현/실측: 같은 반복 행(row)인데도 작가가 실제로 그려둔
    격자 칸의 높이가 945/947/941px처럼 몇 px씩 자연스럽게 들쭉날쭉했다.
    내용(픽셀) 차이는 실측 0.6~1.5(축소본 평균 절대 차이, 완전히 같은
    반복임을 뜻함)로 전혀 다르지 않았는데, 기존 `max_size_diff_px=2.0`이
    945 vs 941(차이 4px), 947 vs 941(차이 6px) 두 쌍을 크기만으로 걸러내
    "반복"으로 인식하지 못했다 -- 그 결과 그 칸들이 매번 새로 GrabCut
    실루엣 추적을 거쳐, 완전히 같은 인쇄물인데도 반복마다 조금씩 다른
    모양의 칼선이 새로 생겼다(대표 인스턴스의 칼선을 복제하는 대신). 실측된
    진짜 반복 사이의 최대 크기 차이(6px)에 여유를 두고 8.0으로 올렸다 --
    내용 비교(`max_mean_diff`)가 여전히 1차 판별 기준이므로, 크기만 비슷하고
    내용이 실제로 다른 도안까지 잘못 묶이는 일은 없다.

    Returns: 박스 인덱스 그룹들의 리스트. 각 그룹은 `boxes_px`에 대한
    인덱스 리스트이고, 그룹의 첫 인덱스가 "대표"(이것만 실제로 추적하고
    나머지는 복제)다. 입력 순서는 항상 보존된다(그룹 자체도, 그룹 안
    인덱스 순서도).
    """
    img = cv2.imread(image_path, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"Could not read image: {image_path}")
    h, w = img.shape[:2]

    thumbs = []
    sizes = []
    for x0, y0, x1, y1 in boxes_px:
        xi0, yi0 = max(0, int(round(x0))), max(0, int(round(y0)))
        xi1, yi1 = min(w, int(round(x1))), min(h, int(round(y1)))
        sizes.append((xi1 - xi0, yi1 - yi0))
        if xi1 <= xi0 or yi1 <= yi0:
            thumbs.append(None)
            continue
        crop = img[yi0:yi1, xi0:xi1]
        thumb = cv2.resize(crop, (thumb_size, thumb_size), interpolation=cv2.INTER_AREA)
        thumbs.append(thumb.astype(np.float32))

    n = len(boxes_px)
    used = [False] * n
    groups = []
    for i in range(n):
        if used[i]:
            continue
        group = [i]
        used[i] = True
        if thumbs[i] is not None:
            for j in range(i + 1, n):
                if used[j] or thumbs[j] is None:
                    continue
                if abs(sizes[i][0] - sizes[j][0]) > max_size_diff_px:
                    continue
                if abs(sizes[i][1] - sizes[j][1]) > max_size_diff_px:
                    continue
                diff = float(np.abs(thumbs[i] - thumbs[j]).mean())
                if diff <= max_mean_diff:
                    group.append(j)
                    used[j] = True
        groups.append(group)
    return groups


def map_box_between_frames_px(box_px: tuple, from_frame_px: tuple, to_frame_px: tuple) -> tuple:
    """box_px가 from_frame_px 사각형 안에서 차지하는 상대 위치(비율)를 그대로
    to_frame_px 사각형 안 절대 좌표로 옮겨 돌려준다 -- 실제 칼선 지오메트리에
    적용하는 core.repeat_grid.fit_cutline_result_to_box와 정확히 같은 선형
    변환을, 아직 칼선을 만들기 전 단계의 평범한 bbox 하나에 미리 적용한
    것(2026-09-11(51차), 아래 detect_repeat_aware_sub_element_boxes_px가
    "이 조각이 다른 반복 칸에서는 어디쯤에 해당하는가"를 계산하는 데 씀)."""
    fx0, fy0, fx1, fy1 = from_frame_px
    tx0, ty0, tx1, ty1 = to_frame_px
    fw = max(1e-6, fx1 - fx0)
    fh = max(1e-6, fy1 - fy0)
    tw = tx1 - tx0
    th = ty1 - ty0
    bx0, by0, bx1, by1 = box_px
    rx0 = tx0 + (bx0 - fx0) / fw * tw
    ry0 = ty0 + (by0 - fy0) / fh * th
    rx1 = tx0 + (bx1 - fx0) / fw * tw
    ry1 = ty0 + (by1 - fy0) / fh * th
    return (rx0, ry0, rx1, ry1)


def group_content_cells_px(image_path: str, cell_boxes: list):
    """빈 칸(실제 내용 없음, `cell_has_content_px`)을 걸러내고, 남은 칸들
    중 완전히 같은 그림의 반복을 `group_identical_boxes_px`로 묶는다 --
    칸 안을 낱개로 쪼개지는 않는다. `detect_repeat_aware_sub_element_boxes_px`의
    앞 단계를 그대로 뽑아낸 것(순수 리팩터링)이고, 2026-09-28부터 무테 자동
    인식("칸 전체가 스티커 한 장" -- 멍푸 결정)이 이것만 쓴다.

    Returns: (filtered_cells, groups) -- groups는 filtered_cells 인덱스
    리스트들의 리스트, 각 그룹의 첫 원소가 대표."""
    filtered = []
    for box in cell_boxes:
        try:
            has_content = cell_has_content_px(image_path, box)
        except Exception:  # noqa: BLE001
            has_content = True
        if has_content:
            filtered.append(tuple(box))

    if not filtered:
        return [], []

    try:
        cell_groups = group_identical_boxes_px(image_path, filtered)
    except Exception:  # noqa: BLE001
        cell_groups = [[i] for i in range(len(filtered))]
    return filtered, cell_groups


def detect_repeat_aware_sub_element_boxes_px(image_path: str, cell_boxes: list, suspicious_regions: list = None):
    """2026-09-11(51차) 피드백("똑같은 도안 여러 개일 때 하나는 섬세하게
    작업하고 나머지에 복붙하라고 했는데 아예 각각 엉망으로 인식하고 있어",
    "도안 인식이 엉망이고 사각형 칼선까지 생겨") -- 50차로 다시 켠 칸 안
    낱개 요소 쪼개기가 반복(복제) 칸과 정면으로 충돌하던 버그를 고침.

    50차 당시 호출 순서(gui.app)는 "칸을 전부 먼저 각자 쪼갠 뒤 -> 그
    조각들을 반복 그룹으로 묶기"였다. 그런데 같은 그림이 완전히 반복돼도
    칸마다 Canny 경계 재계산이 안티에일리어싱/크롭 경계 등 미세한 잡음으로
    조각의 개수·순서를 매번 조금씩 다르게 내놓을 수 있어서, 그룹핑이 조각
    단위로는 서로 짝을 맞추지 못했다 -- 결국 반복 칸마다 따로(그리고
    제각각 다르게) 실루엣을 추적하게 됐고, 그 중 일부(예: 작고 애매한
    조각)는 GrabCut이 배경과 못 갈라 사각형 그대로 칼선이 되어버렸다
    ("사각형 칼선까지 생겨"의 원인).

    고친 순서: 먼저 칸(패널) 단위로 "완전히 같은 그림의 반복"을
    묶고(`group_identical_boxes_px`, 원래 6차/29차 취지 그대로), 그
    대표 칸 하나에만 `split_cell_into_sub_elements_px`를 실제로 돌려
    조각을 찾는다. 나머지 반복 칸에는 그 조각들을 각자의 상대 위치
    (`map_box_between_frames_px`)로 옮겨 붙이기만 한다 -- 조각 하나하나가
    실제로는 딱 한 번씩만 인식되고, 나머지는 전부 "복붙"이라는 원래 의도
    (2026-09-07(6차) "반복되는 스티커는 한개의 칼선을 먼저 완성하고 나머지에
    그대로 적용해") 그대로 유지됨.

    반복이 아예 없는 칸(자기 혼자인 그룹)은 대표=자기 자신이라 그냥
    이 함수 안에서 한 번만 쪼개지고 끝난다 -- 회귀 없음.

    Returns: (boxes, groups) -- boxes는 최종 낱개 요소 bbox의 평면 리스트
    (원본 이미지 좌표), groups는 boxes에 대한 인덱스 리스트들의 리스트로
    각 원소의 groups[k][0]이 "실제로 새로 실루엣을 추적해야 하는 대표"이고
    groups[k][1:]은 그 결과를 복제(평행이동/크기맞춤)하면 되는 반복
    인스턴스다 -- gui.app의 기존 처리 방식(group[0]만 생성, 나머지는
    fit_cutline_result_to_box로 복제)과 100% 동일한 규약이라 호출하는 쪽은
    바뀌지 않아도 됨.

    빈 칸(실제 내용 없음)은 `cell_has_content_px`로 먼저 걸러낸다(44차와
    동일한 안전 실패 시 "내용 있음"으로 간주하는 방침도 그대로).

    `suspicious_regions`: 2026-09-28 추가, 기본 None=동작 변화 없음. 리스트를
    넘기면 시트 전체에서 "내용은 있는데 끝내 못 찾은" 큰 영역들을 원본 이미지
    좌표로 모아준다(각 대표 칸 처리마다 그대로 누적) -- gui.app이 자동 인식
    완료 후 "이 부분 확인해 보세요" 안내를 띄우는 용도."""
    filtered, cell_groups = group_content_cells_px(image_path, cell_boxes)
    if not filtered:
        return [], []

    boxes = []
    groups = []
    for cell_group in cell_groups:
        primary_idx = cell_group[0]
        primary_box = filtered[primary_idx]
        try:
            sub_boxes = split_cell_into_sub_elements_px(
                image_path, primary_box, suspicious_regions=suspicious_regions,
            )
        except Exception:  # noqa: BLE001
            sub_boxes = [primary_box]
        for sub_box in sub_boxes:
            group_indices = [len(boxes)]
            boxes.append(sub_box)
            for other_idx in cell_group[1:]:
                other_box = filtered[other_idx]
                mapped = map_box_between_frames_px(sub_box, primary_box, other_box)
                group_indices.append(len(boxes))
                boxes.append(mapped)
            groups.append(group_indices)
    return boxes, groups
