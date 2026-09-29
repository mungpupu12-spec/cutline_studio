"""
CutLine Studio - desktop prototype (CustomTkinter)

A minimal but complete desktop app:
  1. Open a raster (PNG/JPG) or vector (SVG) design file
  2. Set safety / cut / bleed offsets (mm) and working DPI
  3. Preview the generated cut lines over the artwork
  4. Export an Illustrator-ready SVG with labeled, colored layers

Run with:  python app.py

2026-08-26 UI/UX 전면 개편: 버튼이 화면 한쪽에 몰려 잘리는 문제를 근본적으로
없애기 위해 왼쪽 조작 패널 전체를 CTkScrollableFrame(항상 스크롤 가능)으로
감쌌고, 최신 트렌드(카드형 섹션, 여유로운 여백, 하나의 브랜드 컬러, 명확한
1차/2차 버튼 위계)를 반영해 tkinter/ttk 대신 customtkinter로 시각 언어를
새로 그렸다. 모든 상태 변수 이름/타입과 비즈니스 로직 메서드는 기존 그대로
유지했다 -- 바뀐 것은 오직 "어떻게 그려지는가"뿐이다.
"""

import os
import sys

# ---------------------------------------------------------------------------
# 2026-08-27 피드백("한 번도 프로그램을 테스트조차 못 해봤어"): PyInstaller로
# --windowed(콘솔 없음) 옵션으로 만든 .exe는 sys.stdout/sys.stderr가 None인
# 경우가 있음(콘솔 자체가 없어서). 이 상태에서 어딘가의 print()나
# traceback.print_exc()가 그대로 실행되면 "'NoneType' object has no
# attribute 'write'"라는 *새* 예외가 터지면서, 원래 처리하려던 예외를
# 잡으려던 코드 자체가 죽어버리고 -- 결과적으로 앱이 아무 에러 메시지도
# 없이 새까만 창만 남긴 채 조용히 꺼져버림. 소스에서 python으로 직접
# 실행하면 콘솔이 항상 있어서 이 문제가 재현되지 않다가, 실제 배포한
# --windowed .exe에서만 이렇게 터지는 게 전형적인 증상이라 아주 이른
# 시점(다른 import보다도 먼저)에 안전한 값으로 막아둔다.
if sys.stdout is None or sys.stderr is None:
    import io as _io

    _safe_stream = _io.TextIOWrapper(_io.BytesIO(), encoding="utf-8", errors="replace")
    if sys.stdout is None:
        sys.stdout = _safe_stream
    if sys.stderr is None:
        sys.stderr = _safe_stream

# 2026-09-07 피드백("창이 여전히 화면 아래로 치우쳐 있어"): Windows에서 화면
# DPI 배율(125%/150% 등)이 걸려 있을 때, 이 앱이 "나는 DPI를 직접 처리한다"고
# 미리 선언해두지 않으면 Windows가 대신 창을 확대해서 그려주는 DPI
# 가상화(virtualization)가 걸린다 -- 이 경우 앱이 계산해서 요청한 크기/좌표와
# 실제 화면에 그려지는 위치가 서로 어긋나서, 정중앙으로 계산해 배치해도 결과
# 창이 그보다 아래/오른쪽으로 밀려 보일 수 있다. Tk 창을 하나라도 만들기
# 전에 이 선언을 해두면 그 어긋남이 사라진다(Windows 전용 API라 다른
# 플랫폼/개발 환경에서는 그냥 조용히 넘어간다).
if sys.platform.startswith("win"):
    try:
        import ctypes as _ctypes
        _ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            _ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

import threading
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.cutline_core import generate_cutlines, OffsetSpec, px_to_mm
from core.cutline_types import CutlineType
from core.image_style import ImageStyle, DEFAULT_STYLE_MARGIN_MM, _measure_content_bbox_px
from core.interactive_cutline import (
    image_outer_region_px,
    generate_borderless_cut_from_silhouette,
    generate_domusong_inside_cutline,
    generate_cutline_auto,
    generate_cutline_by_style,
    generate_cutline_for_selection,
    has_white_or_black_border,
    is_silhouette_undersized,
)
from core.segmentation import segment_design_with_hints
from core.style_classify import rectangularity as _shape_rectangularity
from core.multi_design import (
    cell_has_content_px,
    detect_design_bboxes_px,
    detect_elements_by_background_flood_px,
    detect_repeat_aware_sub_element_boxes_px,
    group_content_cells_px,
    expand_box_to_neighbor_midpoint_px,
    find_design_bbox_at_point_px,
    group_identical_boxes_px,
    split_cell_into_sub_elements_px,
)
from core.repeat_grid import fit_cutline_result_to_box
from core.svg_export import export_svg
from core.cut_check import check_cut_spacing, summarize_cut_spacing
from core.preview import render_preview
from core.accumulate import combine_results
from core import license_client as lic

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

APP_TITLE = "컷 라인 스튜디오"
# 2026-08-27 피드백: "프로그램 실행화면의 이미지와 그래픽이 다 깨져 보여
# 고화질로 만들고" -- 기존 640px 캡은 요즘 고해상도(HiDPI/레티나 배율)
# 모니터에서는 미리보기가 눈에 띄게 흐릿/각져 보이는 원인이었음. 실제 처리
# 해상도(내보내기용)는 그대로 두고, 화면에 "보여주기"용 미리보기 캡만
# 큰 폭으로 올림.
PREVIEW_MAX_SIDE = 1400


def _resource_dir():
    """프로젝트 루트 경로 -- 평소 소스에서 실행할 때와, PyInstaller로 만든
    .exe 안에서 실행될 때(2026-08-26, "exe 앱으로 만들어줘") 둘 다 올바르게
    동작해야 함. PyInstaller onefile 실행 시 실제 파일들은 매번 임시 폴더
    (`sys._MEIPASS`)에 풀리므로, 그 안에 --add-data로 함께 넣어둔 assets/
    같은 리소스를 찾으려면 `__file__` 대신 이 값을 기준으로 삼아야 함."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _work_file_dir():
    """AI 파일을 변환한 임시 래스터, 미리보기 PNG 등 앱이 실행 중에 스스로
    만들어 쓰는 "작업용" 파일들을 저장할 폴더.

    2026-09-07 피드백("Could not read image: ...\\_internal\\
    _ai_source_raster.png"): 예전엔 이런 파일들을 `__file__` 기준 경로에
    썼는데, .exe로 빌드된 상태에서는 그게 PyInstaller의 `_internal`
    폴더(= _resource_dir()) 안이었다 -- 이 폴더는 (1) 앱을 다시 빌드할
    때마다 통째로 새로 만들어지는 "읽기 전용 번들 리소스" 폴더지 실행 중
    계속 써야 하는 임시 작업 폴더가 아니고, (2) 같은 파일명을 실행 중인
    여러 인스턴스가 공유해서 쓰다 보니, 재빌드나 다른 창에서의 새 로드와
    타이밍이 겹치면 파일이 없어지거나 도중에 덮어써질 수 있었다. 대신 OS
    표준 임시 폴더 아래, 지금 실행 중인 이 프로세스(PID)만 쓰는 전용
    하위 폴더에 쓰도록 바꿔서 이런 충돌을 원천적으로 없앤다."""
    import tempfile

    d = os.path.join(tempfile.gettempdir(), f"CutLineStudio_work_{os.getpid()}")
    os.makedirs(d, exist_ok=True)
    return d


# 2026-09-12(57차): is_silhouette_undersized가 걸려 무테(BORDERLESS) 스타일로
# 재시도할 때 붙이던 안내 문구가 지금까지는 "안전한 사각형(무테 방식) 컷으로
# 자동 대체했습니다"로 고정돼 있었다 -- 그런데 이 재시도(core.image_style.
# generate_style_cutline)가 실제로 성공해서 진짜 실루엣을 추적해내는 경우
# (57차 area-ratio 수정 이후 실측: 재시도 중 실제 사각형으로 떨어진 사례
# 0건, 전부 진짜 추적 성공)에도 이 문구가 그대로 붙어, 작가에게 "사각형으로
# 잘렸다"는 잘못된 정보를 준다(실제로는 캐릭터 윤곽을 그대로 따라간 결과).
# 재시도 결과의 실제 도형(rectangularity, core.style_classify와 동일한
# 척도)을 보고 문구를 갈라 정확하게 안내한다.
_RECT_LIKE_THRESHOLD = 0.95

# 2026-09-26 피드백("푸들은 얼굴에 칼선이 여러개 중첩 되어 있고 이 문제는
# 지금 한달째 못 고치고 있어"): 실제 파일(너구리+꽃+푸들 시트)로 직접
# 재현해서 찾은 두 번째 원인. is_silhouette_undersized(기본 min_ratio=0.35)는
# 원래 "자동 인식/격자로 찾은 칸 하나 전체"(그 칸 = 캐릭터 하나) 기준으로
# 만들어진 안전장치라, 정상적인 캐릭터라면 칸의 최소 35%는 채워야 한다는
# 전제가 맞다. 그런데 detect_repeat_aware_sub_element_boxes_px가 만든
# "낱개 조각" 박스(sibling_boxes_px가 함께 넘어오는 게 바로 이 문맥의 신호)는
# 이미 그 조각의 실제 내용 경계에 딱 맞게(+헤일로 여유만) 잘라놓은 것이라,
# 조각 모양이 원래부터 울퉁불퉁하면(꽃잎 4개짜리 꽃, 눈/코/블러셔처럼 서로
# 떨어진 얼굴 디테일 묶음 등) 정상적으로 잘 추적됐어도 박스 대비 채움 비율이
# 낮게 나온다(실측: 정상 케이스가 19~31%까지 나옴). 기존 0.35 기준을 그대로
# 쓰면 이런 정상 결과까지 "잘못 추적됨"으로 오판해 사각형(무테) 대체로
# 바꿔버리고, 그 사각형이 이웃 조각들과 겹치는 부분을 빼면서 삐죽삐죽한/
# 이중으로 보이는 칼선이 생긴다 -- 이게 실제로 화면에서 보이던 "칼선이
# 중첩되어 보인다" 증상의 진짜 원인이었다.
# 반면 진짜 GrabCut 실패(캐릭터 몸 안의 작은 부분만 잘못 잡힌 경우)는 채움
# 비율이 훨씬 더 극단적으로 낮게 나온다(실측: 같은 시트에서 실제 실패 사례가
# 0.29% -- 정상 케이스의 최저치 19%와 60배 이상 차이). 그래서 안전장치를
# 완전히 없애지 않고, "낱개 조각" 문맥에서만 훨씬 더 낮은(그래도 진짜 실패는
# 확실히 잡는) 기준으로 낮춘다.
_UNDERSIZED_RATIO_FOR_FINE_SUBELEMENT = 0.10


def _undersized_retry_note(ratio_pct: float, fallback_design) -> str:
    is_rect_like = True
    try:
        if fallback_design is not None and not fallback_design.is_empty:
            is_rect_like = _shape_rectangularity(fallback_design) >= _RECT_LIKE_THRESHOLD
    except Exception:
        is_rect_like = True  # 판단 실패 시 기존과 같이 보수적으로 "사각형" 문구 유지
    prefix = (
        f"실루엣 추적 결과가 선택 영역의 {ratio_pct:.0f}%밖에 안 돼(예: 몸통 "
        f"전체가 아니라 배 같은 일부만 잡혔을 가능성) 잘못됐다고 보고, "
    )
    if is_rect_like:
        return prefix + "안전한 사각형(무테 방식) 컷으로 자동 대체했습니다."
    return prefix + (
        "무테 방식으로 다시 실제 선/색 경계를 추적했고, 이번엔 정상적으로 "
        "실루엣을 따라간 모양이 나와 그 결과를 그대로 사용했습니다."
    )


# 2026-09-13(66차) 피드백("하나의 요소가 여러개의 칼선으로 쪼개지고 중첩되는
# 문제를 우선적으로 해결해줘") -- core.multi_design._split_touching_blobs를
# 고쳐서 이 문제를 자동으로 예방하려는 시도를 7차례(전역/블롭별 정규화 ->
# 무조건 복원 -> 크기 비율 -> 비율+거리 -> 비율+거리+최소면적 -> 색 기반
# 판별까지) 반복했으나, 매번 실제 9개 파일 검증에서 다른 도안이 나빠지는
# 새 회귀가 나왔다(자세한 경위는 각 시도의 커밋 로그/대화 기록 참고). 마지막
# 시도(색 기반 판별)에서는 "복원해야 할 부분"과 "복원하면 안 되는 부분"이
# 실제 색조차 둘 다 배경과 뚜렷이 다른 "진짜 도안 색"이라, 이미지 내용만으로
# 자동으로 구별할 방법이 없다는 것까지 실측으로 확인됐다(어느 쪽이 맞는지는
# 작가가 그 도안을 얼마나 타이트하게/여유 있게 재단하고 싶은지에 달려 있어서,
# 이미지 자체가 아니라 제작 의도의 문제이기 때문).
#
# 그래서 방향을 바꿔서(멍푸 결정: "자동화 포기, 사람이 확인하는 안전장치로
# 전환"), 알고리즘으로 미리 막으려 하지 않고, 결과가 나온 뒤 "한 도안이었어야
# 할 영역이 여러 조각(칼선)으로 쪼개져 나왔다"는, 이 문제의 가장 확실한 증상
# 자체를 직접 감지해서 사람에게 알린다.
#
# 처음엔 "조각끼리 실제로 겹치는가"만 봤는데, 이 문제의 실제 진원인 리본
# 캐릭터 케이스로 직접 검증해보니 틀렸다 -- 이 케이스의 두 조각(몸통
# 49866px^2, 리본/머리장식 9572px^2, 둘 다 명백히 실제 내용물)은 실제로는
# 겹치지 않고 20.6px 떨어져만 있었다(core.segmentation.FRAGMENT_ADJACENCY_PX
# =20.0 문턱값을 딱 넘겨서 "붙어있는 조각"으로 합쳐지지도, "겹침"으로 잡히지도
# 않은 경계 사례). 그래서 "겹침"뿐 아니라 "실제 내용이 있는 조각 2개 이상이
# 서로 아주 가깝게(≈FRAGMENT_ADJACENCY_PX 수준) 떨어져 있는 경우"까지
# 넓혀서 감지한다 -- 겹치는 경우는 거리 0으로 이 조건에 자동으로 포함된다.
# 진짜로 서로 다른 여러 조각으로 이뤄진 정상적인 도안(예: 귀걸이 한 쌍처럼
# 원래 떨어져 있는 도안)은 그 조각들끼리 이 정도로 가깝게 붙어있지 않으므로
# 오탐이 적다.
_SPLIT_WARNING_GAP_PX = 25.0  # 이 거리 이하로 붙어있으면 경고(segmentation.py의 20.0에 약간의 여유를 더함)
_SPLIT_WARNING_MIN_AREA_PX = 200.0  # 이보다 작은 조각은 잡티/노이즈로 보고 무시


def _collect_warning_notes(adjustments):
    """combine_results가 만든 안내 문구 목록에서 사람이 확인해야 할 경고(⚠)만
    골라 돌려준다. "[N번째 영역] ⚠ ..." 형태의 항목별 경고는 같은 문구끼리
    묶어 "(3·5·9번째 영역, 총 3곳)"처럼 한 줄로 만든다. 반환:
    (경고 문구 목록, {묶은 문구: 첫 번째 영역 번호})."""
    import re as _re
    top: list = []
    grouped: dict = {}
    order: list = []
    for note in adjustments or []:
        if note.startswith("⚠"):
            top.append(note)
            continue
        m = _re.match(r"^\[(\d+)번째 영역\] (⚠.*)$", note)
        if m:
            msg = m.group(2)
            if msg not in grouped:
                grouped[msg] = []
                order.append(msg)
            grouped[msg].append(int(m.group(1)))
    lines = list(top)
    first_region: dict = {}
    for msg in order:
        nums = grouped[msg]
        shown = "·".join(str(n) for n in nums[:8]) + ("…" if len(nums) > 8 else "")
        line = f"{msg} ({shown}번째 영역, 총 {len(nums)}곳)"
        lines.append(line)
        first_region[line] = nums[0]
    return lines, first_region


def _split_overlap_warning_note(design) -> str | None:
    """design(Polygon 또는 MultiPolygon)이 실제 내용이 있는 여러 조각으로
    쪼개져 있고 그 조각들이 서로 겹치거나 아주 가깝게 붙어 있으면, 사람이
    확인해야 한다는 안내 문구를 돌려준다. 문제 없으면 None."""
    if design is None or design.is_empty:
        return None
    all_geoms = list(design.geoms) if hasattr(design, "geoms") else [design]
    geoms = [g for g in all_geoms if g is not None and not g.is_empty and g.area >= _SPLIT_WARNING_MIN_AREA_PX]
    if len(geoms) < 2:
        return None
    try:
        min_gap = min(
            geoms[i].distance(geoms[j])
            for i in range(len(geoms))
            for j in range(i + 1, len(geoms))
        )
    except Exception:
        return None
    if min_gap > _SPLIT_WARNING_GAP_PX:
        return None
    if min_gap <= 0:
        detail = "일부가 서로 겹친 채로"
    else:
        detail = f"서로 약 {min_gap:.0f}px밖에 안 떨어진 채로"
    return (
        f"⚠ 이 도안은 {detail} 조각 {len(geoms)}개로 쪼개져서 칼선이 "
        "생성됐습니다. 원래 하나였어야 할 도안이 실루엣 추적 과정에서 잘못 "
        "나뉘었을 가능성이 높으니, 이 영역은 결과를 직접 확인하고 필요하면 "
        "다시 선택해 만들어주세요."
    )


def _missing_body_warning_notes(suspicious_regions_px: list) -> list[str]:
    """2026-09-28(파스텔색 캐릭터가 저대비 배경과 만나면 몸통 실루엣이
    통째로 빠지는 문제, 실제 파일 여러 개로 확인 -- core.multi_design의
    `suspicious_regions` 진단 정보 참고): 이 문제를 "자동으로 고치는" 시도는
    전부 실측 결과 다른 도안을 잘못 합치는 회귀 위험이 있어 보류하고, 대신
    발생 가능한 위치만 사람에게 알려주는 훨씬 안전한 안내로 대체한다.

    `_split_overlap_warning_note`와 같은 자리(adjustments)에 "⚠"로 시작하는
    문구를 추가해, 기존 "확인이 필요한 영역이 있습니다" 안내창에 자동으로
    같이 뜨도록 한다(새 UI를 만들지 않음 -- 이미 있는, 이미 익숙한 안내
    방식 재사용)."""
    notes = []
    for (x0, y0, x1, y1) in suspicious_regions_px:
        notes.append(
            f"⚠ 원본 이미지 좌표 ({int(x0)}, {int(y0)})~({int(x1)}, {int(y1)}) 부근에 "
            "색 경계가 흐려 자동으로 칼선을 못 만들었을 수 있는 영역이 있습니다. "
            "배경과 색이 비슷한 캐릭터 몸통 등이 통째로 빠졌을 수 있으니, 이 부분은 "
            "직접 눈으로 확인해 주세요."
        )
    return notes


# ---------------------------------------------------------------------------
# 2026-08-26 디자인 리뉴얼: "세련되고 고급지게" 요청에 맞춘 단일 브랜드 팔레트
# + 타이포그래피. 색은 여기 한 곳에서만 정의하고, 아래 위젯들은 전부 이 값을
# 참조한다 -- 나중에 톤을 바꾸고 싶으면 이 블록만 고치면 된다.
#
# 2026-08-26 추가 피드백("사용 프로그램의 컬러도 흑백과 코발트 블루로 변경")에
# 따라 브랜드 포인트 컬러를 인디고에서 코발트 블루(#0047AB, 안료명 그대로)로
# 바꾸고, 그 외 전부를 순수 흑백/그레이 톤으로 정리 -- 세이프티/칼선/블리딩
# 색상 스와치(초록/빨강/파랑)는 인쇄 실무에서 이미 굳어진 의미 있는 신호라서
# "장식용 브랜드 컬러"가 아니므로 그대로 둠(장식 팔레트와 기능 신호는 다른
# 것이므로 섞지 않음).
# ---------------------------------------------------------------------------
BG_APP = "#F5F5F6"        # 창 배경 (거의 흰색에 가까운 그레이) -- 카드가 그 위에 뜬 느낌
BG_CARD = "#FFFFFF"       # 섹션 카드 배경 (순백)
BORDER = "#E2E2E5"        # 카드/입력창 테두리 (그레이)
# 2026-08-27 피드백: "프로그램 글자색 블랙으로 통일, 서브 컬러는 진한
# 회색" -- 기존엔 거의 검정에 가까운 값(#0A0A0C)이었는데 완전한 검정으로,
# 보조 텍스트는 눈에 덜 띄던 회색(#6E6E74)에서 대비가 뚜렷한 진한 회색으로.
TEXT_PRIMARY = "#000000"   # 순검정
# 2026-08-31 피드백("글자들이 잘 안보여 -- 폰트와 색상 진하게"): 보조
# 텍스트(설명 문구)가 연한 회색이라 잘 안 보인다는 피드백에 따라 훨씬 짙은
# 회색으로 한 번 더 진하게 함(#45454A -> #202024, 검정에 아주 가까움).
TEXT_SECONDARY = "#202024"  # 진한 회색(거의 검정)
ACCENT = "#0047AB"        # 브랜드 포인트 컬러 (코발트 블루)
ACCENT_HOVER = "#003682"
ACCENT_SOFT = "#E8F0FB"   # 보조 버튼 hover / 배경 강조용 아주 연한 코발트 톤
COLOR_SAFETY = "#16A44A"
COLOR_CUT = "#DC2626"
COLOR_BLEED = "#2563EB"
CANVAS_BG = "#EFEFF1"
FONT_FAMILY = "Malgun Gothic"

# 2026-08-26 추가 피드백: "폰트 0.8 포인트 키우고"에 이어, 2026-08-27
# 피드백: "크기 5포인트 키우기" -- 기존 0.8에 5를 더 얹음(총 +5.8). 기존
# 폰트 크기 전부에 이 값을 더한 뒤 반올림(tkinter 폰트 크기는 정수만
# 받으므로).
FONT_SIZE_BUMP = 5.8


def _bumped(size):
    return round(size + FONT_SIZE_BUMP)


# 2026-08-26 피드백으로 "버튼도 더 라운딩 넣고 크기 10mm 키워"(~38px)를
# 적용했었으나, 2026-08-27 피드백: "처음 화면의 버튼이 너무 크고 굵어 ...
# 모든 버튼 크기 줄여" -- 그 확대를 되돌리고 훨씬 절제된 값으로 축소.
# 완전히 0으로 없애지 않고 아주 작은 값만 남긴 이유는 클릭 대상이 너무
# 얇아지지 않도록 하기 위함("중간 길이" 요청과 맞춤).
MM_TO_PX_AT_96DPI = 96.0 / 25.4
BUTTON_SIZE_BUMP_PX = round(1.5 * MM_TO_PX_AT_96DPI)  # ~6px, "중간" 크기

# 2026-08-27 피드백: "도무송은 무조건 선택이 아니라 ... 카테고리 형태로
# 선택" -- 작업 종류/재단 도형을 카테고리 버튼 + 팝업 방식으로 바꾸면서,
# 버튼에 표시할 짧은 라벨을 값(코드)과 분리해서 여기 모아둠.
#
# 2026-09-07 피드백("칼선의 종류는 크게 무테/유테/도무송/조각스티커
# 이렇게 있는데 지금은 그런 옵션이 잘 표현 되지 않고 있어"): 예전엔 "작업
# 종류"가 조각 스티커/스티커/도무송 3개였고, "스티커"를 고른 뒤에만 다시
# 유테/무테를 고르는 2단계 구조였음 -- 실제로 사용자가 머릿속에서 나누는
# 기준은 처음부터 무테/유테/도무송/조각 스티커 4가지 대등한 선택지라서,
# 2단계로 나뉜 게 오히려 "그 옵션이 잘 안 보인다"로 느껴졌음. 이제
# job_type 자체가 이 4개 값(BORDERLESS/LINE_ART/DOMUSONG/FULL_CUT) 중
# 하나이고, 유테/무테를 고르는 별도의 하위 선택창은 없앴다(core.image_style.
# ImageStyle의 BORDERLESS/LINE_ART 값과 이름을 그대로 맞춰써서 값 하나로
# 바로 core 함수에 넘길 수 있게 함). "자동 감지"(과거 AUTO)도 이 4가지
# 대등한 선택지 목록에는 없어 제거함 -- 유테/무테를 직접 고르는 게 이제
# 첫 화면에서부터 명확하므로.
JOB_TYPE_LABELS = {
    "BORDERLESS": "무테",
    "LINE_ART": "유테",
    # 2026-09-10 피드백("칼선 종류 선택을 2개 이상 선택 가능하게 해줘 -- 도안별로
    # 알맞은 종류를 판단해서 순서대로 자동 적용, 다만 도무송은 따로 선택하게
    # 하자"): 위 2026-08-27 결정으로 없앴던 "자동 감지"를, 무테/유테 두 개
    # 사이에서만 다시 들여온다 -- core.interactive_cutline.generate_cutline_auto가
    # 이미 실제 파일 2개로 검증된 채로 core에 남아있었지만(선택 영역 안 실제
    # 내용의 사각형도로 무테/유테를 판단) GUI에서만 연결이 빠져 있었음. 도무송은
    # 그림만 보고는 "이 상품을 실제로 어떤 방식으로 찍어낼지"(비즈니스 결정)를
    # 알 수 없어 자동 판단 대상에서 계속 뺀다(멍푸님이 직접 확인한 결정) --
    # 그래서 목록에 별도 항목("도무송")으로 여전히 남아있고 자동 판단에는
    # 섞이지 않는다.
    # 2026-09-10(39차) 피드백("자동 판단은 자동 생성으로 고쳐"): 화면에
    # 보이는 이름만 "자동 생성"으로 바꿈(내부 값 AUTO_STYLE은 그대로).
    "AUTO_STYLE": "무테+유테 자동 생성",
    # 2026-09-10(35차 이어서) 피드백("도무송, 무테 동시 다중 선택하고
    # 만들어야 해. 이거 너무 번거러워... 표시해둔 것만 도무송(추천)"): 한
    # 시트 안에 도무송으로 만들 도안(카드형)과 무테/유테로 만들 도안
    # (캐릭터 실루엣 등)이 섞여 있을 때, 예전처럼 도무송 다 만들고 나서
    # 무테/유테로 작업 종류를 바꿔 자동 인식을 다시 돌리는 2단계가 번거
    # 롭다는 지적 -- 그렇다고 "도안마다 도무송인지 그림만 보고 자동 판단"
    # 하는 건 이미 위험하다고 확인됐으므로(제작 방식은 그림만으로 알 수
    # 없음), 대신 이 작업 종류를 고르면 전용 버튼 3개(① 도안 자동 인식 --
    # 표시하기, ② 표시한 도무송 칼선 생성, ③ 나머지 무테/유테 칼선 생성)가
    # 나타나 사람이 직접 도무송 대상만 화면에서 클릭해 표시하고, 도무송을
    # 먼저 생성한 뒤 나머지는 이어서 자동으로 무테/유테 처리하도록 한다
    # (_on_mixed_detect/_on_mixed_generate_domusong/_on_mixed_generate_rest 참고).
    "MIXED_AUTO": "무테/유테+도무송",
    "DOMUSONG": "도무송",
    "FULL_CUT": "조각",
    # 2026-09-08(9차) 피드백("칼선 종류에 키스컷 마스킹 테이프도 추가해"):
    # "키스컷 마테 좋은예/나쁜예" 실제 파일(스크래치패드에서만 분석, 실제
    # 내용은 저장/커밋하지 않음)을 직접 열어 재단선 레이어를 확인해보니,
    # 마스킹테이프 롤은 (1) 개별 모티프마다 유테(선화)와 완전히 같은 방식
    # (실루엣을 촘촘하고 일정한 간격으로 따라가는 키스컷)으로 잘리고,
    # (2) 그와는 별개로 롤 전체 길이를 가로지르는 세이프티/칼선/블리딩
    # 3단 테두리 선이 한 번 추가로 그려져 있었다(개별 모티프와 겹치거나
    # 이어지지 않는, 롤 자체의 폭을 정의하는 선). 그래서 "작업 종류"
    # 자체는 유테와 똑같이 다루고(_generate_one_item 참고), 대신 롤
    # 전체 테두리를 한 번만 추가하는 별도 버튼을 새로 만든다
    # (_on_add_roll_border 참고) -- 이게 "연속된 롤(시트) 전체를 고려해야
    # 함"이라고 하신 부분의 실제 구현이다.
    "MASKING_TAPE": "키스컷",
}
JOB_TYPE_PLACEHOLDER = "작업 종류를 선택하세요"
# 2026-09-01 피드백("도무송은 선택 취소인 상태에서 자율선택으로 변경"):
# 작업 종류뿐 아니라 그 아래 세부 옵션(재단 도형)도 처음부터 하나가 미리
# 골라져 있어서 "강제"처럼 보였다는 지적 -- 이것도 job_type과 똑같이 빈
# 값("")을 기본값으로 하고, 아래 placeholder를 보여준다.
CUTLINE_TYPE_PLACEHOLDER = "재단 도형을 선택하세요"
CUTLINE_TYPE_LABELS = {
    CutlineType.FULL_CUT.name: "조각 스티커",
    CutlineType.RECTANGLE.name: "직사각형",
    CutlineType.SQUARE.name: "정사각형",
    CutlineType.ELLIPSE.name: "타원형",
    CutlineType.CIRCLE.name: "정원형",
}

# 2026-08-31 피드백: "튜토리얼을 예시 이미지를 제작해서 사용법을 이미지와
# 문장으로 슬라이드 구성으로 만들어" -- 각 단계별 도식 이미지(assets/
# tutorial/step*.png, assets/make_tutorial_slides.py로 생성)와 한 줄
# 설명을 짝지어, 아래 _open_tutorial_dialog()가 좌우로 넘기는 슬라이드로
# 보여준다.
# 2026-09-07 피드백("칼선의 종류는 크게 무테/유테/도무송/조각스티커")에
# 맞춰 작업 종류가 4가지 대등한 선택지로 바뀌면서, 예전 4번째 슬라이드
# (유테/무테 세부 선택)는 더 이상 별도 단계가 아니게 됨 -- 2번 슬라이드에서
# 이미 4가지 중 하나로 바로 고르므로 이 단계를 통째로 뺐다(step4.png 파일
# 자체는 남아있지만 여기서는 더 이상 참조하지 않음).
# 2026-09-07 피드백("2번째 문구 변경", "3번 슬라이드 글이 이해가 안 돼",
# "4번 슬라이드 ...으로 변경") -- 그대로 반영해 문구를 더 짧고 명확하게 다듬음.
# 2026-09-10(36차 이어서) 피드백("말이 너무 길어 단순하고 직관적으로 15자
# 이내로"): 슬라이드마다 문장 길이가 들쭉날쭉해서(특히 3번이 3줄) 창 높이를
# "가장 긴 캡션" 기준으로 한 번만 정하는 계산(아래 max_req_h)이 슬라이드마다
# 다른 만큼의 빈 공간을 남기고 있었다 -- 모든 캡션을 15자 안팎의 짧은 한
# 줄로 통일하면 "가장 긴 캡션"과 "가장 짧은 캡션"의 차이가 거의 없어져
# 빈 공간 자체가 크게 줄어든다(자세한 원인은 아래 max_req_h 주석 참고).
TUTORIAL_SLIDES = [
    ("step1.png", "도안 파일을 선택하세요"),
    ("step2.png", "작업 종류를 고르세요"),
    ("step3.png", "도안을 자동으로 인식해요"),
    ("step5.png", "영역 추가로 칼선 생성"),
    ("step6.png", "SVG로 내보내기 저장"),
]
# 튜토리얼 내용을 바꿀 때마다 이 번호를 올리면, "다시 보지 않기"를 이미
# 체크한 사용자에게도 새로워진 튜토리얼이 한 번은 다시 뜬다(아래
# _load_tutorial_seen/_save_tutorial_seen이 이 번호를 함께 기록/비교함)
# -- 2026-08-31 피드백("슬라이드 튜토리얼 어디갔어?")의 원인이, 예전 버전을
# 테스트하며 이미 "다시 보지 않기"가 저장되어 있어서 새 슬라이드 튜토리얼
# 자체가 아예 뜨지 않았을 가능성이 높아 추가한 안전장치.
TUTORIAL_VERSION = 5

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")


class CutLineApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        # 2026-08-31 피드백("검정 화면이 또 뜨고 없애면 모든 창이 꺼져"):
        # 실행 즉시 창을 화면에 보여준 채로 아이콘/폰트/라이선스 확인/전체
        # 레이아웃 구성까지 끝내다 보니, 그 사이(특히 라이선스 서버 응답을
        # 기다리는 동안이나 위젯을 만드는 동안) 아직 배경도 안 칠해지고
        # 아무 것도 안 그려진 창이 그대로 화면에 떠 있었을 수 있음 --
        # Windows에서는 이게 "검은 화면"으로 보임. 이제 준비가 완전히 끝날
        # 때까지 창 자체를 숨겨두고(withdraw), 다 만들어진 뒤에만 한 번에
        # 보여준다(_proceed_past_license_gate 끝, main()의 예외 처리
        # 참고) -- 화면에 뭔가 어중간하게 떠 있는 상태 자체가 아예 없어짐.
        self.withdraw()
        self.title(APP_TITLE)
        # 2026-08-31 피드백: "화면 비율을 가로비를 줄여 세로비를 넓혀" --
        # 기존 1200x600(2:1, 가로로 아주 긴 비율)은 왼쪽 조작 패널에 섹션이
        # 많아 세로 스크롤이 잦고, 가로는 오히려 남는 여백이 많았음. 가로를
        # 줄이고 세로를 늘려 더 세로로 긴 비율(1000x760, 약 1.3:1)로 변경.
        # 2026-09-07 피드백("프로그램 시작 할 때 튜토리얼과 메인 창이 아래로
        # 내려가 있어"): 크기만 주고 위치는 안 주면 창관리자가 알아서
        # 배치하는데, 일부 Windows 환경에서 화면 아래쪽으로 치우쳐 떴음 --
        # 화면 정중앙 좌표를 직접 계산해서 명시적으로 배치(튜토리얼 대화상자는
        # 이 메인 창 위치 기준으로 가운데 정렬되므로, 이렇게 하면 같이 따라
        # 중앙에 온다. _open_tutorial_dialog 참고).
        self.geometry(self._centered_geometry(1000, 760, top_margin_px=30))
        self.minsize(920, 680)
        try:
            self.configure(fg_color=BG_APP)
        except Exception:  # noqa: BLE001 -- 팔레트 적용은 장식일 뿐, 실패해도 앱은 떠야 함
            pass

        # 2026-08-26 피드백("exe 앱 디자인도 세련되고 고급지게"): 창/작업표시줄
        # 아이콘을 새 흑백+코발트 블루 브랜드 마크로 지정 -- assets/make_icon.py
        # 참고. Windows(.ico)와 그 외 플랫폼(.png) 둘 다 지원, 파일이 없거나
        # 실패해도 장식일 뿐이므로 앱 실행에는 영향 없음.
        try:
            assets_dir = os.path.join(_resource_dir(), "assets")
            icon_ico = os.path.join(assets_dir, "app_icon.ico")
            icon_png = os.path.join(assets_dir, "app_icon.png")
            if sys.platform.startswith("win") and os.path.isfile(icon_ico):
                self.iconbitmap(icon_ico)
            elif os.path.isfile(icon_png):
                self._icon_photo = tk.PhotoImage(file=icon_png)  # 참조 유지(GC 방지)
                self.iconphoto(True, self._icon_photo)
        except Exception:  # noqa: BLE001
            traceback.print_exc()

        self.font_title = ctk.CTkFont(family=FONT_FAMILY, size=_bumped(21), weight="bold")
        self.font_section = ctk.CTkFont(family=FONT_FAMILY, size=_bumped(14), weight="bold")
        self.font_body = ctk.CTkFont(family=FONT_FAMILY, size=_bumped(13))
        # 2026-08-31 피드백("글자들이 잘 안보여 -- 폰트와 색상 진하게"): 설명
        # 문구 폰트가 가늘고 작아서 잘 안 보인다는 피드백에 따라 크기를 조금
        # 키우고(11 -> 12) 굵게(bold) 바꿈.
        self.font_caption = ctk.CTkFont(family=FONT_FAMILY, size=_bumped(12), weight="bold")
        self.font_button = ctk.CTkFont(family=FONT_FAMILY, size=_bumped(13), weight="bold")
        # 2026-08-27 피드백: "미리보기 창의 문구 30%키우기" -- 미리보기
        # 캔버스 위에 직접 그리는 안내 문구(tk.Canvas.create_text, CTk 폰트
        # 자동 적용 대상이 아님)는 그동안 폰트를 따로 지정하지 않아 아주
        # 작은 기본 크기로 보였음. 본문 글자 크기 기준으로 30% 키운 전용
        # 폰트를 만들어 명시적으로 적용.
        self.font_canvas = ctk.CTkFont(family=FONT_FAMILY, size=round(_bumped(13) * 1.3))
        # 2026-09-07 피드백("설명 파트는 글씨는 10포인트 줄이고. 1.2.3. 번
        # 순으로 문제를 한줄로 설명해"): 오류/경고를 보여주던 OS 기본
        # messagebox는 폰트 크기를 코드에서 조절할 수 없어서(운영체제가 직접
        # 그리는 대화상자라 폰트가 이 앱 설정을 안 따름), 폰트를 직접 지정할
        # 수 있는 자체 대화상자(_show_note_dialog)로 바꾸면서 본문(font_body)
        # 보다 뚜렷하게 작은 전용 폰트를 새로 만듦.
        self.font_note = ctk.CTkFont(family=FONT_FAMILY, size=_bumped(11))

        self.input_path = tk.StringVar()
        self.is_vector = tk.BooleanVar(value=False)
        # 어도비 일러스트(.ai) 파일을 고르면 그 안의 실제 인쇄용 이미지를
        # 추출한 내부 작업용 PNG 경로가 self.input_path에 들어가므로(기존
        # 시트 합성 결과와 같은 방식), 내보내기 기본 파일명은 이 원본 이름을
        # 따로 기억해뒀다가 씀 (core.ai_import, _choose_file/_on_export 참고).
        self._export_basename = None
        self.dpi = tk.DoubleVar(value=300.0)
        self.safety_mm = tk.DoubleVar(value=1.0)
        self.cut_mm = tk.DoubleVar(value=2.0)
        self.bleed_mm = tk.DoubleVar(value=3.0)
        self.status = tk.StringVar(value="파일을 선택하세요 (주 형식: 어도비 일러스트 .ai / PNG / JPG)")

        # 작업 종류(2026-09-07 개편, "칼선의 종류는 크게 무테/유테/도무송/
        # 조각스티커 이렇게 있는데 지금은 그런 옵션이 잘 표현 되지 않고
        # 있어"): 도안 하나에 외곽선 하나를 그 이미지의 실제 모양에 맞춰
        # 만드는 것이 기본이고, 실제로 쓰이는 방식은 대등한 4가지다.
        #   무테(BORDERLESS)  -- 테두리 외곽선이 없는 이미지. 선택한 셀
        #                        (사각형) 안쪽으로 margin만큼 줄인 칼선.
        #   유테(LINE_ART)    -- 이미 테두리 외곽선이 그려진 이미지. 실제
        #                        실루엣(테두리 포함)을 인식해 바깥쪽으로
        #                        margin만큼 늘린 칼선.
        #   도무송(DOMUSONG)  -- 여백에 놓인 다른 이미지 등을 정해진 5개
        #                        도형(사각형/정사각/타원형/정원/완칼) 중
        #                        하나로 재단.
        #   조각 스티커(FULL_CUT) -- 이미지 단독 하나(또는 선택한 요소
        #                        하나)의 실제 실루엣을 그대로 따라가는 칼선.
        # (예전엔 "스티커"를 고른 뒤에만 유테/무테를 다시 고르는 2단계
        # 구조였는데, 이제 이 4개가 바로 job_type 값 자체 -- core.image_style.
        # ImageStyle의 BORDERLESS/LINE_ART와 이름을 그대로 맞춰 값 하나로
        # 바로 core 함수에 넘긴다. 이 값들을 코드에서 계속 그대로 쓰므로,
        # 이름이 바뀐 게 아니라 "스티커"라는 중간 단계 하나가 없어진 것.)
        # 드래그(또는 자동 인식) -> "이 영역 추가"를 반복하면, 그 각각의
        # 칼선이 전부 한 파일에 누적된다(아래 self._accumulated).
        # 처음엔 아무 것도 고르지 않은 빈 상태("")로 시작하고, 버튼도
        # "작업 종류를 선택하세요" placeholder로 보여준다(_make_category_picker,
        # JOB_TYPE_PLACEHOLDER 참고). 실제로 값을 쓰는 곳(_validate_inputs,
        # _generate_one_item)도 빈 값이면 먼저 선택하라고 안내하도록 맞춤.
        self.job_type = tk.StringVar(value="")

        # 무테/유테(BORDERLESS/LINE_ART)일 때만 쓰는 간격 값 -- 무테는
        # 셀 안쪽으로, 유테는 실루엣 바깥쪽으로 이 값(mm)만큼 적용.
        self.style_margin_mm = tk.DoubleVar(value=DEFAULT_STYLE_MARGIN_MM)

        # 도무송(DOMUSONG)일 때만 쓰는 세부 옵션: 5개 도형 중 선택. 완칼
        # (FULL_CUT) 잡타입은 이 값을 쓰지 않고 항상 실제 실루엣을 따라간다.
        # 2026-09-01 피드백: 이것도 미리 골라져 있지 않고 빈 값으로 시작
        # (CUTLINE_TYPE_PLACEHOLDER 참고).
        self.cutline_type = tk.StringVar(value="")
        # 유색/복잡한 배경에서도 선택 영역 안의 실제 도안만 자동으로 분리
        # (GrabCut). 끄면 드래그한 사각형 자체를 그대로 도형 크기 기준으로 씀.
        self.use_grabcut = tk.BooleanVar(value=True)

        # 2026-09-07 피드백("도무송과 무테를 동시에 선택할 수 있게 다중선택
        # 기능을 만들고"): job_type 자체를 단일 선택(StringVar)에서 다중
        # 선택(집합)으로 바꾸면 _validate_inputs/_generate_one_item 등 이미
        # 많은 곳에서 job_type 값 하나를 기준으로 분기하는 로직 전체를
        # 다시 짜야 해서 위험이 크다 -- 대신 가장 흔히 같이 쓰이는 조합
        # (도무송으로 원하는 도형을 자르면서, 동시에 같은 영역에 무테
        # 사각형 칼선도 함께 만드는 것)을 바로 지원하는 체크박스를 추가.
        # 켜져 있으면 "이 영역 추가"/"자동으로 여러 개 인식 + 추가"를 누를
        # 때 도무송 칼선 하나 + 무테 칼선 하나, 총 2개가 같은 영역에 대해
        # 함께 누적된다(둘 다 같은 selection_px를 씀). 기본값 꺼짐(False)
        # -- 예전처럼 도무송 하나만 원하는 사용자에게 동작이 바뀌지 않도록.
        self.domusong_also_borderless = tk.BooleanVar(value=False)

        # 정밀도(업샘플 배율) -- core.cutline_core.auto_supersample이 이미지/
        # 선택 영역이 커지면 이 값을 자동으로 낮춘다(처리 속도 보호). 여기서
        # 고르는 값은 "요청하는 상한"이지 무조건 그대로 쓰이는 값이 아님 --
        # 자동으로 낮아지면 완료 후 상태 메시지에 그대로 표시됨.
        self.precision = tk.IntVar(value=4)

        # 누적 작업 상태: 드래그 + 옵션 선택으로 만들어진 CutlineResult 하나
        # 하나가 여기 쌓이고, self._last_result는 그 전부를 합친(core.
        # accumulate.combine_results) 결과 -- "여러 번 드래그 + 옵션 선택 →
        # 전부 한 파일에 누적"이라는 실제 작업 원리를 그대로 구현한 것.
        self._accumulated = []
        self.accum_status = tk.StringVar(value="누적된 영역 없음")

        self._last_result = None
        self._preview_photo = None  # keep a reference so Tk doesn't GC it
        self._source_image = None  # PIL.Image of the currently loaded raster file
        self._display_scale = 1.0  # displayed-canvas-px -> original-image-px is 1/scale
        # 2026-09-07(7차) 피드백("키스컷 이미지 축소 되어 전체 화면이 보이지
        # 않음. 칼선이 잘 됐는지 확대해서 볼 수 있는 기능 필요"): 기존
        # _display_scale은 "캔버스 안에 다 들어오도록" 자동으로 맞추는
        # 배율이라, 그 상태 그대로는 세밀한 칼선을 확대해서 눈으로 확인할
        # 방법이 없었음. self._preview_zoom은 그 자동 맞춤 배율(base scale)
        # 위에 곱해지는 배율(1.0=100%, 100% 초과 시 캔버스보다 커지므로
        # 스크롤바로 이동해서 봄) -- _render_preview/_on_zoom_* 참고.
        # self._preview_full_image는 지금 화면에 표시 중인 "원본 해상도"
        # PIL 이미지(원본 소스이든 방금 만든 칼선 미리보기든) -- 확대/축소
        # 버튼을 누를 때마다 파일을 다시 읽지 않고 이 원본에서 다시
        # 리사이즈만 하면 되도록 캐시해 둔다.
        self._preview_zoom = 1.0
        self._preview_full_image = None
        # 2026-09-28(수동 확인 기능): "의심 영역 경고"(_missing_body_warning_notes)
        # 목록의 항목을 클릭하면 미리보기를 그 좌표로 이동/확대해서 보여주는
        # 기능에 쓰는 상태. 자동 수정이 아니라 "찾아가기 쉽게" 보조만 하는
        # 기능이라 강조 사각형 색을 기존 선택 사각형(코발트 점선)과 다르게
        # 주황색으로 구분한다 -- _navigate_preview_to_original_box 참고.
        self._nav_highlight_rect_id = None
        self._nav_highlight_halo_id = None
        self._last_suspicious_regions_px = []
        # 2026-09-28(GrabCut 보조 기능 -- 트라이맵 힌트 보정 도구): 화면에서
        # 직접 "여긴 확실히 전경/배경"이라고 점 찍어 GrabCut 실루엣을 다시
        # 계산하는 수동 보정 모드의 상태. self._grabcut_hint_meta는
        # self._accumulated의 각 인덱스가 "어느 실루엣 추적 결과인지"(원본
        # 박스/margin_mm/dpi/같은 반복 그룹의 다른 인덱스들)를 기록해둔
        # 것 -- _run_auto_detect_and_add_all에서 유테(LINE_ART) 실루엣
        # 추적이 실제로 쓰인 항목만 채워 넣으므로, 이 정보가 있는 항목만
        # 힌트 보정 대상으로 제안한다(다른 경로로 추가된 항목은 그대로 두고
        # 아무 영향 없음).
        self._hint_mode_active = False
        self._hint_target_index = None
        self._hint_new_box_px = None
        self._hint_fg_points_px = []
        self._hint_bg_points_px = []
        self._hint_current_label = "fg"
        self._hint_marker_ids = []
        self._hint_panel = None
        self._grabcut_hint_meta = {}
        # 2026-09-08 피드백("확대 축소 기능에 손바닥 모양의 이동할 수 있는
        # 기능 추가"): 확대했을 때 스크롤바만으로는 이동이 불편하다는 지적
        # -- "이동(패닝)" 버튼을 눌러 켜면 캔버스 위 마우스 커서가 손바닥
        # 모양(hand2)으로 바뀌고, 왼쪽 버튼을 누른 채 끌면(기존의 "영역
        # 드래그 선택" 대신) 화면 자체가 손으로 끄는 것처럼 이동한다(tk
        # Canvas의 scan_mark/scan_dragto 사용). 다시 누르면 꺼지고 원래의
        # 영역 드래그-선택 모드로 돌아간다 -- _on_canvas_press/_drag/_release
        # 에서 이 값에 따라 분기.
        self._pan_mode = False
        self._selection_canvas_rect = None  # (x0,y0,x1,y1) in CANVAS px, while dragging
        self._selection_px = None  # (x0,y0,x1,y1) in ORIGINAL IMAGE px, once released
        # 2026-09-07 피드백: .ai 파일 안에 작가 본인이 이미 그려 놓은 진짜
        # 재단선 격자를 읽었으면(core.ai_cutline_reader.load_real_grid_cells)
        # 그 칸 목록 [(x0,y0,x1,y1), ...]이 여기 담긴다 -- 없으면 None.
        self._real_grid_cells_px = None
        self._drag_start = None
        self._selection_rect_id = None
        # 2026-09-07 피드백("드래그로 이미지 선택하는게 어려워서 커서를
        # 만들고 원하는 위치에 두면 자동으로 인식하게 설정"): 마우스를 누른
        # 채로 정확히 끌어야 하는 드래그 방식이 어렵다는 지적이 계속돼서,
        # "클릭 한 번 -> 그 자리의 도안을 자동으로 인식"으로 아예 바꿈(예전엔
        # 이 자리에 "두 번 클릭으로 첫/두 번째 모서리 찍기" 방식이 있었는데,
        # 클릭 한 번으로 충분해졌으니 없앰) -- _on_canvas_release/
        # _auto_select_at_point 참고. 드래그 자체도 여전히 되므로, 자동 인식이
        # 잘 안 맞는 경우를 위한 수동 대안으로 남겨둔다.
        self._selection_halo_id = None
        # 2026-09-26 피드백("칸 인식 하는 방법을 드래그에서 가져다 대면
        # 사각형으로 예비 모양이 뜨는 버전으로... 취소하기도 좋을것 같아"):
        # 클릭하기 전, 마우스를 그 자리에 가져다 대기만 해도(누르지 않고)
        # 그 위치의 도안 예비 모양을 옅은 선으로 먼저 보여준다. 다른 곳으로
        # 옮기면 그냥 사라지므로(아무것도 확정되지 않음) 취소가 필요 없고,
        # 원하는 자리에서 클릭하면 그 예비 모양이 그대로 확정된다.
        self._hover_preview_rect_id = None
        self._hover_preview_box_px = None  # 지금 미리보기로 표시 중인 도안 bbox(원본 이미지 px)
        self._design_boxes_cache_key = None  # (path, mtime) -- 이미지가 안 바뀌었으면 재사용
        self._design_boxes_cache = None  # 위 캐시에 대응하는 detect_design_bboxes_px 결과
        # 2026-09-26 피드백("이제 도안은 3초내 인식하는데 스크롤에 응답하지
        # 않아서 사용할 수가 없어"): 파일을 불러오자마자 화면은 바로
        # 반응하게 됐지만(_run_load_ai의 순서 변경), 그 직후 백그라운드
        # 스레드에서 이 캐시를 미리 데워두는 계산(_get_cached_design_boxes ->
        # detect_design_bboxes_px, 실측 28~30초)이 아직 끝나기 전에 사용자가
        # 캔버스에 마우스를 올리면(호버 미리보기), 그 이벤트는 메인 스레드
        # (Tk 이벤트 루프)에서 처리되는데 캐시가 아직 비어 있으니 그 마우스
        # 이벤트 처리 자체가 detect_design_bboxes_px를 "메인 스레드에서"
        # 새로 또 돌려버린다 -- 결과적으로 그 계산이 끝나는 30초 동안 Tk
        # 이벤트 루프 전체가 멈춰, 스크롤을 포함한 모든 입력이 먹통이 된다
        # (같은 파일을 두 스레드가 동시에 두 번 계산하는 것도 낭비).
        # 아래 락으로 "이미 다른 스레드가 이 파일을 계산 중"인지 구분해서,
        # 메인 스레드(호버 등, blocking=False)는 그럴 땐 그냥 기다리지 않고
        # None을 돌려받아 화면을 멈추지 않고(잠깐 미리보기만 안 뜸), 백그라운드
        # 스레드(blocking=True)만 실제로 계산이 끝날 때까지 기다린다.
        self._design_boxes_locks = {}  # (path, mtime) -> threading.Lock
        self._design_boxes_locks_guard = threading.Lock()

        # 2026-09-10(35/36차 이어서) 피드백("도무송, 무테 동시 다중 선택하고
        # 만들어야 해... 표시해둔 것만 도무송(추천)", "도무송은 먼저 선택해서
        # 생성하고, 그다음에 다음 종류 선택하는 방향으로 가고"): 작업 종류
        # "무테/유테+도무송"(MIXED_AUTO)에서만 쓰는 상태 --
        # ① _on_mixed_detect로 시트 안 도안들을 전부 찾아 화면에 표시만
        # 해두고(아직 칼선 생성 안 함), 사람이 그중 도무송으로 만들 것만
        # 직접 클릭해서 표시(빨간 테두리)하면 ② _on_mixed_generate_domusong이
        # 표시된 것만 먼저 도무송 도형으로 칼선을 만들고, 확인 후 ③
        # _on_mixed_generate_rest가 나머지(표시 안 한 파란 테두리)를 자동으로
        # 무테/유테 스타일로 칼선을 만든다. "도안마다 도무송인지 그림만 보고 자동
        # 판단"은 이미 위험하다고 확인된 바 있어(실제로는 사각형이어도
        # 도무송이 아닐 수 있는, 제작 방식에 대한 결정) 이 방식은 쓰지
        # 않고, 표시는 항상 사람이 직접 한다.
        self._mixed_mode = False  # True인 동안은 캔버스 클릭이 새 영역 선택이 아니라 표시 토글로 동작
        self._mixed_path = None
        self._mixed_boxes = []  # [(x0,y0,x1,y1), ...] in ORIGINAL image px, 감지된 낱개 인스턴스 전부
        self._mixed_groups = []  # [[box_idx, ...], ...] -- 동일 도안 반복 그룹(group[0]=대표)
        self._mixed_marked = set()  # 도무송으로 표시된 "그룹 인덱스"(gi) 집합 -- 그룹 전체가 함께 표시됨
        self._mixed_rect_ids = {}  # gi -> [(halo_id, rect_id), ...] 캔버스 아이템, 재색칠용
        self._mixed_hover_gi = None  # 지금 마우스가 올라가 있는 그룹 인덱스(클릭 전 미리보기 강조용)
        # 2026-09-10(36차 이어서) 피드백("도무송은 먼저 선택해서 생성하고,
        # 그다음에 다음 종류 선택하는 방향으로 가고"): ②(표시한 도무송)와
        # ③(나머지 무테/유테)이 서로 다른 시점에 눌릴 수 있어, 이미 생성을
        # 끝낸 그룹 인덱스를 따로 기록해둬야 어느 버튼을 먼저/나중에 누르든
        # 같은 그룹이 두 번 생성되지 않는다.
        self._mixed_processed = set()
        # ②/③ 둘을 합쳐 "이번 표시 세션에서 실제로 새로 만든 개수"만 따로
        # 셈 -- self._accumulated는 이 세션 이전에 다른 방식으로 이미
        # 쌓아둔 항목까지 섞여 있을 수 있어, 그 길이를 그대로 "생성 완료"
        # 개수로 보여주면 실제와 다르게 부풀려질 수 있기 때문.
        self._mixed_added_count = 0

        # 설치 후 첫 실행에 자동으로 뜨는 튜토리얼 + "다시 보지 않기" 설정
        # (2026-08-26 피드백). 저장소 밖 사용자 홈 폴더에 작은 설정 파일로
        # 남겨서 git 저장소/파일 배포와는 완전히 무관하게 유지됨.
        self.tutorial_dont_show_again = tk.BooleanVar(value=False)

        # 2026-08-26 피드백("개인/기업 라이선스 분리 + 기업 라이선스 1대
        # 초과 설치 시 잠금"): 실제 화면(카드 섹션들)을 그리기 전에 먼저
        # 라이선스 상태부터 확인한다. 통과 못 하면 그 화면 대신 라이선스
        # 입력/재활성화 화면만 보여주고, 정상 화면은 아예 만들지 않는다
        # (버튼을 코드로 비활성화하는 방식이 아니라 화면 자체를 안 만드는
        # 구조적 잠금 -- core/license_client.py 참고).
        self._license_gate_frame = None
        self._build_license_loading()
        # "확인 중..." 화면을 실제로 그려서 화면에 보이게 강제로 한 번
        # repaint한 다음, 그 뒤에 (블로킹) 네트워크 확인을 진행한다 (2026-08-27
        # 피드백: 인터넷이 느리면 서버 응답을 최대 REQUEST_TIMEOUT_SEC(6초)까지
        # 기다리는 동안 화면에 아무 표시도 없이 완전히 멈춘 것처럼 보였음 --
        # 오늘 겪었던 "버튼이 안 눌려요" 상황을 고객이 프로그램 켤 때마다 겪을
        # 수 있다는 지적).
        #
        # self.after(...)로 미루지 않고 update_idletasks()로 즉시 강제
        # repaint하는 이유: after()는 mainloop가 실제로 돌아야 실행되는데,
        # 헤드리스 회귀 테스트들은 mainloop를 절대 돌리지 않으므로(의도적으로,
        # 아래 _show_tutorial_if_needed 예약과 같은 이유) after로 미루면 이
        # 라이선스 검사 자체가 테스트에서 영원히 실행되지 않게 된다.
        # update_idletasks()는 mainloop 없이도 지금까지 쌓인 화면 변경사항을
        # 강제로 그려주므로, 동기적으로 실행되면서도(테스트 호환성 유지)
        # 로딩 화면이 실제로 보이게 만들 수 있다.
        self.update_idletasks()
        self._proceed_past_license_gate()

    def _build_license_loading(self):
        """라이선스 서버에 확인 중임을 알리는 화면. 실제 확인 로직은 전혀
        없고 순수하게 "확인 중..." 표시만 한다 -- 확인 자체가 끝나면
        _proceed_past_license_gate()가 이 프레임을 지우고 결과에 맞는
        화면(정상 레이아웃 또는 라이선스 입력 화면)으로 바꾼다."""
        if self._license_gate_frame is not None:
            self._license_gate_frame.destroy()

        frame = ctk.CTkFrame(self, fg_color=BG_APP, corner_radius=0)
        frame.pack(fill="both", expand=True)
        self._license_gate_frame = frame

        card = ctk.CTkFrame(frame, fg_color=BG_CARD, corner_radius=16, border_width=1, border_color=BORDER)
        card.place(relx=0.5, rely=0.5, anchor="center")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(padx=40, pady=32)

        ctk.CTkLabel(
            inner, text="라이선스 확인 중...", font=self.font_section, text_color=TEXT_PRIMARY,
        ).pack()
        ctk.CTkLabel(
            inner, text="잠시만 기다려주세요.", font=self.font_caption, text_color=TEXT_SECONDARY,
        ).pack(pady=(6, 0))

    def _proceed_past_license_gate(self):
        result = lic.check_license()
        if result.ok:
            if self._license_gate_frame is not None:
                self._license_gate_frame.destroy()
                self._license_gate_frame = None
            self._build_layout()
            # mainloop가 실제로 돌기 시작한 뒤에 뜨도록 예약 -- 창이 화면에
            # 자리잡기 전에 모달을 띄우면 위치가 어긋날 수 있음. (테스트
            # 스크립트들은 mainloop를 돌리지 않으므로 이 콜백은 절대 실행되지
            # 않아 기존 헤드리스 회귀 테스트에 영향이 없음.)
            self.after(300, self._show_tutorial_if_needed)
        else:
            self._build_license_gate(result)
        # __init__에서 self.withdraw()로 숨겨뒀던 창을, 지금까지의 화면(정상
        # 레이아웃이든 라이선스 입력 화면이든) 구성이 다 끝난 지금에서야
        # 처음으로 보여준다. 이미 떠 있는 상태에서 또 호출돼도(예: 라이선스
        # 재시도) deiconify()는 아무 부작용 없이 그냥 무시됨.
        self.deiconify()
        # 2026-08-31 피드백("튜토리얼만 보이고 프로그램 창은 안 보여"): 일부
        # Windows 환경에서는 deiconify()만으로 창이 실제로 화면에 나타나지
        # 않는 경우가 있었다 -- 특히 이 프로그램처럼 시작하자마자 곧바로
        # withdraw()로 숨겼다가(한 번도 화면에 그려진 적 없는 창) 나중에
        # 되살리는 경우, 운영체제가 "새로 뜨는 창을 뒤로 보내는" 포커스
        # 보호 규칙과 겹쳐서 창이 아예 숨겨진 채로 남아있을 수 있다(300ms
        # 뒤에 뜨는 튜토리얼 창은 별개의 새 창이라 정상적으로 보이므로,
        # "튜토리얼만 보인다"는 증상과 정확히 일치). state("normal")로
        # 최소화/숨김 상태를 한 번 더 명시적으로 풀고, lift() + focus_force()
        # + 잠깐의 topmost로 강제로 맨 앞까지 끌어올린다. 이미 정상적으로
        # 보이는 경우에도 안전하게 반복 호출 가능(부작용 없음) -- mainloop가
        # 없는 헤드리스 테스트에서도 이 호출들 자체는 예외 없이 통과한다.
        self._force_window_to_front()
        # 혹시 위 동기 호출 시점에 아직 창 관리자(윈도우 매니저)가 준비되지
        # 않았을 경우를 대비해, mainloop가 실제로 돌기 시작한 뒤에도 한 번 더
        # 같은 처리를 예약해둔다(테스트에서는 mainloop를 안 돌리므로 그냥
        # 실행되지 않을 뿐, 부작용 없음).
        self.after(50, self._force_window_to_front)
        # 2026-09-08(10차) 피드백("프로그램 시작하고 도안 찾는 버튼이
        # 사라졌다가 수 초 후에 나타나") 대응: 왼쪽 조작 패널의 스크롤
        # 영역을 창이 실제로 화면에 뜬 지금 다시 강제로 맞춘다(위 left_col
        # 생성부의 주석 참고) -- 창관리자가 실제 창 크기를 확정하는 시점이
        # 기기마다 다를 수 있어, 지금 한 번 + 조금 뒤 한 번 더 시도한다.
        self._refresh_left_panel_scrollregion()
        self.after(50, self._refresh_left_panel_scrollregion)
        self.after(500, self._refresh_left_panel_scrollregion)

    def _refresh_left_panel_scrollregion(self):
        """왼쪽 조작 패널(CTkScrollableFrame)의 내부 스크롤 영역을 지금의
        실제 크기 기준으로 강제로 다시 계산한다. customtkinter는 이걸
        프레임 자신의 <Configure> 이벤트에서만 자동으로 하므로(위 left_col
        생성부 주석 참고), 창을 숨긴 채로 레이아웃을 구성했다가 나중에
        보여주는 이 앱의 시작 순서와 맞물리면 첫 섹션이 스크롤 영역
        계산이 안 맞은 채로 잠깐 안 보일 수 있다. customtkinter 내부
        구현(_parent_canvas)에 기대는 부분이라, 혹시 나중에 버전이 바뀌어
        그 속성이 없어지거나 이름이 달라져도 앱이 죽지 않도록 실패를
        조용히 무시한다."""
        left_col = getattr(self, "_left_col", None)
        if left_col is None:
            return
        try:
            left_col.update_idletasks()
            canvas = left_col._parent_canvas  # noqa: SLF001 -- customtkinter 내부 구현
            canvas.configure(scrollregion=canvas.bbox("all"))
        except Exception:  # noqa: BLE001
            pass

    def _centered_geometry(self, w, h, shift_up_ratio=0.0, top_margin_px=None):
        """가로 w x 세로 h 크기로 창 위치를 정하는 "WxH+X+Y" 문자열을 만든다.
        가로는 항상 화면 정중앙.

        2026-09-07(1차) 피드백("여전히 아래로 치우쳐 있어. 정중앙에 위치하고
        10cm정도 위로 이동해 고정하면 좋을것 같아")에는 화면 높이의 비율
        (shift_up_ratio)만큼 위로 옮기는 방식으로 대응했었는데, 2026-09-07
        (2차) 피드백("첫 화면에 뜨는 위치를 윈도우 상단으로 고정하면 잘
        보일것 같아")은 그 정도로는 부족하고 아예 "화면 맨 위 쪽에 고정"을
        원한다는 뜻이라, 비율 계산 대신 화면 위쪽에서부터의 고정 픽셀
        여백(top_margin_px)을 우선 적용하도록 바꿨다 -- 화면 높이가 얼마든
        (모니터 크기와 무관하게) 항상 위쪽에 붙어서 뜬다. top_margin_px를
        안 주면 예전처럼 shift_up_ratio 방식으로 계산(하위 호환).
        화면 크기를 못 읽어오는 예외적인 경우(예: 헤드리스 테스트)에도
        앱이 죽지 않도록, 실패하면 위치 없이 크기만 돌려준다(창관리자 기본
        배치로 그냥 뜸)."""
        try:
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
        except Exception:  # noqa: BLE001
            return f"{w}x{h}"
        x = max(0, (sw - w) // 2)
        if top_margin_px is not None:
            y = max(0, top_margin_px)
        else:
            y = max(0, (sh - h) // 2 - int(sh * shift_up_ratio))
        return f"{w}x{h}+{x}+{y}"

    def _force_window_to_front(self):
        """deiconify() 이후에도 창이 화면에 나타나지 않는 경우를 대비한
        안전장치. 이미 정상적으로 보이고 있어도 반복 호출해서 문제될 게
        없는 동작들만 모아둠(각각 실패해도 나머지는 계속 진행)."""
        try:
            self.state("normal")
        except Exception:  # noqa: BLE001
            pass
        try:
            self.lift()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.focus_force()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.attributes("-topmost", True)
            self.after(200, lambda: self.attributes("-topmost", False))
        except Exception:  # noqa: BLE001
            pass

    def _build_license_gate(self, result):
        """라이선스가 없거나/잠겨 있을 때 정상 화면 대신 보여주는 잠금
        화면. 재시도해서 통과하면 그제서야 _build_layout()이 호출된다."""
        if self._license_gate_frame is not None:
            self._license_gate_frame.destroy()

        frame = ctk.CTkFrame(self, fg_color=BG_APP, corner_radius=0)
        frame.pack(fill="both", expand=True)
        self._license_gate_frame = frame

        card = ctk.CTkFrame(frame, fg_color=BG_CARD, corner_radius=16, border_width=1, border_color=BORDER)
        card.place(relx=0.5, rely=0.5, anchor="center")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(padx=32, pady=28)

        title_text = "잠긴 라이선스" if result.state == "locked" else "라이선스 활성화"
        ctk.CTkLabel(
            inner, text=title_text, font=self.font_section, text_color=TEXT_PRIMARY, anchor="w",
        ).pack(fill="x", pady=(0, 6))

        status_label = ctk.CTkLabel(
            inner, text=result.message or "라이선스 키를 입력해주세요.", font=self.font_body,
            text_color=TEXT_SECONDARY, anchor="w", justify="left", wraplength=310,
        )
        status_label.pack(fill="x", pady=(0, 14))

        # 2026-08-27 피드백: "가로 길이를 조금 넓이고" -- 입력창/버튼 모두
        # 이 inner 프레임 폭을 기준으로 채워지므로(pack fill="x"), 입력창
        # 폭을 340 -> 400으로 넓히면 버튼 가로 길이도 함께 넓어짐.
        ctk.CTkLabel(inner, text="라이선스 키", font=self.font_caption, text_color=TEXT_SECONDARY, anchor="w").pack(fill="x")
        key_var = tk.StringVar(value=lic.get_cached_license_key())
        key_entry = ctk.CTkEntry(inner, textvariable=key_var, width=400, height=36, corner_radius=10)
        key_entry.pack(fill="x", pady=(2, 12))

        code_var = tk.StringVar()
        if result.state == "locked":
            ctk.CTkLabel(
                inner, text="재활성화 코드 (발급처에서 받은 코드)", font=self.font_caption,
                text_color=TEXT_SECONDARY, anchor="w",
            ).pack(fill="x")
            code_entry = ctk.CTkEntry(inner, textvariable=code_var, width=400, height=36, corner_radius=10)
            code_entry.pack(fill="x", pady=(2, 12))

        def _on_submit():
            key = key_var.get().strip()
            code = code_var.get().strip()
            if not key:
                status_label.configure(text="라이선스 키를 입력해주세요.", text_color=COLOR_CUT)
                return
            # 2026-08-27 피드백: 서버 확인이 몇 초 걸리는 동안 버튼을 눌러도
            # 아무 반응이 없는 것처럼 보였음. 클릭 즉시 상태 문구를 바꾸고
            # 버튼을 비활성화한 뒤 강제로 한 번 다시 그리게 해서(update_idletasks),
            # 실제 네트워크 확인이 끝나기 전에도 "처리 중"이라는 걸 알 수 있게 한다.
            status_label.configure(text="확인 중입니다...", text_color=TEXT_SECONDARY)
            submit_btn.configure(state="disabled")
            self.update_idletasks()
            if code:
                r = lic.reactivate(key, code)
            else:
                r = lic.activate(key)
            if r.ok:
                self._proceed_past_license_gate()
            else:
                # 상태가 바뀌었을 수 있으니(예: 잠긴 게 아니라 활성화가
                # 필요한 상태였음) 화면을 그 결과에 맞춰 다시 그린다.
                self._build_license_gate(r)

        btn_text = "재활성화" if result.state == "locked" else "활성화"
        submit_btn = self._btn_primary(inner, btn_text, _on_submit)
        submit_btn.pack(fill="x")

    # ------------------------------------------------------------------
    # 작은 빌더 도우미들 (시각 언어를 한 곳에서 일관되게 유지하기 위한 것들) --
    # 비즈니스 로직은 전혀 담지 않는다.
    # ------------------------------------------------------------------
    def _section(self, parent, title, subtitle=None):
        """카드형 섹션 하나를 만들고, 내용물을 채워 넣을 내부 프레임을 반환."""
        card = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=14, border_width=1, border_color=BORDER)
        card.pack(fill="x", pady=(0, 10), padx=2)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=16, pady=12)
        ctk.CTkLabel(
            inner, text=title, font=self.font_section, text_color=TEXT_PRIMARY,
            anchor="w", justify="left",
        ).pack(fill="x")
        if subtitle:
            ctk.CTkLabel(
                inner, text=subtitle, font=self.font_caption, text_color=TEXT_SECONDARY,
                anchor="w", justify="left", wraplength=330,
            ).pack(fill="x", pady=(3, 0))
        return inner

    def _btn_primary(self, parent, text, command):
        """핵심 동작(추가/미리보기, 내보내기)에만 쓰는 강조 버튼.
        2026-08-27 피드백("버튼이 너무 크고 굵어 ... 모든 버튼 크기 줄여"):
        기존 corner_radius=24 + 10mm 확대를 되돌리고, Figma류 UI에서 흔히
        쓰는 좀 더 절제된 라운드(16px)와 "중간" 높이로 축소."""
        return ctk.CTkButton(
            parent, text=text, command=command, font=self.font_button,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#FFFFFF",
            corner_radius=16, height=40 + BUTTON_SIZE_BUMP_PX,
        )

    def _refresh_domusong_yn_buttons(self):
        """0번 "도무송 여부" 버튼 중 현재 작업 종류에 해당하는 쪽만 강조."""
        yes = getattr(self, "_yn_yes_btn", None)
        no = getattr(self, "_yn_no_btn", None)
        if yes is None or no is None:
            return
        job = self.job_type.get()
        chosen = yes if job in ("MIXED_AUTO", "DOMUSONG") else (no if job else None)
        for b in (yes, no):
            if b is chosen:
                b.configure(fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#FFFFFF",
                            border_width=0)
            else:
                b.configure(fg_color="transparent", hover_color=ACCENT_SOFT,
                            text_color=TEXT_PRIMARY, border_width=1)

    def _btn_secondary(self, parent, text, command):
        """나머지 보조 동작에 쓰는 아웃라인 버튼. 같은 이유로 라운드/높이를
        축소(20 -> 12, 36 -> 32 + 소폭 bump)."""
        return ctk.CTkButton(
            parent, text=text, command=command, font=self.font_body,
            fg_color="transparent", hover_color=ACCENT_SOFT, text_color=TEXT_PRIMARY,
            border_width=1, border_color=BORDER, corner_radius=12,
            height=32 + BUTTON_SIZE_BUMP_PX,
        )

    # 2026-08-31 피드백: "도무송 카테고리식 이해가 안돼? 첨부한 이미지와
    # 같이 만들어" -- 첨부해준 참고 이미지가 Notion의 속성 선택 UI(버튼
    # 바로 아래 붙는 드롭다운, 옵션마다 파스텔 색 알약, 현재 값 옆 ✕로
    # 선택 해제)였음. 예전엔 화면 가운데 뜨는 별도 모달 팝업이었는데, 이제
    # 버튼 바로 아래 붙는 테두리 없는 드롭다운 + 옵션별 색깔 알약으로 다시
    # 만듦(아래 _toggle_category_dropdown).
    # 2026-09-10(39차) 피드백("유형 선택지 색 파스텔 블루로 통일"): 옵션마다
    # 다른 색(파랑/초록/주황/보라/분홍)을 돌아가며 쓰던 것을, 하나의 파스텔
    # 블루로 통일함(원래 있던 팔레트 중 첫 번째 색 그대로 재사용).
    _PILL_COLORS = [
        ("#E8F0FB", "#1D4E96"),
    ]

    def _make_category_picker(self, parent, current_label_var, options, variable, popup_title,
                               on_change=None, allow_clear=False):
        """2026-08-27/31 피드백: 화면에 항상 펼쳐진 라디오버튼 대신, 지금
        고른 값만 보여주는 버튼 하나 + 버튼 아래 드롭다운으로 고르게 함.
        allow_clear=True면(작업 종류에 사용) 아무 것도 안 고른 빈 값("")도
        정상 상태로 취급해서, 버튼이 채워진 색 대신 빈 테두리 버튼으로
        보이고 드롭다운에 "✕ 선택 해제"가 추가된다 -- 값 하나가 "무조건"
        미리 선택돼 있지 않게 하기 위함."""
        btn = ctk.CTkButton(
            parent, textvariable=current_label_var,
            command=lambda: self._toggle_category_dropdown(btn, options, variable, on_change, allow_clear),
            font=self.font_button, anchor="w",
            corner_radius=10, height=38 + BUTTON_SIZE_BUMP_PX,
        )

        def _refresh_look(*_args):
            if variable.get():
                btn.configure(fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#FFFFFF", border_width=0)
            else:
                btn.configure(
                    fg_color="#FFFFFF", hover_color=ACCENT_SOFT, text_color=TEXT_SECONDARY,
                    border_width=1, border_color=BORDER,
                )

        variable.trace_add("write", _refresh_look)
        _refresh_look()
        return btn

    def _close_open_dropdown(self):
        dd = getattr(self, "_open_dropdown", None)
        if dd is not None:
            try:
                dd.grab_release()
            except Exception:  # noqa: BLE001
                pass
            try:
                dd.destroy()
            except Exception:  # noqa: BLE001
                pass
        self._open_dropdown = None
        self._dropdown_anchor = None
        if getattr(self, "_dropdown_click_bound", False):
            try:
                self.unbind_all("<Button-1>")
            except Exception:  # noqa: BLE001
                pass
            try:
                self.unbind_all("<Escape>")
            except Exception:  # noqa: BLE001
                pass
            self._dropdown_click_bound = False

    def _toggle_category_dropdown(self, anchor_widget, options, variable, on_change, allow_clear):
        """options: [(값, 라벨), ...]. 같은 버튼을 다시 누르면 닫기만 하고
        끝(토글). 다른 드롭다운이 열려 있었으면 먼저 닫는다.

        2026-09-08 피드백("옵션 자리 이탈 사라지지 않아서 강제 종료"): 이
        드롭다운은 창 테두리가 없는(overrideredirect) + 항상 맨 위
        (topmost) 뜨는 팝업 창이다. 예전엔 이 팝업을 만들고 위치를 계산하는
        코드 전체가 아무 보호 장치 없이 실행됐는데, 만약 그 중간 어디선가
        (예: 버튼을 만들거나 위치를 계산하는 도중) 예상 못한 예외가 나면
        self._open_dropdown에 이 창이 등록되기도 전에 함수가 중간에
        끊겨버려서, 화면엔 이 창이 그대로 남아있는데 앱 스스로는 "열린
        드롭다운이 없다"고 착각하는 상태가 될 수 있었다. 그렇게 되면
        _close_open_dropdown()이 아무 것도 찾지 못해 절대 닫을 수 없는
        (테두리도 없고 항상 맨 위인) 유령 창이 화면에 남아, 결국 프로그램을
        강제 종료하는 것 말고는 없앨 방법이 없어진다. 이제 이 함수 전체를
        try/except로 감싸서, 어떤 이유로 실패하든 방금 만든 창을 즉시
        파괴하고 아무 것도 남기지 않는다. 또한 Esc 키를 누르면 항상 열린
        드롭다운을 닫도록 추가해서, 혹시라도 다른 이유로 화면에서 사라지지
        않는 경우에도 창 전체를 끄지 않고 Esc 키만으로 빠져나갈 수 있는
        비상 탈출구를 만들어 둔다."""
        was_open_for_this = (
            getattr(self, "_dropdown_anchor", None) is anchor_widget
            and getattr(self, "_open_dropdown", None) is not None
        )
        self._close_open_dropdown()
        if was_open_for_this:
            return

        dropdown = ctk.CTkToplevel(self)
        try:
            self._build_and_position_dropdown(dropdown, anchor_widget, options, variable, on_change, allow_clear)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            try:
                dropdown.destroy()
            except Exception:  # noqa: BLE001
                pass
            self._open_dropdown = None
            self._dropdown_anchor = None

    def _build_and_position_dropdown(self, dropdown, anchor_widget, options, variable, on_change, allow_clear):
        """_toggle_category_dropdown이 새 드롭다운을 실제로 만들고 화면에
        배치하는 부분만 따로 뗀 것 -- 호출부에서 이 전체를 try/except로
        감쌀 수 있도록(위 docstring의 "유령 창 방지" 참고) 함수를 분리."""
        try:
            dropdown.overrideredirect(True)
        except Exception:  # noqa: BLE001
            pass
        try:
            dropdown.attributes("-topmost", True)
        except Exception:  # noqa: BLE001
            pass

        # 2026-09-26(3차, 멍푸가 실제 화면으로 재확인 -- "옵션 밖으로
        # 나오는거 고치라고 서너번 말했는데 왜 개선이 안돼"): 위(2차) 수정
        # (card.pack_propagate(False) + card 폭 고정)은 카드 "틀" 자체가
        # 커지는 건 막았지만, 그 안의 옵션 버튼 하나하나가 자기 글자 폭만큼
        # 요구하는 최소 크기까지는 못 줄였다 -- Tk는 부모 프레임 폭을
        # 고정해도 자식 위젯이 그보다 넓기를 "요구"하면 그 자식을 잘라내지
        # 않고 프레임 밖으로 그대로 삐져나오게 그린다(팩 매니저가 넘치는
        # 자식을 클리핑하지 않기 때문). 실제로 그녀의 화면 캡처로 확인:
        # "무테/유테+도무송" 같은 긴 옵션 글자가 Windows 폰트로는 앵커
        # 버튼 폭보다 넓게 요구되어, 카드 폭을 고정했는데도 그 버튼만
        # 오른쪽 미리보기 화면까지 삐져나왔다.
        #
        # 근본 원인은 "카드가 넓어짐"이 아니라 "버튼 글자가 한 줄로
        # 강제됨"이었으므로, 이번엔 각 옵션 버튼 내부 글자 라벨에 명시적
        # wraplength(카드 폭 기준, 아래에서 앵커 폭으로 미리 계산)를 줘서
        # 글자가 넓으면 그냥 두 줄로 줄바꿈되게 한다 -- 줄바꿈된 라벨은
        # 세로로만 커지고 가로로는 절대 wraplength를 넘지 않으므로, 어떤
        # 글꼴/해상도에서도 버튼이 카드 폭을 넘어설 수 없다(2차 수정과
        # 함께 이중으로 안전).
        self.update_idletasks()
        popup_x = anchor_widget.winfo_rootx()
        popup_w = max(anchor_widget.winfo_width(), 240)

        # 2026-09-28("옵션 삐져 나온 공백 없애라고" -- 실제 화면 스크린샷으로
        # 재확인): 여태까지의 6차례 수정은 모두 "카드/버튼 자체가 팝업 폭(w)
        # 보다 넓어지는 것"만 막았는데, 이번엔 그 반대 방향 원인이다 --
        # popup_w의 기준인 anchor_widget(지금 고른 값을 보여주는 버튼,
        # 예: "무테/유테+도무송")은 왼쪽 조작 패널(self._left_col, 폭
        # 380px로 고정된 CTkScrollableFrame) *안에 스크롤되는 내용물*이라서,
        # 이 스크롤 프레임이 세로 스크롤만 지원하고 가로로는 내용물을
        # 자르지 않다 보니, 버튼 자신의 winfo_width()가 실제 화면에 보이는
        # 패널 폭보다 더 크게 보고될 수 있다(글자가 길어서 버튼이 자기
        # 내용에 맞춰 자연스럽게 넓어진 경우 등). 그러면 이 팝업 카드도 그
        # 만큼 넓게 뜨는데, 안쪽 옵션 버튼들(무테/유테/자동생성 등)은 그보다
        # 짧은 글자라 카드보다 좁게 그려져서, 카드의 나머지 빈 공간이 왼쪽
        # 패널 경계를 넘어 오른쪽 미리보기 화면 쪽으로 "삐져나온 공백"처럼
        # 보이게 된다. 기존의 가로(x) 보정은 화면 전체 폭(screen_w) 기준
        # 이라서, 이 패널 폭보다는 훨씬 넓은 화면에서는 전혀 걸리지 않았다.
        # 팝업은 항상 이 패널의 실제 화면 폭 안에서만 뜨게(패널보다 넓어질
        # 수 없게) 먼저 못박는다 -- 패널 자체가 width=380으로 고정돼 있어
        # 이 폭은 글꼴/해상도와 무관하게 안정적이다.
        left_col = getattr(self, "_left_col", None)
        if left_col is not None:
            try:
                panel_right = left_col.winfo_rootx() + left_col.winfo_width()
                max_w_in_panel = panel_right - popup_x - 4
                if max_w_in_panel >= 200:
                    popup_w = min(popup_w, max_w_in_panel)
            except Exception:  # noqa: BLE001
                pass

        # 버튼 pack(padx=8)의 좌우 여백 + 버튼 내부 여백을 뺀, 글자가 실제로
        # 쓸 수 있는 폭.
        option_wraplength_px = max(60, popup_w - 2 * 8 - 16)

        card = ctk.CTkFrame(dropdown, fg_color=BG_CARD, corner_radius=10, border_width=1, border_color=BORDER)
        card.pack(fill="both", expand=True)
        # 2026-09-08(8차) 피드백 확인 결과("그냥 옵션을 고르려던 중이었는데
        # 자리 이탈 + 사라지지 않음 + 선택도 안 됨"): 드물게 예외가 났을
        # 때만이 아니라, 그냥 평소처럼 옵션을 고르려는 순간에도 벌어졌다는
        # 점이 핵심 -- 이 팝업이 창 테두리 없는(overrideredirect) +
        # 항상 맨 위(topmost) 창인데, 정작 이 창 자체에 입력 포커스를
        # 넘기는 처리가 전혀 없었다. Windows에서는 이런 팝업이 화면엔
        # 맨 위로 보여도 실제 마우스 클릭은 그 아래 깔린 메인 창으로
        # 새어나가는 경우가 있는데, 그러면 (a) 옵션 버튼을 눌러도 반응이
        # 없고(선택 안 됨), (b) 바깥 클릭 감지(_on_global_click)는 "화면
        # 좌표상 팝업 영역 안"이라고 착각해 안 닫아버려서(자리는 그대로
        # 보이는데 사라지지도 않음) 결과적으로 완전히 멈춘 것처럼 보이고
        # 클릭이 전혀 안 먹히니 강제 종료 말고는 답이 없었을 것으로 보인다.
        # 근본 대응: 이 팝업을 진짜 모달로 만든다(lift+focus_force+
        # grab_set) -- grab_set 이후로는 이 앱의 모든 마우스 클릭이
        # "이 팝업 창(과 그 안의 버튼들)"에만 전달되도록 OS/Tk가 강제하므로,
        # 클릭이 엉뚱한 곳으로 새어나가는 경로 자체가 사라진다. 대신 팝업이
        # 뜬 동안은 "바깥의 다른 버튼을 클릭"해서 닫던 방식이 항상 보장되진
        # 않으므로(로컬 grab은 같은 앱의 다른 창까지 클릭을 전달하지 않을
        # 수 있음), 그 대체 수단으로 (1) 옵션을 고르면 당연히 닫히고,
        # (2) 카드 안의 버튼이 아닌 빈 공간을 클릭해도 닫히도록(아래
        # card.bind) 새로 추가하고, (3) 위에서 이미 추가한 Esc 키로도
        # 언제든 닫을 수 있다 -- 기존의 "바깥 클릭 시 닫기"(_on_global_click)
        # 도 혹시 클릭이 정상적으로 앱에 전달되는 환경에서는 여전히 그대로
        # 동작하므로 없애지 않고 그대로 둔다(이중 안전장치).
        card.bind("<Button-1>", lambda _e: self._close_open_dropdown())

        def _pick(value):
            variable.set(value)
            if on_change:
                on_change()
            self._close_open_dropdown()

        def _clamp_button_width(btn):
            # 위 option_wraplength_px 설명 참고: 라벨(_text_label)에
            # wraplength를 강제해, 글자가 길어도 옆으로 삐져나오지 않고
            # 카드 폭 안에서 줄바꿈되게 한다.
            #
            # 2026-09-26(6차, "옵션 이탈 좀 고치라고!!!" -- 앞서 5차까지
            # wraplength+카드 폭 고정으로 고쳤다고 판단했지만 실제 그녀의
            # Windows 화면에서 여전히 재발): wraplength는 "띄어쓰기가 있는
            # 자리에서만" 줄을 바꾸는 힌트일 뿐이다. 만약 옵션 글자 중
            # 띄어쓰기 없이 쭉 이어지는 부분이 wraplength보다 길면(예:
            # 폰트가 달라 한 단어처럼 붙어 측정되는 경우), Tk는 그 한 덩어리를
            # 줄바꿈하지 못하고 그대로 wraplength보다 넓게 그려버린다 --
            # 이게 바로 이 사무실 리눅스 환경(글꼴 다름)에서는 재현이 안
            # 되면서 그녀의 실제 Windows 글꼴에서만 계속 재발했던 이유로
            # 보인다. wraplength(글자 줄바꿈 "힌트")에만 기대지 말고, 버튼
            # 위젯 자체의 width를 카드 폭 기준으로 못박아 강제 고정한다 --
            # 이러면 내용이 아무리 넓어지려 해도 버튼 자체가 그 이상 커질
            # 수 없다(내용은 필요하면 잘려 보일지언정, 카드/팝업 밖으로
            # 넘치는 일은 이제 폰트/줄바꿈 여부와 상관없이 원천적으로
            # 불가능해진다).
            try:
                btn.configure(width=option_wraplength_px)
            except Exception:  # noqa: BLE001
                pass
            try:
                if btn._text_label is not None:
                    btn._text_label.configure(wraplength=option_wraplength_px)
            except Exception:  # noqa: BLE001
                pass
            return btn

        # 2026-09-26(멍푸 피드백 "옵션 스크롤로 볼수 있게 해줘 이렇게 자꾸
        # 형식을 어기면 완성도가 떨어져"): 옵션 개수가 많거나 화면이 작으면
        # 이 팝업이 화면보다 커질 수 있는데, 바로 아래(y좌표 계산부)의 기존
        # 코드는 그럴 때 그냥 팝업을 화면 위쪽에 눌러 담기만 해서 -- 뒤쪽
        # 옵션(예: 도무송 세부 옵션의 "원형" 등)이 화면 밖으로 밀려나 보이지도
        # 않고 클릭도 안 됐다. 이제 옵션 목록을 아예 스크롤 가능한 영역에
        # 담아서, 몇 개가 있든 스크롤(휠/드래그)로 전부 볼 수 있고 전부 누를
        # 수 있게 한다.
        def _build_buttons(parent):
            first = True
            if allow_clear and variable.get():
                _clamp_button_width(ctk.CTkButton(
                    parent, text="✕ 선택 해제", command=lambda: _pick(""), font=self.font_caption,
                    fg_color="transparent", hover_color=ACCENT_SOFT, text_color=TEXT_SECONDARY,
                    anchor="w", corner_radius=8, height=30,
                )).pack(fill="x", padx=8, pady=(8, 2))
                first = False

            for i, (value, label) in enumerate(options):
                bg, fg = self._PILL_COLORS[i % len(self._PILL_COLORS)]
                selected = value == variable.get()
                _clamp_button_width(ctk.CTkButton(
                    parent, text=label, command=lambda v=value: _pick(v), font=self.font_body,
                    fg_color=bg, hover_color=bg, text_color=fg, anchor="w", corner_radius=8,
                    height=36, border_width=2 if selected else 0, border_color=ACCENT,
                )).pack(fill="x", padx=8, pady=(8 if first else 3, 3))
                first = False

            ctk.CTkFrame(parent, fg_color="transparent", height=6).pack()

        # 1차: 지금까지 하던 대로 card에 바로 옵션을 채워서 "자연스러운
        # 높이"(스크롤 없이 다 펼치면 몇 px인지)를 먼저 재본다.
        _build_buttons(card)
        x = popup_x
        w = popup_w
        dropdown.update_idletasks()
        natural_h = card.winfo_reqheight()

        screen_h_probe = dropdown.winfo_screenheight()
        margin_probe = 8
        # 2026-09-26(4차, "옵션이 칸안에서 스크롤이 되어야지 ... 오늘
        # 하루종일 말해야해"): 이전엔 "화면보다 큰 경우에만" 스크롤을 켰는데,
        # 실제로는 옵션이 5~7개만 돼도(화면보다는 작지만) 팝업 카드 자체가
        # 계속 늘어나서 뒤에 있는 다른 칸(작업 종류 라벨, 그 아래 도무송
        # 세부 옵션 버튼들)을 덮어버렸다 -- 화면 밖으로 나가진 않았지만
        # "다른 칸 위로 떠서 가려버리는" 것도 결국 같은 문제. 이제 옵션이
        # 몇 개든, 화면 크기와 상관없이 팝업 자체의 키를 작게(대략 4개 정도
        # 보이는 높이) 고정하고, 그보다 많으면 무조건 스크롤 영역으로
        # 바꿔서 항상 작은 칸 하나 안에서 스크롤로만 보게 한다.
        FIXED_VISIBLE_CAP_PX = 210
        max_popup_h = min(screen_h_probe - 2 * margin_probe, FIXED_VISIBLE_CAP_PX)

        if natural_h > max_popup_h:
            # 화면에 다 못 들어감 -- 방금 만든 옵션 버튼들을 지우고, 같은
            # 버튼들을 스크롤 가능한 프레임 안에 다시 만든다(card 자체는
            # 재사용, 안쪽 내용만 스크롤 영역으로 교체).
            for child in list(card.winfo_children()):
                child.destroy()
            scroll_area = ctk.CTkScrollableFrame(
                card, fg_color="transparent", corner_radius=0,
                width=w, height=max_popup_h,
            )
            scroll_area.pack(fill="both", expand=True)
            _build_buttons(scroll_area)
            dropdown.update_idletasks()
            h = max_popup_h
        else:
            h = natural_h

        # 2026-09-26(멍푸 피드백, 실제 화면: "옵션 크기안에 들어가게 해,
        # 빠져나오게 하지마" -- 이 팝업이 버튼(anchor_widget)과 같은 폭(w)
        # 으로 뜨도록 위에서 이미 계산했는데도, 실제 그녀 컴퓨터에서는
        # 왼쪽 패널 폭을 넘어 오른쪽 미리보기 화면까지 삐져나왔다. 원인:
        # 안의 옵션 버튼들(예: "직사각형", "정사각형")은 폭을 따로 고정
        # 하지 않고 글자 크기에 맞춰 저절로 커지는데, 이 카드(card)가 원래
        # "자식이 더 넓으면 나(card)도 같이 커진다"는 기본 동작(pack
        # propagate)을 그대로 갖고 있어서, 그 순간 card가(그리고 card를
        # 담은 이 팝업 창까지) w보다 넓게 저절로 늘어날 수 있었다 --
        # Linux 환경에서는 글꼴 렌더링 폭이 우연히 w 안에 들어가 재현이
        # 안 됐지만, 실제 Windows 폰트 렌더링에서는 그 폭을 넘어설 수
        # 있다는 뜻. 이제 card 크기를 w(가로)로 못박고(propagate 끔), 그
        # 안의 버튼이 아무리 넓어지려 해도 이 카드 밖으로는 못 나가게
        # 한다 -- 글자가 넓으면 카드 안에서 잘려 보일지언정, 옆 화면을
        # 침범하는 일은 없다.
        card.pack_propagate(False)
        card.configure(width=w, height=h)

        # 2026-09-26(실제 화면으로 확인, "옵션도 다 잘려" -- 도무송 세부
        # 옵션처럼 버튼이 창 아래쪽에 있으면 팝업이 화면 밖으로 넘어가
        # 아래쪽 옵션(사각형/타원형/원형 등)이 안 보이고 클릭도 안 됐다):
        # 팝업 높이(h)가 버튼 개수만큼 늘어나는데, 여기선 화면 경계를 전혀
        # 확인 안 하고 항상 "버튼 바로 아래"에만 띄웠다. 화면 아래쪽에
        # 붙어있는 버튼일수록 팝업 전체가 화면 밖(또는 작업표시줄 아래)으로
        # 나가버려, 뒤쪽 옵션일수록 안 보이고 안 눌리는 문제가 실제로
        # 재현됨. 화면 높이 안에 들어가도록: 아래에 자리가 부족하면 먼저
        # 버튼 위쪽에 띄우는 걸 시도하고, 위쪽에도 다 안 들어가면(팝업
        # 자체가 화면보다 큰 경우) 화면 위/아래 여백 안에서 y를 눌러 담아
        # 최소한 위쪽 옵션이라도 화면 안에 들어오게 한다.
        screen_h = dropdown.winfo_screenheight()
        margin = 8
        y_below = anchor_widget.winfo_rooty() + anchor_widget.winfo_height() + 4
        y_above = anchor_widget.winfo_rooty() - h - 4
        if y_below + h <= screen_h - margin:
            y = y_below
        elif y_above >= margin:
            y = y_above
        else:
            y = max(margin, min(y_below, screen_h - margin - h))

        # 2026-09-26(6차): 세로(y)는 위에서 이미 화면 높이 기준으로 눌러
        # 담는데, 가로(x)는 지금까지 anchor_widget의 화면 좌표를 그대로
        # 써서 화면 폭 기준 점검이 아예 없었다 -- 창이 화면 오른쪽 끝에
        # 가깝게 있으면(작은 화면, 창을 오른쪽으로 옮겨둔 경우 등) 팝업이
        # 화면 오른쪽 바깥으로 밀려나 옵션이 화면 밖으로 "이탈"할 수 있다.
        # 세로와 똑같은 방식으로 가로도 화면 폭 안에 눌러 담는다.
        screen_w = dropdown.winfo_screenwidth()
        if x + w > screen_w - margin:
            x = screen_w - margin - w
        if x < margin:
            x = margin
        dropdown.geometry(f"{w}x{h}+{x}+{y}")

        # 위 주석 참고: 이 팝업 자체가 실제로 입력을 받도록 강제한다 --
        # 그냥 띄우기만 하고 포커스/grab을 넘기지 않으면 화면엔 맨 위로
        # 보여도 클릭이 아래 깔린 메인 창으로 새어나갈 수 있었다.
        try:
            dropdown.lift()
            dropdown.focus_force()
        except Exception:  # noqa: BLE001
            pass
        try:
            dropdown.grab_set()
        except Exception:  # noqa: BLE001
            pass

        self._open_dropdown = dropdown
        self._dropdown_anchor = anchor_widget

        def _on_global_click(event):
            dd = getattr(self, "_open_dropdown", None)
            if dd is None:
                return
            try:
                dx, dy = dd.winfo_rootx(), dd.winfo_rooty()
                dw, dh = dd.winfo_width(), dd.winfo_height()
            except Exception:  # noqa: BLE001
                self._close_open_dropdown()
                return
            inside_dropdown = dx <= event.x_root <= dx + dw and dy <= event.y_root <= dy + dh
            if not inside_dropdown and event.widget is not anchor_widget:
                self._close_open_dropdown()

        self.bind_all("<Button-1>", _on_global_click, add="+")
        # 2026-09-08 피드백 대응: Esc 키로도 언제든 열린 드롭다운을 닫을 수
        # 있게 함(위 docstring 참고) -- 클릭 감지가 어떤 이유로든 놓치는
        # 경우를 위한 비상 탈출구.
        self.bind_all("<Escape>", lambda _e: self._close_open_dropdown(), add="+")
        self._dropdown_click_bound = True

    def _make_number_field(self, parent, label, var, unit="mm", from_=0.0, to=1000.0,
                            increment=0.5, color=None):
        """숫자 입력 한 줄: (선택) 색상 스와치 + 라벨 + [-][입력칸][+] + 단위.
        기존 ttk.Spinbox/Entry들을 이걸로 통일해서 대체한다."""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=4)
        if color:
            swatch = ctk.CTkFrame(row, width=12, height=12, corner_radius=3, fg_color=color)
            swatch.pack(side="left", padx=(0, 8))
            swatch.pack_propagate(False)
        ctk.CTkLabel(
            row, text=label, font=self.font_body, text_color=TEXT_PRIMARY, anchor="w",
        ).pack(side="left", fill="x", expand=True)

        stepper = ctk.CTkFrame(row, fg_color="transparent")
        stepper.pack(side="right")

        def _step(delta):
            try:
                current = var.get()
            except (tk.TclError, ValueError):
                current = 0
            new_val = round(current + delta, 4)
            new_val = max(from_, min(to, new_val))
            var.set(new_val)

        ctk.CTkButton(
            stepper, text="−", width=30, height=30, corner_radius=10,
            fg_color="transparent", hover_color=ACCENT_SOFT, text_color=TEXT_PRIMARY,
            border_width=1, border_color=BORDER, font=self.font_body,
            command=lambda: _step(-increment),
        ).pack(side="left")
        ctk.CTkEntry(
            stepper, textvariable=var, width=60, height=30, corner_radius=10,
            border_width=1, border_color=BORDER, fg_color="#FFFFFF", justify="center",
            font=self.font_body,
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            stepper, text="+", width=30, height=30, corner_radius=10,
            fg_color="transparent", hover_color=ACCENT_SOFT, text_color=TEXT_PRIMARY,
            border_width=1, border_color=BORDER, font=self.font_body,
            command=lambda: _step(increment),
        ).pack(side="left")
        if unit:
            ctk.CTkLabel(
                stepper, text=unit, font=self.font_caption, text_color=TEXT_SECONDARY,
            ).pack(side="left", padx=(6, 0))
        return row

    def _set_interactive_state(self, widget, state):
        """widget과 그 자손 위젯들 중 state(활성/비활성) 옵션을 지원하는
        것(라디오버튼/체크박스/버튼/입력창/콤보박스 등)에만 적용. 프레임나
        라벨처럼 state 옵션이 없는 위젯은 조용히 건너뜀 -- 2026-08-27
        피드백("도무송은 무조건 선택이 아니라 선택할 수 있는 선택지를
        줘야지")에 따라 작업 종류와 관련 없는 세부 옵션 섹션 전체를 흐리게
        비활성화하는 데 씀(_on_job_type_changed 참고)."""
        try:
            widget.configure(state=state)
        except Exception:  # noqa: BLE001 -- state를 지원 안 하는 위젯(프레임/라벨 등)은 그냥 무시
            pass
        for child in widget.winfo_children():
            self._set_interactive_state(child, state)

    def _on_job_type_changed(self, *_args):
        """작업 종류(무테/유테/도무송/조각 스티커) 4개 중 무엇을 골랐는지에
        따라 관련 없는 세부 옵션 섹션(오프셋 간격/유테·무테 간격/도무송
        세부 옵션)을 흐리게 비활성화. 실제 생성 로직(_generate_one_item)
        에서도 그 작업 종류일 때만 해당 값을 읽으므로, 화면에서도 그때만
        만질 수 있게 맞춘 것 -- 이전엔 관련 없어도 항상 클릭 가능해서
        "지금 골라야 하는 값"처럼 보였음."""
        job = self.job_type.get()
        if hasattr(self, "job_type_label_var"):
            self.job_type_label_var.set(JOB_TYPE_LABELS.get(job) or JOB_TYPE_PLACEHOLDER)
        # 2026-09-08(9차) 피드백 대응: 마스킹테이프는 두 섹션을 동시에 씀 --
        # 개별 모티프 키스컷 간격은 "유테/무테 간격"(sec5, 유테와 동일)을,
        # 롤 전체 테두리는 "오프셋 간격"(sec4, 세이프티/칼선/블리딩)을 그대로
        # 재사용한다.
        self._set_interactive_state(
            self._sec4_offsets,
            "normal" if job in ("FULL_CUT", "DOMUSONG", "MASKING_TAPE", "MIXED_AUTO") else "disabled",
        )
        self._set_interactive_state(
            self._sec5_sticker,
            "normal" if job in ("BORDERLESS", "LINE_ART", "AUTO_STYLE", "MASKING_TAPE", "MIXED_AUTO") else "disabled",
        )
        self._set_interactive_state(
            self._sec6_domusong, "normal" if job in ("DOMUSONG", "MIXED_AUTO") else "disabled"
        )
        if hasattr(self, "_roll_border_btn"):
            self._set_interactive_state(
                self._roll_border_btn, "normal" if job == "MASKING_TAPE" else "disabled"
            )
        # 2026-09-10(35차 이어서): "무테/유테+도무송" 전용 버튼
        # 2개도 다른 작업 종류일 땐 흐리게 비활성화 -- _roll_border_btn과
        # 같은 패턴.
        if hasattr(self, "mixed_detect_btn"):
            self._set_interactive_state(
                self.mixed_detect_btn, "normal" if job == "MIXED_AUTO" else "disabled"
            )
        if hasattr(self, "mixed_generate_btn"):
            self._set_interactive_state(
                self.mixed_generate_btn, "normal" if job == "MIXED_AUTO" else "disabled"
            )
        if hasattr(self, "mixed_generate_rest_btn"):
            self._set_interactive_state(
                self.mixed_generate_rest_btn, "normal" if job == "MIXED_AUTO" else "disabled"
            )

        # 2026-09-26 피드백("파트로 나뉘어져 있고 불필요한 기능이 많고
        # 이해가 안돼"): "무테/유테+도무송"에서는 안 쓰는(심지어 눌러도
        # 오류만 뜨는) 버튼 3개를 아예 화면에서 치워서, 이 작업 종류를
        # 고르면 "작업 종류 선택 -> ①②③" 순서만 남아 한눈에 보이게 한다.
        # 다른 작업 종류로 바꾸면 원래 자리에 그대로 되돌아온다(숨기기 전
        # pack 옵션을 _sec3_mixed_hide_widgets에 그대로 기억해뒀다가 재사용).
        if hasattr(self, "_sec3_mixed_hide_widgets"):
            # winfo_ismapped()는 Tk가 아직 그 pack()을 실제로 처리하기 전
            # (대기 중인 idle 작업이 남아있을 때)엔 실제로 붙어 있어도
            # False를 돌려줄 수 있어(측정 자체가 화면 갱신 타이밍에 좌우됨),
            # 그걸로 "지금 보이는지"를 판단하면 드물게 pack_forget()을
            # 건너뛰어 안 숨겨질 수 있다 -- 대신 우리가 직접 기억해두는
            # 불리언(_sec3_mixed_hidden)으로 판단해 타이밍에 관계없이
            # 항상 정확하게 만든다.
            want_hidden = job == "MIXED_AUTO"
            if getattr(self, "_sec3_mixed_hidden", None) != want_hidden:
                for widget, pack_kwargs in self._sec3_mixed_hide_widgets:
                    try:
                        if want_hidden:
                            widget.pack_forget()
                        else:
                            widget.pack(**pack_kwargs)
                    except Exception:  # noqa: BLE001
                        pass
                self._sec3_mixed_hidden = want_hidden

    # ------------------------------------------------------------------
    # 설치 후 첫 실행 튜토리얼 (2026-08-26 피드백: "설치 후 첫 화면에
    # 튜토리얼을 자동으로 뛰우고 아래 선택 칸을 넣어서 다시 보지 않기도
    # 추가해"). 설정은 저장소(git) 밖 사용자 홈 폴더에 저장 -- 앱 파일을
    # 새로 받거나 업데이트해도 "다시 보지 않기"가 그대로 유지되고, git
    # 저장소에는 이 설정 파일이 절대 섞여 들어가지 않음.
    # ------------------------------------------------------------------
    def _tutorial_config_path(self):
        return os.path.join(os.path.expanduser("~"), ".cutline_studio", "config.json")

    def _load_tutorial_seen(self):
        import json

        try:
            with open(self._tutorial_config_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            # 저장된 버전이 지금 튜토리얼 버전과 다르면(= 튜토리얼 내용이
            # 바뀐 뒤 처음 실행) "본 적 없음"으로 취급해서 새 튜토리얼이
            # 한 번은 다시 뜨게 함.
            if int(data.get("tutorial_version", 0)) != TUTORIAL_VERSION:
                return False
            return bool(data.get("tutorial_seen"))
        except Exception:  # noqa: BLE001 -- 설정이 없거나 손상됐으면 그냥 다시 보여줌(안전한 쪽)
            return False

    def _save_tutorial_seen(self, value):
        import json

        path = self._tutorial_config_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {"tutorial_seen": bool(value), "tutorial_version": TUTORIAL_VERSION},
                    f, ensure_ascii=False,
                )
        except Exception:  # noqa: BLE001 -- 저장 실패해도 앱 동작에는 영향 없음(다음에도 다시 뜰 뿐)
            traceback.print_exc()

    def _show_tutorial_if_needed(self):
        if self._load_tutorial_seen():
            return
        # 튜토리얼(자식 창)을 띄우기 직전에 본창도 한 번 더 앞으로 끌어올려서
        # ("튜토리얼만 보이고 프로그램 창은 안 보여" 대비, _force_window_to_front
        # 참고) -- 튜토리얼을 닫았을 때 본창이 이미 정상적으로 화면에 있는
        # 상태이도록 함.
        self._force_window_to_front()
        self._open_tutorial_dialog()

    def _open_tutorial_dialog(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title(f"{APP_TITLE} 사용법")
        # 2026-08-31 피드백("첫 창에 뜨는 튜토리얼 너무 커"): 예전 680x640은
        # 본창(1000x760)에 비해서도 과하게 큰 편이었음 -- 이미지 표시 크기를
        # 줄인 것과 함께 대화상자 자체도 훨씬 작게.
        # 2026-09-07 피드백("튜토리얼3번이 이미지가 잘렸어"): 높이를 460으로
        # 고정해뒀던 게 원인 -- 슬라이드마다 캡션 길이가 달라서(3번 슬라이드가
        # 특히 김) 캡션이 2~3줄로 줄바꿈되면 실제 필요한 내용 높이가 460을
        # 넘어서는데, 창 크기를 코드로 명시적으로 한 번 지정하면(.geometry)
        # Tk가 그 이후로는 내용에 맞춰 자동으로 창을 키워주지 않아서, 넘친
        # 만큼 아래쪽 버튼(건너뛰기 등)이 화면 밖으로 밀려 잘려 보였음.
        # 이제 고정 높이 대신, 슬라이드가 바뀔 때마다(_render 참고) 실제
        # 내용에 필요한 높이를 다시 계산해서 창 크기를 맞춘다(어떤 캡션
        # 길이가 와도, 나중에 슬라이드가 추가되어도 다시 잘리지 않음).
        dialog.geometry("440x460")
        # 2026-09-08 피드백("프로그램이 클릭이 안돼", "프로그램이 안 닫혀",
        # "강제 종료해야해"): 이 창을 resizable(False, False)로 고정해둔
        # 채로 슬라이드마다 크기를 다시 계산해 geometry()를 반복 호출하니,
        # 특히 grab_set()으로 입력을 이 창에 묶어둔 상태에서 크기 고정을
        # 껐다 켰다 반복하는 게 겹치면서 창(그리고 프로그램 전체)이 멈춰
        # 버리는 심각한 문제가 있었다. 사용자가 마우스로 직접 늘리고
        # 줄일 수 있게 되는 것(작은 부작용)보다, 절대 멈추지 않는 것이
        # 훨씬 중요하므로 resizable(False, False) 자체를 없앤다 -- 이제
        # 이 창은 항상 "크기 조절 가능" 상태이고, geometry()만으로 매
        # 슬라이드에 맞게 안전하게 커지고 줄어든다(아래 _reflow_dialog_size
        # 참고, 더 이상 resizable을 껐다 켰다 하지 않음).
        try:
            dialog.configure(fg_color=BG_APP)
        except Exception:  # noqa: BLE001
            pass
        try:
            dialog.transient(self)
        except Exception:  # noqa: BLE001
            pass

        wrap = ctk.CTkFrame(dialog, fg_color=BG_CARD, corner_radius=16, border_width=1, border_color=BORDER)
        wrap.pack(fill="both", expand=True, padx=14, pady=14)

        ctk.CTkLabel(
            wrap, text="처음이신가요? 이렇게 쓰면 됩니다", font=self.font_section,
            text_color=TEXT_PRIMARY, anchor="w",
        ).pack(fill="x", padx=18, pady=(16, 8))

        image_wrap = ctk.CTkFrame(wrap, fg_color=CANVAS_BG, corner_radius=12)
        image_wrap.pack(padx=18)
        image_label = ctk.CTkLabel(image_wrap, text="")
        image_label.pack(padx=8, pady=8)

        # 2026-09-07 피드백("튜토리얼 2번째 글 정렬"): 위 이미지/번호 행/이전-
        # 다음 버튼은 전부 가운데 정렬로 보이는데, 이 캡션만 anchor="w"(왼쪽
        # 정렬)이라 특히 문장이 짧은 슬라이드에서 오른쪽에 빈 공간이 남아
        # 눈에 띄게 안 맞아 보였음 -- 가운데 정렬로 통일.
        caption_var = tk.StringVar()
        ctk.CTkLabel(
            wrap, textvariable=caption_var, font=self.font_body, text_color=TEXT_PRIMARY,
            anchor="center", justify="center", wraplength=380,
        ).pack(fill="x", padx=18, pady=(10, 4))

        # 2026-09-07 피드백("슬라이드 단순하게 번호 1.2.3.순서로 변경해 헷갈리니까"):
        # 점(●) 여러 개로 진행 상태를 표시하던 방식이 몇 번째 단계인지 한눈에
        # 안 들어와서 헷갈린다는 지적 -- 점 대신 실제 번호(1 2 3 4 5 6)를 나란히
        # 보여주고, 지금 보고 있는 번호만 진한 코발트색으로 강조한다.
        dots_row = ctk.CTkFrame(wrap, fg_color="transparent")
        dots_row.pack(pady=(2, 0))
        dot_labels = [
            ctk.CTkLabel(dots_row, text=str(i + 1), font=self.font_caption, text_color=TEXT_SECONDARY)
            for i in range(len(TUTORIAL_SLIDES))
        ]
        for lbl in dot_labels:
            lbl.pack(side="left", padx=6)

        # 슬라이드 이미지는 assets/tutorial/step*.png(assets/make_tutorial_slides.py로
        # 생성)에서 불러옴 -- 파일이 없거나(예: 아직 빌드 전 개발 환경) 로드에
        # 실패해도 캡션/이전-다음 네비게이션은 그대로 동작하도록 방어적으로 처리.
        # CTkImage 참조는 리스트에 유지해서 GC로 이미지가 사라지지 않게 함.
        assets_dir = os.path.join(_resource_dir(), "assets", "tutorial")
        slide_images = []
        for fname, _caption in TUTORIAL_SLIDES:
            img = None
            try:
                if Image is not None:
                    path = os.path.join(assets_dir, fname)
                    if os.path.isfile(path):
                        pil_img = Image.open(path)
                        # 2026-08-31 피드백("첫 창에 뜨는 튜토리얼 너무 커"):
                        # 원본 PNG(560x260) 그대로 띄우면 대화상자가 너무
                        # 커 보였음 -- CTkImage의 size는 원본 해상도와 별개로
                        # 화면 표시 크기만 지정하므로, 훨씬 작게 표시.
                        img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(320, 149))
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                img = None
            slide_images.append(img)

        state = {"idx": 0}

        def _render():
            idx = state["idx"]
            _fname, caption = TUTORIAL_SLIDES[idx]
            img = slide_images[idx]
            if img is not None:
                image_label.configure(image=img, text="")
            else:
                image_label.configure(image=None, text="(이미지 없음)")
            # 2026-09-07 피드백("1/6 이렇게 하지 말고 그냥 숫자 1,2,3,4,5,6
            # 번호 대로 바꿔"): "1/6" 같은 분수 표기 대신, 있는 그대로의
            # 번호 하나만 붙인다(아래 dot_labels의 1 2 3 4 5 6 표시와 통일).
            caption_var.set(f"{idx + 1}. {caption}")
            for i, lbl in enumerate(dot_labels):
                lbl.configure(text_color=ACCENT if i == idx else TEXT_SECONDARY)
            prev_btn.configure(state="disabled" if idx == 0 else "normal")
            is_last = idx == len(TUTORIAL_SLIDES) - 1
            next_btn.configure(state="disabled" if is_last else "normal")
            confirm_btn.configure(text="시작하기" if is_last else "건너뛰기")
            # 2026-09-08(4차) 피드백("튜토리얼 창 문제 개선 안 됐어, 맨
            # 첫번째 창 규격으로 고정해, 왜 갑자기 이상해진거야?"): 슬라이드
            # 마다 창 크기를 다시 재고 geometry()를 다시 적용하는 방식
            # 자체를(토글 방식 -> 멈춤 버그, 단순 재계산 -> 빈 공간/크기
            # 요동 문제) 계속 조금씩 고쳐왔지만, 매번 다시 문제가 생겼다.
            # 이제는 그 근본 원인(창 크기를 슬라이드마다 다시 계산/적용하는
            # 것 자체)을 없앤다 -- 창 크기는 아래 _open_tutorial_dialog
            # 끝부분에서(모든 슬라이드의 캡션 중 가장 긴 것 기준으로) 딱
            # 한 번만 정하고, 그 뒤로 슬라이드가 바뀌어도 크기나 위치를
            # 다시 건드리지 않는다. 그래서 이 함수는 더 이상 크기 계산을
            # 하지 않는다.

        def _go(delta):
            state["idx"] = max(0, min(len(TUTORIAL_SLIDES) - 1, state["idx"] + delta))
            _render()

        nav_row = ctk.CTkFrame(wrap, fg_color="transparent")
        nav_row.pack(fill="x", padx=18, pady=(10, 0))
        prev_btn = self._btn_secondary(nav_row, "◀ 이전", lambda: _go(-1))
        prev_btn.pack(side="left")
        next_btn = self._btn_primary(nav_row, "다음 ▶", lambda: _go(1))
        next_btn.pack(side="right")

        # 2026-09-10 피드백("튜토리얼 창의 여백이 많아서 지저분해 보여, 하단의
        # 여백은 줄이고"): 이 창의 높이는 슬라이드마다 다시 계산하지 않고
        # 모든 슬라이드 중 캡션이 가장 긴 것 기준으로 딱 한 번만 정해진다
        # (2026-09-08(4차) 피드백으로 이미 고정된 방식 -- 슬라이드마다 다시
        # 계산하면 멈춤 버그가 있었어서 절대 되돌리면 안 됨, 위 주석 참고).
        # 문제는 이 footer(체크박스+버튼 줄)가 side="bottom"으로 창의 실제
        # 맨 아래에 딱 붙어 있어서, 캡션이 짧은 슬라이드를 볼 때는 바로 위
        # nav_row(이전/다음)와 이 footer 사이에 큰 빈 공간이 남아 마치
        # 레이아웃이 깨진 것처럼 보였다. side="bottom"을 빼고 nav_row
        # 바로 다음 순서로 자연스럽게 이어 붙이면, 남는 여백은 이제 창의
        # 맨 아래(버튼 아래쪽 여백)로만 모여서 평범한 여유 공간처럼 보인다
        # (창 높이 계산 로직 자체는 전혀 안 건드림 -- 멈춤 버그 재발 위험 없음).
        footer = ctk.CTkFrame(wrap, fg_color="transparent")
        footer.pack(fill="x", padx=18, pady=(12, 16))

        def _on_close():
            if self.tutorial_dont_show_again.get():
                self._save_tutorial_seen(True)
            dialog.destroy()

        ctk.CTkCheckBox(
            footer, text="다시 보지 않기", variable=self.tutorial_dont_show_again,
            font=self.font_body, text_color=TEXT_PRIMARY, fg_color=ACCENT, hover_color=ACCENT_HOVER,
        ).pack(side="left")
        confirm_btn = self._btn_primary(footer, "건너뛰기", _on_close)
        confirm_btn.pack(side="right")

        # 2026-09-08(4차) 피드백("맨 첫번째 창 규격으로 고정해"): 창 크기를
        # 딱 한 번만 정한다 -- 모든 슬라이드의 캡션을 하나씩 잠깐 채워보며
        # 가장 큰 요구 높이를 재고(어떤 슬라이드로 가든 절대 잘리지 않게),
        # 그 값으로 한 번만 geometry를 맞춘 뒤 idx=0 내용으로 되돌린다.
        # 이후 슬라이드가 바뀌어도(_render) 이 크기/위치는 절대 다시
        # 계산하거나 바꾸지 않는다 -- 그래서 창이 커졌다 작아졌다 하거나
        # 화면에서 움직이는 일 자체가 이제 구조적으로 없다.
        # 2026-09-08(5차) 피드백("3번째 슬라이드 겹침 문제"): 이 높이 측정
        # 루프가 캡션 텍스트만 슬라이드별로 바꿔가며 재보고, 이미지는
        # 계속 비워둔 채로("(이미지 없음)" 상태, 실제 149px짜리 이미지가
        # 없는 훨씬 낮은 높이) 측정하고 있었던 게 진짜 원인이었다 -- 그래서
        # max_req_h가 "이미지 있음" 상태의 실제 필요 높이보다 계속
        # 작게 계산됐고, 그 부족분만큼 맨 아래 '다시 보지 않기'/버튼 줄이
        # 창 밖으로 밀리며 겹쳐 보였다(3번 슬라이드는 캡션이 3줄까지
        # 늘어나 부족분이 가장 커서 가장 눈에 띄었을 뿐, 사실 모든
        # 슬라이드가 같은 문제였음). 이제 캡션과 함께 그 슬라이드의 실제
        # 이미지도 넣은 채로 측정해서, _render()가 나중에 채워 넣을 실제
        # 모습과 정확히 같은 상태에서 필요한 높이를 잰다.
        # 2026-09-10(36차 이어서) 피드백("튜토리얼 하단의 공백 모두 삭제"):
        # 여기 시작값이 400으로 고정돼 있어서, 캡션을 아무리 짧게 줄여도
        # (바로 위 TUTORIAL_SLIDES 수정 참고) 실제 필요한 높이가 400보다
        # 작으면 이 max() 때문에 창이 400 밑으로는 절대 안 줄어들어, 그
        # 차이만큼이 버튼 아래 빈 공간으로 항상 남아있었다 -- 이게 지난번
        # footer 순서 수정으로도 완전히 없어지지 않았던 진짜 원인. 250은
        # 실제 디자인 의도가 있는 값이 아니라, 혹시 있을 극단적인 경우
        # (이미지 로드 실패 등으로 reqheight 계산이 비정상적으로 작게
        # 나오는 경우)에 대비한 최소 안전값일 뿐 -- 평소에는 아래 루프가
        # 실제로 잰 값이 그대로 쓰인다.
        max_req_h = 250
        for _i, (_fname, _caption_text) in enumerate(TUTORIAL_SLIDES):
            caption_var.set(f"{_i + 1}. {_caption_text}")
            _img = slide_images[_i]
            if _img is not None:
                image_label.configure(image=_img, text="")
            else:
                image_label.configure(image=None, text="(이미지 없음)")
            dialog.update_idletasks()
            max_req_h = max(max_req_h, wrap.winfo_reqheight() + 28)
        try:
            sh = self.winfo_screenheight()
            max_req_h = min(max_req_h, max(300, sh - 80))
        except Exception:  # noqa: BLE001
            pass
        dialog.geometry(f"440x{max_req_h}")
        dialog.update_idletasks()

        _render()

        dialog.protocol("WM_DELETE_WINDOW", _on_close)
        try:
            dialog.lift()
            dialog.focus_force()
        except Exception:  # noqa: BLE001
            pass
        try:
            dialog.grab_set()
        except Exception:  # noqa: BLE001
            pass

        self.update_idletasks()
        try:
            px, py = self.winfo_rootx(), self.winfo_rooty()
            pw = self.winfo_width()
            dw = dialog.winfo_width()
            dialog.geometry(f"440x{max_req_h}+{px + max(0, (pw - dw) // 2)}+{py + 40}")
        except Exception:  # noqa: BLE001 -- 위치 계산 실패해도 대화상자 자체는 뜸(장식일 뿐)
            pass

    # ------------------------------------------------------------------
    def _build_layout(self):
        # --- 상단 헤더 바 (브랜드 + 짧은 설명) ---------------------------------
        # 2026-08-27: 창 크기 20% 확대에 맞춰 헤더 높이도 비례 확대(68 -> 82).
        header_bar = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=0, height=82)
        header_bar.pack(side="top", fill="x")
        header_bar.pack_propagate(False)
        title_wrap = ctk.CTkFrame(header_bar, fg_color="transparent")
        title_wrap.pack(side="left", padx=24, pady=10)
        # 2026-09-08 피드백("컷라인 스튜디오 밑에 설명글 지워"): 제목
        # 바로 아래에 있던 부제(설명) 글줄을 없앰 -- 제목만 남긴다.
        ctk.CTkLabel(
            title_wrap, text=APP_TITLE, font=self.font_title, text_color=TEXT_PRIMARY,
        ).pack(anchor="w")
        # 2026-09-07 피드백("화면과 파일 초기화 리셋 버튼을 오른쪽 상단에
        # 만들어"): 지금까지 고른 파일/선택 영역/누적된 칼선/작업 종류 등
        # 화면에 남아있는 모든 상태를 프로그램을 막 실행했을 때와 같은 빈
        # 상태로 되돌리는 버튼. 실제 파일을 디스크에서 지우는 게 아니라
        # 화면과 내부 상태만 초기화 -- 오른쪽 위, 헤더 바 안에 배치.
        self._btn_secondary(header_bar, "화면 초기화", self._on_reset_all).pack(
            side="right", padx=24, pady=10,
        )
        ctk.CTkFrame(header_bar, fg_color=BORDER, height=1, corner_radius=0).pack(
            side="bottom", fill="x"
        )

        # 2026-09-08 피드백("자동 인식 처리중을 상단으로 고정해 안 보이니까
        # 작업하는지 몰라"): 기존 상태 메시지(self.status)는 왼쪽 스크롤
        # 패널의 맨 아래, 모든 섹션 다음에만 있어서 섹션이 많으면 스크롤을
        # 끝까지 내려야 보이는 위치였다 -- 자동 인식처럼 몇 초 이상 걸리는
        # 작업 중에는 그 위치가 화면 밖으로 스크롤돼 있어서 지금 작업 중인지
        # 전혀 알 수 없었다. 헤더 바로 아래, 스크롤 위치와 무관하게 항상
        # 화면에 보이는 위치에 같은 self.status를 그대로 미러링하는 얇은
        # 배너를 추가한다(기존 하단 상태 카드는 자세한 안내를 위해 그대로
        # 둠 -- 둘 다 같은 self.status를 보여주므로 항상 서로 일치한다).
        top_status_bar = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=0, height=34)
        top_status_bar.pack(side="top", fill="x")
        top_status_bar.pack_propagate(False)
        ctk.CTkLabel(
            top_status_bar, textvariable=self.status, font=self.font_caption,
            text_color=TEXT_SECONDARY, anchor="w", justify="left",
        ).pack(fill="x", padx=24, pady=6)
        ctk.CTkFrame(top_status_bar, fg_color=BORDER, height=1, corner_radius=0).pack(
            side="bottom", fill="x"
        )

        # --- 본문: 왼쪽 스크롤 조작 패널 + 오른쪽 미리보기 카드 -------------------
        body = ctk.CTkFrame(self, fg_color=BG_APP, corner_radius=0)
        body.pack(side="top", fill="both", expand=True)

        # 조작 패널을 CTkScrollableFrame으로 감싼 게 이번 개편의 핵심 -- 섹션이
        # 몇 개가 되든, 창 높이가 얼마든, 스크롤바가 항상 생기므로 버튼이
        # 화면 밖으로 잘려서 눌리지 않는 문제 자체가 구조적으로 사라진다.
        # 2026-08-27: 창 크기 20% 확대에 맞춰 좌측 패널 폭도 비례 확대(380 -> 456).
        left_col = ctk.CTkScrollableFrame(
            body, width=380, fg_color=BG_APP, corner_radius=0,
            scrollbar_button_color=BORDER, scrollbar_button_hover_color=TEXT_SECONDARY,
        )
        left_col.pack(side="left", fill="y", padx=(12, 6), pady=12)
        # 2026-09-08(10차) 피드백("프로그램 시작하고 도안 찾는 버튼이
        # 사라졌다가 수 초 후에 나타나") 대응을 위해 self에 보관 -- 이
        # 왼쪽 패널 전체를 만드는 동안 창은 아직 self.withdraw()로 숨겨져
        # 있는데(검은 화면 방지를 위해 일부러 그렇게 함, 위 __init__ 참고),
        # CTkScrollableFrame은 내부 스크롤 영역(scrollregion)을 이 프레임
        # 자신의 <Configure> 이벤트에서만 다시 계산한다(customtkinter
        # ctk_scrollable_frame.py의 self.bind("<Configure>", ...) 참고).
        # 창이 화면에 아직 안 뜬 상태로 위젯을 다 채워 넣으면 그 Configure
        # 이벤트들이 실제 창 크기 기준으로 제대로 발생하지 않을 수 있어서,
        # 첫 섹션(도안 파일 선택 -- "찾아보기" 버튼)이 스크롤 영역 계산이
        # 안 맞은 채로 잠깐 화면에 그려지지 않다가, 나중에(창 크기가 실제로
        # 정리되는 시점에) 다시 계산되면서 뒤늦게 나타날 수 있다 -- 증상과
        # 정확히 일치. self._proceed_past_license_gate()에서 deiconify() 직후
        # 스크롤 영역을 한 번 더 강제로 재계산해서, 창이 실제로 화면에 뜬
        # 뒤의 진짜 크기 기준으로 다시 맞춘다.
        self._left_col = left_col

        right_col = ctk.CTkFrame(
            body, fg_color=BG_CARD, corner_radius=16, border_width=1, border_color=BORDER,
        )
        right_col.pack(side="left", fill="both", expand=True, padx=(6, 12), pady=12)

        preview_header_row = ctk.CTkFrame(right_col, fg_color="transparent")
        preview_header_row.pack(fill="x", padx=20, pady=(18, 8))
        ctk.CTkLabel(
            preview_header_row, text="미리보기", font=self.font_section, text_color=TEXT_PRIMARY, anchor="w",
        ).pack(side="left")
        # 2026-09-07(7차) 피드백("키스컷 이미지 축소 되어 전체 화면이 보이지
        # 않음. 칼선이 잘 됐는지 확대해서 볼 수 있는 기능 필요"): 미리보기
        # 섹션 제목 오른쪽에 확대/축소/원본크기 버튼 + 현재 배율(%) 표시.
        zoom_row = ctk.CTkFrame(preview_header_row, fg_color="transparent")
        zoom_row.pack(side="right")
        self.zoom_pct_label = ctk.CTkLabel(
            zoom_row, text="100%", font=self.font_caption, text_color=TEXT_SECONDARY, width=44,
        )
        ctk.CTkButton(
            zoom_row, text="확대 +", command=self._on_zoom_in, font=self.font_caption,
            fg_color="transparent", hover_color=ACCENT_SOFT, text_color=TEXT_PRIMARY,
            border_width=1, border_color=BORDER, corner_radius=8, width=56, height=28,
        ).pack(side="left", padx=(0, 6))
        self.zoom_pct_label.pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            zoom_row, text="축소 −", command=self._on_zoom_out, font=self.font_caption,
            fg_color="transparent", hover_color=ACCENT_SOFT, text_color=TEXT_PRIMARY,
            border_width=1, border_color=BORDER, corner_radius=8, width=56, height=28,
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            zoom_row, text="원본크기", command=self._on_zoom_reset, font=self.font_caption,
            fg_color="transparent", hover_color=ACCENT_SOFT, text_color=TEXT_PRIMARY,
            border_width=1, border_color=BORDER, corner_radius=8, width=64, height=28,
        ).pack(side="left")
        # 2026-09-08 피드백("확대 축소 기능에 손바닥 모양의 이동할 수 있는
        # 기능 추가"): 확대된 상태에서 스크롤바만으로 원하는 부분을 찾아
        # 이동하기 불편하다는 지적 -- 이 버튼을 누르면 "이동 모드"가 켜져서
        # 마우스 커서가 손바닥 모양(hand2)으로 바뀌고, 캔버스 위에서 왼쪽
        # 버튼을 누른 채 끌면 화면이 그 방향으로 손으로 끄는 것처럼
        # 이동한다(평소엔 왼쪽 버튼 드래그 = 영역 선택이므로, 이 버튼으로
        # 두 동작을 명확히 구분). 다시 누르면 꺼져서 원래의 선택 모드로
        # 돌아간다 -- _on_toggle_pan_mode 참고.
        self.pan_mode_btn = ctk.CTkButton(
            zoom_row, text="🖐 이동", command=self._on_toggle_pan_mode, font=self.font_caption,
            fg_color="transparent", hover_color=ACCENT_SOFT, text_color=TEXT_PRIMARY,
            border_width=1, border_color=BORDER, corner_radius=8, width=60, height=28,
        )
        self.pan_mode_btn.pack(side="left", padx=(6, 0))

        canvas_wrap = ctk.CTkFrame(right_col, fg_color=CANVAS_BG, corner_radius=12)
        canvas_wrap.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        # 확대(zoom > 100%) 시 이미지가 보이는 영역보다 커질 수 있으므로,
        # 가로/세로 스크롤바를 함께 넣어 어느 부분이든 이동해서 볼 수 있게
        # 한다(2026-09-07 7차 피드백). grid로 배치해야 캔버스 오른쪽/아래에
        # 스크롤바가 딱 붙는다.
        # grid_propagate(False): 확대된 이미지가 요청하는 캔버스 크기가
        # 실제 화면(canvas_wrap이 부모로부터 받은 칸)보다 커져도, 그 요청
        # 크기에 맞춰 canvas_wrap 자체(→ 나아가 창 전체)가 따라서 커지는
        # 것을 막는다 -- 없으면 고배율 확대 시 창이 화면 밖으로 계속
        # 커지려 들거나 레이아웃이 깨질 수 있음. 캔버스는 sticky="nsew" +
        # 아래 weight=1로 이 "고정된" 칸을 그대로 채우고, 칸보다 큰 부분은
        # scrollregion + 스크롤바로 이동해서 본다.
        canvas_wrap.grid_propagate(False)
        canvas_wrap.grid_rowconfigure(0, weight=1)
        canvas_wrap.grid_columnconfigure(0, weight=1)
        self.preview_canvas = tk.Canvas(canvas_wrap, background=CANVAS_BG, highlightthickness=0)
        self.preview_canvas.grid(row=0, column=0, sticky="nsew", padx=(2, 0), pady=(2, 0))
        self._preview_vbar = tk.Scrollbar(canvas_wrap, orient="vertical", command=self.preview_canvas.yview)
        self._preview_vbar.grid(row=0, column=1, sticky="ns", pady=(2, 0))
        self._preview_hbar = tk.Scrollbar(canvas_wrap, orient="horizontal", command=self.preview_canvas.xview)
        self._preview_hbar.grid(row=1, column=0, sticky="ew", padx=(2, 0))
        self.preview_canvas.configure(
            yscrollcommand=self._preview_vbar.set, xscrollcommand=self._preview_hbar.set,
        )
        self._canvas_placeholder = self.preview_canvas.create_text(
            10, 10, anchor="nw", text="미리보기가 여기에 표시됩니다", fill=TEXT_SECONDARY,
            font=self.font_canvas,
        )
        self.preview_canvas.bind("<ButtonPress-1>", self._on_canvas_press)
        self.preview_canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self.preview_canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        # 2026-09-26 피드백("칸 인식... 가져다 대면 사각형으로 예비 모양이
        # 뜨는 버전으로"): 누르지 않고 그냥 움직이기만 해도(Motion) 그 자리의
        # 예비 모양을 보여준다 -- 단일 선택 화면과 무테/유테+도무송(①②③)
        # 화면 둘 다에서 동작(핸들러 안에서 모드별로 분기).
        self.preview_canvas.bind("<Motion>", self._on_canvas_hover)
        self.preview_canvas.bind("<Leave>", self._on_canvas_hover_leave)

        # ---- 0. 도안 파일 (기본 흐름: 파일 하나 고르면 바로 칼선 작업 시작) --------
        # 2026-08-27 피드백: "가이드를 작업 파일로 변경 ... 보통 가이드가
        # 있는 파일로 업로드하니 이중으로 작업할 필요가 없어", "해봤는데
        # 추출 버튼이 없고" -- 예전엔 이 섹션이 "가이드 + 여러 도안 -> 시트
        # 합성" 다음(두 번째)에 있어서, 가이드가 이미 포함된 파일 하나로
        # 작업하고 싶은(가장 흔한) 경우에도 먼저 그 "가이드" 섹션부터
        # 채워야 하는 것처럼 보여 혼란스러웠음. 실제로 칼선을 뽑는 버튼
        # ("이 영역 추가 + 미리보기" / "SVG로 내보내기")은 이 섹션 흐름을
        # 타고 아래로 이어지는데, 가이드 섹션에 갇히면 거기까지 도달하지
        # 못함. 순서를 바꿔 이 섹션을 맨 위(기본 흐름)로 올리고, 아래
        # "가이드 + 여러 도안" 섹션은 여러 개의 서로 다른 도안을 한 시트에
        # 배치할 때만 쓰는 고급/선택 기능임을 분명히 함.
        sec1 = self._section(left_col, "도안 파일", "주 형식: AI(.ai) · PNG/JPG도 가능")
        file_row = ctk.CTkFrame(sec1, fg_color="transparent")
        file_row.pack(fill="x", pady=(8, 4))
        ctk.CTkEntry(
            file_row, textvariable=self.input_path, font=self.font_body, corner_radius=12,
            border_width=1, border_color=BORDER, fg_color="#FFFFFF", height=34,
        ).pack(side="left", fill="x", expand=True)
        self._btn_secondary(file_row, "찾아보기", self._choose_file).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(
            sec1,
            text="AI는 원본 그대로 추출 · PNG/JPG도 가능",
            font=self.font_caption, text_color=TEXT_SECONDARY, anchor="w", justify="left",
            wraplength=330,
        ).pack(fill="x")

        # ---- 2. 작업 해상도 ------------------------------------------------------
        # 2026-08-31 피드백("도안의 개념이 뭐야? 가이드에 도안이 들어있는데
        # 왜 또 필요해? 도안 기능 삭제"): 여기 있던 "가이드 + 여러 도안 →
        # 시트 합성(고급)" 섹션은 위 '도안 파일' 하나와는 별개로, 서로 다른
        # 여러 개의 도안 파일을 하나의 가이드 시트 슬롯에 자동 배치하는
        # 고급 기능이었음. 실제로 쓰는 파일은 항상 가이드+도안이 이미 함께
        # 들어있는 파일 하나뿐이라 이 섹션 자체가 헷갈리기만 한다는 피드백에
        # 따라 통째로 삭제(관련 메서드/변수도 함께 제거 -- 아래 _choose_file
        # 참고).
        sec2 = self._section(left_col, "작업 해상도")
        self._make_number_field(sec2, "DPI", self.dpi, unit="", from_=72, to=1200, increment=1)
        precision_row = ctk.CTkFrame(sec2, fg_color="transparent")
        precision_row.pack(fill="x", pady=(6, 0))
        ctk.CTkLabel(
            precision_row, text="정밀도(업샘플)", font=self.font_body, text_color=TEXT_PRIMARY,
        ).pack(side="left")
        precision_display = tk.StringVar(value=str(self.precision.get()))

        def _on_precision_selected(choice):
            self.precision.set(int(choice))

        ctk.CTkComboBox(
            precision_row, values=["1", "2", "3", "4"], variable=precision_display,
            command=_on_precision_selected, width=70, height=30, corner_radius=12,
            border_width=1, border_color=BORDER, button_color=ACCENT,
            button_hover_color=ACCENT_HOVER, dropdown_hover_color=ACCENT_SOFT,
            font=self.font_body, state="readonly",
        ).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(
            precision_row, text="x", font=self.font_caption, text_color=TEXT_SECONDARY,
        ).pack(side="left", padx=(6, 0))
        # 2026-08-27 피드백("작업해상도 등 문장 잘림 문장이 한줄씩 이어지게"):
        # 이 설명 문구가 라벨/콤보박스 옆에 한 줄로 붙어있다 보니, 폰트 크기가
        # 커지면서(+5pt) 패널 폭을 넘어가 오른쪽이 잘려 보이는 원인이었음.
        # 같은 줄에 우겨넣지 않고 아래 줄로 내려서 자연스럽게 줄바꿈되게 함.
        ctk.CTkLabel(
            sec2, text="클수록 정밀하지만 느려집니다 (기본 4)",
            font=self.font_caption, text_color=TEXT_SECONDARY, anchor="w", justify="left",
            wraplength=330,
        ).pack(fill="x", pady=(3, 0))
        ctk.CTkLabel(
            sec2,
            text="너무 크면 속도 보호로 자동 조정될 수 있음",
            font=self.font_caption, text_color=TEXT_SECONDARY, anchor="w", justify="left",
            wraplength=330,
        ).pack(fill="x", pady=(4, 0))

        # ---- 0. 도무송 여부 -------------------------------------------------------
        # 2026-09-26 피드백("사용순서 이전으로 고치고, 대신 0번으로 도무송
        # 먼저 선택하기로 바꿔"): 아래 "작업 종류" 섹션은 이번 세션 초반의
        # 4단계 분리(1.옵션 선택/2.도무송 유/무 확인/3.도무송 칼선 개별
        # 설정/4.나머지 칼선 생성)를 완전히 되돌려 원래 모습(단일 섹션)으로
        # 복원하고, 그 대신 이 화면 맨 위에 아주 단순한 "0번" 확인 하나만
        # 새로 추가함: 이 시트에 도무송이 있는지 없는지만 먼저 고르면, 그에
        # 맞는 기본 작업 종류(있음 -> 무테/유테+도무송, 없음 -> 무테)가 바로
        # 아래 "작업 종류"에 자동으로 선택된다. 시작점만 잡아주는 것이라,
        # 고른 뒤에도 "작업 종류"에서 언제든 다른 값으로 바꿀 수 있다.
        sec0 = self._section(
            left_col, "0. 도무송 여부", "이 시트에 도무송이 있나요? 먼저 골라주세요"
        )
        domusong_yn_row = ctk.CTkFrame(sec0, fg_color="transparent")
        domusong_yn_row.pack(fill="x", pady=(4, 0))
        # 2026-09-29(멍푸 PC에서 실제 사용 중 발견): "예"가 늘 파란 강조색이라
        # "아니오"를 눌러도 화면상 아무것도 안 바뀌어, 무엇을 골랐는지 알 수
        # 없었다. 이제 현재 작업 종류에 맞춰 고른 쪽만 강조한다. 좁은 화면에서
        # 글자가 잘리던("니오 (도무송 없") 것도 짧은 문구로 줄임.
        self._yn_yes_btn = self._btn_secondary(
            domusong_yn_row, "예, 있음", lambda: self.job_type.set("MIXED_AUTO")
        )
        self._yn_yes_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._yn_no_btn = self._btn_secondary(
            domusong_yn_row, "아니오, 없음", lambda: self.job_type.set("BORDERLESS")
        )
        self._yn_no_btn.pack(side="left", fill="x", expand=True)
        self.job_type.trace_add("write", lambda *_: self._refresh_domusong_yn_buttons())
        self._refresh_domusong_yn_buttons()
        # 2026-09-26(같은 날 재요청, 첨부 스크린샷에서 이 설명 문구를 직접
        # 동그라미 치고 "도무송 선택 후, 도안에 맞는 칼선 무테/유테/
        # 아웃라인으로 선택 작업합니다로 수정" 지시): 문구를 그대로 교체.
        ctk.CTkLabel(
            sec0,
            text="도무송 선택 후, 도안에 맞는 칼선 무테/유테/아웃라인으로 선택 작업합니다",
            font=self.font_caption, text_color=TEXT_SECONDARY, anchor="w", justify="left",
            wraplength=330,
        ).pack(fill="x", pady=(6, 0))

        # ---- 작업 종류 (칼선 기준) --------------------------------------------------
        # 2026-08-27 피드백: "도무송은 무조건 선택이 아니라 ... 아예 카테고리
        # 형태로 선택하고 작은창 안에 여러가지 선택지가 있게 수정해" -- 지금
        # 고른 값만 보여주는 버튼 하나 + 팝업으로 고르게 함.
        sec3 = self._section(left_col, "작업 종류", "칼선을 만들 기준을 고르세요.")
        self.job_type_label_var = tk.StringVar(
            value=JOB_TYPE_LABELS.get(self.job_type.get()) or JOB_TYPE_PLACEHOLDER
        )
        self._make_category_picker(
            sec3, self.job_type_label_var,
            [
                ("BORDERLESS", "무테"),
                ("LINE_ART", "유테"),
                ("AUTO_STYLE", "무테+유테 자동 생성"),
                ("MIXED_AUTO", "무테/유테+도무송"),
                ("DOMUSONG", "도무송"),
                ("FULL_CUT", "조각"),
                ("MASKING_TAPE", "키스컷"),
            ],
            self.job_type, "작업 종류 선택",
            on_change=lambda: self.job_type_label_var.set(
                JOB_TYPE_LABELS.get(self.job_type.get()) or JOB_TYPE_PLACEHOLDER
            ),
            allow_clear=True,
        ).pack(fill="x", pady=(6, 0))
        # 드래그 영역 지우기/원본 다시 보기는 무테/유테/도무송/조각 스티커 어떤
        # 작업 종류에서든 쓰이는 공통 동작이라 여기(항상 눌리는 위치)에 둠.
        sel_btn_row = ctk.CTkFrame(sec3, fg_color="transparent")
        sel_btn_row_pack = {"fill": "x", "pady": (8, 0)}
        sel_btn_row.pack(**sel_btn_row_pack)
        self._btn_secondary(sel_btn_row, "선택 영역 지우기", self._clear_selection).pack(
            side="left", fill="x", expand=True, padx=(0, 6)
        )
        self._btn_secondary(sel_btn_row, "원본 다시 보기", self._show_source_again).pack(
            side="left", fill="x", expand=True
        )
        # 2026-09-07 피드백: "스티커는 도안을 자동 인식해서 칼선을 생성해야지.
        # 하나씩 선택하는 건 비효율적이야" -- 한 시트 안에 서로 떨어진 도안이
        # 여러 개 있을 때, 하나씩 드래그하지 않고 한 번에 전부 찾아서 순서대로
        # 추가해주는 버튼. 조각 스티커(FULL_CUT)는 이미지 전체를 그대로 쓰므로
        # "여러 개로 나눈다"는 개념 자체가 없어 대상에서 제외(무테/유테/도무송만).
        auto_detect_all_btn = self._btn_primary(
            sec3, "자동으로 여러 개 인식 + 추가", self._auto_detect_and_add_all
        )
        auto_detect_all_btn_pack = {"fill": "x", "pady": (8, 0)}
        auto_detect_all_btn.pack(**auto_detect_all_btn_pack)
        # 2026-09-10(36차 이어서) 피드백("도무송은 먼저 선택해서 생성하고,
        # 그다음에 다음 종류 선택하는 방향으로 가고"): ②/③ 두 버튼으로
        # 나눔(_on_mixed_generate_domusong/_on_mixed_generate_rest). job_type이
        # "무테/유테+도무송"이 아닐 땐 흐리게 비활성화된다(_on_job_type_changed).
        self.mixed_detect_btn = self._btn_primary(
            sec3, "① 도안 자동 인식 (표시하기)", self._on_mixed_detect
        )
        self.mixed_detect_btn.pack(fill="x", pady=(8, 0))
        self.mixed_generate_btn = self._btn_primary(
            sec3, "② 표시한 도무송 칼선 생성", self._on_mixed_generate_domusong
        )
        self.mixed_generate_btn.pack(fill="x", pady=(6, 0))
        self.mixed_generate_rest_btn = self._btn_primary(
            sec3, "③ 남은 칼선 생성", self._on_mixed_generate_rest
        )
        self.mixed_generate_rest_btn.pack(fill="x", pady=(6, 0))
        # 2026-09-08(9차) 피드백("칼선 종류에 키스컷 마스킹 테이프도
        # 추가해", "연속된 롤(시트) 전체를 고려해야 함"): 마스킹테이프 롤은
        # 개별 모티프 키스컷(위 "자동으로 여러 개 인식 + 추가"로 이미 다
        # 됨)과는 별개로, 롤 전체 폭을 정의하는 세이프티/칼선/블리딩 테두리
        # 선이 한 번 더 필요하다 -- 이 버튼이 그 한 번을 추가한다(여러 번
        # 눌러도 매번 새로 하나씩 누적되니, 잘못 눌렀으면 "선택 영역
        # 지우기" 옆의 누적 초기화로 지우고 다시 하면 된다).
        self._roll_border_btn = self._btn_secondary(
            sec3, "롤 전체 테두리 추가 (마스킹테이프)", self._on_add_roll_border
        )
        roll_border_btn_pack = {"fill": "x", "pady": (8, 0)}
        self._roll_border_btn.pack(**roll_border_btn_pack)
        # 2026-09-26 피드백("파트로 나뉘어져 있고 불필요한 기능이 많고
        # 이해가 안돼 ... 옵션을 한칸에 볼 수 있게 한것 처럼 해줘"): "무테/
        # 유테+도무송"을 고르면 이 위 세 개(선택 영역 지우기/원본 다시
        # 보기/자동으로 여러 개 인식+추가/롤 테두리 추가)는 전혀 안 쓰는
        # 기능인데도(①②③ 전용 버튼이 따로 있음) 계속 화면에 남아있어
        # 화면이 붐볐다 -- 이 declutter 동작 자체는 이번에 되돌리는 대상이
        # 아니므로(사용순서 되돌리기와는 별개 문제) 그대로 유지한다.
        # (widget, pack_kwargs) 순서대로 -- "무테/유테+도무송"에서 숨길 때/
        # 되돌릴 때 이 순서 그대로 다시 pack해야 원래 자리(카테고리 버튼
        # 바로 아래)로 돌아온다.
        self._sec3_mixed_hide_widgets = [
            (sel_btn_row, sel_btn_row_pack),
            (auto_detect_all_btn, auto_detect_all_btn_pack),
            (self._roll_border_btn, roll_border_btn_pack),
        ]

        # ---- 도무송 세부 옵션 -------------------------------------------------------
        # 2026-09-26 피드백에 따라 "작업 종류" 바로 다음(원래 자리)으로
        # 되돌림(이번 세션 초반엔 이 섹션을 화면 맨 위로 옮겼었는데, 그것도
        # 이번에 되돌리는 대상).
        sec6 = self._section(
            left_col, "도무송 세부 옵션", "도무송 선택 시에만 적용"
        )
        self.cutline_type_label_var = tk.StringVar(
            value=CUTLINE_TYPE_LABELS.get(self.cutline_type.get()) or CUTLINE_TYPE_PLACEHOLDER
        )
        self._make_category_picker(
            sec6, self.cutline_type_label_var,
            [
                (CutlineType.FULL_CUT.name, "조각 스티커"),
                (CutlineType.RECTANGLE.name, "직사각형"),
                (CutlineType.SQUARE.name, "정사각형"),
                (CutlineType.ELLIPSE.name, "타원형"),
                (CutlineType.CIRCLE.name, "정원형"),
            ],
            self.cutline_type, "재단 도형 선택",
            on_change=lambda: self.cutline_type_label_var.set(
                CUTLINE_TYPE_LABELS.get(self.cutline_type.get()) or CUTLINE_TYPE_PLACEHOLDER
            ),
            allow_clear=True,
        ).pack(fill="x", pady=(2, 0))
        ctk.CTkCheckBox(
            sec6, text="유색/복잡한 배경에서도 자동 분리 (GrabCut)", variable=self.use_grabcut,
            font=self.font_body, text_color=TEXT_PRIMARY, fg_color=ACCENT, hover_color=ACCENT_HOVER,
        ).pack(anchor="w", pady=(6, 0))
        # 2026-09-07 피드백("도무송과 무테를 동시에 선택할 수 있게 다중선택
        # 기능을 만들고"): 도무송 도형 칼선과, 같은 영역의 무테(사각형)
        # 칼선을 한 번에 함께 추가하는 다중선택 체크박스.
        ctk.CTkCheckBox(
            sec6, text="같은 영역에 무테 칼선도 함께 추가 (도무송+무테 동시 적용)",
            variable=self.domusong_also_borderless,
            font=self.font_body, text_color=TEXT_PRIMARY, fg_color=ACCENT, hover_color=ACCENT_HOVER,
        ).pack(anchor="w", pady=(6, 0))

        # ---- 오프셋 간격 (조각 스티커/도무송) ---------------------------------------
        sec4 = self._section(
            left_col, "오프셋 간격", "조각 스티커/도무송용 · mm, 바깥쪽 기준"
        )
        self._make_number_field(
            sec4, "세이프티 (safety)", self.safety_mm, color=COLOR_SAFETY,
            from_=0.0, to=100.0, increment=0.5,
        )
        self._make_number_field(
            sec4, "칼선 (cutting line)", self.cut_mm, color=COLOR_CUT,
            from_=0.0, to=100.0, increment=0.5,
        )
        self._make_number_field(
            sec4, "블리딩 (bleeding)", self.bleed_mm, color=COLOR_BLEED,
            from_=0.0, to=100.0, increment=0.5,
        )

        # ---- 유테/무테 세부 옵션 ----------------------------------------------------
        # 2026-09-07 피드백에 따라 "작업 종류"에서 이미 무테/유테를 직접
        # 고르므로(위 sec3), 여기 있던 별도의 유테/무테 하위 선택 콤보박스는
        # 없앰 -- 간격(mm) 값 하나만 남김.
        sec5 = self._section(
            left_col, "유테/무테 간격", "무테/유테 선택 시에만 적용"
        )
        self._make_number_field(
            sec5, "유테/무테 간격", self.style_margin_mm, unit="mm (기본 1.2)",
            from_=0.1, to=20.0, increment=0.1,
        )

        # 2026-08-27 피드백: "도무송은 무조건 선택이 아니라 선택할 수 있는
        # 선택지를 줘야지" -- 예전엔 이 섹션들의 라디오버튼/체크박스가 작업
        # 종류(조각 스티커/스티커/도무송)와 상관없이 항상 활성 상태로 보여서, 관련
        # 없을 때도 뭔가를 강제로 골라야 하는 것처럼 보였음(실제로 값 자체는
        # 해당 작업 종류가 아니면 아예 쓰이지도 않는데도). 이제 작업 종류에
        # 맞지 않는 섹션은 흐리게 비활성화해서, 딱 지금 관련 있는 선택지만
        # 실제로 고를 수 있게 함.
        self._sec4_offsets = sec4
        self._sec5_sticker = sec5
        self._sec6_domusong = sec6
        self.job_type.trace_add("write", self._on_job_type_changed)
        self._on_job_type_changed()

        # ---- 7. 누적 ---------------------------------------------------------------
        sec7 = self._section(
            left_col, "누적", "여러 번 추가해도 한 파일에 쌓입니다"
        )
        # 2026-08-27 피드백("프로그램 글자색 블랙으로 통일"): 여기만 브랜드
        # 컬러(파란색)로 튀던 걸 다른 본문 텍스트와 같은 검정으로 맞춤.
        ctk.CTkLabel(
            sec7, textvariable=self.accum_status, font=self.font_body, text_color=TEXT_PRIMARY,
            anchor="w",
        ).pack(fill="x", pady=(4, 8))
        self.generate_btn = self._btn_primary(sec7, "이 영역 추가 + 미리보기", self._on_generate)
        self.generate_btn.pack(fill="x", pady=(0, 6))
        self._btn_secondary(sec7, "누적 초기화 (모두 지우기)", self._on_reset_accumulation_clicked).pack(
            fill="x", pady=(0, 8)
        )
        self.export_btn = self._btn_primary(sec7, "SVG로 내보내기", self._on_export)
        self.export_btn.configure(state="disabled")
        self.export_btn.pack(fill="x")

        # ---- 상태 메시지 카드 --------------------------------------------------------
        status_card = ctk.CTkFrame(
            left_col, fg_color=BG_CARD, corner_radius=14, border_width=1, border_color=BORDER
        )
        status_card.pack(fill="x", pady=(0, 4), padx=2)
        ctk.CTkLabel(
            status_card, textvariable=self.status, font=self.font_caption,
            text_color=TEXT_SECONDARY, anchor="w", justify="left", wraplength=310,
        ).pack(fill="x", padx=16, pady=12)

    # ------------------------------------------------------------------
    def _choose_file(self):
        # 2026-08-26 피드백: "주 입력 파일은 어도비 일러스트야" -- 세 가지
        # 선택지(.ai/.png/.jpg)로 좁히고 .ai를 맨 앞(기본 필터)에 둠. "모든
        # 파일"은 예전처럼 다른 확장자(.svg 등)도 열 수 있도록 남겨둠.
        path = filedialog.askopenfilename(
            title="도안 파일 선택 (주 형식: 어도비 일러스트 .ai)",
            filetypes=[
                ("Adobe Illustrator (.ai)", "*.ai"),
                ("PNG", "*.png"),
                ("JPG/JPEG", "*.jpg *.jpeg"),
                ("모든 파일", "*.*"),
            ],
        )
        if not path:
            return

        self._clear_selection()
        self._on_reset_accumulation()  # 새 파일이므로 이전 파일의 누적 항목은 폐기
        # 2026-09-07 피드백("격자는 모든 스티커 발주 도안에 있어") 대응:
        # 실제 재단선 격자는 파일마다 다르므로, 새 파일을 고르면 이전
        # 파일에서 읽었던 격자 정보를 반드시 버림(안 그러면 전혀 다른
        # 파일에 엉뚱한 격자가 적용될 수 있음).
        self._real_grid_cells_px = None
        # 내보내기 기본 파일명은 항상 지금 고른 "원본" 파일 이름을 따름 --
        # .ai는 아래에서 내부용 래스터 경로로 self.input_path가 바뀌므로,
        # 여기서 미리 기억해둠(_on_export에서 사용).
        self._export_basename = os.path.splitext(os.path.basename(path))[0]

        if path.lower().endswith(".ai"):
            self.is_vector.set(False)
            self.status.set(f"어도비 일러스트 파일에서 이미지를 추출하는 중: {os.path.basename(path)} ...")
            threading.Thread(target=self._run_load_ai, args=(path,), daemon=True).start()
            return

        self.is_vector.set(path.lower().endswith(".svg"))
        self.input_path.set(path)
        self.status.set(f"불러옴: {os.path.basename(path)}")
        if not self.is_vector.get() and Image is not None:
            try:
                self._load_source_preview(path)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
        else:
            self._source_image = None
            self.preview_canvas.delete("all")
            max_w, _ = self._preview_viewport_size()
            self.preview_canvas.config(width=max_w, height=240)
            self.preview_canvas.create_text(
                10, 10, anchor="nw", text="SVG는 영역 드래그 선택을 지원하지 않습니다.", fill=TEXT_SECONDARY,
                font=self.font_canvas,
            )

    def _run_load_ai(self, ai_path):
        """어도비 일러스트(.ai) 파일 안의 실제 인쇄용 이미지를 추출해서
        내부 작업용 PNG로 저장 -- core.ai_import 참고. 파일이 크면 시간이
        걸릴 수 있어 기존 시트 합성/가이드 로딩과 같은 방식으로 백그라운드
        스레드에서 처리."""
        try:
            from core.ai_import import load_ai_as_raster

            raster_path = os.path.join(_work_file_dir(), "_ai_source_raster.png")
            load_ai_as_raster(ai_path, raster_path, dpi=self.dpi.get())

            # 2026-09-07 피드백("모든 스티커는 인쇄 여백을 아끼려고 서로
            # 거의 맞닿을 만큼 촘촘하게 배치해, 격자는 모든 스티커 발주
            # 도안에 있어... 재단선이라고 해", "도안을 하나의 덩어리로
            # 보지 말고 사각형의 틀에 스티커 요소가 그려져 있다고 기본
            # 값을 지정해"): .ai 파일에 작가 본인이 이미 그려 놓은 진짜
            # 재단선 격자가 있으면(core.ai_cutline_reader.
            # load_real_grid_cells), 픽셀에서 도안 경계를 추측하는 대신
            # 그 진짜 칸을 그대로 읽어서 쓴다 -- 있으면 최선, 이 레이어가
            # 없거나 다른 형식(닫힌 실루엣 등)이면 조용히 넘어가고 예전
            # 방식(자동 인식)을 그대로 씀.
            grid_cells = None
            try:
                from core.ai_cutline_reader import load_real_grid_cells

                grid_result = load_real_grid_cells(ai_path, raster_path)
                grid_cells = grid_result.cells_px
            except Exception:  # noqa: BLE001
                grid_cells = None

            # 2026-09-26(성능 회귀 대응, 피드백 "파일 불러오니까 무거워지면서
            # 렉걸렸어"): 마우스를 캔버스 위에서 처음 움직이는 순간(_on_canvas_
            # hover -> _find_design_box_near -> _get_cached_design_boxes)에
            # detect_design_bboxes_px가 처음 한 번 실행되는데, 이 함수는 시트
            # 전체를 훑는 무거운 작업이라(core.multi_design._detect_boxes_from_
            # gray의 중복 계산 문제를 이번에 고쳤어도) 실제 10칸 이상인 큰 시트
            # 에서는 여전히 수 초~수십 초가 걸린다.
            #
            # 2026-09-26(같은 날 재발견, 피드백 "일러스트 파일이 바로 인식
            # 되지 않는게 반복돼, 평균 3초대였어"): 위 대응으로 이 계산을
            # 여기(_run_load_ai)로 옮기면서, 실수로 `_on_ai_loaded` 예약보다
            # *먼저* 실행되게 해버렸다 -- `_on_ai_loaded`가 "불러옴" 상태
            # 표시와 미리보기를 켜는 자리인데, 그 예약 자체가 이 무거운 계산이
            # 끝날 때까지 미뤄지니 파일을 열 때마다(마우스를 옮기든 안 옮기든
            # 상관없이 매번) 원래 약 2.5초면 뜨던 "불러옴" 표시가 실제 파일
            # (10칸 시트)로 재본 결과 30초 넘게 걸렸다 -- 있던 문제(첫 마우스
            # 이동 시 가끔 멈춤)를 없애려다 더 나쁜 문제(매번 항상 느림)를
            # 새로 만든 것. 그래서 `_on_ai_loaded` 예약을 먼저 하고, 이 캐시
            # 준비는 (여전히 이 백그라운드 스레드 안에서, UI를 막지 않고)
            # 그 다음에 하도록 순서만 바꾼다 -- "불러옴" 표시는 예전처럼
            # 빠르게 뜨고, 캐시는 그 뒤로도 계속 백그라운드에서 마저 준비돼
            # 첫 마우스 이동 전에 끝날 가능성이 높다(실측 재확인: 순서만
            # 바꿔도 두 동작 모두 그대로 유지됨).
            self.after(0, self._on_ai_loaded, ai_path, raster_path, grid_cells)
            try:
                self._get_cached_design_boxes(raster_path)
            except Exception:  # noqa: BLE001
                pass
        except Exception as e:  # noqa: BLE001
            self._report_exception_to_server("어도비 일러스트(.ai) 파일 로드 중")
            self.after(0, self._on_error, f"어도비 일러스트 파일을 여는 중 오류:\n{e}")

    def _on_ai_loaded(self, ai_path, raster_path, grid_cells=None):
        self.input_path.set(raster_path)
        self._real_grid_cells_px = grid_cells
        # 2026-09-09(29차) 피드백("불러온 파일에 대한 설명은 10장 이내로"):
        # 기존엔 파일 안에서 찾은 재단선 격자 설명까지 한 문장에 다 이어붙여
        # 상태 표시줄에서 길게 잘려 보였다 -- 이제 상태 표시줄은 항상 짧게만
        # 쓰고("불러옴: N칸 인식"), 격자를 못 찾았을 때만 구분되는 짧은
        # 문구를 쓴다. 어떤 버튼이 무엇을 하는지는 그 버튼 자체의 이름
        # (예: '자동으로 여러 개 인식 + 추가')으로 충분하므로 여기서 다시
        # 설명하지 않는다.
        if grid_cells:
            self.status.set(f"불러옴: {len(grid_cells)}칸 인식")
        else:
            self.status.set("불러옴: 로드 완료")
        if Image is not None:
            try:
                self._load_source_preview(raster_path)
            except Exception:  # noqa: BLE001
                traceback.print_exc()

    def _preview_viewport_size(self):
        """실제로 미리보기 캔버스가 화면에서 차지하는 크기(px)를 구함.

        2026-08-27 피드백: "미리보기 창이 이미지 하나로 가득차서 볼 수가
        없어", "이미지가 잘려보이면 칼선이 잘 들어갔는지 알 수 없어" --
        직전 수정에서 PREVIEW_MAX_SIDE를 640 -> 1400으로 크게 올려 화질은
        좋아졌지만, 캔버스를 항상 그 크기로 만들어버려서 실제 창(1200x600)
        보다 이미지가 커지는 경우 창 밖으로 잘려나가 오히려 전체를 볼 수
        없는 문제가 생겼음. 화질을 위해 해상도를 올리는 것과, 화면에
        다 들어오게 맞추는 것은 서로 다른 문제라서 --  지금 실제로 그려진
        캔버스 영역 크기를 기준으로 항상 그 안에 맞춰 넣고, PREVIEW_MAX_SIDE는
        그 위에 얹는 "더 이상은 필요없다"는 상한선으로만 쓴다."""
        self.preview_canvas.update_idletasks()
        w = self.preview_canvas.winfo_width()
        h = self.preview_canvas.winfo_height()
        # 창이 아직 화면에 자리잡기 전(첫 실행 시점 등)이면 winfo_width/height가
        # 1처럼 의미 없는 값을 줄 수 있음 -- 이땐 무난한 기본값으로 대체.
        if w < 100:
            w = 900
        if h < 100:
            h = 500
        return min(w, PREVIEW_MAX_SIDE), min(h, PREVIEW_MAX_SIDE)

    def _render_preview(self):
        """캔버스에 self._preview_full_image(원본 해상도 PIL 이미지)를
        "캔버스 안에 다 들어오는 배율(base scale)" × self._preview_zoom으로
        다시 그린다. 2026-09-07(7차) 피드백("칼선이 잘 됐는지 확대해서 볼
        수 있는 기능 필요")으로 추가된 확대/축소 기능의 실제 렌더링 로직 --
        원본 소스 이미지(_load_source_preview)와 생성된 칼선 미리보기
        (_show_preview)가 예전엔 이 리사이즈/PhotoImage/create_image 로직을
        거의 그대로 두 곳에 복사해 갖고 있었는데, 배율 곱셈을 두 군데서
        따로 관리하면 어긋나기 쉬워 하나로 합쳤다.

        확대(zoom>1.0)로 이미지가 보이는 캔버스보다 커지면 스크롤바로 이동해서
        보게 되므로, 캔버스 자체의 width/height는 항상 "화면에 실제로 보이는
        칸"이 아니라 "이미지 전체 크기"로 맞추고 scrollregion도 같이 갱신한다
        -- 그래야 스크롤이 이미지 전체 범위를 커버한다."""
        img = self._preview_full_image
        if img is None:
            return
        w, h = img.size
        max_w, max_h = self._preview_viewport_size()
        base_scale = min(max_w / w, max_h / h, 1.0)
        scale = base_scale * self._preview_zoom
        self._display_scale = scale
        disp_w = max(1, int(w * scale))
        disp_h = max(1, int(h * scale))
        # 2026-08-27 피드백("이미지와 그래픽이 다 깨져 보여 고화질로"): 리사이즈
        # 방식을 명시적으로 LANCZOS(고품질 다운/업샘플링)로 지정 -- 기본값보다
        # 특히 칼선처럼 얇은 선/디테일이 있는 이미지를 축소/확대할 때 뭉개짐이
        # 덜함.
        disp = img if (disp_w, disp_h) == (w, h) else img.resize(
            (disp_w, disp_h), Image.Resampling.LANCZOS
        )
        self._preview_photo = ImageTk.PhotoImage(disp)
        self.preview_canvas.delete("all")
        self.preview_canvas.config(width=disp_w, height=disp_h)
        self.preview_canvas.create_image(0, 0, anchor="nw", image=self._preview_photo)
        self.preview_canvas.config(scrollregion=(0, 0, disp_w, disp_h))
        # canvas.delete("all")로 기존 선택 사각형도 함께 지워졌으므로, 관련
        # 상태 변수도 전부 같이 초기화(안 그러면 이미 없어진 캔버스 아이템
        # ID가 남아있는 상태가 됨).
        self._selection_rect_id = None
        self._selection_halo_id = None
        # 2026-09-28: 같은 이유로 "의심 영역 경고" 이동 강조 사각형(주황색)도
        # 이 시점에 같이 지워졌으므로 아이디 변수를 같이 초기화한다.
        self._nav_highlight_rect_id = None
        self._nav_highlight_halo_id = None
        self.zoom_pct_label.configure(text=f"{round(self._preview_zoom * 100)}%")

    _ZOOM_STEPS = [0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0]

    def _on_zoom_in(self):
        if self._preview_full_image is None:
            return
        larger = [s for s in self._ZOOM_STEPS if s > self._preview_zoom + 1e-9]
        self._preview_zoom = larger[0] if larger else self._ZOOM_STEPS[-1]
        self._render_preview()

    def _on_zoom_out(self):
        if self._preview_full_image is None:
            return
        smaller = [s for s in self._ZOOM_STEPS if s < self._preview_zoom - 1e-9]
        self._preview_zoom = smaller[-1] if smaller else self._ZOOM_STEPS[0]
        self._render_preview()

    def _on_zoom_reset(self):
        if self._preview_full_image is None:
            return
        self._preview_zoom = 1.0
        self._render_preview()

    def _on_toggle_pan_mode(self):
        """2026-09-08 피드백("확대 축소 기능에 손바닥 모양의 이동할 수
        있는 기능 추가"): "🖐 이동" 버튼을 누르면 켜지는 이동(패닝) 모드.
        평소엔 캔버스에서 왼쪽 버튼을 누른 채 끌면 "영역 선택"이 되는데,
        이 모드가 켜져 있는 동안은 그 대신 화면 자체가 손으로 끄는 것처럼
        이동한다(tk.Canvas의 scan_mark/scan_dragto -- _on_canvas_press/
        _drag/_release의 self._pan_mode 분기 참고). 두 동작이 똑같이
        "왼쪽 버튼 드래그"라서 이 버튼으로 명확히 구분해야 헷갈리지 않는다.
        커서를 손바닥 모양(hand2)으로 바꿔서 지금 어느 모드인지 눈으로도
        바로 알 수 있게 하고, 버튼 자체도 켜져 있을 때 강조색으로 바뀐다."""
        self._pan_mode = not self._pan_mode
        if self._pan_mode:
            # 이동 모드로 들어가는 순간, 혹시 막 시작된 "누른 채 끄는 중"인
            # 선택 드래그가 있었으면 어중간한 상태로 남지 않도록 취소한다
            # (이미 확정된 선택 사각형 자체는 그대로 유지 -- 이동 모드를
            # 껐다 켜도 이전에 선택해 둔 영역이 사라지지 않는다).
            self._drag_start = None
            self._clear_hover_preview()
            self.preview_canvas.configure(cursor="hand2")
            self.pan_mode_btn.configure(
                fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#FFFFFF", border_width=0,
            )
            self.status.set(
                "이동 모드: 캔버스를 마우스 왼쪽 버튼으로 누른 채 끌면 화면이 이동합니다. "
                "'🖐 이동' 버튼을 다시 누르면 선택 모드로 돌아갑니다."
            )
        else:
            self.preview_canvas.configure(cursor="")
            self.pan_mode_btn.configure(
                fg_color="transparent", hover_color=ACCENT_SOFT, text_color=TEXT_PRIMARY,
                border_width=1, border_color=BORDER,
            )
            self.status.set("선택 모드로 돌아왔습니다 -- 캔버스에서 드래그하면 영역을 선택합니다.")

    def _navigate_preview_to_original_box(self, box_px):
        """2026-09-28(멍푸 요청 "수동 기능 추가" -- 의심 영역 경고를 자동으로
        고치는 대신, 경고 목록에서 항목을 클릭하면 미리보기 화면이 그 원본
        이미지 좌표로 이동/확대되어 사람이 직접 눈으로 쉽게 찾아가 확인할 수
        있게만 해주는 보조 기능): `box_px`는 원본 이미지 픽셀 좌표
        (x0, y0, x1, y1) -- `core.multi_design`의 `suspicious_regions`와
        정확히 같은 좌표계(core/multi_design.py의 `suspicious_regions` 문서,
        `_missing_body_warning_notes` 참고).

        자동 수정을 시도하지 않는다는 원칙(위 두 곳의 문서 참고, 잘못된
        도안 병합 회귀 위험 때문에 보류함)은 전혀 건드리지 않는다 -- 이 함수는
        오직 "찾아가기 쉽게" 화면만 이동/확대하고, 눈에 잘 띄도록 주황색
        점선 사각형으로 그 위치를 표시할 뿐, 칼선이나 선택 영역(self.
        _selection_px)은 전혀 바꾸지 않는다.

        미리보기에 아무 이미지도 없으면(아직 아무 파일도 안 불러왔거나
        화면이 초기화된 상태) 조용히 아무 것도 하지 않는다."""
        img = self._preview_full_image
        if img is None or not box_px:
            return
        w, h = img.size
        x0, y0, x1, y1 = box_px
        x0, x1 = sorted((max(0, min(w, x0)), max(0, min(w, x1))))
        y0, y1 = sorted((max(0, min(h, y0)), max(0, min(h, y1))))
        if x1 <= x0 or y1 <= y0:
            return
        bw, bh = x1 - x0, y1 - y0

        # 목표: 이 박스가 여백을 넉넉히 두고(대략 화면의 1/2.5 정도를
        # 차지하도록) 화면 중앙에 오도록 배율을 계산 -- 너무 딱 맞게
        # 확대하면 주변 맥락(배경/이웃 도안)이 안 보여 오히려 판단하기
        # 어려워지므로 일부러 여유를 둔다.
        max_w, max_h = self._preview_viewport_size()
        base_scale = min(max_w / w, max_h / h, 1.0)
        pad = 2.5
        want_scale_w = (max_w / (bw * pad)) if bw > 0 else self._ZOOM_STEPS[-1]
        want_scale_h = (max_h / (bh * pad)) if bh > 0 else self._ZOOM_STEPS[-1]
        want_scale = min(want_scale_w, want_scale_h)
        want_zoom = (want_scale / base_scale) if base_scale > 0 else 1.0
        want_zoom = max(self._ZOOM_STEPS[0], min(self._ZOOM_STEPS[-1], want_zoom))
        # 기존 확대/축소 버튼과 같은 배율 단계에 맞춰서, 이후 손으로 +/-를
        # 눌러도 배율 표시가 어색하게 튀지 않도록 한다.
        self._preview_zoom = min(self._ZOOM_STEPS, key=lambda z: abs(z - want_zoom))
        self._render_preview()

        scale = self._display_scale or 1.0
        self.preview_canvas.update_idletasks()
        vw = self.preview_canvas.winfo_width() or max_w
        vh = self.preview_canvas.winfo_height() or max_h
        disp_w, disp_h = w * scale, h * scale
        cx, cy = (x0 + x1) / 2 * scale, (y0 + y1) / 2 * scale
        if disp_w > vw:
            self.preview_canvas.xview_moveto(max(0.0, min(1.0, (cx - vw / 2) / disp_w)))
        if disp_h > vh:
            self.preview_canvas.yview_moveto(max(0.0, min(1.0, (cy - vh / 2) / disp_h)))

        bx0, by0, bx1, by1 = x0 * scale, y0 * scale, x1 * scale, y1 * scale
        if self._nav_highlight_rect_id is not None:
            self.preview_canvas.delete(self._nav_highlight_rect_id)
            self._nav_highlight_rect_id = None
        if self._nav_highlight_halo_id is not None:
            self.preview_canvas.delete(self._nav_highlight_halo_id)
            self._nav_highlight_halo_id = None
        self._nav_highlight_halo_id = self.preview_canvas.create_rectangle(
            bx0, by0, bx1, by1, outline="#FFFFFF", width=5
        )
        self._nav_highlight_rect_id = self.preview_canvas.create_rectangle(
            bx0, by0, bx1, by1, outline="#E67E22", width=3, dash=(6, 4)
        )
        self.preview_canvas.tag_raise(self._nav_highlight_rect_id, self._nav_highlight_halo_id)

    # ---- 2026-09-28(GrabCut 보조 기능 -- 트라이맵 힌트 보정 도구): 화면에서
    # 직접 "여긴 확실히 전경/배경"이라고 점을 찍어 GrabCut 실루엣을 다시
    # 계산하는 수동 보정. 자동 수정이 아니라 사람이 확인/지정한 곳만
    # 반영한다는 이 프로젝트의 기존 원칙을 그대로 따른다 -- 다만 한 칸에
    # 준 힌트는(멍푸 요청 "반복 칸에 자동 적용") 같은 반복 그룹의 나머지
    # 칸에도 자동으로 복제된다(이 복제 자체는 기존 fit_cutline_result_to_box
    # 와 완전히 같은, 이미 검증된 메커니즘).
    def _find_accumulated_index_for_box(self, box_px, min_iou=0.3):
        """`box_px`(원본 이미지 좌표, 보통 의심 영역 경고의 좌표)와 가장 많이
        겹치는 self._grabcut_hint_meta 항목의 self._accumulated 인덱스를
        찾는다. IoU가 min_iou 미만이면 대응하는 항목이 없다고 보고 None."""
        if not box_px:
            return None
        bx0, by0, bx1, by1 = box_px
        b_area = max(1.0, (bx1 - bx0) * (by1 - by0))
        best_idx, best_iou = None, 0.0
        for idx, meta in self._grabcut_hint_meta.items():
            if idx >= len(self._accumulated):
                continue
            mx0, my0, mx1, my1 = meta["box_px"]
            ix0, iy0 = max(bx0, mx0), max(by0, my0)
            ix1, iy1 = min(bx1, mx1), min(by1, my1)
            if ix1 <= ix0 or iy1 <= iy0:
                continue
            inter = (ix1 - ix0) * (iy1 - iy0)
            m_area = max(1.0, (mx1 - mx0) * (my1 - my0))
            union = b_area + m_area - inter
            iou = inter / union if union > 0 else 0.0
            if iou > best_iou:
                best_iou, best_idx = iou, idx
        return best_idx if best_iou >= min_iou else None

    def _subtract_existing_cutlines(self, design, box_px):
        """`design`(원본 좌표 실루엣)에서 이미 누적된 칼선 영역을 빼고, 새로
        남은 의미 있는 조각만 돌려준다(없으면 None). 새 도안 추가 시 같은
        요소에 칼선이 두 겹 생기는 것(이중 칼선)을 막는 용도."""
        from shapely.geometry import MultiPolygon, box as _sbox
        from shapely.ops import unary_union as _union

        if design is None or design.is_empty:
            return None
        region = _sbox(*box_px)
        existing = [
            it.design for it in self._accumulated
            if it.design is not None and not it.design.is_empty and it.design.intersects(region)
        ]
        remaining = design
        if existing:
            # 칼선 폭/경계 오차만큼 살짝 넓혀서 빼야 칼선 바로 바깥의 가는
            # 띠가 "새 조각"으로 남지 않는다.
            remaining = design.difference(_union(existing).buffer(6.0))
        parts = list(remaining.geoms) if hasattr(remaining, "geoms") else [remaining]
        bx0, by0, bx1, by1 = box_px
        min_area = max(1500.0, 0.002 * (bx1 - bx0) * (by1 - by0))
        kept = [p for p in parts if not p.is_empty and p.area >= min_area]
        if not kept:
            return None
        # 빼는 과정에서 생긴 톱니 가장자리는 이후 유테 경로의 매끄럽게
        # 다듬기가 정리한다(실제 요소 경계 쪽은 원래 실루엣 그대로).
        return MultiPolygon(kept) if len(kept) > 1 else kept[0]

    def _start_hint_correction(self, box_px):
        """"힌트로 보정" 버튼 핸들러 -- 트라이맵 힌트 보정 도구 진입점.

        2026-09-28 실제 파일로 검증하며 발견한 것: "의심 영역" 경고가 항상
        "이미 인식된 도안인데 GrabCut이 잘못 잡은" 경우만은 아니었다 --
        배경과 색이 비슷한 장식(실측: 10칸 시트의 양배추 장식 2개)은 애초에
        낱개 요소 "박스" 탐지 단계에서부터 통째로 빠져서, 자동 인식 결과
        어디에도 대응하는 항목 자체가 없는 경우가 실제로 있었다. 그래서
        대응하는 기존 항목을 찾으면(match_idx) 그것을 고치고, 못 찾으면
        이 좌표를 그대로 "새 도안"으로 다뤄서 처음부터 만든다 -- 두 경우
        모두 사람이 힌트를 찍어야 하는 것은 같고, 차이는 재계산 후 기존
        항목을 교체하느냐 새로 추가하느냐뿐이다."""
        match_idx = self._find_accumulated_index_for_box(box_px)
        self._hint_mode_active = True
        self._hint_fg_points_px = []
        self._hint_bg_points_px = []
        self._hint_current_label = "fg"
        if match_idx is not None:
            meta = self._grabcut_hint_meta[match_idx]
            self._hint_target_index = match_idx
            self._hint_new_box_px = None
            self._navigate_preview_to_original_box(meta["box_px"])
        else:
            self._hint_target_index = None
            self._hint_new_box_px = tuple(box_px)
            self._navigate_preview_to_original_box(box_px)
        self._show_hint_correction_panel(is_new=match_idx is None)

    def _show_hint_correction_panel(self, is_new=False):
        """힌트 그리기 모드용 비모달 패널 -- 전경/배경 점 찍기 전환, 다시
        계산, 초기화, 닫기(모드 종료) 버튼을 담는다. _show_suspicious_
        regions_dialog와 같은 이유로 모달이 아니다(캔버스를 계속 봐야 함)."""
        if self._hint_panel is not None:
            try:
                self._hint_panel.destroy()
            except Exception:  # noqa: BLE001
                pass
            self._hint_panel = None

        panel = ctk.CTkToplevel(self)
        panel.title(APP_TITLE)
        panel.resizable(False, False)
        try:
            panel.configure(fg_color=BG_APP)
        except Exception:  # noqa: BLE001
            pass
        try:
            panel.transient(self)
        except Exception:  # noqa: BLE001
            pass

        wrap = ctk.CTkFrame(
            panel, fg_color=BG_CARD, corner_radius=16, border_width=1, border_color=BORDER,
        )
        wrap.pack(fill="both", expand=True, padx=14, pady=14)

        ctk.CTkLabel(
            wrap, text="힌트로 실루엣 보정", font=self.font_section, text_color=ACCENT,
            anchor="w", justify="left", wraplength=320,
        ).pack(fill="x", padx=18, pady=(16, 4))
        desc = (
            "미리보기에서 확실히 캐릭터(전경)인 곳과 확실히 배경인 곳을 "
            "각각 클릭해 점을 찍은 뒤 '다시 계산'을 누르세요. 자동으로 "
            "고치지 않고, 표시한 곳만 반영해 다시 계산합니다."
        )
        if is_new:
            desc += (
                " 이 위치는 자동 인식에서 아예 빠져 있던 자리라, 새 도안으로 "
                "추가됩니다."
            )
        ctk.CTkLabel(
            wrap, text=desc, font=self.font_caption, text_color=TEXT_SECONDARY,
            anchor="w", justify="left", wraplength=320,
        ).pack(fill="x", padx=18, pady=(0, 8))

        self._hint_status_var = tk.StringVar(value="")
        self._update_hint_status_text()
        ctk.CTkLabel(
            wrap, textvariable=self._hint_status_var, font=self.font_caption,
            text_color=TEXT_PRIMARY, anchor="w", justify="left", wraplength=320,
        ).pack(fill="x", padx=18, pady=(0, 8))

        mode_row = ctk.CTkFrame(wrap, fg_color="transparent")
        mode_row.pack(fill="x", padx=18, pady=(0, 8))
        self._hint_fg_btn = ctk.CTkButton(
            mode_row, text="🟢 전경(캐릭터) 점", font=self.font_caption,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#FFFFFF",
            corner_radius=8, width=150, height=28,
            command=lambda: self._set_hint_label("fg"),
        )
        self._hint_fg_btn.pack(side="left")
        self._hint_bg_btn = ctk.CTkButton(
            mode_row, text="🔴 배경 점", font=self.font_caption,
            fg_color="transparent", hover_color=ACCENT_SOFT, text_color=TEXT_PRIMARY,
            border_width=1, border_color=BORDER, corner_radius=8, width=110, height=28,
            command=lambda: self._set_hint_label("bg"),
        )
        self._hint_bg_btn.pack(side="left", padx=(8, 0))

        action_row = ctk.CTkFrame(wrap, fg_color="transparent")
        action_row.pack(fill="x", padx=18, pady=(0, 8))
        ctk.CTkButton(
            action_row, text="초기화", font=self.font_caption,
            fg_color="transparent", hover_color=ACCENT_SOFT, text_color=TEXT_PRIMARY,
            border_width=1, border_color=BORDER, corner_radius=8, width=90, height=28,
            command=self._clear_hint_points,
        ).pack(side="left")

        self._btn_primary(wrap, "다시 계산", self._recompute_with_hints).pack(
            fill="x", padx=18, pady=(4, 4)
        )
        ctk.CTkButton(
            wrap, text="닫기", font=self.font_caption,
            fg_color="transparent", hover_color=ACCENT_SOFT, text_color=TEXT_SECONDARY,
            border_width=1, border_color=BORDER, corner_radius=8,
            command=self._exit_hint_mode,
        ).pack(fill="x", padx=18, pady=(0, 16))

        try:
            panel.lift()
        except Exception:  # noqa: BLE001
            pass

        self.update_idletasks()
        try:
            panel.update_idletasks()
            px, py = self.winfo_rootx(), self.winfo_rooty()
            panel.geometry(f"+{px + 24}+{py + 60}")
        except Exception:  # noqa: BLE001
            pass

        self._hint_panel = panel
        self._update_hint_mode_buttons()

    def _update_hint_status_text(self):
        if not hasattr(self, "_hint_status_var"):
            return
        self._hint_status_var.set(
            f"전경 점 {len(self._hint_fg_points_px)}개 / 배경 점 {len(self._hint_bg_points_px)}개"
        )

    def _update_hint_mode_buttons(self):
        if not hasattr(self, "_hint_fg_btn"):
            return
        is_fg = self._hint_current_label == "fg"
        self._hint_fg_btn.configure(
            fg_color=ACCENT if is_fg else "transparent",
            text_color="#FFFFFF" if is_fg else TEXT_PRIMARY,
            border_width=0 if is_fg else 1,
        )
        self._hint_bg_btn.configure(
            fg_color="#C0392B" if not is_fg else "transparent",
            text_color="#FFFFFF" if not is_fg else TEXT_PRIMARY,
            border_width=0 if not is_fg else 1,
        )

    def _set_hint_label(self, label):
        self._hint_current_label = label
        self._update_hint_mode_buttons()

    def _on_hint_click_at_point(self, cx, cy):
        """힌트 그리기 모드에서 캔버스 클릭 -- 캔버스 좌표를 원본 이미지
        좌표로 되돌려 현재 선택된 라벨(전경/배경) 점 목록에 추가한다."""
        scale = self._display_scale or 1.0
        ox, oy = cx / scale, cy / scale
        if self._hint_current_label == "fg":
            self._hint_fg_points_px.append((ox, oy))
        else:
            self._hint_bg_points_px.append((ox, oy))
        self._update_hint_status_text()
        self._redraw_hint_markers()

    def _redraw_hint_markers(self):
        for marker_id in self._hint_marker_ids:
            try:
                self.preview_canvas.delete(marker_id)
            except Exception:  # noqa: BLE001
                pass
        self._hint_marker_ids = []
        scale = self._display_scale or 1.0
        r = 5
        for (ox, oy) in self._hint_fg_points_px:
            cx, cy = ox * scale, oy * scale
            mid = self.preview_canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r, fill="#2ECC71", outline="#FFFFFF", width=2,
            )
            self._hint_marker_ids.append(mid)
        for (ox, oy) in self._hint_bg_points_px:
            cx, cy = ox * scale, oy * scale
            mid = self.preview_canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r, fill="#E74C3C", outline="#FFFFFF", width=2,
            )
            self._hint_marker_ids.append(mid)

    def _clear_hint_points(self):
        self._hint_fg_points_px = []
        self._hint_bg_points_px = []
        self._update_hint_status_text()
        self._redraw_hint_markers()

    def _exit_hint_mode(self):
        self._hint_mode_active = False
        self._hint_target_index = None
        self._hint_new_box_px = None
        self._hint_fg_points_px = []
        self._hint_bg_points_px = []
        for marker_id in self._hint_marker_ids:
            try:
                self.preview_canvas.delete(marker_id)
            except Exception:  # noqa: BLE001
                pass
        self._hint_marker_ids = []
        if self._hint_panel is not None:
            try:
                self._hint_panel.destroy()
            except Exception:  # noqa: BLE001
                pass
            self._hint_panel = None

    def _recompute_with_hints(self):
        """"다시 계산" 버튼 핸들러 -- segment_design_with_hints로 대표
        인스턴스의 실루엣만 다시 계산하고, 같은 반복 그룹의 나머지 칸에는
        (멍푸 요청 "반복 칸에 자동 적용") fit_cutline_result_to_box로 그
        보정된 모양을 그대로 복제한다.

        `self._hint_target_index`가 있으면(기존 자동 인식 항목을 보정)
        그 항목과 같은 반복 그룹 전체를 교체하고, 없으면(self._hint_new_box_px
        -- 애초에 도안 박스로도 인식되지 못했던 자리, _start_hint_correction
        문서 참고) 이 좌표를 새 도안으로 추가한다."""
        is_new = self._hint_target_index is None
        if is_new and self._hint_new_box_px is None:
            return
        if not self._hint_fg_points_px and not self._hint_bg_points_px:
            self._show_note_dialog(
                "점을 먼저 찍어주세요",
                ["전경(캐릭터) 또는 배경 점을 하나 이상 찍은 뒤 다시 계산할 수 있습니다."],
                kind="error",
            )
            return
        if is_new:
            rep_box = self._hint_new_box_px
            margin_mm = self.style_margin_mm.get()
            dpi = self.dpi.get()
            # 무테 작업이면 새 도안도 그림 안쪽으로(배경 포함 금지).
            style_name = "BORDERLESS" if self.job_type.get() == "BORDERLESS" else "LINE_ART"
        else:
            meta = self._grabcut_hint_meta.get(self._hint_target_index)
            if meta is None:
                return
            rep_box = meta["box_px"]
            margin_mm = meta["margin_mm"]
            dpi = meta["dpi"]
            style_name = meta.get("style", "LINE_ART")
        path = self.input_path.get().strip()
        if not path or not os.path.isfile(path):
            self._show_note_dialog("파일을 찾을 수 없습니다", ["원본 도안 파일을 다시 확인해주세요."], kind="error")
            return

        self.status.set("힌트를 반영해 실루엣을 다시 계산하는 중...")
        self.update_idletasks()
        try:
            note_sink: list = []
            corrected = segment_design_with_hints(
                path, rep_box,
                fg_hints_px=list(self._hint_fg_points_px),
                bg_hints_px=list(self._hint_bg_points_px),
                supersample=self.precision.get(),
                note_sink=note_sink,
            )
            if is_new:
                # 2026-09-28(멍푸 "이중 칼선" 지적): 새 도안 추가 경로는 경고
                # 영역 전체를 다시 추적하므로, 그 안에 이미 칼선이 있는 요소
                # (예: 옆의 강아지들)까지 또 잡혀 같은 요소에 칼선이 두 겹이
                # 된다. 이미 있는 칼선 영역을 빼고, 새로 생긴 조각(빠져 있던
                # 요소)만 남긴다.
                corrected = self._subtract_existing_cutlines(corrected, rep_box)
                if corrected is None:
                    self._show_note_dialog(
                        "새로 추가할 도안이 없습니다",
                        [
                            "힌트로 다시 찾은 모양이 전부 이미 칼선이 있는 요소였습니다.",
                            "빠진 요소 안쪽에 초록 점을 찍고 다시 계산해보세요.",
                        ],
                        kind="info",
                    )
                    self.status.set("새로 추가할 도안이 없습니다(이중 칼선 방지).")
                    return
            new_rep_result = generate_cutline_by_style(
                image_path=path,
                style=ImageStyle[style_name],
                dpi=dpi,
                selection_px=rep_box,
                margin_mm=margin_mm,
                supersample=self.precision.get(),
                precomputed_content_px=corrected,
            )
            new_rep_result.adjustments = list(note_sink) + list(new_rep_result.adjustments or []) + [
                "사람이 화면에서 직접 표시한 힌트(전경/배경 점)로 실루엣을 보정했습니다."
            ]

            if is_new:
                self._accumulated.append(new_rep_result)
                new_index = len(self._accumulated) - 1
                self._grabcut_hint_meta[new_index] = {
                    "box_px": rep_box,
                    "margin_mm": margin_mm,
                    "dpi": dpi,
                    "group_indices": [new_index],
                    "style": style_name,
                    "is_representative": True,
                }
                updated = 1
            else:
                meta = self._grabcut_hint_meta[self._hint_target_index]
                group_indices = meta.get("group_indices") or [self._hint_target_index]
                rep_index = None
                for idx in group_indices:
                    if self._grabcut_hint_meta.get(idx, {}).get("is_representative"):
                        rep_index = idx
                        break
                if rep_index is None:
                    rep_index = self._hint_target_index

                updated = 0
                for idx in group_indices:
                    if idx >= len(self._accumulated):
                        continue
                    if idx == rep_index:
                        self._accumulated[idx] = new_rep_result
                    else:
                        other_meta = self._grabcut_hint_meta.get(idx)
                        if other_meta is None:
                            continue
                        self._accumulated[idx] = fit_cutline_result_to_box(
                            new_rep_result, rep_box, other_meta["box_px"],
                            note=(
                                "동일 도안이 반복되는 것으로 감지되어, 힌트로 보정된 "
                                "실루엣을 그 칸 크기에 맞춰 복제했습니다."
                            ),
                        )
                    updated += 1

            combined = combine_results(self._accumulated)
            preview_png = os.path.join(_work_file_dir(), "_last_preview.png")
            render_preview(combined, preview_png, original_image_path=path)
            self._last_result = combined
        except Exception as e:  # noqa: BLE001
            self._report_exception_to_server("힌트 보정 재계산 처리 중")
            self._show_note_dialog(
                "다시 계산 중 오류가 발생했습니다", [self._friendly_error_text(str(e))], kind="error",
            )
            self.status.set("힌트 보정 중 오류가 발생했습니다.")
            return

        self._clear_hint_points()
        self._show_preview(preview_png, item_result=None)
        if is_new:
            self.status.set("힌트 보정 완료 -- 새 도안으로 1개 추가했습니다.")
        else:
            self.status.set(f"힌트 보정 완료 -- 같은 반복 그룹 {updated}개 칸에 반영했습니다.")

    def _load_source_preview(self, path):
        """Show the raw (not-yet-processed) image on the canvas so the
        artist can drag a selection rectangle over one design BEFORE
        generating -- this is the "영역을 드래그 해서 생성" workflow."""
        img = Image.open(path).convert("RGBA")
        self._source_image = img
        self._preview_full_image = img
        # 새 파일(또는 새 선택을 위해 원본을 다시 불러올 때)마다 100%로
        # 되돌린다 -- 이전 이미지에서 확대했던 배율이 새 이미지에 그대로
        # 남아 혼란을 주지 않도록.
        self._preview_zoom = 1.0
        self._render_preview()
        # _render_preview()의 canvas.delete("all")로 기존 선택 사각형도
        # 함께 지워졌으므로, 관련 상태 변수도 전부 같이 초기화.
        self._drag_start = None
        self._selection_px = None
        # 2026-09-10(35차 이어서): "무테/유테+도무송"의 표시
        # 모드 상태도 여기서 함께 초기화 -- 새 파일을 열거나 "원본 다시
        # 보기"를 누르면, 이전에 감지해둔 도안 목록/표시는 지금 화면의
        # 이미지와 더 이상 맞지 않으므로 남겨두면 안 된다. _on_mixed_detect_
        # done은 이 함수를 먼저 호출한 뒤 자신의 상태를 다시 채워 넣으므로
        # 순서상 문제 없음.
        self._mixed_mode = False
        self._mixed_path = None
        self._mixed_boxes = []
        self._mixed_groups = []
        self._mixed_marked = set()
        self._mixed_rect_ids = {}
        self._mixed_hover_gi = None
        self._mixed_processed = set()
        self._mixed_added_count = 0

    def _show_source_again(self):
        """After generating once, bring back the raw source image (with
        drag-select active again) so a different region/type can be tried
        without re-browsing for the file."""
        path = self.input_path.get().strip()
        if not path or self.is_vector.get() or not os.path.isfile(path) or Image is None:
            return
        self._clear_selection()
        try:
            self._load_source_preview(path)
            self.status.set("원본을 다시 표시했습니다 -- 영역을 드래그해서 선택하세요.")
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    # ---- 영역 선택(클릭으로 자동 인식 + 수동 드래그) --------------------
    # 2026-09-07 피드백("드래그도 너무 어려워 쉽게 끌어서 선택하게 해줘",
    # "빨간색 선이 지정되고 추가 드래그가 안 돼", "드래그로 이미지 선택하는게
    # 어려워서 커서를 만들고 원하는 위치에 두면 자동으로 인식하게 설정")에
    # 따라 크게 세 가지를 바꿈.
    #   1. 선택 사각형을 흰색 굵은 테두리(halo) + 진한 코발트 점선으로 이중
    #      그려서, 어떤 배경색 위에서도 잘 보이게 함(기존엔 얇은 점선 하나뿐
    #      이라 화려한 그림 위에서는 거의 안 보일 수 있었음).
    #   2. 마우스를 누른 채 정확히 끄는 게 어렵다는 지적이 계속돼서, 드래그
    #      없이 그냥 원하는 도안 위를 클릭 한 번만 하면 core.multi_design.
    #      find_design_bbox_at_point_px가 그 자리의 도안을 자동으로 찾아
    #      선택 영역으로 삼는다(_on_canvas_release/_auto_select_at_point
    #      참고). 드래그는 자동 인식이 잘 안 맞는 경우를 위해 그대로 남김.
    #   3. (2026-09-08 업데이트) 결과 화면(칼선이 그려진 미리보기)을 보고
    #      있을 때, 한동안은 캔버스 위 클릭/드래그를 시작하기만 해도 자동
    #      으로 원본을 다시 불러와 그 자리에서 곧장 새 선택을 시작하게
    #      했었다("원본 다시 보기"를 따로 눌러야 해서 "드래그가 안 된다"는
    #      지적 때문). 그런데 이 자동 전환이 너무 민감해서, 실수로 살짝만
    #      눌러도 방금 만든 칼선 결과가 화면에서 사라져버리는 문제로
    #      이어졌다("완성된 칼선을 실수로 드래그 하면 칼선이 사라지는
    #      오류"). 그래서 다시 되돌려, 결과 화면 위 클릭/드래그는 이제 아무
    #      것도 바꾸지 않고(_on_canvas_press 참고), 새 선택을 시작하려면
    #      '원본 다시 보기' 버튼을 명시적으로 눌러야 한다 -- 한 번 더
    #      눌러야 하는 불편함보다, 실수로 결과를 잃는 위험을 없애는 쪽을
    #      우선했다.
    def _clear_selection(self):
        self._selection_px = None
        self._drag_start = None
        if self._selection_rect_id is not None:
            self.preview_canvas.delete(self._selection_rect_id)
            self._selection_rect_id = None
        if self._selection_halo_id is not None:
            self.preview_canvas.delete(self._selection_halo_id)
            self._selection_halo_id = None
        # 2026-09-28: 새 선택을 시작하는 시점엔 "의심 영역 경고" 이동
        # 강조 사각형(주황색)도 같이 지워서 옛 표시가 남아 헷갈리지 않게 한다.
        if self._nav_highlight_rect_id is not None:
            self.preview_canvas.delete(self._nav_highlight_rect_id)
            self._nav_highlight_rect_id = None
        if self._nav_highlight_halo_id is not None:
            self.preview_canvas.delete(self._nav_highlight_halo_id)
            self._nav_highlight_halo_id = None
        self._clear_hover_preview()

    def _selection_size_mm(self, cx0, cy0, cx1, cy1):
        scale = self._display_scale or 1.0
        w_mm = px_to_mm(abs(cx1 - cx0) / scale, self.dpi.get())
        h_mm = px_to_mm(abs(cy1 - cy0) / scale, self.dpi.get())
        return w_mm, h_mm

    def _draw_selection_rect(self, x0, y0, x1, y1):
        """선택 사각형을 (다시)그린다 -- 흰 테두리(halo) 먼저, 그 위에 코발트
        점선. 기존 아이템이 있으면 좌표만 갱신, 없으면 새로 만든다."""
        if self._selection_halo_id is None:
            self._selection_halo_id = self.preview_canvas.create_rectangle(
                x0, y0, x1, y1, outline="#FFFFFF", width=5
            )
        else:
            self.preview_canvas.coords(self._selection_halo_id, x0, y0, x1, y1)
        if self._selection_rect_id is None:
            self._selection_rect_id = self.preview_canvas.create_rectangle(
                x0, y0, x1, y1, outline=ACCENT, width=2, dash=(5, 3)
            )
        else:
            self.preview_canvas.coords(self._selection_rect_id, x0, y0, x1, y1)
        # 코발트 점선이 흰 테두리보다 위(나중에 그려짐)에 오도록 쌓는 순서를
        # 맞춤 -- 항목을 새로 만들 때만 순서가 바뀔 수 있어 매번 보정.
        self.preview_canvas.tag_raise(self._selection_rect_id, self._selection_halo_id)

    def _finalize_selection(self, x0, y0, x1, y1):
        """드래그(누른 채 끌기)든 두 번 클릭이든, 두 지점이 정해지면 여기서
        공통으로 마무리한다."""
        cx0, cx1 = sorted((x0, x1))
        cy0, cy1 = sorted((y0, y1))
        if (cx1 - cx0) < 4 or (cy1 - cy0) < 4:
            self._clear_selection()
            self.status.set(
                "선택 영역이 너무 작습니다. 다시 드래그하거나, 클릭 두 번(모서리 -> 반대쪽 모서리)으로 선택해보세요."
            )
            return
        # 확정된 선택 사각형을 그리므로, 그 아래 남아있을 수 있는 호버
        # 미리보기(옅은 회색 점선)는 이제 필요 없다.
        self._clear_hover_preview()
        self._draw_selection_rect(cx0, cy0, cx1, cy1)
        scale = self._display_scale
        self._selection_px = (cx0 / scale, cy0 / scale, cx1 / scale, cy1 / scale)
        w_mm, h_mm = self._selection_size_mm(cx0, cy0, cx1, cy1)
        self.status.set(
            f"선택 완료 ({w_mm:.1f} × {h_mm:.1f}mm) -- 왼쪽에서 칼선 종류를 고르고 "
            "'이 영역 추가 + 미리보기'를 누르세요."
        )

    # ---- 2026-09-26 피드백("칸 인식... 가져다 대면 예비 모양이 뜨는
    # 버전으로... 취소하기도 좋을것 같아") 대응: 클릭 전 마우스만 올려도
    # 그 자리의 도안 예비 모양을 옅은 선으로 보여주는 "호버 미리보기".
    # 단일 선택 화면(이 아래)과 무테/유테+도무송 ①②③ 화면
    # (_on_canvas_hover 안에서 _mixed_mode 분기, _mixed_hover_at_point) 둘
    # 다 지원한다. 아무것도 확정하지 않으므로 다른 곳으로 옮기면 그냥
    # 사라지는 것 자체가 "취소" 역할을 한다.
    def _get_cached_design_boxes(self, path, blocking=True):
        """detect_design_bboxes_px는 시트 전체를 다시 훑는 무거운 작업이라,
        마우스가 움직일 때마다 매번 새로 돌리면 화면이 그대로 멈춘다.
        같은 파일이면(경로+수정시각) 한 번만 계산해서 재사용.

        2026-09-26 피드백("도안은 3초내 인식하는데 스크롤에 응답하지
        않아서 사용할 수가 없어"): 파일을 불러온 직후 백그라운드 스레드가
        이 캐시를 미리 데우는 중(28~30초 소요)일 때, 마우스 호버 같은
        메인 스레드 이벤트가 여기 들어오면 캐시가 아직 비어 있으니 그
        이벤트 처리 스레드(=메인 스레드)에서 detect_design_bboxes_px를
        또 새로 돌려버렸다 -- 그 30초 동안 Tk 이벤트 루프 자체가 멈춰
        스크롤/클릭 등 모든 입력이 먹통이 됐다(실제 재현 확인).
        `blocking=False`로 부르면, 이미 다른 스레드가 같은 파일을 계산
        중일 때 그 계산을 기다리지 않고 즉시 None을 돌려준다(화면은 안
        멈추고, 잠깐 호버 미리보기만 안 뜬다) -- `blocking=True`(기본,
        버튼 클릭으로 시작된 백그라운드 스레드 전용)만 실제로 계산이
        끝날 때까지 기다려서 같은 계산을 두 번 하지 않는다."""
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return None
        key = (path, mtime)
        if self._design_boxes_cache_key == key and self._design_boxes_cache is not None:
            return self._design_boxes_cache

        with self._design_boxes_locks_guard:
            lock = self._design_boxes_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._design_boxes_locks[key] = lock

        if not lock.acquire(blocking=blocking):
            # 다른 스레드가 이미 이 파일을 계산 중 -- 여기서 기다리지 않음
            # (blocking=False로 부른 쪽, 대개 마우스 호버).
            return None
        try:
            # 락을 기다리는 사이 다른 스레드가 이미 끝내놨을 수 있으니 재확인.
            if self._design_boxes_cache_key == key and self._design_boxes_cache is not None:
                return self._design_boxes_cache
            try:
                boxes = detect_design_bboxes_px(path)
            except Exception:  # noqa: BLE001
                return None
            self._design_boxes_cache_key = key
            self._design_boxes_cache = boxes
            return boxes
        finally:
            lock.release()
            # (path, mtime) 키의 락 객체는 딕셔너리에 그대로 남겨둔다 --
            # 세션 중 실제로 열어보는 파일 개수는 매우 적어 메모리 부담이
            # 없고, 여기서 지우려고 하면 "막 release한 락을 다른 스레드가
            # 아직 들고 있는 사이 새 락 객체가 또 만들어지는" 좁은 경쟁
            # 상태(race)가 생겨 잠금 보장이 깨질 수 있다 -- 지우지 않는
            # 쪽이 더 안전하다.

    def _find_design_box_near(self, path, px_x, px_y):
        # blocking=False: 이 함수는 마우스 호버/클릭(메인 스레드)에서 불리므로
        # 위 _get_cached_design_boxes 설명대로 절대 화면을 멈추게 하면 안 된다.
        boxes = self._get_cached_design_boxes(path, blocking=False)
        if not boxes:
            return None
        return find_design_bbox_at_point_px(path, (px_x, px_y), boxes_px=boxes)

    def _hide_hover_preview_rect(self):
        """캔버스에 그려진 미리보기 사각형만 지운다 -- 클릭이 드래그가 아닌
        "그냥 클릭"으로 확정될 수 있으므로, 이 시점엔 어떤 도안이었는지
        기억(_hover_preview_box_px)은 아직 지우지 않는다."""
        if self._hover_preview_rect_id is not None:
            self.preview_canvas.delete(self._hover_preview_rect_id)
            self._hover_preview_rect_id = None

    def _clear_hover_preview(self):
        self._hide_hover_preview_rect()
        self._hover_preview_box_px = None

    def _draw_hover_preview_rect(self, x0, y0, x1, y1):
        """확정된 선택(흰 테두리+코발트 점선, _draw_selection_rect)과는
        구별되도록, 옅은 회색 얇은 점선으로만 그린다 -- 아직 클릭 전이라는
        느낌을 주기 위함."""
        if self._hover_preview_rect_id is None:
            self._hover_preview_rect_id = self.preview_canvas.create_rectangle(
                x0, y0, x1, y1, outline="#9AA5B1", width=1, dash=(3, 2)
            )
        else:
            self.preview_canvas.coords(self._hover_preview_rect_id, x0, y0, x1, y1)

    def _on_canvas_hover(self, event):
        if self._pan_mode or self._drag_start is not None:
            return
        cx = self.preview_canvas.canvasx(event.x)
        cy = self.preview_canvas.canvasy(event.y)
        if self._mixed_mode:
            self._mixed_hover_at_point(cx, cy)
            return
        if self._source_image is None:
            self._clear_hover_preview()
            return
        path = self.input_path.get().strip()
        if not path or self.is_vector.get() or not os.path.isfile(path):
            return
        scale = self._display_scale or 1.0
        px_x, px_y = cx / scale, cy / scale
        box = self._find_design_box_near(path, px_x, px_y)
        if box is None:
            self._clear_hover_preview()
            return
        if box == self._hover_preview_box_px:
            return
        self._hover_preview_box_px = box
        bx0, by0, bx1, by1 = box
        self._draw_hover_preview_rect(bx0 * scale, by0 * scale, bx1 * scale, by1 * scale)

    def _on_canvas_hover_leave(self, _event):
        self._clear_hover_preview()
        if self._mixed_mode:
            self._mixed_clear_hover()

    def _on_canvas_press(self, event):
        if self._hint_mode_active:
            # 힌트 그리기 모드에서는 클릭이 곧 힌트 점 하나이므로(드래그 X),
            # 눌렀을 때는 아무 것도 하지 않고 뗄 때(_on_canvas_release)만 처리.
            return
        if self._pan_mode:
            # 이동 모드: 영역 선택과는 완전히 별개로 취급 -- 원본/결과
            # 화면을 자동으로 다시 불러오는 등 선택 관련 로직을 전혀 거치지
            # 않고, 그냥 지금 이 지점을 "잡은" 기준점으로만 기록한다
            # (scan_mark는 실제 스크롤 위치를 바꾸지 않고 다음 scan_dragto
            # 호출의 기준점만 잡아둔다).
            self.preview_canvas.scan_mark(event.x, event.y)
            return
        if self._mixed_mode:
            # 2026-09-10(35차 이어서): "무테/유테+도무송"의 표시
            # 단계에서는 캔버스를 눌러도 새 드래그 선택을 시작하지 않는다 --
            # 실제 토글은 클릭이 끝나는 _on_canvas_release에서 처리한다.
            return
        if self._source_image is None:
            # 2026-09-08(10차) 피드백("완성된 칼선을 실수로 드래그 하면
            # 칼선이 사라지는 오류 수정해"): 결과(칼선이 그려진) 화면을 보고
            # 있을 때, 예전엔 캔버스를 누르기만 해도(실수로 살짝 눌러도)
            # 곧바로 원본 이미지로 자동 전환해버렸다 -- 데이터 자체(누적된
            # 칼선, self._accumulated)는 안 지워지지만, 화면에서 방금 만든
            # 칼선이 갑자기 사라져 보이니 사용자 입장에선 "칼선이 사라지는
            # 오류"와 똑같다. 이제는 결과 화면 위에서 클릭/드래그해도 아무
            # 것도 바꾸지 않고, 새 영역을 고르려면 이미 있는 '원본 다시
            # 보기' 버튼을 명시적으로 눌러야 한다(그 버튼은 그대로 잘
            # 동작함) -- 실수로 화면을 스치기만 해도 결과가 사라지던 위험을
            # 원천적으로 없앤다.
            self.status.set(
                "지금은 완성된 칼선 결과 화면입니다. 새 영역을 선택하려면 "
                "'원본 다시 보기' 버튼을 눌러주세요."
            )
            return

        # 2026-09-07(7차) 확대/축소 기능 추가로 캔버스가 스크롤될 수 있게
        # 되면서, event.x/event.y(캔버스 "위젯" 좌표, 스크롤 안 된 상태
        # 기준)를 그대로 쓰면 스크롤된 만큼 어긋난다. canvasx/canvasy가
        # 스크롤 위치를 반영한 실제 "캔버스 콘텐츠" 좌표를 주므로 이걸로
        # 바꿔야 확대 상태에서도 선택 좌표가 정확하다(스크롤 안 된 상태에선
        # canvasx(x) == x라서 기존 동작과 동일).
        cx = self.preview_canvas.canvasx(event.x)
        cy = self.preview_canvas.canvasy(event.y)
        self._drag_start = (cx, cy)
        # 지금 보이던 미리보기 사각형만 지운다 -- 이게 진짜 드래그가 아니라
        # 그냥 클릭이었다면 _auto_select_at_point에서 이 위치의 도안을
        # 그대로 재사용해 다시 계산하지 않는다.
        self._hide_hover_preview_rect()
        if self._selection_rect_id is not None:
            self.preview_canvas.delete(self._selection_rect_id)
            self._selection_rect_id = None
        if self._selection_halo_id is not None:
            self.preview_canvas.delete(self._selection_halo_id)
            self._selection_halo_id = None
        self._draw_selection_rect(cx, cy, cx, cy)

    def _on_canvas_drag(self, event):
        if self._hint_mode_active:
            return
        if self._pan_mode:
            # gain=1: 마우스가 실제로 움직인 픽셀만큼 그대로(1:1) 화면을
            # 끈다 -- tk.Canvas의 scan_mark/scan_dragto 표준 사용법.
            self.preview_canvas.scan_dragto(event.x, event.y, gain=1)
            return
        if self._mixed_mode:
            return
        if self._drag_start is None:
            return
        x0, y0 = self._drag_start
        cx = self.preview_canvas.canvasx(event.x)
        cy = self.preview_canvas.canvasy(event.y)
        self._draw_selection_rect(x0, y0, cx, cy)
        w_mm, h_mm = self._selection_size_mm(x0, y0, cx, cy)
        self.status.set(f"드래그 중: {w_mm:.1f} × {h_mm:.1f}mm (마우스를 놓으면 선택 완료)")

    def _on_canvas_release(self, event):
        if self._hint_mode_active:
            cx = self.preview_canvas.canvasx(event.x)
            cy = self.preview_canvas.canvasy(event.y)
            self._on_hint_click_at_point(cx, cy)
            return
        if self._pan_mode:
            return
        if self._mixed_mode:
            # 2026-09-10(35차 이어서): 드래그 거리와 상관없이, 클릭(뗀) 지점에
            # 있는 표시 대상을 토글한다 -- 새 영역 선택이 아니라 이미 감지된
            # 도안 중 하나를 "도무송으로 표시"할지 고르는 것이므로 기존
            # 드래그/클릭 구분 로직(_finalize_selection/_auto_select_at_point)
            # 과는 완전히 별개로 처리.
            cx = self.preview_canvas.canvasx(event.x)
            cy = self.preview_canvas.canvasy(event.y)
            self._on_mixed_toggle_at_point(cx, cy)
            return
        if self._drag_start is None:
            return
        x0, y0 = self._drag_start
        x1 = self.preview_canvas.canvasx(event.x)
        y1 = self.preview_canvas.canvasy(event.y)
        self._drag_start = None
        cx0, cx1 = sorted((x0, x1))
        cy0, cy1 = sorted((y0, y1))
        if (cx1 - cx0) < 4 or (cy1 - cy0) < 4:
            # 2026-09-07 피드백("드래그로 이미지 선택하는게 어려워서 커서를
            # 만들고 원하는 위치에 두면 자동으로 인식하게 설정"): 드래그 없이
            # 그냥 클릭만 한 경우(사실상 커서를 원하는 자리에 놓기만 한 것),
            # 그 지점에 있는 도안을 자동으로 찾아 바로 선택 영역으로 삼는다.
            self._auto_select_at_point(x0, y0)
            return
        self._finalize_selection(cx0, cy0, cx1, cy1)

    def _auto_select_at_point(self, cx, cy):
        """2026-09-07 피드백("드래그로 이미지 선택하는게 어려워서 커서를
        만들고 원하는 위치에 두면 자동으로 인식하게 설정"): 드래그 대신
        캔버스 좌표 (cx, cy) 위치를 클릭 한 번만 하면, core.multi_design.
        find_design_bbox_at_point_px로 그 자리의 도안 전체를 자동으로 찾아
        선택 영역으로 삼는다. 클릭한 자리에 도안이 없으면(배경을 클릭했거나
        너무 멀리 벗어난 경우) 조용히 무시하지 않고, 드래그로 직접 선택할
        수도 있다는 안내를 상태창에 남긴다."""
        path = self.input_path.get().strip()
        if not path or self.is_vector.get() or not os.path.isfile(path):
            self.status.set("자동 선택은 PNG/JPG 도안에서만 가능합니다 -- 드래그로 영역을 직접 선택하세요.")
            return
        scale = self._display_scale or 1.0
        px_x, px_y = cx / scale, cy / scale
        # 2026-09-26: 클릭 바로 직전까지 호버 미리보기가 이미 같은 자리를
        # 계산해뒀다면(가장 흔한 경우) 그걸 그대로 쓴다 -- 다시 계산할
        # 필요가 없어 "찾는 중" 없이 바로 확정된다.
        box = self._hover_preview_box_px
        if box is None:
            self.status.set("클릭한 위치의 도안을 찾는 중...")
            self.update_idletasks()
            try:
                box = self._find_design_box_near(path, px_x, px_y)
            except Exception as e:  # noqa: BLE001
                self._report_exception_to_server("클릭 지점 자동 선택 중")
                self.status.set(f"자동 선택 중 오류가 발생했습니다: {e} -- 드래그로 직접 선택해보세요.")
                return
        if box is None:
            self.status.set(
                "클릭한 위치에서 도안을 찾지 못했습니다 -- 도안 위를 좀 더 정확히 "
                "클릭하거나, 마우스로 드래그해서 영역을 직접 선택해보세요."
            )
            return
        bx0, by0, bx1, by1 = box
        self._finalize_selection(bx0 * scale, by0 * scale, bx1 * scale, by1 * scale)

    # ---- "무테/유테+도무송" 전용: ①감지+표시 -> ②생성 -------
    # 2026-09-10(35차 이어서) 피드백("도무송, 무테 동시 다중 선택하고
    # 만들어야 해. 이거 너무 번거러워... 표시해둔 것만 도무송(추천)"): 한
    # 시트 안에 도무송 도안(카드형)과 무테/유테 도안(캐릭터 실루엣 등)이
    # 섞여 있을 때, 도안마다 어느 쪽인지 그림만 보고 프로그램이 자동
    # 판단하는 건 이미 위험하다고 확인됐다(카드처럼 보여도 실제로는
    # 도무송이 아닐 수 있는, 제작 방식에 대한 결정이라 그림만으로는 알 수
    # 없음 -- AUTO_STYLE을 만들 때 이미 한 번 검토하고 도무송은 계속
    # 별도로 두기로 확인함). 그래서 자동 판단 대신, 감지된 도안들을 화면에
    # 전부 표시해두고 사람이 도무송으로 만들 것만 직접 클릭해서 표시하게
    # 하고, 표시 안 한 나머지만 자동(AUTO_STYLE과 같은 방식)으로 처리한다.
    def _draw_mixed_boxes(self):
        """self._mixed_groups의 모든 인스턴스를 캔버스에 사각형으로 그린다
        -- 표시 안 됨(무테/유테 예정)은 기존 선택 사각형과 같은 코발트색,
        표시됨(도무송 예정)은 칼선에 이미 쓰이는 빨간색(COLOR_CUT)으로
        구분한다(새 색을 추가하지 않고 이미 화면에 있는 의미 있는 색만
        재사용 -- '알록달록 금지' 피드백 반영)."""
        scale = self._display_scale or 1.0
        self._mixed_rect_ids = {}
        self._mixed_hover_gi = None
        for gi, group in enumerate(self._mixed_groups):
            color = COLOR_CUT if gi in self._mixed_marked else ACCENT
            ids = []
            for idx in group:
                x0, y0, x1, y1 = self._mixed_boxes[idx]
                cx0, cy0, cx1, cy1 = x0 * scale, y0 * scale, x1 * scale, y1 * scale
                halo_id = self.preview_canvas.create_rectangle(
                    cx0, cy0, cx1, cy1, outline="#FFFFFF", width=4
                )
                rect_id = self.preview_canvas.create_rectangle(
                    cx0, cy0, cx1, cy1, outline=color, width=3
                )
                self.preview_canvas.tag_raise(rect_id, halo_id)
                ids.append((halo_id, rect_id))
            self._mixed_rect_ids[gi] = ids

    def _redraw_mixed_group(self, gi):
        """전체를 다시 그리지 않고, 표시가 바뀐 그룹 하나의 사각형 색만
        바꾼다(캔버스 아이템을 지우고 다시 만들 필요 없음)."""
        color = COLOR_CUT if gi in self._mixed_marked else ACCENT
        for _halo_id, rect_id in self._mixed_rect_ids.get(gi, []):
            self.preview_canvas.itemconfig(rect_id, outline=color, width=3)

    def _mixed_group_at_point(self, px, py):
        """원본 이미지 픽셀 좌표 (px, py)를 포함하는 그룹 인덱스(gi)를
        찾는다 -- 토글(클릭)과 강조(호버)가 똑같은 판정을 쓰도록 공통화."""
        for gi, group in enumerate(self._mixed_groups):
            for idx in group:
                x0, y0, x1, y1 = self._mixed_boxes[idx]
                if x0 <= px <= x1 and y0 <= py <= y1:
                    return gi
        return None

    def _on_mixed_toggle_at_point(self, cx, cy):
        """캔버스 좌표(cx, cy)를 포함하는 감지된 도안(그룹)을 찾아 도무송
        표시를 켜고/끈다. 동일 도안이 반복되는 그룹이면(_mixed_groups),
        그중 하나만 클릭해도 그룹 전체(=화면에 흩어진 반복 인스턴스 전부)가
        함께 표시/해제된다 -- 같은 그림인데 하나는 도무송, 하나는 무테로
        서로 다르게 처리될 이유가 없기 때문."""
        scale = self._display_scale or 1.0
        px, py = cx / scale, cy / scale
        gi = self._mixed_group_at_point(px, py)
        if gi is None:
            self.status.set("클릭한 위치에 감지된 도안이 없습니다 -- 파란/빨간 테두리 안쪽을 클릭해보세요.")
            return
        # 방금 강조돼 있던 굵은 테두리(호버 미리보기)를 정상 굵기로 되돌린
        # 뒤 색만 바꾼다 -- 클릭 직후에도 굵게 남아있으면 확정된 표시인지
        # 아직 미리보기인지 구분이 안 된다.
        self._mixed_clear_hover()
        if gi in self._mixed_marked:
            self._mixed_marked.discard(gi)
        else:
            self._mixed_marked.add(gi)
        self._redraw_mixed_group(gi)
        n_marked = len(self._mixed_marked)
        n_total = len(self._mixed_groups)
        self.status.set(
            f"도무송으로 표시됨: {n_marked}/{n_total}개 -- 나머지 "
            f"{n_total - n_marked}개는 무테/유테로 자동 처리됩니다. 다 골랐으면 "
            "'② 표시한 대로 칼선 생성'을 누르세요."
        )

    def _mixed_hover_at_point(self, cx, cy):
        """2026-09-26 피드백("칸 인식... 가져다 대면 예비 모양이 뜨는
        버전으로... 취소하기도 좋을것 같아"): 이미 감지된 사각형들 위에서,
        클릭하면 어느 것이 토글될지 미리 굵게 강조해 보여준다. 새로 계산할
        게 없으므로(박스 목록은 이미 다 있음) 가볍다."""
        scale = self._display_scale or 1.0
        px, py = cx / scale, cy / scale
        gi = self._mixed_group_at_point(px, py)
        if gi == self._mixed_hover_gi:
            return
        self._mixed_clear_hover()
        if gi is None:
            return
        self._mixed_hover_gi = gi
        for _halo_id, rect_id in self._mixed_rect_ids.get(gi, []):
            self.preview_canvas.itemconfig(rect_id, width=6)

    def _mixed_clear_hover(self):
        if self._mixed_hover_gi is None:
            return
        for _halo_id, rect_id in self._mixed_rect_ids.get(self._mixed_hover_gi, []):
            self.preview_canvas.itemconfig(rect_id, width=3)
        self._mixed_hover_gi = None

    def _on_mixed_detect(self):
        """"① 도안 자동 인식 (표시하기)" 버튼: 시트 안 도안을 전부 찾아
        화면에 테두리로 표시만 한다(아직 칼선을 만들지 않음). 감지 자체가
        느릴 수 있어(_run_auto_detect_and_add_all과 동일한 이유) 배경
        스레드로 돌리고, 캔버스에 그리는 마무리(_on_mixed_detect_done)만
        메인 스레드로 되돌린다(self.after)."""
        path = self._validate_inputs()
        if not path:
            return
        if self.job_type.get() != "MIXED_AUTO":
            self._show_note_dialog(
                "이 버튼은 '무테/유테+도무송'에서만 사용할 수 있습니다",
                ["작업 종류에서 '무테/유테+도무송'을 먼저 선택하세요."],
                kind="error",
            )
            return
        if self.is_vector.get():
            self._show_note_dialog(
                "자동 인식을 사용할 수 없습니다",
                ["AI(벡터) 파일은 자동 인식을 지원하지 않습니다."],
                kind="error",
            )
            return
        self.generate_btn.configure(state="disabled")
        self.status.set("도안을 자동으로 찾는 중...")
        threading.Thread(target=self._run_mixed_detect, args=(path,), daemon=True).start()

    def _run_mixed_detect(self, path):
        # 2026-09-07 피드백("실제 재단선 격자를 읽은 파일이면 그 실제 칸을
        # 그대로 쓴다") + 2026-09-11(51차) -- _run_auto_detect_and_add_all과
        # 완전히 같은 감지 로직을 그대로 재사용(별도 새 알고리즘 없음, 이미
        # 검증된 것만 씀). 실제 격자가 있으면 detect_repeat_aware_sub_
        # element_boxes_px가 "반복 패널 그룹핑 먼저 -> 대표 하나만 쪼개기 ->
        # 나머지 반복엔 그 조각을 옮겨 붙이기"를 한 번에 처리해 boxes/groups를
        # 함께 돌려준다(51차 이전엔 순서가 반대라 반복 패널마다 따로 쪼개져
        # 결과가 제각각이었음, 자세한 경위는 core.multi_design.
        # detect_repeat_aware_sub_element_boxes_px 문서 참고).
        # 2026-09-11(52차) 피드백("도무송 칼선이 많이 밀리고 자리잡지 못
        # 했어", "중심을 벗어나"): 51차가 boxes를 칸(패널) 단위에서 그 안의
        # 낱개 조각 단위로 더 잘게 만들면서, 도무송의 "옆 도안 침범 금지"
        # 이웃 계산(expand_box_to_neighbor_midpoint_px, 46차)이 같은 패널
        # 안의 다른 조각(예: 바로 옆 하트 스티커)까지 "이웃"으로 잡아버리는
        # 회귀가 생겼다 -- 원래 46차의 의도는 "다음 패널을 침범하지 말라"는
        # 것이었는데, 조각 단위 boxes를 그대로 쓰면 같은 패널 안 조각끼리도
        # 훨씬 가까운 거리에서 서로를 이웃으로 인식해 도무송 여유 계산이
        # 한쪽으로 심하게 치우치고(비대칭), 그 비대칭 경계에서 칼선이
        # 잘려나가 중심을 벗어난 것처럼 보인다. 도무송 이웃 계산에는 이
        # 조각 단위 boxes 대신 원래의 칸(패널) 단위 cell_boxes를 따로 저장해
        # 쓴다(cell_boxes_for_domusong, self._mixed_cell_boxes_px).
        #
        # 2026-09-26(실제 파일로 발견, "도무송 카드 하나가 눈/코/리본 등
        # 15개 조각으로 쪼개져 각각 칼선이 따로 생긴다"): 위 51차 방식은
        # ①단계에서 이미 모든 칸을 낱개 조각 단위(detect_repeat_aware_sub_
        # element_boxes_px)로 쪼개놓고, ②(도무송)/③(나머지)은 그중 어느
        # 조각을 마킹했는지만 나중에 나눴다. 그런데 "카드 전체가 도무송
        # 하나"인 칸도 내부에 그림 요소가 여러 개면(눈, 코, 리본 등) 그
        # 낱개 조각들이 전부 별도 그룹으로 쪼개져 나타나, 사용자가 카드
        # 하나를 클릭해 표시해도 나머지 조각들은 표시가 안 된 채로 남아
        # ③에서 따로 칼선이 생겨버렸다(=카드 하나에 도무송+무테 칼선이
        # 겹쳐 생기는 이중 칼선 문제). 시도했던 두 가지 알고리즘적 분류
        # (반복 여부로 카드/스티커 구분, 감지 소스 단계에서 반복 아닌 칸의
        # 세부 쪼개기 생략)는 모두 기존 정상 동작을 깨뜨려 되돌렸다(반복
        # 여부만으로는 "과분할된 카드"와 "원래 낱개가 여러 개인 정상
        # 스티커 칸"을 구분할 수 없음, test_repeat_aware_sub_element_split.
        # py::test_no_repeat_single_cell_still_splits_normally 참고).
        #
        # 대신 멍푸 피드백대로 구조 자체를 바꿨다: ①은 이제 항상 칸(패널)
        # 단위까지만 인식하고 멈춘다(세부 조각 쪼개기 자체를 아예 안 함).
        # 그래서 도무송으로 마킹하는 단위가 항상 "칸 전체"가 되어, 카드
        # 내부에 조각이 몇 개든 ②에서 통짜 하나로 칼선이 만들어지고 끝난다.
        # 세부 조각 쪼개기(detect_repeat_aware_sub_element_boxes_px)는 이제
        # ③(나머지, _run_mixed_generate_rest_fine)에서 도무송으로 마킹 안
        # 된 칸만 모아 그때 비로소 실행한다 -- 알고리즘으로 애매하게
        # 분류하려던 문제를, "사용자가 어느 버튼으로 어느 칸을 처리했는지"
        # 라는 명확한 신호로 대체해 근본적으로 없앤 것.
        try:
            if self._real_grid_cells_px:
                cell_boxes = list(self._real_grid_cells_px)
            else:
                # 2026-09-26(같은 날 추가 발견, 피드백 "불필요한 무거운
                # 스킬이 있는것 같아 하나씩 확인해봐"): 이 detect_design_
                # bboxes_px(시트 전체 스캔, 실측 10칸 시트에서 약 30초)는
                # 파일을 불러올 때(_run_load_ai)나 마우스를 캔버스에 올릴 때
                # 이미 한 번 계산해 _get_cached_design_boxes에 캐시해 둔 것과
                # 완전히 같은 계산인데, 여기서는 그 캐시를 안 쓰고 매번
                # detect_design_bboxes_px(path)를 직접 새로 불러 처음부터 다시
                # 돌리고 있었다 -- "① 도안 자동 인식" 버튼을 누를 때마다(같은
                # 파일인데도) 약 30초가 또 걸리는 원인. 캐시를 그대로 재사용
                #하도록 바꾼다(같은 함수, 같은 인자라 결과는 완전히 동일 --
                # 회귀 없음).
                cell_boxes = self._get_cached_design_boxes(path) or []
            # 2026-09-26 피드백(실제 파일로 재현, "하단의 빈공간 선택하는거
            # 고치고"): 작가가 그려둔 실제 재단선 격자를 그대로 쓰는 경우
            # (self._real_grid_cells_px), 시트의 물리적 배치상 어쩔 수 없이
            # 생기는 "내용이 전혀 없는 칸"이 격자 안에 섞여 있을 수 있다 --
            # 실측: 한 실제 파일 맨 아래에 반복 무늬가 시트 높이에 딱 안 맞아
            # 남는 높이 251px짜리 흰 여백 칸이 실제 격자에 그대로 들어있었고,
            # ①이 이걸 걸러내지 않아 화면에 클릭 가능한 사각형으로 그려져
            # 사용자가 빈 공간을 도안으로 착각해 표시/선택할 수 있었다.
            # detect_repeat_aware_sub_element_boxes_px(③)가 이미 같은 기준
            # (cell_has_content_px)으로 빈 칸을 걸러내고 있으므로, ①에서도
            # 똑같이 미리 걸러 애초에 화면에 나타나지도 않게 한다(판정 자체가
            # 실패하면 안전하게 "내용 있음"으로 간주 -- 회귀 없음).
            filtered_cell_boxes = []
            for b in cell_boxes:
                try:
                    has_content = cell_has_content_px(path, b)
                except Exception:  # noqa: BLE001
                    has_content = True
                # 2026-09-29(멍푸 PC에서 실제 사용 중 발견): 이웃 칸 테두리가 살짝
                # 걸친 맨 아래 빈 띠 칸은 위 검사를 통과해 ③에서 사각형 칼선이
                # 생겼다(배경 칼선). 자동 인식과 같은 기준(이미지 외곽이 없으면
                # 빈 칸)으로 한 번 더 거른다.
                if has_content and self._real_grid_cells_px:
                    try:
                        has_content = image_outer_region_px(path, b) is not None
                    except Exception:  # noqa: BLE001
                        pass
                if has_content:
                    filtered_cell_boxes.append(b)
            cell_boxes = filtered_cell_boxes
            boxes = cell_boxes
            try:
                groups = group_identical_boxes_px(path, boxes)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                groups = [[i] for i in range(len(boxes))]
        except Exception as e:  # noqa: BLE001
            self._report_exception_to_server("칼선 생성/미리보기 처리 중")
            self.after(0, self._on_error, str(e))
            return
        if not boxes:
            self.after(0, self._on_auto_detect_none)
            return
        self.after(0, self._on_mixed_detect_done, path, boxes, groups, cell_boxes)

    def _on_mixed_detect_done(self, path, boxes, groups, cell_boxes=None):
        self._load_source_preview(path)  # 여기서 _mixed_* 상태가 한 번 초기화됨
        self._mixed_path = path
        self._mixed_boxes = boxes
        self._mixed_groups = groups
        # 2026-09-11(52차): 도무송 이웃-중간지점 확장은 조각 단위가 아니라
        # 칸(패널) 단위 경계를 써야 한다(위 _run_mixed_detect 참고).
        self._mixed_cell_boxes_px = cell_boxes if cell_boxes else boxes
        self._mixed_marked = set()
        self._mixed_processed = set()
        self._clear_hover_preview()  # 단일 선택 화면의 호버 미리보기가 남아있지 않게
        self._mixed_mode = True
        self._draw_mixed_boxes()
        self.generate_btn.configure(state="normal")
        self.status.set(
            f"{len(groups)}개 도안을 찾았습니다. 이 중 도무송으로 만들 도안을 클릭해서 표시"
            "(빨간 테두리)하세요 -- 표시 안 한 나머지는 무테/유테로 자동 처리됩니다. 다 "
            "골랐으면 '② 표시한 대로 칼선 생성'을 누르세요."
        )

    # 2026-09-10(36차 이어서) 피드백("도무송은 먼저 선택해서 생성하고,
    # 그다음에 다음 종류 선택하는 방향으로 가고"): 표시된 도무송과 나머지
    # 무테/유테를 한 번에 같이 생성하던 방식(35차 _on_mixed_generate)을,
    # 순서대로 진행할 수 있게 ②(표시한 도무송만)/③(나머지만) 두 단계로
    # 나눴다. 어느 버튼을 먼저 눌러도 되고, self._mixed_processed로 이미
    # 만든 그룹은 기록해둬서 두 번 만들지 않는다 -- 모든 그룹이 다 끝나면
    # (어느 버튼에서 끝나든) 그때 최종 결과 화면으로 전환된다.
    def _mixed_generate_common_checks(self):
        """② / ③ 공통 검증. 통과하면 (path, groups, marked) 튜플, 실패하면
        None(이미 오류 팝업을 띄운 뒤)."""
        if self.job_type.get() != "MIXED_AUTO":
            self._show_note_dialog(
                "이 버튼은 '무테/유테+도무송'에서만 사용할 수 있습니다",
                ["작업 종류에서 '무테/유테+도무송'을 먼저 선택하세요."],
                kind="error",
            )
            return None
        if not self._mixed_groups:
            self._show_note_dialog(
                "먼저 도안을 자동 인식하세요",
                ["'① 도안 자동 인식 (표시하기)'을 먼저 눌러 도안을 찾고, 도무송으로 만들 것을 표시하세요."],
                kind="error",
            )
            return None
        path = self._mixed_path
        if not path or not os.path.isfile(path):
            self._show_note_dialog(
                "도안 파일을 다시 확인해주세요", ["파일이 이동되었거나 삭제된 것 같습니다."], kind="error"
            )
            return None
        return path

    def _on_mixed_generate_domusong(self):
        """"② 표시한 도무송 칼선 생성" 버튼: 빨간 테두리로 표시해둔 그룹만
        먼저 도무송 도형으로 칼선을 만든다(나머지는 아직 건드리지 않음)."""
        path = self._mixed_generate_common_checks()
        if not path:
            return
        if not self.cutline_type.get():
            self._show_note_dialog(
                "먼저 '재단 도형'을 선택하세요",
                ["도무송으로 표시한 도안에 쓸 재단 도형(도무송 세부 옵션)을 먼저 골라주세요."],
                kind="error",
            )
            return
        to_process = [
            gi for gi in range(len(self._mixed_groups))
            if gi in self._mixed_marked and gi not in self._mixed_processed
        ]
        if not to_process:
            self._show_note_dialog(
                "생성할 도무송 도안이 없습니다",
                ["먼저 화면에서 도무송으로 만들 도안을 클릭해 표시하거나, 이미 모두 생성된 상태입니다."],
                kind="error",
            )
            return
        self.generate_btn.configure(state="disabled")
        self.status.set(f"표시한 도무송 도안 {len(to_process)}개를 생성하는 중...")
        threading.Thread(
            target=self._run_mixed_generate_subset, args=(path, to_process), daemon=True
        ).start()

    def _on_mixed_generate_rest(self):
        """"③ 나머지 무테/유테 칼선 생성" 버튼: 표시 안 된(파란 테두리)
        칸만 모아, 이제야(2026-09-26 재구성, 위 _run_mixed_detect 주석
        참고) 낱개 조각 단위로 세밀하게 쪼개서 AUTO_STYLE과 같은 방식으로
        칼선을 만든다 -- 도무송으로 표시한 칸은 애초에 여기 넘기는 목록에
        없으므로, 그 칸 내부 조각이 여기서 또 따로 쪼개져 칼선이 겹치는
        일이 구조적으로 없다."""
        path = self._mixed_generate_common_checks()
        if not path:
            return
        to_process = [
            gi for gi in range(len(self._mixed_groups))
            if gi not in self._mixed_marked and gi not in self._mixed_processed
        ]
        if not to_process:
            self._show_note_dialog(
                "생성할 무테/유테 도안이 없습니다",
                ["표시 안 한 도안이 없거나, 이미 모두 생성된 상태입니다."],
                kind="error",
            )
            return
        self.generate_btn.configure(state="disabled")
        self.status.set(f"나머지 무테/유테 칸 {len(to_process)}개를 세부 조각까지 분석해 생성하는 중...")
        threading.Thread(
            target=self._run_mixed_generate_rest_fine, args=(path, to_process), daemon=True
        ).start()

    def _run_mixed_generate_rest_fine(self, path, cell_indices):
        """"③" 전용 생성 루프(2026-09-26 신설). cell_indices에 담긴 칸들
        (도무송으로 마킹 안 된 칸)만 모아 그 좌표들로
        detect_repeat_aware_sub_element_boxes_px를 호출해 이제야 낱개 조각
        단위로 쪼갠다 -- 도무송으로 마킹된 칸의 좌표는 이 목록에 아예 안
        들어있으므로, ②에서 이미 통짜로 처리된 도무송 카드 내부 조각이
        여기서 또 나뉘어 겹친 칼선이 생기는 문제(_run_mixed_detect 주석의
        "카드 하나가 15개 조각으로" 문제)가 원천적으로 발생하지 않는다.
        완료/누적/최종 결과 전환 처리는 _run_mixed_generate_subset과 같은
        구조를 그대로 따른다(self._mixed_processed에 칸 단위 gi를 기록해
        ②/③ 어느 쪽에서 진행하든 전체 완료 여부를 함께 판단)."""
        # 2026-09-28: 이 경로는 "의심 영역 경고"(suspicious_regions)를 아직
        # 계산하지 않으므로, 직전 실행(예: "자동으로 여러 개 인식 + 추가")에서
        # 남아있던 좌표가 이번 결과의 경고 클릭-이동 기능에 잘못 쓰이지
        # 않도록 비워둔다 -- _run_auto_detect_and_add_all 참고.
        self._last_suspicious_regions_px = []
        cell_boxes_px = self._mixed_boxes
        cell_groups = self._mixed_groups
        remaining_flat = [
            cell_boxes_px[idx] for gi in cell_indices for idx in cell_groups[gi]
        ]
        try:
            fine_boxes, fine_groups = detect_repeat_aware_sub_element_boxes_px(path, remaining_flat)
        except Exception as e:  # noqa: BLE001
            self._report_exception_to_server("나머지 무테/유테 세부 분석 중")
            self.after(0, self._on_error, str(e))
            for gi in cell_indices:
                self._mixed_processed.add(gi)
            self.after(0, lambda: self.generate_btn.configure(state="normal"))
            return
        # 도무송으로 표시된 칸(마킹 당시의 칸 단위 경계, self._mixed_cell_boxes_px)은
        # fine_boxes 목록에 없으므로, "이웃 중간지점까지 확장" 경계 계산에
        # 별도로 더해줘야 ③의 낱개 조각이 옆 도무송 칸을 침범하지 않는다
        # (2026-09-11(52차)의 옆 칸 침범 금지 원칙을 도무송/나머지 분리
        # 구조에도 그대로 유지).
        domusong_cell_boxes = [
            self._mixed_cell_boxes_px[idx]
            for gi in range(len(cell_groups))
            if gi in self._mixed_marked
            for idx in cell_groups[gi]
        ]
        neighbor_boxes_for_bounds = list(fine_boxes) + domusong_cell_boxes

        rest_start_index = len(self._accumulated)
        added = 0
        errors = []
        n_fine = len(fine_groups)
        with Image.open(path) as _im:
            image_size = _im.size
        for i, fgi in enumerate(fine_groups):
            primary_idx = fgi[0]
            x0, y0, x1, y1 = fine_boxes[primary_idx]
            selection_px = (x0, y0, x1, y1)
            self.after(
                0, self.status.set,
                f"나머지 무테/유테 세부 칼선 생성 중... ({i + 1}/{n_fine})",
            )
            try:
                style_bounds_px = expand_box_to_neighbor_midpoint_px(
                    selection_px, neighbor_boxes_for_bounds, image_size,
                )
                sibling_boxes_px = [b for j, b in enumerate(fine_boxes) if j != primary_idx]
                item_result = generate_cutline_auto(
                    image_path=path,
                    dpi=self.dpi.get(),
                    selection_px=selection_px,
                    bounds_px=style_bounds_px,
                    margin_mm=self.style_margin_mm.get(),
                    supersample=self.precision.get(),
                    sibling_boxes_px=sibling_boxes_px,
                )
                cell_area_px = max(0.0, (x1 - x0) * (y1 - y0))
                design_area_px = item_result.design.area if item_result.design is not None else 0.0
                if is_silhouette_undersized(
                    design_area_px, cell_area_px,
                    min_ratio=_UNDERSIZED_RATIO_FOR_FINE_SUBELEMENT,
                ):
                    fallback = generate_cutline_by_style(
                        image_path=path,
                        style=ImageStyle.BORDERLESS,
                        dpi=self.dpi.get(),
                        selection_px=selection_px,
                        bounds_px=style_bounds_px,
                        margin_mm=self.style_margin_mm.get(),
                        supersample=self.precision.get(),
                    )
                    ratio_pct = (design_area_px / cell_area_px * 100) if cell_area_px > 0 else 0.0
                    fallback.adjustments = list(item_result.adjustments or []) + list(
                        fallback.adjustments or []
                    ) + [_undersized_retry_note(ratio_pct, fallback.design)]
                    item_result = fallback
                self._accumulated.append(item_result)
                added += 1
            except Exception as e:  # noqa: BLE001
                self._report_exception_to_server("나머지 무테/유테 세부 처리 중")
                errors.append(f"{i + 1}번째 조각: {self._friendly_error_text(str(e))}")
                continue
            for other_idx in fgi[1:]:
                ox0, oy0, ox1, oy1 = fine_boxes[other_idx]
                try:
                    self._accumulated.append(
                        fit_cutline_result_to_box(
                            item_result, (x0, y0, x1, y1), (ox0, oy0, ox1, oy1),
                            note=(
                                "동일 도안이 반복되는 것으로 감지되어, 대표 인스턴스의 "
                                "칼선을 그 칸 크기에 맞춰 복제했습니다."
                            ),
                        )
                    )
                    added += 1
                except Exception as e:  # noqa: BLE001
                    self._report_exception_to_server("반복 도안 복제 처리 중")
                    errors.append(f"{other_idx + 1}번째 조각(반복 복제): {self._friendly_error_text(str(e))}")

        # 2026-09-28: ③에도 보조 탐지 자동 적용(놓친 요소만 추가, 흰/검은
        # 테두리가 있으면 유테, 없으면 무테 안쪽) -- 실제 손 칼선 대조 결과.
        try:
            added += self._supplement_missing_elements(
                path, rest_start_index, remaining_flat, auto_style=True,
            )
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        self._resolve_cut_conflicts(rest_start_index)

        for gi in cell_indices:
            self._mixed_processed.add(gi)
        self._selection_px = None
        self._mixed_added_count += added

        groups = self._mixed_groups
        all_done = len(self._mixed_processed) >= len(groups)
        if not all_done:
            n_remaining = len(groups) - len(self._mixed_processed)
            self.after(
                0, self.status.set,
                f"나머지 무테/유테 {added}개 영역 생성 완료(누적 {self._mixed_added_count}개 영역). "
                f"아직 칸 {n_remaining}개 남음 -- "
                "'② 표시한 도무송 칼선 생성'을 눌러 이어서 진행하세요.",
            )
            self.after(0, self.accum_status.set, f"누적 {len(self._accumulated)}개 영역")
            self.after(0, lambda: self.generate_btn.configure(state="normal"))
            if errors:
                self.after(
                    0, self._show_note_dialog,
                    "일부 도안 생성 실패", errors, "error",
                )
            return

        n_boxes_total = len(cell_boxes_px)
        added_total = self._mixed_added_count
        self._mixed_mode = False
        self._mixed_boxes = []
        self._mixed_groups = []
        self._mixed_marked = set()
        self._mixed_rect_ids = {}
        self._mixed_hover_gi = None
        self._mixed_processed = set()
        self._mixed_added_count = 0

        if added_total == 0 and not self._accumulated:
            self.after(0, self._on_error, "모든 도안 처리에 실패했습니다.\n" + "\n".join(errors))
            return
        try:
            combined = combine_results(self._accumulated)
            preview_png = os.path.join(_work_file_dir(), "_last_preview.png")
            render_preview(combined, preview_png, original_image_path=path)
            self._last_result = combined
        except Exception as e:  # noqa: BLE001
            self._report_exception_to_server("칼선 생성/미리보기 처리 중")
            self.after(0, self._on_error, str(e))
            return
        self.after(0, self._show_auto_detect_result, preview_png, added_total, n_boxes_total, errors)

    def _run_mixed_generate_subset(self, path, group_indices):
        """group_indices에 담긴 그룹들만 생성한다 -- is_domusong 여부는
        매번 self._mixed_marked를 기준으로 다시 확인하므로(호출한 쪽이
        넘긴 값을 무조건 믿지 않음), ②/③ 어느 쪽에서 불렸든 항상 정확하다."""
        # 2026-09-28: 이 경로도 아직 "의심 영역 경고"를 계산하지 않으므로,
        # 이전 실행의 좌표가 이번 경고 클릭-이동 기능에 잘못 남지 않도록
        # 비워둔다 -- _run_auto_detect_and_add_all 참고.
        self._last_suspicious_regions_px = []
        groups = self._mixed_groups
        boxes = self._mixed_boxes
        added = 0
        errors = []
        n_subset = len(group_indices)
        for i, gi in enumerate(group_indices):
            group = groups[gi]
            primary_idx = group[0]
            x0, y0, x1, y1 = boxes[primary_idx]
            self._selection_px = (x0, y0, x1, y1)
            is_domusong = gi in self._mixed_marked
            self.after(
                0, self.status.set,
                f"칼선 생성 중... ({i + 1}/{n_subset}, "
                f"{'도무송' if is_domusong else '무테/유테'})",
            )
            try:
                if is_domusong:
                    offset_mm = OffsetSpec(
                        safety_mm=self.safety_mm.get(),
                        cut_mm=self.cut_mm.get(),
                        bleed_mm=self.bleed_mm.get(),
                    )
                    cutline_type = CutlineType[self.cutline_type.get()]
                    # 2026-09-07(7차) 피드백과 동일한 이유로, 옆 칸을 침범하지
                    # 않도록 경계를 넘긴다 -- 단, 2026-09-10(46차) 피드백
                    # ("도무송 칼선이 안쪽에 생성 안 됨")으로 확인된 대로
                    # "지금 칸 자체"를 그대로 쓰면 도무송의 최소 여유(40차 15mm
                    # -- 53차에 실제 인쇄소 가이드 실측으로 2.0mm로 조정됨)와
                    # 충돌해 칼선이 도안 경계에 겹쳐 사라져 보인다. 실제 이웃
                    # 칸까지의 중간 지점으로 넓혀, 이웃을 침범하지 않으면서도
                    # 여유가 있는 만큼은 확보한다.
                    # 2026-09-11(52차) 피드백("도무송 칼선이 밀리고 중심을
                    # 벗어나"): 여기서 "이웃"은 반드시 다른 패널(칸) 단위여야
                    # 한다 -- boxes(51차 이후 조각 단위)를 그대로 쓰면 같은
                    # 패널 안 다른 조각(예: 바로 옆 하트)까지 이웃으로 잡혀
                    # 여유 계산이 한쪽으로 치우치는 회귀가 생겼다.
                    with Image.open(path) as _im:
                        domusong_bounds_px = expand_box_to_neighbor_midpoint_px(
                            self._selection_px,
                            getattr(self, "_mixed_cell_boxes_px", None) or boxes,
                            _im.size,
                        )
                    # 2026-09-28: 표시한 칸의 이미지 안쪽에 도형(위 수동 도무송과
                    # 같은 규칙) -- domusong_bounds_px는 더 이상 필요 없지만
                    # (칼선이 이미지 밖으로 안 나감) 계산은 그대로 둔다.
                    item_result = generate_domusong_inside_cutline(
                        image_path=path,
                        region_px=self._selection_px,
                        cutline_type=cutline_type,
                        dpi=self.dpi.get(),
                        offset_mm=offset_mm,
                    )
                    # 2026-09-10(34차) 피드백과 동일: 도무송 직접 표시는
                    # 칼선/블리딩 두 선만 있으면 되고 세이프티(초록)는 화면/
                    # 내보내기에서 뺀다(최소 간격 계산에는 이미 반영된 뒤라
                    # 안전).
                    item_result.offsets.pop("safety", None)
                else:
                    # 2026-09-14(실제 파일로 발견, "칼선이 개체를 안 둘러싸고
                    # 여러 개체를 휘감는다"): 도무송(위 if 분기)은 이미
                    # expand_box_to_neighbor_midpoint_px로 "옆 조각을 침범하지
                    # 않는 한계"를 bounds_px로 넘기는데, 이 무테/유테 분기는
                    # bounds_px를 아예 넘기지 않아 generate_cutline_auto ->
                    # generate_style_cutline(LINE_ART)이 기본값(이미지 전체)을
                    # 썼다. 그 결과 _grow_design_into_low_contrast_halo_px의
                    # 저대비 헤일로 확장이 자기 조각 박스를 넘어 옆 캐릭터
                    # 영역까지 자유롭게 번져도 막을 경계가 전혀 없었다(실측:
                    # 세트5 파일에서 곰+고양이+체크무늬 장식 3개가 폴리곤
                    # 1개로 뭉쳐 나옴).
                    #
                    # 여기 겸사겸사 넣은 expand_box_to_neighbor_midpoint_px
                    # bounds_px는 시트 가장자리 안전망일 뿐 -- 그 함수는 원래
                    # "겹치지 않는 격자 칸"용이라, 실제로는 서로 다른 캐릭터의
                    # bbox끼리 겹치는 경우(실측: 이번 문제 사례)를 놓친다.
                    # 진짜 안전장치는 sibling_boxes_px -- 같은 칸 안의 다른
                    # 낱개 요소 박스들을 직접 넘겨서, _grow_design_into_low_
                    # contrast_halo_px가 그 박스 영역으로는 색이 halo처럼
                    # 보여도 절대 편입하지 않게 막는다(core.image_style 문서
                    # 참고, 실측으로 이 방식만 실제로 효과 있음을 확인).
                    with Image.open(path) as _im:
                        style_bounds_px = expand_box_to_neighbor_midpoint_px(
                            self._selection_px, boxes, _im.size,
                        )
                    sibling_boxes_px = [b for j, b in enumerate(boxes) if j != primary_idx]
                    item_result = generate_cutline_auto(
                        image_path=path,
                        dpi=self.dpi.get(),
                        selection_px=self._selection_px,
                        bounds_px=style_bounds_px,
                        margin_mm=self.style_margin_mm.get(),
                        supersample=self.precision.get(),
                        sibling_boxes_px=sibling_boxes_px,
                    )
                    cell_area_px = max(0.0, (x1 - x0) * (y1 - y0))
                    design_area_px = item_result.design.area if item_result.design is not None else 0.0
                    if is_silhouette_undersized(
                        design_area_px, cell_area_px,
                        min_ratio=_UNDERSIZED_RATIO_FOR_FINE_SUBELEMENT,
                    ):
                        fallback = generate_cutline_by_style(
                            image_path=path,
                            style=ImageStyle.BORDERLESS,
                            dpi=self.dpi.get(),
                            selection_px=self._selection_px,
                            bounds_px=style_bounds_px,
                            margin_mm=self.style_margin_mm.get(),
                            supersample=self.precision.get(),
                        )
                        ratio_pct = (design_area_px / cell_area_px * 100) if cell_area_px > 0 else 0.0
                        fallback.adjustments = list(item_result.adjustments or []) + list(
                            fallback.adjustments or []
                        ) + [_undersized_retry_note(ratio_pct, fallback.design)]
                        item_result = fallback
                self._accumulated.append(item_result)
                added += 1
            except Exception as e:  # noqa: BLE001
                self._report_exception_to_server("자동 인식 배치 처리 중")
                errors.append(f"{gi + 1}번째 도안: {self._friendly_error_text(str(e))}")
                continue
            finally:
                self._mixed_processed.add(gi)
            # 같은 그룹의 나머지 반복 인스턴스는 다시 추적하지 않고, 대표
            # 인스턴스 결과를 그대로 복제한다 -- 단, 2026-09-10(48차)
            # 피드백("칼선이 겹치거나 뭉쳐")으로 확인된 대로 반복 칸끼리
            # 크기가 몇 px 다를 수 있어(group_identical_boxes_px가 최대
            # 8px 차이까지 "같은 반복"으로 허용) 순수 평행이동만 하면 대상
            # 칸을 벗어나거나 안 맞게 남을 수 있다 -- 대표 칸(x0,y0,x1,y1)을
            # 각 대상 칸에 정확히 맞춰 늘이고 옮기는 fit_cutline_result_to_box로
            # 교체.
            for other_idx in group[1:]:
                ox0, oy0, ox1, oy1 = boxes[other_idx]
                try:
                    self._accumulated.append(
                        fit_cutline_result_to_box(
                            item_result, (x0, y0, x1, y1), (ox0, oy0, ox1, oy1),
                            note=(
                                "동일 도안이 반복되는 것으로 감지되어, 대표 인스턴스의 "
                                "칼선을 그 칸 크기에 맞춰 복제했습니다."
                            ),
                        )
                    )
                    added += 1
                except Exception as e:  # noqa: BLE001
                    self._report_exception_to_server("반복 도안 복제 처리 중")
                    errors.append(f"{other_idx + 1}번째 도안(반복 복제): {self._friendly_error_text(str(e))}")
        self._selection_px = None
        self._mixed_added_count += added

        all_done = len(self._mixed_processed) >= len(groups)
        if not all_done:
            # 2026-09-10(36차 이어서): 아직 다른 버튼으로 처리할 그룹이
            # 남아있으면, 화면(표시해 둔 파란/빨간 테두리)은 그대로 두고
            # 상태 메시지와 누적 개수만 갱신한다 -- 결과 미리보기로 전환해
            # 버리면 아직 안 만든 나머지를 표시할 화면 자체가 사라진다.
            n_remaining = len(groups) - len(self._mixed_processed)
            self.after(
                0, self.status.set,
                f"{n_subset}개 생성 완료(이번 표시 작업에서 누적 {self._mixed_added_count}개 영역). "
                f"아직 {n_remaining}개 남음 -- "
                + ("'③ 남은 칼선 생성'을 눌러 이어서 진행하세요."
                   if any(gi not in self._mixed_marked for gi in range(len(groups)) if gi not in self._mixed_processed)
                   else "'② 표시한 도무송 칼선 생성'을 눌러 이어서 진행하세요."),
            )
            self.after(0, self.accum_status.set, f"누적 {len(self._accumulated)}개 영역")
            self.after(0, lambda: self.generate_btn.configure(state="normal"))
            if errors:
                self.after(
                    0, self._show_note_dialog,
                    "일부 도안 생성 실패", errors, "error",
                )
            return

        # 모든 그룹이 끝났으면(둘 중 어느 버튼이 마지막이었든) 이제 결과
        # 미리보기로 전환하고 표시 상태를 정리한다.
        n_boxes_total = len(boxes)
        added_total = self._mixed_added_count
        self._mixed_mode = False
        self._mixed_boxes = []
        self._mixed_groups = []
        self._mixed_marked = set()
        self._mixed_rect_ids = {}
        self._mixed_hover_gi = None
        self._mixed_processed = set()
        self._mixed_added_count = 0

        if added_total == 0 and not self._accumulated:
            self.after(0, self._on_error, "모든 도안 처리에 실패했습니다.\n" + "\n".join(errors))
            return
        try:
            combined = combine_results(self._accumulated)
            preview_png = os.path.join(_work_file_dir(), "_last_preview.png")
            render_preview(combined, preview_png, original_image_path=path)
            self._last_result = combined
        except Exception as e:  # noqa: BLE001
            self._report_exception_to_server("칼선 생성/미리보기 처리 중")
            self.after(0, self._on_error, str(e))
            return
        self.after(0, self._show_auto_detect_result, preview_png, added_total, n_boxes_total, errors)

    def _validate_inputs(self):
        # 2026-09-07 피드백("팝업 안 보였어"): 아래 검증 경고들은 원래 OS
        # 기본 tkinter.messagebox였는데, 사용자 환경에서 메인 창과 무관한
        # 위치에 떠서 실제로는 안 보였던 것으로 추정됨 -- 메인 창 위 확실한
        # 위치에 뜨는 것이 확인된 자체 대화상자(_show_note_dialog /
        # _show_confirm_dialog)로 교체.
        path = self.input_path.get().strip()
        if not path or not os.path.isfile(path):
            self._show_note_dialog("먼저 도안 파일을 선택하세요", ["먼저 유효한 도안 파일을 선택하세요."], kind="error")
            return None
        if not self.job_type.get():
            self._show_note_dialog(
                "먼저 작업 종류를 선택하세요",
                ["먼저 '작업 종류'를 선택하세요 (무테/유테/도무송/조각 스티커/마스킹테이프)."],
                kind="error",
            )
            return None
        if self.job_type.get() in ("BORDERLESS", "LINE_ART", "AUTO_STYLE"):
            # 무테/유테(+자동 판단)는 세이프티·칼선·블리딩을 아예 쓰지 않고
            # style_margin_mm 하나만 쓰므로, 그 셋의 순서 검사는 조각 스티커/도무송
            # 잡타입일 때만 의미가 있음.
            return path
        vals = {
            "safety": self.safety_mm.get(),
            "cut": self.cut_mm.get(),
            "bleed": self.bleed_mm.get(),
        }
        if not (vals["safety"] < vals["cut"] < vals["bleed"]):
            proceed = self._show_confirm_dialog(
                "순서를 다시 확인해주세요",
                [
                    "일반적으로 세이프티 < 칼선 < 블리딩 순서로 커집니다.",
                    "현재 값은 이 순서가 아닙니다. 계속 진행할까요?",
                ],
            )
            if not proceed:
                return None
        return path

    def _on_generate(self):
        path = self._validate_inputs()
        if not path:
            return
        self.generate_btn.configure(state="disabled")
        self.status.set("처리 중...")
        threading.Thread(target=self._run_generate, args=(path,), daemon=True).start()

    def _generate_one_item(self, path, bounds_px_override=None):
        """
        Build ONE CutlineResult from the CURRENT job type + sub-option +
        (if any) drag-selection -- does NOT touch self._accumulated. Called
        once per "이 영역 추가" press; the caller appends the result and
        recombines (see _run_generate).

        `bounds_px_override`: 2026-09-07(7차) 피드백("도무송은 모두 이미지
        안쪽으로 칼선이 들어가야해"): 도무송(재단 도형)이나 완칼은 실제
        선택 영역(칸) 밖으로 칼선이 넘어가면 안 되는데, 기본값은 "전체
        이미지" 경계까지만 막아준다 -- 자동 인식으로 한 시트 안에 여러
        칸이 나란히 있을 때는 그것만으로는 부족해서(전체 이미지 안이기만
        하면 옆 칸까지 넘어가도 안 걸러짐), 자동 인식 배치 처리
        (_generate_one_item_for_auto_detect)는 이 값에 "지금 처리 중인 그
        칸 자체"의 좌표를 넘겨서 그 칸을 절대 벗어나지 못하게 한다. 수동으로
        하나씩 작업할 때(None)는 기존과 동일하게 전체 이미지 경계까지만
        적용된다."""
        job = self.job_type.get()

        if job == "MIXED_AUTO":
            # 2026-09-10(35/36차 이어서) 피드백: "무테/유테+도무송"는
            # 도안마다 다른 처리(도무송 vs 무테/유테)가 필요해서 한 번의
            # 드래그 선택 하나만으로는 의미가 없다 -- 전용 버튼(① 도안 자동
            # 인식, ② 표시한 도무송 칼선 생성, ③ 나머지 무테/유테 칼선 생성 --
            # _on_mixed_detect/_on_mixed_generate_domusong/_on_mixed_generate_rest)
            # 으로만 만들 수 있고, 기존 "이 영역 추가"(_on_generate/_run_generate)
            # 로는 만들 수 없음을 명확히 안내.
            raise ValueError(
                "'무테/유테+도무송'는 이 버튼으로 만들 수 없습니다 -- "
                "'① 도안 자동 인식', '② 표시한 도무송 칼선 생성', "
                "'③ 남은 칼선 생성' 버튼을 순서대로 사용하세요."
            )

        if self.is_vector.get():
            # SVG 입력은 이 프로토타입에서 드래그 선택(스티커/도무송 세부
            # 요소 선택)을 지원하지 않음 -- 항상 벡터 전체를 조각 스티커처럼 트레이싱.
            offset_mm = OffsetSpec(
                safety_mm=self.safety_mm.get(),
                cut_mm=self.cut_mm.get(),
                bleed_mm=self.bleed_mm.get(),
            )
            return generate_cutlines(
                image_path=path,
                is_vector=True,
                dpi=self.dpi.get(),
                offset_mm=offset_mm,
                supersample=self.precision.get(),
            )

        if job == "FULL_CUT":
            if self._selection_px is not None:
                offset_mm = OffsetSpec(
                    safety_mm=self.safety_mm.get(),
                    cut_mm=self.cut_mm.get(),
                    bleed_mm=self.bleed_mm.get(),
                )
                return generate_cutline_for_selection(
                    image_path=path,
                    selection_px=self._selection_px,
                    cutline_type=CutlineType.FULL_CUT,
                    dpi=self.dpi.get(),
                    offset_mm=offset_mm,
                    use_grabcut=True,
                    supersample=self.precision.get(),
                    bounds_px=bounds_px_override,
                )
            offset_mm = OffsetSpec(
                safety_mm=self.safety_mm.get(),
                cut_mm=self.cut_mm.get(),
                bleed_mm=self.bleed_mm.get(),
            )
            return generate_cutlines(
                image_path=path,
                is_vector=False,
                dpi=self.dpi.get(),
                offset_mm=offset_mm,
                supersample=self.precision.get(),
            )

        if job == "AUTO_STYLE":
            # 2026-09-10 피드백("칼선 종류 선택을 2개 이상 선택 가능하게 해줘 --
            # 도안별로 알맞은 종류를 판단해서 순서대로 자동 적용, 도무송은
            # 따로"): 무테/유테 둘 중 어느 쪽인지를 이 선택 영역의 실제 내용
            # (사각형도)으로 판단하는, core에 이미 실제 파일 2개로 검증된 채로
            # 있던 generate_cutline_auto를 그대로 연결한다 -- 도무송은 이
            # 판단 대상에 포함하지 않음(그림만 보고는 알 수 없는 비즈니스
            # 결정이라 여전히 "도무송"을 따로 골라야 함).
            if self._selection_px is None:
                raise ValueError(
                    "무테+유테 자동 생성은 도안 안 요소 하나를 드래그로 지정해야 합니다 -- "
                    "오른쪽 이미지에서 영역을 먼저 선택하세요."
                )
            return generate_cutline_auto(
                image_path=path,
                dpi=self.dpi.get(),
                selection_px=self._selection_px,
                bounds_px=bounds_px_override,
                margin_mm=self.style_margin_mm.get(),
                supersample=self.precision.get(),
            )

        if job in ("BORDERLESS", "LINE_ART", "MASKING_TAPE"):
            # 2026-09-07 피드백에 따라 무테/유테는 이제 "작업 종류"에서 바로
            # 고르는 값이라, 여기서 별도의 image_style/AUTO 판단은 하지 않고
            # job 값 그대로 스타일로 사용한다.
            # 2026-09-08(9차): 마스킹테이프의 개별 모티프는 실제 키스컷 마테
            # 참고 파일에서 확인한 대로 항상 유테(선화)와 완전히 같은 방식
            # (실루엣을 촘촘하고 일정하게 따라가는 컷)이라, ImageStyle에는
            # 별도 항목을 추가하지 않고 여기서 LINE_ART로 명시적으로
            # 매핑한다(ImageStyle[job]을 그대로 쓰면 "MASKING_TAPE"라는
            # 이름의 존재하지 않는 항목을 찾다가 오류가 난다).
            if self._selection_px is None:
                raise ValueError(
                    "무테/유테/마스킹테이프는 도안 안 요소 하나를 드래그로 지정해야 합니다 -- "
                    "오른쪽 이미지에서 영역을 먼저 선택하세요."
                )
            style = ImageStyle.LINE_ART if job == "MASKING_TAPE" else ImageStyle[job]
            return generate_cutline_by_style(
                image_path=path,
                style=style,
                dpi=self.dpi.get(),
                selection_px=self._selection_px,
                margin_mm=self.style_margin_mm.get(),
                supersample=self.precision.get(),
            )

        if job != "DOMUSONG":
            # 2026-08-31 안전장치: job_type이 빈 값("")이거나 알 수 없는
            # 값이면 예전처럼 조용히 도무송으로 취급하지 않고 명확히 알림
            # -- _validate_inputs에서 이미 막아주지만, 혹시 몰라 한 번 더.
            raise ValueError("작업 종류를 먼저 선택하세요 (무테/유테/도무송/조각 스티커/마스킹테이프).")

        if self._selection_px is None:
            raise ValueError(
                "도무송은 재단할 영역을 드래그로 지정해야 합니다 -- 오른쪽 "
                "이미지에서 영역을 먼저 선택하세요."
            )
        offset_mm = OffsetSpec(
            safety_mm=self.safety_mm.get(),
            cut_mm=self.cut_mm.get(),
            bleed_mm=self.bleed_mm.get(),
        )
        if not self.cutline_type.get():
            raise ValueError("먼저 '재단 도형'을 선택하세요.")
        cutline_type = CutlineType[self.cutline_type.get()]
        # 2026-09-28 멍푸: "도무송 직접 선택 후 칼선 종류에 따라 이미지 안쪽으로
        # 칼선 생성" -- 선택한 영역 안 실제 이미지 외곽을 기준으로, 고른
        # 도형을 이미지 *안쪽*에 넣는다(칼선·블리딩 모두 이미지 안).
        # 수동 드래그와 자동 인식 둘 다 이 경로를 탄다.
        result = generate_domusong_inside_cutline(
            image_path=path,
            region_px=self._selection_px,
            cutline_type=cutline_type,
            dpi=self.dpi.get(),
            offset_mm=offset_mm,
        )
        # 2026-09-10 피드백("안쪽의 초록 선은 필요없어"): 사용자가 화면에서
        # 직접 "재단 도형 선택"으로 도무송을 만들 때는 칼선/블리딩 두 선만
        # 있으면 되고, 가장 안쪽 세이프티(초록) 선은 필요 없다고 명확히
        # 확인함. enforce_minimum_gap()은 이미 세이프티를 기준으로 세 선의
        # 최소 간격을 계산해 칼선/블리딩 위치에 반영한 뒤이므로, 여기서
        # 세이프티 키만 지우는 건 그 계산 결과에 전혀 영향을 주지 않는다 --
        # 화면/내보내기에 안 보이게 할 뿐. 롤 전체 테두리(마스킹테이프,
        # _run_add_roll_border)는 실제 참고 파일에 세이프티까지 포함된 3단
        # 구조가 확인되어 있어 그쪽은 그대로 두고, 여기(직접 도무송 선택)만
        # 다르게 처리한다.
        result.offsets.pop("safety", None)
        return result

    def _maybe_generate_extra_borderless(self, path):
        """2026-09-07 다중선택 기능: job_type이 도무송이고
        self.domusong_also_borderless 체크박스가 켜져 있으며 현재
        selection_px가 있으면, 같은 영역에 대해 무테(BORDERLESS) 스타일
        칼선도 하나 더 만들어 돌려준다(없으면 None). 도무송 칼선 자체의
        생성/실패 여부와는 독립적으로 호출하는 쪽에서 별도 처리."""
        if self.job_type.get() != "DOMUSONG":
            return None
        if not self.domusong_also_borderless.get():
            return None
        if self._selection_px is None:
            return None
        return generate_cutline_by_style(
            image_path=path,
            style=ImageStyle.BORDERLESS,
            dpi=self.dpi.get(),
            selection_px=self._selection_px,
            margin_mm=self.style_margin_mm.get(),
            supersample=self.precision.get(),
        )

    def _on_add_roll_border(self):
        """2026-09-08(9차) 피드백("칼선 종류에 키스컷 마스킹 테이프도
        추가해", "연속된 롤(시트) 전체를 고려해야 함"): 실제 "키스컷 마테
        좋은예/나쁜예" 참고 파일(스크래치패드에서만 열어 확인, 실제 내용은
        저장/커밋하지 않음)의 칼선 레이어를 보면, 개별 모티프 키스컷과는
        완전히 별개로 롤 전체 폭을 정의하는 세이프티/칼선/블리딩 3단
        테두리 선이 한 번 더 그려져 있었다. 개별 모티프는 "자동으로 여러
        개 인식 + 추가"(유테와 동일)로 이미 처리되므로, 이 버튼은 그
        나머지 절반(롤 전체 테두리)을 한 번 추가한다."""
        path = self._validate_inputs()
        if not path:
            return
        if self.job_type.get() != "MASKING_TAPE":
            self._show_note_dialog(
                "마스킹테이프에서만 사용할 수 있습니다",
                ["이 버튼은 작업 종류가 '키스컷 마스킹테이프'일 때만 사용할 수 있습니다."],
                kind="error",
            )
            return
        if self.is_vector.get() or Image is None:
            self._show_note_dialog(
                "이 파일에는 사용할 수 없습니다",
                ["롤 전체 테두리 추가는 PNG/JPG 같은 래스터 도안에서만 지원합니다."],
                kind="error",
            )
            return
        self.generate_btn.configure(state="disabled")
        self.status.set("롤 전체 테두리를 만드는 중...")
        threading.Thread(target=self._run_add_roll_border, args=(path,), daemon=True).start()

    def _run_add_roll_border(self, path):
        try:
            with Image.open(path) as im:
                w, h = im.size
            # 롤 전체에서 "실제로 인쇄된 내용"이 어디까지인지 싼 값으로
            # 먼저 재본다(core.image_style._measure_content_bbox_px --
            # GrabCut 없이 알파/배경색 거리 기준, 이미 이 프로젝트가 무테
            # 안전 체크에 쓰던 것과 동일한 함수) -- 그래야 이미지 캔버스
            # 자체의 여백까지 다 포함해서 테두리를 그리지 않고, 실제 인쇄
            # 영역 기준으로 세이프티/칼선/블리딩이 계산된다(참고 파일에서
            # 확인한 실제 구조와 동일).
            content_bbox = _measure_content_bbox_px(path, (0, 0, w, h))
            extra_note = None
            if content_bbox is None:
                content_bbox = (0, 0, w, h)
                extra_note = (
                    "롤 안 도안 내용의 경계를 자동으로 찾지 못해, 이미지 전체 경계를 "
                    "기준으로 테두리를 만들었습니다."
                )
            offset_mm = OffsetSpec(
                safety_mm=self.safety_mm.get(),
                cut_mm=self.cut_mm.get(),
                bleed_mm=self.bleed_mm.get(),
            )
            # cutline_type=RECTANGLE + use_grabcut=False: 실루엣을 추적하지
            # 않고 content_bbox 그대로를 사각형으로 쓴다(개별 모티프처럼
            # 실루엣을 따라갈 필요가 없는, 롤 자체의 폭을 정의하는 선이므로).
            # bounds_px를 이미지 전체로 둬서, 블리딩이 실제 인쇄 캔버스
            # 밖으로는 못 나가게 막는다(다른 모든 오프셋과 동일한 안전
            # 규칙).
            item_result = generate_cutline_for_selection(
                image_path=path,
                selection_px=content_bbox,
                cutline_type=CutlineType.RECTANGLE,
                dpi=self.dpi.get(),
                offset_mm=offset_mm,
                use_grabcut=False,
                bounds_px=(0, 0, w, h),
                supersample=self.precision.get(),
            )
            if extra_note:
                item_result.adjustments = list(item_result.adjustments or []) + [extra_note]
            self._accumulated.append(item_result)
            combined = combine_results(self._accumulated)
            preview_png = os.path.join(_work_file_dir(), "_last_preview.png")
            render_preview(
                combined, preview_png,
                original_image_path=None if self.is_vector.get() else path,
            )
            self._last_result = combined
            self.after(0, self._show_preview, preview_png, item_result)
        except Exception as e:  # noqa: BLE001
            self._report_exception_to_server("칼선 생성/미리보기 처리 중")
            self.after(0, self._on_error, str(e))

    def _grid_cell_containing(self, box_px):
        """요소 박스 중심이 들어 있는 실제 칸(.ai 격자) -- 없으면 None."""
        cells = getattr(self, "_real_grid_cells_px", None) or []
        if box_px is None or not cells:
            return None
        cx, cy = (box_px[0] + box_px[2]) / 2.0, (box_px[1] + box_px[3]) / 2.0
        for c in cells:
            if c[0] <= cx <= c[2] and c[1] <= cy <= c[3]:
                return tuple(c)
        return None

    def _generate_one_item_for_auto_detect(self, path):
        """자동 인식(_run_auto_detect_and_add_all)으로 찾은 영역 전용 생성
        경로.

        2026-09-07(1차) 피드백("이미지 라인을 기준으로 칼선을 제작하는게
        아니라 직사각형으로 인식하고 있어")로, 자동 인식이 찾아낸 영역은
        정의상 배경과 눈에 띄게 떨어진 "독립된 도안"이라고 보고 무테가
        선택돼 있어도 유테와 똑같이 실루엣을 추적(GrabCut)하도록 강제한다.

        2026-09-08 피드백("재단선 인식이 아닌 재단선 안의 스티커 파츠를
        칼선을 제작해야지", "그 안의 스티커의 요소를 칼선으로 만들어야지.
        왜 자꾸 사각형으로 인지해")로 명확해진 것: self._real_grid_cells_px
        (작가가 실제로 그려놓은 재단선 격자)가 있을 때도 그 칸 자체를
        그대로 사각형으로 잘라버리면 안 된다 -- 격자는 어디에 스티커가
        각각 놓여 있는지 그 "배열/위치"를 실제 데이터로 정확히 알려주는
        것일 뿐, 그 칸 안에 실제로 그려진 스티커 요소의 모양은 여전히
        따로 찾아서 그 모양대로 칼선을 만들어야 한다. 이전 버전은 격자가
        있으면 사용자가 무테/유테/도무송 중 무엇을 골랐는지와 상관없이
        무조건 사각형(무테 간격 컷)으로 강제해버리는 버그가 있었다(도무송을
        골라도 무시하고 사각형 처리 -- 도무송 세부 옵션 화면이 떠 있는데도
        "무테 칼선 경고" 팝업이 뜬 게 바로 이 버그의 증거). 지금부터는
        격자가 있든 없든(즉 self._real_grid_cells_px 유무와 무관하게)
        "어디에 도안이 있는지"만 다르게 정하고(격자가 있으면 그 실제 칸을,
        없으면 detect_design_bboxes_px로 찾은 칸을), "그 칸 안의 실제
        스티커 모양을 어떻게 자를지"는 아래처럼 항상 똑같이 사용자가 고른
        작업 종류(job_type)를 그대로 따른다:
          - 무테/유테(BORDERLESS/LINE_ART) 선택 시: 칸 경계를 그대로 잘라
            먹지 않고, 그 칸 안에서 실제 스티커 실루엣을 추적(GrabCut)해
            그 모양대로 칼선을 만든다(유테와 동일한 방식 -- 무테라고 해서
            사각형으로 자르지 않는다).
          - 도무송(DOMUSONG) 선택 시: 사용자가 고른 재단 도형으로 자른다
            (기존 _generate_one_item 그대로).
          - 조각 스티커(FULL_CUT) 선택 시: 실제 실루엣을 그대로 따라간다.

        2026-09-08(2차) 피드백("햄스터를 칼선을 따야 하는데 햄스터 배를
        동그랗게 칼선을 생성했어"): 위 실루엣 추적(GrabCut)이 빽빽한 반복
        패턴 시트에서는 캐릭터 전체가 아니라 몸 안의 색 대비가 가장 강한
        작은 부분(배 무늬 등)만 잘못 잡는 경우가 실제로 확인됐다 -- 이건
        core.interactive_cutline.generate_cutline_from_known_silhouette의
        문서에도 이미 기록돼 있는, 이전 세션에서도 한 번 확인됐던 GrabCut의
        근본적인 한계라("sure foreground" 시드를 중심에 둬도 똑같이
        실패했었음) 시딩을 더 정교하게 하는 식으로는 못 고친다. 대신,
        결과가 명백히 잘못됐을 때(선택 영역의 극히 일부만 실루엣으로
        잡혔을 때)를 감지해서 더 안전한 사각형(무테 방식) 컷으로 자동
        대체한다 -- is_silhouette_undersized 참고."""
        job = self.job_type.get()
        if job == "AUTO_STYLE":
            # 2026-09-10 피드백: 무테+유테 자동 판단도 자동 인식 배치 경로에서는
            # 바로 위 BORDERLESS/LINE_ART와 똑같은 안전장치(실루엣이 선택 영역의
            # 극히 일부만 잡히면 안전한 사각형 컷으로 자동 대체, is_silhouette_
            # undersized)를 그대로 적용해야 한다 -- generate_cutline_auto는
            # 어느 스타일이 골라지든 그 스타일로 바로 실루엣을 추적하므로, 유테로
            # 골라졌을 때 이 추적이 실패하는 경우까지 똑같이 대비해야 하기 때문.
            if self._selection_px is None:
                raise ValueError("자동 인식된 영역이 없습니다.")
            result = generate_cutline_auto(
                image_path=path,
                dpi=self.dpi.get(),
                selection_px=self._selection_px,
                margin_mm=self.style_margin_mm.get(),
                supersample=self.precision.get(),
                sibling_boxes_px=list(getattr(self, "_auto_detect_sibling_boxes_px", None) or []),
                art_region_px=self._grid_cell_containing(self._selection_px),
            )
            x0, y0, x1, y1 = self._selection_px
            cell_area_px = max(0.0, (x1 - x0) * (y1 - y0))
            design_area_px = result.design.area if result.design is not None else 0.0
            if is_silhouette_undersized(design_area_px, cell_area_px):
                fallback = generate_cutline_by_style(
                    image_path=path,
                    style=ImageStyle.BORDERLESS,
                    dpi=self.dpi.get(),
                    selection_px=self._selection_px,
                    margin_mm=self.style_margin_mm.get(),
                    supersample=self.precision.get(),
                )
                ratio_pct = (design_area_px / cell_area_px * 100) if cell_area_px > 0 else 0.0
                fallback.adjustments = list(result.adjustments or []) + list(fallback.adjustments or []) + [
                    _undersized_retry_note(ratio_pct, fallback.design)
                ]
                return fallback
            return result

        if job == "BORDERLESS":
            # 2026-09-28(멍푸: "무테는 이미지 안쪽에 칼선이 들어간다", "배경색이
            # 칼선으로 잡히면 안 됨", "테스트 칼선 보면서 대조해"): 예전(9/7)엔
            # 무테를 골라도 요소를 유테처럼 바깥으로 밀어 배경까지 잘랐다.
            # 테스트 폴더의 실제 손 칼선을 읽어 대조해보니, 무테 시트는 칸 안
            # 요소(캐릭터·소품)마다 칼선이 따로 있고 모두 요소 실루엣에서 약
            # 0.7~1.5mm *안쪽*이었다. 그래서 요소마다 GrabCut 실루엣을 찾아
            # 그림 안쪽으로 여백만큼 줄인다(core.image_style의
            # _borderless_inward_from_silhouette -- sibling_boxes_px가 이
            # 경로의 신호). 배경은 절대 스티커에 들어가지 않는다.
            if self._selection_px is None:
                raise ValueError("자동 인식된 영역이 없습니다.")
            return generate_cutline_by_style(
                image_path=path,
                style=ImageStyle.BORDERLESS,
                dpi=self.dpi.get(),
                selection_px=self._selection_px,
                margin_mm=self.style_margin_mm.get(),
                supersample=self.precision.get(),
                sibling_boxes_px=list(getattr(self, "_auto_detect_sibling_boxes_px", None) or []),
                art_region_px=self._grid_cell_containing(self._selection_px),
            )

        if job in ("LINE_ART", "MASKING_TAPE"):
            # 2026-09-08(9차): 마스킹테이프도 자동 인식에서는 유테와 완전히
            # 같은 방식(실루엣 추적)으로 처리한다 -- 아래 로직은 이미 항상
            # ImageStyle.LINE_ART를 쓰므로 이 조건에 추가하는 것만으로 충분.
            if self._selection_px is None:
                raise ValueError("자동 인식된 영역이 없습니다.")
            result = generate_cutline_by_style(
                image_path=path,
                style=ImageStyle.LINE_ART,
                dpi=self.dpi.get(),
                selection_px=self._selection_px,
                margin_mm=self.style_margin_mm.get(),
                supersample=self.precision.get(),
            )
            x0, y0, x1, y1 = self._selection_px
            cell_area_px = max(0.0, (x1 - x0) * (y1 - y0))
            design_area_px = result.design.area if result.design is not None else 0.0
            if is_silhouette_undersized(design_area_px, cell_area_px):
                fallback = generate_cutline_by_style(
                    image_path=path,
                    style=ImageStyle.BORDERLESS,
                    dpi=self.dpi.get(),
                    selection_px=self._selection_px,
                    margin_mm=self.style_margin_mm.get(),
                    supersample=self.precision.get(),
                )
                ratio_pct = (design_area_px / cell_area_px * 100) if cell_area_px > 0 else 0.0
                fallback.adjustments = list(fallback.adjustments or []) + [
                    _undersized_retry_note(ratio_pct, fallback.design)
                ]
                return fallback
            return result
        # 2026-09-07(7차) 피드백("도무송은 모두 이미지 안쪽으로 칼선이
        # 들어가야해"): 도무송/완칼은 옆 칸을 침범하지 못하도록 경계를
        # 넘긴다(수동 단일 작업에서는 이 함수를 거치지 않으므로 영향 없음).
        # 2026-09-10(46차) 피드백("도무송 칼선이 안쪽에 생성 안 됨")으로
        # 확인된 대로, "지금 칸 자체"를 그대로 쓰면 도무송의 최소 여유(40차
        # 15mm -- 53차에 실제 인쇄소 가이드 실측으로 2.0mm로 조정됨)와
        # 충돌해 칼선이 사라져 보인다 -- 실제 이웃 칸까지의 중간 지점으로
        # 넓혀준다(이웃 정보가 없으면 기존과 동일하게 그 칸 자체만 쓴다).
        all_boxes = getattr(self, "_auto_detect_all_boxes_px", None) or []
        with Image.open(path) as _im:
            domusong_bounds_px = expand_box_to_neighbor_midpoint_px(
                self._selection_px, all_boxes, _im.size
            )
        return self._generate_one_item(path, bounds_px_override=domusong_bounds_px)

    def _run_generate(self, path):
        try:
            item_result = self._generate_one_item(path)
            # 2026-09-13(66차): 수동으로 영역을 골라 만들 때도 겹치는 조각
            # 경고는 똑같이 붙여준다(자동 인식 배치 경로와 동일한 안전장치).
            try:
                warn = _split_overlap_warning_note(item_result.design)
            except Exception:
                warn = None
            if warn:
                item_result.adjustments = list(item_result.adjustments or []) + [warn]
            self._accumulated.append(item_result)
            # 2026-09-07 다중선택 기능: 도무송+무테 동시 적용 체크박스가
            # 켜져 있으면, 방금 도무송으로 자른 것과 같은 영역에 대해
            # 무테 칼선도 하나 더 만들어 함께 누적한다.
            extra_item = self._maybe_generate_extra_borderless(path)
            if extra_item is not None:
                try:
                    warn = _split_overlap_warning_note(extra_item.design)
                except Exception:
                    warn = None
                if warn:
                    extra_item.adjustments = list(extra_item.adjustments or []) + [warn]
                self._accumulated.append(extra_item)
            combined = combine_results(self._accumulated)
            preview_png = os.path.join(_work_file_dir(), "_last_preview.png")
            render_preview(
                combined,
                preview_png,
                original_image_path=None if self.is_vector.get() else path,
            )
            self._last_result = combined
            self.after(0, self._show_preview, preview_png, item_result)
        except Exception as e:  # noqa: BLE001
            self._report_exception_to_server("칼선 생성/미리보기 처리 중")
            self.after(0, self._on_error, str(e))

    def _show_preview(self, preview_png, item_result=None):
        self._clear_selection()
        self._source_image = None  # showing a rendered result now, not a raw source -- no new drag-select until a file is (re)loaded
        if Image is not None:
            img = Image.open(preview_png)
            self._preview_full_image = img
            # 2026-09-07(7차) 피드백("칼선이 잘 됐는지 확대해서 볼 수 있는
            # 기능 필요"): 새로 생성될 때마다 100%로 되돌린다 -- 이전 결과에서
            # 확대해 둔 배율이 이번 결과에도 그대로 남아있으면(예: 이전
            # 결과에서 400%로 확대해 둔 채 다음 영역을 추가했더니 화면이
            # 거의 안 보이는 경우) 오히려 혼란스러움. 필요하면 다시
            # 확대/축소 버튼으로 조절하면 된다.
            self._preview_zoom = 1.0
            self._render_preview()

        n_items = len(self._accumulated)
        n_shapes = len(self._last_result.design.geoms)
        self.accum_status.set(f"누적 {n_items}개 영역 (파트 {n_shapes}개)")
        status_text = (
            f"완료. 누적 {n_items}개 영역, 도안 {n_shapes}개 파트. 내보내기 준비됨.\n"
            f"계속 드래그해서 영역을 추가하려면 '원본 다시 보기'를 누르세요."
        )

        # Reflect the enforced (possibly bumped-up) values of the item JUST
        # added back into the spinboxes -- never silently different from
        # what's on screen. Uses item_result (this one addition), not the
        # combined result, since the combined offset_mm is only ever a
        # representative stand-in for display.
        if item_result is not None and item_result.adjustments:
            adj = item_result.offset_mm
            if self.job_type.get() in ("BORDERLESS", "LINE_ART", "AUTO_STYLE"):
                self.style_margin_mm.set(adj.cut_mm)
            else:
                self.safety_mm.set(adj.safety_mm)
                self.cut_mm.set(adj.cut_mm)
                self.bleed_mm.set(adj.bleed_mm)
            status_text += "\n\n[방금 추가한 영역의 자동 조정]\n" + "\n".join(item_result.adjustments)
            self._show_note_dialog("자동 조정 안내", item_result.adjustments)

        self.status.set(status_text)
        self.generate_btn.configure(state="normal")
        self.export_btn.configure(state="normal")

    def _on_reset_accumulation_clicked(self):
        """2026-09-29(실제 사용 중 발견): 버튼 한 번에 만든 칼선 전체가 확인 없이
        지워졌다 -- 칼선이 있을 때만 한 번 묻는다."""
        n = len(self._accumulated)
        if n and not self._show_confirm_dialog(
            "누적된 칼선을 모두 지울까요?",
            [f"지금까지 만든 {n}개 영역의 칼선이 모두 지워집니다(파일로 저장한 것은 그대로)."],
        ):
            return
        self._on_reset_accumulation()

    def _on_reset_accumulation(self):
        self._exit_hint_mode()
        self._accumulated = []
        self._grabcut_hint_meta = {}
        self._last_result = None
        self.accum_status.set("누적된 영역 없음")
        self.export_btn.configure(state="disabled")
        self.status.set("누적된 영역을 모두 지웠습니다. 다시 드래그해서 시작하세요.")

    def _on_reset_all(self):
        """2026-09-07 피드백("화면과 파일 초기화 리셋 버튼을 오른쪽 상단에
        만들어"): 헤더 바 우측 상단의 '화면 초기화' 버튼이 호출하는 전체
        초기화. 디스크의 실제 파일은 전혀 건드리지 않고(삭제/이동 없음),
        지금까지 고른 도안 파일 경로/선택 영역/누적된 칼선/작업 종류/재단
        도형/미리보기 화면 전부를 프로그램을 막 실행했을 때와 같은 빈
        상태로 되돌린다."""
        self._close_open_dropdown()
        self._clear_selection()
        self._on_reset_accumulation()

        self.input_path.set("")
        self._export_basename = ""
        self.is_vector.set(False)
        self._real_grid_cells_px = None

        self.job_type.set("")
        if hasattr(self, "job_type_label_var"):
            self.job_type_label_var.set(JOB_TYPE_PLACEHOLDER)
        self.cutline_type.set("")
        if hasattr(self, "cutline_type_label_var"):
            self.cutline_type_label_var.set(CUTLINE_TYPE_PLACEHOLDER)
        self.domusong_also_borderless.set(False)

        self.preview_canvas.delete("all")
        self._canvas_placeholder = self.preview_canvas.create_text(
            10, 10, anchor="nw", text="미리보기가 여기에 표시됩니다", fill=TEXT_SECONDARY,
            font=self.font_canvas,
        )
        self._preview_photo = None
        self._display_scale = 1.0

        self.generate_btn.configure(state="disabled")
        self.export_btn.configure(state="disabled")
        self.status.set("화면을 초기화했습니다. 도안 파일을 다시 선택하세요.")

    def _show_note_dialog(self, heading, lines, kind="info"):
        """오류/경고 등 "설명"을 보여주는 자체 대화상자.

        2026-09-07 피드백("설명 파트는 글씨는 10포인트 줄이고. 1.2.3. 번
        순으로 문제를 한줄로 설명해"): OS 기본 messagebox는 운영체제가 직접
        그리는 대화상자라 폰트 크기를 코드에서 조절할 수 없다 -- 그래서
        폰트를 직접 지정할 수 있는 이 자체 대화상자로 바꿨다. `lines`는
        문자열 리스트: 한 줄에 한 가지씩, "1. ", "2. "처럼 번호를 자동으로
        붙여서 보여준다(본문보다 뚜렷하게 작은 font_note 사용)."""
        dialog = ctk.CTkToplevel(self)
        dialog.title(APP_TITLE)
        dialog.resizable(False, False)
        try:
            dialog.configure(fg_color=BG_APP)
        except Exception:  # noqa: BLE001
            pass
        try:
            dialog.transient(self)
        except Exception:  # noqa: BLE001
            pass

        wrap = ctk.CTkFrame(
            dialog, fg_color=BG_CARD, corner_radius=16, border_width=1, border_color=BORDER,
        )
        wrap.pack(fill="both", expand=True, padx=14, pady=14)

        # 2026-09-10 피드백("글자들이 다 같은 색이라 뭐가 중요한건지
        # 모르겠어, 알록달록은 금지"): 이 안내 대화상자는 제목(heading)과
        # 본문 줄이 색이 거의 같아서(예전엔 오류만 빨강, 나머지는 전부
        # 검정) 뭐가 핵심인지 한눈에 안 들어왔다. 새 색을 추가하는 대신,
        # 이미 이 프로그램에 있는 두 "신호" 색(빨강=오류, 파랑=브랜드
        # 포인트)만 제목에 그대로 쓴다 -- 본문 줄은 여전히 또렷한 검정으로
        # 남겨(2026-08-31 "글자가 잘 안보여" 피드백으로 이미 진하게 고친
        # 값 그대로 유지, 다시 흐리게 만들지 않음) 제목만 상대적으로 도드라져
        # 보이게 한다.
        heading_color = "#C0392B" if kind == "error" else ACCENT
        ctk.CTkLabel(
            wrap, text=heading, font=self.font_section, text_color=heading_color,
            anchor="w", justify="left", wraplength=380,
        ).pack(fill="x", padx=18, pady=(16, 8))

        for i, line in enumerate(lines, start=1):
            ctk.CTkLabel(
                wrap, text=f"{i}. {line}", font=self.font_note, text_color=TEXT_PRIMARY,
                anchor="w", justify="left", wraplength=380,
            ).pack(fill="x", padx=18, pady=(0, 4))

        self._btn_primary(wrap, "확인", dialog.destroy).pack(fill="x", padx=18, pady=(12, 16))

        try:
            dialog.lift()
            dialog.focus_force()
            dialog.grab_set()
        except Exception:  # noqa: BLE001
            pass

        self.update_idletasks()
        try:
            dialog.update_idletasks()
            px, py = self.winfo_rootx(), self.winfo_rooty()
            pw, ph = self.winfo_width(), self.winfo_height()
            dw, dh = dialog.winfo_width(), dialog.winfo_height()
            dialog.geometry(f"+{px + max(0, (pw - dw) // 2)}+{py + max(0, (ph - dh) // 2)}")
        except Exception:  # noqa: BLE001 -- 위치 계산 실패해도 대화상자 자체는 뜸(장식일 뿐)
            pass

    def _show_suspicious_regions_dialog(self, lines, box_by_note):
        """2026-09-28(멍푸 요청 "수동 기능 추가"): "확인이 필요한 영역이
        있습니다" 안내를 보여주는 전용 대화상자. 일반 _show_note_dialog와
        달리 두 가지가 다르다.

        1. `box_by_note`(문구 -> 원본 이미지 좌표 튜플)에 좌표가 있는 줄은
           평범한 라벨 대신 버튼으로 만들어, 누르면 미리보기 화면이 그
           좌표로 이동/확대되도록 한다(_navigate_preview_to_original_box).
           좌표가 없는 줄(예: 도안 겹침 경고처럼 좌표 정보가 없는 경우)은
           예전처럼 그냥 눌러도 아무 일 없는 보통 글자로 남는다 -- 클릭
           가능 여부를 새 UI 요소 없이 버튼 자체의 마우스오버 색으로만
           구분한다.
        2. 이동한 결과(미리보기 화면)를 실제로 봐야 의미가 있는 창이라서,
           _show_note_dialog처럼 grab_set()으로 다른 곳을 완전히 막지
           않는다(모달이 아님) -- 대화상자를 띄운 채로 캔버스를 계속
           스크롤/확대해서 확인할 수 있어야 한다. 또한 화면 정중앙 대신
           메인 창의 왼쪽 위(도안 목록/옵션이 있는 왼쪽 패널 쪽)에 붙여서,
           오른쪽 미리보기 캔버스를 가리지 않게 한다."""
        dialog = ctk.CTkToplevel(self)
        dialog.title(APP_TITLE)
        dialog.resizable(False, False)
        try:
            dialog.configure(fg_color=BG_APP)
        except Exception:  # noqa: BLE001
            pass
        try:
            dialog.transient(self)
        except Exception:  # noqa: BLE001
            pass

        wrap = ctk.CTkFrame(
            dialog, fg_color=BG_CARD, corner_radius=16, border_width=1, border_color=BORDER,
        )
        wrap.pack(fill="both", expand=True, padx=14, pady=14)

        ctk.CTkLabel(
            wrap, text="확인이 필요한 영역이 있습니다", font=self.font_section, text_color=ACCENT,
            anchor="w", justify="left", wraplength=380,
        ).pack(fill="x", padx=18, pady=(16, 4))
        ctk.CTkLabel(
            wrap, text="아래에서 클릭 가능한 항목을 누르면 미리보기가 그 위치로 이동합니다.",
            font=self.font_caption, text_color=TEXT_SECONDARY,
            anchor="w", justify="left", wraplength=380,
        ).pack(fill="x", padx=18, pady=(0, 8))

        for i, line in enumerate(lines, start=1):
            box = box_by_note.get(line)
            text = f"{i}. {line}"
            # 2026-09-28: CTkButton은 CTkLabel과 달리 wraplength를 지원하지
            # 않으므로(길게 줄바꿈되는 실제 경고 문구를 그대로 버튼 글자로
            # 쓰면 잘리거나 창이 옆으로 늘어남), 기존과 똑같이 생긴 라벨로
            # 전체 문구를 그대로 보여주고, 좌표가 있는 항목만 그 아래에
            # 짧고 고정된 문구의 작은 버튼("🔍 이 위치로 이동")을 따로
            # 붙인다 -- 문구 자체의 모양은 예전과 완전히 동일하게 유지.
            ctk.CTkLabel(
                wrap, text=text, font=self.font_note, text_color=TEXT_PRIMARY,
                anchor="w", justify="left", wraplength=380,
            ).pack(fill="x", padx=18, pady=(0, 2 if box is not None else 4))
            if box is not None:
                btn_row = ctk.CTkFrame(wrap, fg_color="transparent")
                btn_row.pack(fill="x", padx=18, pady=(0, 8))
                ctk.CTkButton(
                    btn_row, text="🔍 이 위치로 이동", font=self.font_caption,
                    text_color=TEXT_PRIMARY, fg_color="transparent", hover_color=ACCENT_SOFT,
                    border_width=1, border_color=BORDER, corner_radius=8,
                    anchor="w", width=140, height=26,
                    command=lambda b=box: self._navigate_preview_to_original_box(b),
                ).pack(side="left")
                # 2026-09-28(GrabCut 보조 기능 -- 트라이맵 힌트 보정 도구):
                # "이 위치로 이동" 바로 옆에, 같은 좌표를 화면에서 직접
                # 전경/배경 점을 찍어 GrabCut을 다시 계산하는 보정 모드로
                # 들어가는 버튼을 하나 더 둔다. 대응하는 자동 인식 결과를
                # 못 찾으면(_find_accumulated_index_for_box) 버튼을 누른
                # 뒤에야 안내하고 조용히 아무 것도 하지 않는다 -- 이 대화상자
                # 자체는 좌표 유무만으로 버튼을 보여주므로.
                ctk.CTkButton(
                    btn_row, text="✏️ 힌트로 보정", font=self.font_caption,
                    text_color=TEXT_PRIMARY, fg_color="transparent", hover_color=ACCENT_SOFT,
                    border_width=1, border_color=BORDER, corner_radius=8,
                    anchor="w", width=140, height=26,
                    command=lambda b=box: self._start_hint_correction(b),
                ).pack(side="left", padx=(8, 0))

        self._btn_primary(wrap, "닫기", dialog.destroy).pack(fill="x", padx=18, pady=(12, 16))

        # 모달이 아니므로 lift()만 하고 grab_set()/focus_force()는 하지 않는다
        # -- 이 창을 띄운 채로 뒤의 미리보기 캔버스를 스크롤/확대해서 볼 수
        # 있어야 클릭-이동 기능이 실제로 쓸모가 있다.
        try:
            dialog.lift()
        except Exception:  # noqa: BLE001
            pass

        self.update_idletasks()
        try:
            dialog.update_idletasks()
            px, py = self.winfo_rootx(), self.winfo_rooty()
            # 오른쪽 미리보기 캔버스를 가리지 않도록 왼쪽 위 근처에 붙인다.
            dialog.geometry(f"+{px + 24}+{py + 60}")
        except Exception:  # noqa: BLE001 -- 위치 계산 실패해도 대화상자 자체는 뜸(장식일 뿐)
            pass

    def _show_confirm_dialog(self, heading, lines):
        """예/아니오 확인이 필요할 때 쓰는 자체 대화상자 (_show_note_dialog의
        확인/취소 버전).

        2026-09-07 피드백("팝업 안 보였어" -- '작업 종류'를 선택하지 않고
        '이 영역 추가'를 눌렀을 때 경고가 전혀 뜨지 않았다는 보고): 이
        프로젝트는 OS 기본 tkinter.messagebox 대화상자를 여러 곳에서 써왔는데,
        messagebox는 이 CTk 창들과 별개로 OS가 직접 그리고 위치도 OS가
        정하는 창이라 -- 사용자 환경(Windows)에서 실제 메인 창 뒤/화면
        밖 등 안 보이는 위치에 뜨고 있었을 가능성이 있다(이 세션에서 반복된
        "창 위치가 이상해진다" 계열 문제와 같은 종류). _show_note_dialog는
        이미 메인 창 위 정확한 중앙 위치를 직접 계산해서 확실히 보이는 것이
        스크린샷으로 확인된 방식이므로, 예/아니오 확인이 필요한
        messagebox.askyesno 자리도 같은 방식(자체 CTkToplevel + 메인 창
        중앙 정렬)으로 통일한다. 사용자가 "예"를 누르면 True, "아니오"를
        누르거나 창을 닫으면 False를 반환하며, 응답이 올 때까지 이 호출
        지점에서 블록된다(wait_window) -- 버튼 클릭으로 메인 스레드에서
        호출되는 자리에서만 쓰므로 안전하다."""
        result = {"value": False}
        dialog = ctk.CTkToplevel(self)
        dialog.title(APP_TITLE)
        dialog.resizable(False, False)
        try:
            dialog.configure(fg_color=BG_APP)
        except Exception:  # noqa: BLE001
            pass
        try:
            dialog.transient(self)
        except Exception:  # noqa: BLE001
            pass

        wrap = ctk.CTkFrame(
            dialog, fg_color=BG_CARD, corner_radius=16, border_width=1, border_color=BORDER,
        )
        wrap.pack(fill="both", expand=True, padx=14, pady=14)

        ctk.CTkLabel(
            wrap, text=heading, font=self.font_section, text_color=TEXT_PRIMARY,
            anchor="w", justify="left", wraplength=380,
        ).pack(fill="x", padx=18, pady=(16, 8))

        for i, line in enumerate(lines, start=1):
            ctk.CTkLabel(
                wrap, text=f"{i}. {line}", font=self.font_note, text_color=TEXT_PRIMARY,
                anchor="w", justify="left", wraplength=380,
            ).pack(fill="x", padx=18, pady=(0, 4))

        btn_row = ctk.CTkFrame(wrap, fg_color="transparent")
        btn_row.pack(fill="x", padx=18, pady=(12, 16))
        btn_row.grid_columnconfigure((0, 1), weight=1)

        def _choose(value):
            result["value"] = value
            try:
                dialog.grab_release()
            except Exception:  # noqa: BLE001
                pass
            dialog.destroy()

        self._btn_secondary(btn_row, "아니오", lambda: _choose(False)).grid(
            row=0, column=0, padx=(0, 6), sticky="ew"
        )
        self._btn_primary(btn_row, "예", lambda: _choose(True)).grid(
            row=0, column=1, padx=(6, 0), sticky="ew"
        )
        dialog.protocol("WM_DELETE_WINDOW", lambda: _choose(False))

        try:
            dialog.lift()
            dialog.focus_force()
            dialog.grab_set()
        except Exception:  # noqa: BLE001
            pass

        self.update_idletasks()
        try:
            dialog.update_idletasks()
            px, py = self.winfo_rootx(), self.winfo_rooty()
            pw, ph = self.winfo_width(), self.winfo_height()
            dw, dh = dialog.winfo_width(), dialog.winfo_height()
            dialog.geometry(f"+{px + max(0, (pw - dw) // 2)}+{py + max(0, (ph - dh) // 2)}")
        except Exception:  # noqa: BLE001
            pass

        dialog.wait_window()
        return result["value"]

    def _friendly_error_text(self, message: str) -> str:
        """core.* 파이프라인이 던지는 원시(영어) 예외 메시지 중 사용자에게
        그대로 보여주면 이해하기 어려운 것들을, 알아볼 수 있는 한국어
        설명으로 바꿔서 돌려준다. 알려지지 않은 메시지는 그대로 둔다.

        2026-09-07(7차) 피드백("두번째 스크린샷 갬벳 오류로 오인 하기
        쉬움"): 자동 인식 실패 목록에도 "GrabCut found no foreground..."
        같은 내부 용어가 그대로 노출되고 있었다 -- 단일 생성 오류
        (_on_error)만 번역해주던 것을 자동 인식 배치 오류 목록에도 똑같이
        적용하도록 이 헬퍼로 뽑아냈다."""
        if "OpenCV" in message or "cv::" in message or "Assertion failed" in message:
            # 2026-09-29(PC 사용 중 발견): OpenCV 원문 오류(경로·함수 이름 포함)가
            # 그대로 보였다 -- 이해할 수 있는 말로 바꾼다.
            return (
                "이 도안의 윤곽을 자동으로 찾지 못했습니다(이미지 끝에 딱 붙어 있거나 "
                "배경과 구분이 어려운 경우). 해당 위치를 드래그해 직접 추가해 주세요."
            )
        if "Could not read image" in message:
            return (
                "이미지 파일을 열 수 없습니다(파일이 삭제되었거나 다른 위치로 "
                "이동되었을 수 있음). '도안 파일'에서 다시 선택해보세요."
            )
        if "GrabCut found no foreground" in message or "No design silhouette found" in message:
            return (
                "이 영역에서 배경과 도안을 구분하지 못했습니다(배경과 도안의 색 "
                "차이가 약하거나, 선택 영역이 도안을 충분히 덮지 못했을 수 "
                "있음)."
            )
        if "Selection rectangle is empty/out of bounds" in message:
            return "선택한 영역이 화면 밖으로 벗어났거나 비어 있습니다."
        return message

    def _report_exception_to_server(self, context: str):
        """2026-09-12(56차) 피드백("발생한 오류를 프로그램이 자동적으로
        나한테 전달하는 게 필요해"): 지금 처리 중인 except 블록에서 호출한다
        (현재 예외 정보는 sys.exc_info()로 가져옴). 먼저 기존과 완전히 같은
        traceback.print_exc()로 콘솔에 남기고, 이어서 core.license_client.
        report_error로 라이선스 서버에 베스트에포트 전송을 시도한다.

        `context`는 어느 동작 중이었는지(예: "자동 인식 중")만 남기는 짧은
        한국어 설명 -- 실제 도안 파일 경로나 파일명은 절대 넣지 않는다(고객
        파일 내용까지 서버 로그에 남길 이유는 없음, 오류 자체를 진단하는 데는
        예외 종류/메시지/트레이스백이면 충분).

        이 보고 자체가 실패하거나(네트워크 없음, 서버 응답 없음, 라이선스
        미입력 등) 예외를 새로 일으켜도 절대 밖으로 새어나가면 안 된다 --
        이미 오류 처리 중인 상황을 이 부가 기능이 더 악화시키면 안 되므로
        report_error 자체가 이미 베스트에포트지만, 한 번 더 감싼다."""
        traceback.print_exc()
        try:
            exc_type, exc_value, exc_tb = sys.exc_info()
            tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb)) if exc_type else ""
            lic.report_error(
                error_type=exc_type.__name__ if exc_type else "Unknown",
                message=str(exc_value) if exc_value else "",
                traceback_str=tb_text,
                context=context,
            )
        except Exception:  # noqa: BLE001
            pass

    def _on_error(self, message):
        # 2026-09-07 피드백 대응: "Could not read image: ..." (core.
        # cutline_core/core.segmentation이 cv2.imread 실패 시 던지는 원본
        # FileNotFoundError 메시지)처럼 사용자에게 그대로 보여주면 이해하기
        # 어려운 원시 오류는, 알아볼 수 있는 안내 문구로 바꿔서 보여준다.
        if "Could not read image" in message:
            lines = [
                "이미지 파일을 열 수 없습니다.",
                "파일이 삭제되었거나 다른 위치로 이동되었을 수 있습니다.",
                "왼쪽 위 '도안 파일'에서 파일을 다시 선택한 뒤 다시 시도해주세요.",
            ]
        else:
            lines = [self._friendly_error_text(message)]
        self._show_note_dialog("오류가 발생했습니다", lines, kind="error")
        self.status.set("오류 발생. 파일/설정을 확인하세요.")
        self.generate_btn.configure(state="normal")

    # ------------------------------------------------------------------
    # 자동 여러 개 인식 (2026-09-07 피드백: "스티커는 도안을 자동 인식해서
    # 칼선을 생성해야지. 하나씩 선택하는 건 비효율적이야"). 시트 안에 서로
    # 떨어진 도안이 여러 개 있을 때, core.multi_design.detect_design_bboxes_px
    # 로 위치를 전부 찾은 뒤, 하나씩 순서대로 기존 _generate_one_item에
    # 넣어 누적한다 -- 사람이 하나씩 드래그하던 것과 결과적으로 동일한
    # 파이프라인을 그대로 타므로, 개별 항목의 GrabCut/스타일 로직은 전혀
    # 건드리지 않는다.
    def _auto_detect_and_add_all(self):
        path = self._validate_inputs()
        if not path:
            return
        job = self.job_type.get()
        if job not in ("BORDERLESS", "LINE_ART", "AUTO_STYLE", "DOMUSONG", "MASKING_TAPE"):
            lines = [
                "자동 인식은 무테/유테/무테+유테 자동 생성/도무송/마스킹테이프에서만 사용할 수 있습니다.",
                "조각 스티커는 이미지 전체를 하나로 처리합니다.",
            ]
            if job == "MIXED_AUTO":
                lines.append(
                    "'무테/유테+도무송'는 이 버튼 대신 아래 전용 버튼(① 도안 자동 "
                    "인식, ② 표시한 도무송 칼선 생성, ③ 남은 칼선 생성)을 "
                    "순서대로 사용하세요."
                )
            self._show_note_dialog("자동 인식을 사용할 수 없습니다", lines, kind="error")
            return
        if self.is_vector.get():
            self._show_note_dialog(
                "자동 인식을 사용할 수 없습니다",
                ["AI(벡터) 파일은 자동 인식을 지원하지 않습니다."],
                kind="error",
            )
            return
        # 2026-09-26 피드백("이런 오류창은 무서운데" -- 재단 도형을 안
        # 고르고 도무송 자동 인식을 누르면 도안마다 똑같은 "재단 도형을
        # 먼저 선택하세요" 오류가 수십~수백 개 그대로 쌓여 무섭게 보임):
        # 그 반복 실패를 다 겪고 나서 보여주는 대신, 시작하기 전에 한
        # 번만 깔끔하게 안내한다.
        if job == "DOMUSONG" and not self.cutline_type.get():
            self._show_note_dialog(
                "먼저 '재단 도형'을 선택하세요",
                ["도무송 자동 인식에 쓸 재단 도형(도무송 세부 옵션)을 먼저 골라주세요."],
                kind="error",
            )
            return
        self.generate_btn.configure(state="disabled")
        self.status.set("도안을 자동으로 찾는 중...")
        threading.Thread(
            target=self._run_auto_detect_and_add_all, args=(path,), daemon=True
        ).start()

    def _run_auto_detect_and_add_all(self, path):
        # 2026-09-07 피드백: 실제 재단선 격자를 읽은 파일이면(_on_ai_loaded
        # 참고) 픽셀에서 다시 추측하지 말고 그 실제 칸을 그대로 쓴다 --
        # 작가 본인이 그려 놓은 진짜 데이터가 있는데 굳이 다시 추측할
        # 이유가 없고, 이쪽이 훨씬 정확/안전하다.
        #
        # 2026-09-11(51차) 피드백("똑같은 도안 여러 개일 때... 아예 각각
        # 엉망으로 인식하고 있어", "도안 인식이 엉망이고 사각형 칼선까지
        # 생겨"): 50차가 칸 안 낱개 요소 쪼개기를 다시 켰을 때, 반복 패널을
        # 먼저 그룹핑하지 않고 칸마다 각자 쪼갠 뒤에야 그 조각들을 그룹핑
        # 하려 했던 게 문제였다 -- 완전히 같은 그림이 반복돼도 색 경계
        # 재계산이 매번 미세하게 달라 조각의 개수/순서가 안 맞았고, 결국
        # 반복 패널마다 따로(그리고 제각각 다르게, 일부는 GrabCut이 실패해
        # 사각형 그대로) 처리돼버렸다. `detect_repeat_aware_sub_element_
        # boxes_px`(core.multi_design)가 순서를 "반복 패널 그룹핑 먼저 ->
        # 대표 하나만 실제로 쪼개기 -> 나머지 반복엔 그 조각을 옮겨 붙이기"로
        # 바로잡는다(자세한 경위는 그 함수 문서 참고) -- 원래 6차 취지
        # ("반복되는 스티커는 한개의 칼선을 먼저 완성하고 나머지에 그대로
        # 적용해") 그대로 유지, 격자 없는 파일용 대체 경로(else)는 그대로.
        suspicious_regions_px: list = []
        try:
            if self._real_grid_cells_px:
                # 2026-09-28: 배경색뿐인 칸(이웃 칸 테두리 선이 살짝 걸친 빈 칸
                # 포함 -- 실제 파일로 확인)은 어떤 작업이든 칼선 대상에서 뺀다
                # ("배경색이 칼선으로 잡히면 안 됨").
                grid_cells = [
                    c for c in self._real_grid_cells_px
                    if image_outer_region_px(path, c) is not None
                ]
            if self._real_grid_cells_px and self.job_type.get() == "DOMUSONG":
                # 2026-09-28: 도무송은 칸(카드) 단위(9/26 "카드 하나 = 도무송
                # 하나") -- 칸 이미지 안쪽에 도형 하나("도무송 직접 선택 후 칼선
                # 종류에 따라 이미지 안쪽으로"). 실제 테스트 파일의 카드 칸 손
                # 칼선도 칸 하나에 도형 하나, 이미지 외곽 약 3mm 안쪽으로 실측됨.
                # 빈 칸만 거르고 같은 그림 반복만 묶는다(쪼개기 없음).
                cell_boxes = grid_cells
                boxes, groups = group_content_cells_px(path, cell_boxes)
            elif self._real_grid_cells_px:
                cell_boxes = grid_cells
                boxes, groups = detect_repeat_aware_sub_element_boxes_px(
                    path, cell_boxes, suspicious_regions=suspicious_regions_px,
                )
            else:
                # 2026-09-26: "① 도안 자동 인식"과 같은 이유로 여기도 캐시를
                # 재사용(위 _run_mixed_detect 주석 참고) -- 이 "자동으로 여러
                # 개 인식 + 추가" 버튼도 같은 detect_design_bboxes_px를 매번
                # 새로 돌리고 있었다.
                cell_boxes = self._get_cached_design_boxes(path) or []
                boxes = cell_boxes
                try:
                    groups = group_identical_boxes_px(path, boxes)
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
                    groups = [[i] for i in range(len(boxes))]
        except Exception as e:  # noqa: BLE001
            self._report_exception_to_server("칼선 생성/미리보기 처리 중")
            self.after(0, self._on_error, str(e))
            return
        if not boxes:
            self.after(0, self._on_auto_detect_none)
            return

        n_total = len(boxes)
        added = 0
        errors = []
        # 2026-09-10(46차): _generate_one_item_for_auto_detect의 도무송
        # 경계 확장(expand_box_to_neighbor_midpoint_px)이 "같은 시트의
        # 나머지 칸"을 알 수 있도록 여기 한 번만 저장해둔다.
        # 2026-09-11(52차) 피드백("도무송 칼선이 밀리고 중심을 벗어나"):
        # 51차 이후 boxes는 칸(패널)이 아니라 그 안의 조각 단위라, 도무송의
        # 이웃 계산에 그대로 쓰면 같은 패널 안 다른 조각까지 이웃으로 잡혀
        # 여유가 한쪽으로 치우친다 -- 도무송 이웃 계산 전용으로 칸(패널)
        # 단위 cell_boxes를 따로 저장한다.
        self._auto_detect_all_boxes_px = cell_boxes

        # 2026-09-28(GrabCut 보조 기능): 이 실행에서 힌트 보정 대상으로 삼을
        # 수 있는 job_type인지 미리 판단해둔다. 무테는 보정된 실루엣을
        # 안쪽으로, 유테/마스킹테이프는 바깥으로 오프셋한다(hint_style).
        # AUTO_STYLE/DOMUSONG/FULL_CUT은 아직 연결 안 됨.
        run_start_index = len(self._accumulated)
        hint_eligible_job = self.job_type.get() in ("BORDERLESS", "LINE_ART", "MASKING_TAPE")
        hint_style = "BORDERLESS" if self.job_type.get() == "BORDERLESS" else "LINE_ART"

        # 각 도안은 사람이 직접 드래그한 것처럼 self._selection_px를 그때그때
        # 채워서 기존 _generate_one_item을 그대로 재사용 -- 개별 항목 하나가
        # 실패해도(예: 너무 작거나 이상한 영역) 전체를 중단하지 않고 계속
        # 진행하며, 실패 목록은 마지막에 모아서 알려준다.
        for gi, group in enumerate(groups, start=1):
            primary_idx = group[0]
            x0, y0, x1, y1 = boxes[primary_idx]
            self._selection_px = (x0, y0, x1, y1)
            # 같은 시트의 나머지 요소 박스 -- 무테 경로가 옆 요소를 자기
            # 실루엣으로 끌어오지 않게 막는 데 쓴다.
            self._auto_detect_sibling_boxes_px = [
                b for j, b in enumerate(boxes) if j != primary_idx
            ]
            self.after(
                0, self.status.set,
                f"자동 인식 처리 중... (그룹 {gi}/{len(groups)}, 반복 {len(group)}개)",
            )
            group_hint_indices: list = []  # 이 그룹의 self._accumulated 인덱스들(힌트 보정 대상일 때만 채움)
            try:
                item_result = self._generate_one_item_for_auto_detect(path)
                if item_result.design is None or item_result.design.is_empty:
                    # 2026-09-28: 배경뿐인 칸(무테 "배경색이 칼선으로 잡히면
                    # 안 됨") -- 칼선을 만들지 않고, 같은 그림 반복 칸도 건너뜀.
                    continue
                template_results = [item_result]
                # 2026-09-07 다중선택 기능: 자동 인식 배치 처리에서도 동일하게,
                # 도무송+무테 동시 적용이 켜져 있으면 이 도안 영역에 무테
                # 칼선을 하나 더 추가한다. 이 추가분이 실패해도(예외) 이미
                # 추가된 도무송 칼선(added는 이미 반영됨)까지 잃지는 않도록
                # 별도 try로 감싼다.
                try:
                    extra_item = self._maybe_generate_extra_borderless(path)
                    if extra_item is not None:
                        template_results.append(extra_item)
                except Exception as e:  # noqa: BLE001
                    self._report_exception_to_server("도무송+무테 동시 적용(무테 추가분) 처리 중")
                    errors.append(f"{primary_idx + 1}번째 도안(무테 추가분): {self._friendly_error_text(str(e))}")
                # 2026-09-13(66차): 대표 인스턴스에서 겹치는 조각 경고를 여기서
                # 붙여두면, 아래 반복 복제(fit_cutline_result_to_box)가 원본의
                # adjustments를 그대로 이어받으므로 반복 칸에도 자동으로 같이
                # 표시된다 -- 대표 하나만 검사하면 충분함.
                for t in template_results:
                    try:
                        warn = _split_overlap_warning_note(t.design)
                    except Exception:
                        warn = None
                    if warn:
                        t.adjustments = list(t.adjustments or []) + [warn]
                for t in template_results:
                    self._accumulated.append(t)
                added += 1
                # 힌트 보정 메타데이터 기록: 처음엔 "is_silhouette_undersized로
                # 이미 안전한 사각형(무테)으로 대체된 항목은 대상에서 뺀다"고
                # 생각했었는데, 실제 파일로 검증해보니 정반대였다 -- 하필
                # 그 대체가 걸리는 항목(예: 실측 ratio=0.337, 기준 0.35 미만)
                # 이 바로 "GrabCut이 심하게 실패해서 힌트 보정이 가장 필요한"
                # 사례였다(실제 10칸 시트 파일, 캐릭터 6마리는 다 잡히고
                # 양배추 장식 2개가 통째로 빠진 경우 -- 힌트 2점으로 실제
                # 복구까지 확인함). 그래서 원래 어떤 스타일로 끝났는지와
                # 무관하게, 이 job_type이면 항상 메타를 기록한다 -- 힌트
                # 보정 자체는 항상 유테(LINE_ART) 실루엣 추적을 새로
                # 시도하므로 원래 결과가 사각형 대체였든 아니든 상관없다.
                if hint_eligible_job and len(template_results) == 1:
                    rep_index = len(self._accumulated) - 1
                    group_hint_indices.append(rep_index)
                    self._grabcut_hint_meta[rep_index] = {
                        "box_px": (x0, y0, x1, y1),
                        "margin_mm": self.style_margin_mm.get(),
                        "dpi": self.dpi.get(),
                        "group_indices": group_hint_indices,
                        "style": hint_style,
                        "is_representative": True,
                    }
            except Exception as e:  # noqa: BLE001
                self._report_exception_to_server("템플릿 기반 배치 생성 처리 중")
                errors.append(f"{primary_idx + 1}번째 도안: {self._friendly_error_text(str(e))}")
                continue

            # 같은 그룹의 나머지 인스턴스는 다시 추적하지 않고, 대표
            # 인스턴스 결과를 그대로 복제한다 -- 2026-09-10(48차) 피드백
            # ("칼선이 겹치거나 뭉쳐")으로 확인된 대로, 반복 칸끼리 크기가
            # 몇 px 다를 수 있어(group_identical_boxes_px 최대 8px 허용)
            # 순수 평행이동 대신 대상 칸 크기에 맞춰 늘이고 옮기는
            # fit_cutline_result_to_box를 쓴다.
            for other_idx in group[1:]:
                ox0, oy0, ox1, oy1 = boxes[other_idx]
                try:
                    for t in template_results:
                        self._accumulated.append(
                            fit_cutline_result_to_box(
                                t, (x0, y0, x1, y1), (ox0, oy0, ox1, oy1),
                                note=(
                                    "동일 도안이 반복되는 것으로 감지되어, 대표 인스턴스의 "
                                    "칼선을 그 칸 크기에 맞춰 복제했습니다."
                                ),
                            )
                        )
                    added += 1
                    if group_hint_indices:
                        sib_index = len(self._accumulated) - 1
                        group_hint_indices.append(sib_index)
                        self._grabcut_hint_meta[sib_index] = {
                            "box_px": (ox0, oy0, ox1, oy1),
                            "margin_mm": self.style_margin_mm.get(),
                            "dpi": self.dpi.get(),
                            "group_indices": group_hint_indices,
                            "style": hint_style,
                            "is_representative": False,
                        }
                except Exception as e:  # noqa: BLE001
                    self._report_exception_to_server("반복 도안 복제 처리 중")
                    errors.append(f"{other_idx + 1}번째 도안(반복 복제): {self._friendly_error_text(str(e))}")
        self._selection_px = None

        if self.job_type.get() in ("BORDERLESS", "AUTO_STYLE") and self._real_grid_cells_px:
            try:
                added += self._supplement_missing_elements(
                    path, run_start_index, list(self._real_grid_cells_px),
                    auto_style=self.job_type.get() == "AUTO_STYLE",
                )
            except Exception:  # noqa: BLE001 -- 보조 탐지 실패해도 기존 결과는 그대로
                traceback.print_exc()
        if self.job_type.get() in ("BORDERLESS", "AUTO_STYLE", "LINE_ART", "MASKING_TAPE"):
            # 2026-09-29: 무테만이 아니라 무테+유테 자동·유테·키스컷에서도 칼선끼리
            # 교차·이중 칼선이 실제 파일에서 나왔다(저장 전 점검으로 발견, 키스컷 롤은
            # 교차 12곳) -- 같은 정리를 한다.
            self._resolve_cut_conflicts(run_start_index)

        if added == 0:
            self.after(
                0, self._on_error,
                "모든 도안 처리에 실패했습니다.\n" + "\n".join(errors),
            )
            return

        try:
            combined = combine_results(self._accumulated)
            # 2026-09-28(수동 기능 추가): 이번 실행에서 실제로 계산된
            # suspicious_regions_px를 self에 저장해둔다 -- 비어있어도(이번엔
            # 의심 영역이 없어도) 항상 최신 값으로 덮어써서, 이전 실행의
            # 좌표가 이번 경고 목록과 잘못 짝지어지는 일이 없게 한다
            # (_show_auto_detect_result의 클릭-이동 기능 참고).
            self._last_suspicious_regions_px = list(suspicious_regions_px)
            if suspicious_regions_px:
                combined.adjustments = list(combined.adjustments or []) + _missing_body_warning_notes(
                    suspicious_regions_px
                )
            preview_png = os.path.join(_work_file_dir(), "_last_preview.png")
            render_preview(combined, preview_png, original_image_path=path)
            self._last_result = combined
        except Exception as e:  # noqa: BLE001
            self._report_exception_to_server("칼선 생성/미리보기 처리 중")
            self.after(0, self._on_error, str(e))
            return

        self.after(0, self._show_auto_detect_result, preview_png, added, n_total, errors)

    def _supplement_missing_elements(self, path, start_idx, cell_boxes, auto_style=False):
        """무테 자동 인식 보조(자동 적용): 기존 경로(선 기반 낱개 탐지 +
        GrabCut)가 놓친 요소만 배경 채우기 탐지기
        (detect_elements_by_background_flood_px)로 찾아 더한다. 이미 칼선이
        있는 요소와 30% 이상 겹치면 더하지 않는다(이중 칼선 방지). 칸 단위
        반복 그룹의 대표 칸에서만 찾고, 같은 그림의 다른 칸에는 복제한다.
        실측(실제 손 칼선 대조): 파스텔톤 시트에서 맞은 칼선 16개 -> 이 보조로
        크게 늘어남(병아리·잎·꽃·큰 캐릭터), 다른 시트는 거의 그대로."""
        from shapely.ops import unary_union
        from core.cutline_core import mm_to_px as _mm_to_px

        margin = self.style_margin_mm.get()
        dpi = self.dpi.get()
        inset = _mm_to_px(margin, dpi)
        existing = []
        for i in range(start_idx, len(self._accumulated)):
            cut = self._accumulated[i].offsets.get("cut")
            if cut is not None and not cut.is_empty:
                existing.append(cut.buffer(inset))
        covered = unary_union(existing) if existing else None
        cells, groups = group_content_cells_px(path, [
            c for c in cell_boxes if image_outer_region_px(path, c) is not None
        ])
        added = 0
        note = "무테 보조 탐지: 기존 인식에서 빠진 요소를 배경 채우기로 찾아 그림 안쪽으로 잘랐습니다."
        # 시트 전체의 배경색 모음: 한 칸에서 배경으로 확인된 색은 다른 칸(예: 90도
        # 돌려 놓은 칸)에서 한쪽 가장자리에만 닿아도 배경으로 본다.
        sheet_bg = []
        for grp in groups:
            try:
                detect_elements_by_background_flood_px(path, cells[grp[0]], bg_colors_out=sheet_bg)
            except Exception:  # noqa: BLE001
                pass
        for grp in groups:
            prim = cells[grp[0]]
            cell_area = max(1.0, (prim[2] - prim[0]) * (prim[3] - prim[1]))
            for el in detect_elements_by_background_flood_px(path, prim, known_bg_lab=sheet_bg):
                # 칸 넓이의 1% 미만은 더하지 않는다: 실측으로 배경 건물의 창문
                # 칸 같은 무늬 조각(약 5mm)이 여기 걸렸고, 실제로 빠져 있던
                # 요소(병아리·잎 등, 칸의 1.5% 이상)는 모두 이보다 컸다.
                if el.area < 0.01 * cell_area:
                    continue
                if covered is not None and el.intersection(covered).area / el.area >= 0.3:
                    continue
                el_style = "BORDERLESS"
                if auto_style and has_white_or_black_border(path, el, dpi):
                    # 무테+유테 자동: 흰색/검은색 테두리 선이 있으면 유테(바깥)
                    el_style = "LINE_ART"
                    res = generate_cutline_by_style(
                        image_path=path, style=ImageStyle.LINE_ART, dpi=dpi,
                        selection_px=tuple(el.bounds), margin_mm=margin,
                        supersample=self.precision.get(), precomputed_content_px=el,
                    )
                    res.adjustments = [note.replace("그림 안쪽으로", "테두리 바깥으로")] + list(res.adjustments or [])
                else:
                    res = generate_borderless_cut_from_silhouette(path, el, dpi, margin, note=note)
                if res.offsets["cut"].is_empty:
                    continue
                self._accumulated.append(res)
                rep_index = len(self._accumulated) - 1
                group_indices = [rep_index]
                bx = tuple(el.bounds)
                self._grabcut_hint_meta[rep_index] = {
                    "box_px": bx, "margin_mm": margin, "dpi": dpi,
                    "group_indices": group_indices, "style": el_style,
                    "is_representative": True,
                }
                for other in grp[1:]:
                    ocell = cells[other]
                    self._accumulated.append(
                        fit_cutline_result_to_box(res, prim, ocell, note=(
                            "동일 도안이 반복되는 것으로 감지되어, 대표 칸의 칼선을 그 칸 크기에 맞춰 복제했습니다."
                        ))
                    )
                    sib = len(self._accumulated) - 1
                    group_indices.append(sib)
                    px0, py0, px1, py1 = prim
                    ox0, oy0, ox1, oy1 = ocell
                    sx = (ox1 - ox0) / (px1 - px0) if px1 != px0 else 1.0
                    sy = (oy1 - oy0) / (py1 - py0) if py1 != py0 else 1.0
                    self._grabcut_hint_meta[sib] = {
                        "box_px": (ox0 + (bx[0] - px0) * sx, oy0 + (bx[1] - py0) * sy,
                                   ox0 + (bx[2] - px0) * sx, oy0 + (bx[3] - py0) * sy),
                        "margin_mm": margin, "dpi": dpi,
                        "group_indices": group_indices, "style": el_style,
                        "is_representative": False,
                    }
                added += 1
        return added

    def _resolve_cut_conflicts(self, start_idx):
        """자동 생성한 칼선 정리: (1) 서로 겹치거나 안에 들어간 칼선은 한 조각으로
        합치고, (2) 2mm보다 가까운 두 칼선은 작은 쪽을 상대에게서 2mm 떨어지게
        살짝 줄인다(줄이면 너무 많이 깎이면 합친다). 실제 손 칼선 8개 파일에서
        교차·이중 칼선은 0건, 최소 간격은 약 2mm였다(core.cut_check 참고)."""
        try:
            self._tidy_cut_parts(start_idx)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        try:
            self._merge_overlapping_borderless_cuts(start_idx)
        except Exception:  # noqa: BLE001 -- 합치기 실패해도 개별 칼선은 그대로 남음
            traceback.print_exc()
        try:
            self._separate_too_close_cuts(start_idx)
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    def _tidy_cut_parts(self, start_idx, min_gap_mm=2.0):
        """한 요소의 칼선 안쪽 구멍(창 모양으로 뚫리는 칼선)을 없애고, 같은 요소가
        2mm보다 가깝게 여러 조각으로 갈라진 칼선은 하나로 잇는다.

        2026-09-29(멍푸 PC에서 모든 샘플 마지막 사용 중 발견): 흰 테두리 스티커
        시트에서 칼선 안에 작은 구멍 칼선(최대 약 12x7mm)이 18~21개, 한 요소가
        0.1mm 틈으로 갈라진 칼선이 15곳 있었다(원본 손 칼선에는 둘 다 0건)."""
        from shapely.geometry import MultiPolygon, Polygon
        from shapely.ops import unary_union
        from core.cutline_core import mm_to_px as _mm_to_px

        gap = _mm_to_px(min_gap_mm, self.dpi.get())
        for i in range(start_idx, len(self._accumulated)):
            res = self._accumulated[i]
            cut = res.offsets.get("cut")
            if cut is None or cut.is_empty:
                continue
            parts = [p for p in getattr(cut, "geoms", [cut]) if isinstance(p, Polygon) and not p.is_empty]
            filled = [Polygon(p.exterior) for p in parts]
            filled = [p for p in filled if not any(q is not p and q.contains(p) for q in filled)]
            changed = len(filled) != len(parts) or any(p.interiors for p in parts)
            if len(filled) > 1:
                close = any(
                    filled[a].distance(filled[b]) < gap
                    for a in range(len(filled)) for b in range(a + 1, len(filled))
                )
                if close:
                    joined = unary_union(filled).buffer(gap / 2.0, join_style=1).buffer(-gap / 2.0, join_style=1)
                    filled = [Polygon(p.exterior) for p in getattr(joined, "geoms", [joined])
                              if isinstance(p, Polygon) and not p.is_empty]
                    changed = True
            # 좁게 튀어나온 가시(폭 약 1.6mm 미만 -- 칼로 따라 자를 수 없고 옆 칼선과
            # 가까워지는 원인, 흰 테두리 스티커 시트에서 실측)는 둥글게 다듬는다.
            r = 0.4 * gap
            smoothed = []
            for p in filled:
                o = p.buffer(-r, join_style=1).buffer(r, join_style=1)
                o = max(getattr(o, "geoms", [o]), key=lambda g: g.area) if not o.is_empty else o
                if not o.is_empty and o.area >= 0.9 * p.area and o.symmetric_difference(p).area > 1.0:
                    smoothed.append(Polygon(o.exterior))
                    changed = True
                else:
                    smoothed.append(p)
            filled = smoothed
            if changed and filled:
                res.offsets["cut"] = MultiPolygon(filled)
                res.adjustments = list(res.adjustments or []) + [
                    "칼선 안쪽 구멍과, 2mm보다 가깝게 갈라진 같은 요소의 칼선 조각을 하나로 정리했습니다."
                ]

    def _separate_too_close_cuts(self, start_idx, min_gap_mm=2.0):
        from shapely.geometry import MultiPolygon, Polygon
        from shapely.strtree import STRtree
        from core.cutline_core import mm_to_px as _mm_to_px

        gap_px = _mm_to_px(min_gap_mm, self.dpi.get())
        tol_px = _mm_to_px(0.1, self.dpi.get())
        idxs = [
            i for i in range(start_idx, len(self._accumulated))
            if self._accumulated[i].offsets.get("cut") is not None
            and not self._accumulated[i].offsets["cut"].is_empty
        ]
        if len(idxs) < 2:
            return 0
        fixed = 0
        for _round in range(2):
            geoms = [self._accumulated[i].offsets["cut"] for i in idxs]
            tree = STRtree(geoms)
            changed = False
            for k, g in enumerate(geoms):
                for m in tree.query(g.buffer(gap_px)):
                    m = int(m)
                    if m <= k:
                        continue
                    a, b = geoms[k], geoms[m]
                    if a.is_empty or b.is_empty:
                        continue
                    d = a.distance(b)
                    if d >= gap_px - tol_px:
                        continue
                    small, big = (k, m) if a.area <= b.area else (m, k)
                    sg = geoms[small]
                    shrunk = sg.difference(geoms[big].buffer(gap_px, join_style=1))
                    shrunk = shrunk.buffer(-1.0).buffer(1.0)
                    parts = [q for q in getattr(shrunk, "geoms", [shrunk]) if isinstance(q, Polygon) and not q.is_empty]
                    if not parts:
                        continue
                    largest = max(q.area for q in parts)
                    parts = [q for q in parts if q.area >= 0.05 * largest]
                    new = MultiPolygon(parts)
                    if new.area < 0.85 * sg.area:
                        continue  # 너무 많이 깎이면 건드리지 않음(저장 전 점검에서 알림)
                    res = self._accumulated[idxs[small]]
                    res.offsets["cut"] = new
                    res.adjustments = list(res.adjustments or []) + [
                        f"옆 칼선과 {min_gap_mm:g}mm 미만으로 붙어 있어, 떼는 여백이 끊어지지 않게 "
                        f"이 칼선을 {min_gap_mm:g}mm 떨어지도록 살짝 줄였습니다."
                    ]
                    geoms[small] = new
                    fixed += 1
                    changed = True
            if not changed:
                break
        return fixed

    def _merge_overlapping_borderless_cuts(self, start_idx):
        """이번 무테 자동 인식에서 만든 칼선들 중 서로 *겹치는* 것을 한 조각으로
        합친다 -- 따로 두면 두 칼선이 교차해 실제로 자를 수 없다. 겹치지 않는
        요소는 합치지 않는다(9/14 "요소마다 개별 칼선"). 합친 칼선도 그림 안쪽
        규칙을 지킨다(각 칼선을 여백만큼 되돌려 합친 뒤 다시 여백만큼 안쪽)."""
        from shapely.geometry import MultiPolygon, Polygon
        from shapely.ops import unary_union
        from shapely.strtree import STRtree
        from core.cutline_core import mm_to_px as _mm_to_px

        idxs = []
        for i in range(start_idx, len(self._accumulated)):
            cut = self._accumulated[i].offsets.get("cut")
            if cut is not None and not cut.is_empty:
                idxs.append(i)
        if len(idxs) < 2:
            return 0
        geoms = [self._accumulated[i].offsets["cut"] for i in idxs]
        parent = list(range(len(idxs)))

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        inset = _mm_to_px(self.style_margin_mm.get(), self.dpi.get())
        # 칼선끼리 실제로 겹칠 때만 합친다. (맞닿기만 한 요소까지 합치는
        # 것도 실제 손 칼선과 대조해 봤지만, 한 파일에서는 실제로 따로 잘린
        # 요소들이 합쳐져 오히려 틀어져서 되돌림.)
        tree = STRtree(geoms)
        for k, g in enumerate(geoms):
            for m in tree.query(g):
                m = int(m)
                if m <= k:
                    continue
                if g.intersection(geoms[m]).area > 1.0:
                    parent[find(m)] = find(k)
        comps = {}
        for k in range(len(idxs)):
            comps.setdefault(find(k), []).append(k)
        merged_count = 0
        for members in comps.values():
            if len(members) < 2:
                continue
            keep = idxs[min(members)]
            # 각 칼선을 여백만큼 되돌려(= 원래 그림 윤곽 근사) 합친 뒤 다시
            # 여백만큼 안쪽으로 -- 합친 칼선도 그림 밖으로 나가지 않는다.
            merged = unary_union([geoms[k].buffer(inset, join_style=1) for k in members])
            merged = merged.buffer(-inset, join_style=1)
            if merged.geom_type == "MultiPolygon":
                merged = max(merged.geoms, key=lambda q: q.area) if len(merged.geoms) == 1 else merged
            mp = MultiPolygon([merged]) if isinstance(merged, Polygon) else merged
            res = self._accumulated[keep]
            res.offsets["cut"] = mp
            res.design = mp
            res.adjustments = list(res.adjustments or []) + [
                f"서로 겹치거나 안에 들어간 칼선 {len(members)}개는 교차·이중 칼선이 되지 않도록 한 조각으로 합쳤습니다."
            ]
            keep_meta = self._grabcut_hint_meta.get(keep)
            boxes = [keep_meta["box_px"]] if keep_meta else []
            for k in members:
                i = idxs[k]
                if i == keep:
                    continue
                other = self._accumulated[i]
                other.offsets = {"cut": MultiPolygon([])}
                other.design = MultiPolygon([])
                meta = self._grabcut_hint_meta.pop(i, None)
                if meta is not None:
                    boxes.append(meta["box_px"])
                    grp = meta.get("group_indices")
                    if grp is not None and i in grp:
                        grp.remove(i)
            if keep_meta is not None and boxes:
                keep_meta["box_px"] = (
                    min(b[0] for b in boxes), min(b[1] for b in boxes),
                    max(b[2] for b in boxes), max(b[3] for b in boxes),
                )
            merged_count += 1
        return merged_count

    def _show_auto_detect_result(self, preview_png, added, n_total, errors):
        self._show_preview(preview_png, item_result=None)
        # 2026-09-29(실제 사용 중 발견): 예전 문구 "{찾은 수}개 중 {추가 수}개"는
        # 반복 칸 복사·누락 보충으로 추가 수가 찾은 수보다 많아져 "8개 중 57개"처럼
        # 말이 안 됐다 -- 실제로 추가된 칼선 수와 실패 수만 알린다.
        # 반복 칸 복사본·보충 요소까지 포함해 실제로 저장될 칼선 개수(내보내기 SVG와 같은 수)
        # 를 센다 -- ①②③ 흐름에서 "43개"라고 알렸는데 실제 칼선은 71개였다(9/29 PC 사용).
        n_parts = 0
        for it in self._accumulated:
            c = it.offsets.get("cut") if it is not None else None
            if c is not None and not c.is_empty:
                n_parts += len(getattr(c, "geoms", [c]))
        summary = f"자동 인식 완료: 칼선 {n_parts or added}개가 만들어졌습니다."
        if errors:
            summary += f" (실패 {len(errors)}개)"
        msg = summary
        if errors:
            msg += "\n\n실패한 항목:\n" + "\n".join(errors)
        self.status.set(msg)
        if errors:
            self._show_note_dialog(summary, errors, kind="error")
        # 2026-09-13(66차): "실패"는 아니지만(생성은 됐지만) 사람이 눈으로
        # 확인해야 하는 항목(_split_overlap_warning_note)이 있으면 별도
        # 대화상자로 따로 알린다 -- errors 대화상자와 섞으면 "생성 실패"로
        # 오해할 수 있어 분리함.
        # 2026-09-28 발견: combine_results는 각 항목의 안내 문구 앞에
        # "[N번째 영역] "을 붙여 합치는데, 여기서는 "⚠"로 *시작하는* 문구만
        # 골라서 -- 항목별 경고(겹치는 조각 경고 등)가 자동 인식 경로에서는
        # 한 번도 화면에 뜨지 않고 있었다. 이제 앞머리 번호를 떼고 같은
        # 문구끼리 묶어서(몇 번째 영역들인지 함께) 보여준다.
        warnings, first_region_by_warning = _collect_warning_notes(
            self._last_result.adjustments if self._last_result else []
        )
        if warnings:
            # 2026-09-28(멍푸 요청 "수동 기능 추가"): 이 경고들 중 "몸통 실루엣
            # 누락 의심"류(_missing_body_warning_notes)는 self.
            # _last_suspicious_regions_px에 원본 좌표가 그대로 남아있으므로,
            # 같은 순서로 다시 문구를 만들어 문구<->좌표 짝을 지어준다(문구
            # 안에 좌표가 그대로 박혀있어 사실상 고유하므로 이 매칭은 안전함).
            # 도안 겹침 경고(_split_overlap_warning_note)처럼 좌표가 없는
            # 항목은 그냥 None으로 남아 클릭해도 아무 일도 안 일어나는
            # 일반 문구로 표시된다.
            known_notes = _missing_body_warning_notes(self._last_suspicious_regions_px or [])
            box_by_note = dict(zip(known_notes, self._last_suspicious_regions_px or []))
            # 항목별 경고는 그 항목(첫 번째 영역)의 원래 박스로 이동/보정할 수 있게.
            for note, region_no in first_region_by_warning.items():
                meta = self._grabcut_hint_meta.get(region_no - 1)
                if meta is not None:
                    box_by_note[note] = meta["box_px"]
            self._show_suspicious_regions_dialog(warnings, box_by_note)

    def _on_auto_detect_none(self):
        self.generate_btn.configure(state="normal")
        self.status.set(
            "자동 인식된 도안이 없습니다. 배경과 도안의 구분이 뚜렷한지 확인하거나, "
            "직접 드래그로 선택해보세요."
        )
        self._show_note_dialog(
            "자동 인식된 도안이 없습니다",
            ["배경과 도안의 색 차이가 뚜렷한지 확인하거나, 직접 드래그로 영역을 선택해보세요."],
            kind="error",
        )

    def _on_export(self):
        if self._last_result is None:
            return
        # 2026-09-29(멍푸: "왜 중첩되면 안 되는지"): 저장 직전 칼선끼리 교차·이중
        # 칼선·2mm 미만 간격을 점검해, 있으면 위치와 함께 알리고 그래도 저장할지
        # 묻는다(실제 손 칼선 8개 파일에는 이런 곳이 한 곳도 없었다).
        try:
            report = check_cut_spacing(self._last_result.offsets.get("cut"), self._last_result.dpi)
            issues = summarize_cut_spacing(report)
        except Exception:  # noqa: BLE001 -- 점검 실패가 저장을 막지 않게
            issues = []
        if issues and not self._show_confirm_dialog(
            "칼선 점검: 인쇄 전에 확인이 필요한 곳이 있습니다",
            issues + ["그래도 이대로 저장할까요? (아니오를 누르면 돌아가서 고칠 수 있습니다)"],
        ):
            return
        # .ai로 불러온 경우 self.input_path는 내부 작업용 래스터 경로라서,
        # 그 대신 _choose_file이 기억해둔 원본 파일 이름을 우선 사용
        # (합성 시트처럼 원본이 없으면 기존과 동일하게 input_path에서 유도).
        base = self._export_basename or os.path.splitext(os.path.basename(self.input_path.get()))[0]
        default_name = base + "_cutlines.svg"
        out_path = filedialog.asksaveasfilename(
            title="SVG로 내보내기",
            defaultextension=".svg",
            initialfile=default_name,
            filetypes=[("SVG", "*.svg")],
        )
        if not out_path:
            return
        try:
            export_svg(self._last_result, out_path)
            self.status.set(f"저장됨: {out_path}")
            self._show_note_dialog("SVG로 저장했습니다", [out_path], kind="info")
        except Exception as e:  # noqa: BLE001
            self._report_exception_to_server("SVG 내보내기 저장 중")
            self._show_note_dialog("저장 중 오류가 발생했습니다", [str(e)], kind="error")


def _crash_log_path():
    d = os.path.join(os.path.expanduser("~"), ".cutline_studio")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:  # noqa: BLE001
        pass
    return os.path.join(d, "crash_log.txt")


def main():
    app = None
    try:
        app = CutLineApp()
        app.mainloop()
    except Exception:  # noqa: BLE001
        # 2026-08-27 피드백("한 번도 프로그램을 테스트조차 못 해봤어"):
        # --windowed 빌드는 콘솔이 없어서 시작 중 예외가 나면 진짜 아무
        # 표시도 없이 조용히 꺼져버림. 무엇이 문제였는지 다음엔 반드시 알 수
        # 있도록, 사용자 폴더 아래 항상 쓸 수 있는 위치에 전체 traceback을
        # 파일로 남기고(콘솔/print에 의존하지 않는 순수 파일 쓰기), Tk 자체는
        # 아직 살아있을 가능성이 높으니 메시지 박스로도 보여준다.
        #
        # 2026-08-31 수정: CutLineApp() 생성 도중(__init__ 중간)에 예외가
        # 나면 `app`은 이미 Tk 루트 창이 만들어진 채로 살아있는 상태다(Tk는
        # 프로세스당 루트를 하나만 지원하는 게 원칙이라, 그 상태에서 여기
        # 처럼 tk.Tk()를 또 만들면 두 번째 Tk 인터프리터가 생겨 Windows에서
        # 창이 새까맣게 보이거나 먹통이 되는 등 불안정해질 수 있음 -- "검은
        # 화면이 뜨고 닫으면 모든 창이 꺼진다"는 증상과 정확히 들어맞음).
        # 그래서 이미 만들어진 `app`이 있으면 새 루트를 만들지 않고 그걸
        # 그대로 재사용(숨겨져 있었다면 다시 보이게 한 뒤 메시지박스만
        # 띄우고 정리)한다.
        tb = traceback.format_exc()
        log_path = _crash_log_path()
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(tb)
        except Exception:  # noqa: BLE001
            pass
        try:
            if app is not None:
                try:
                    app.deiconify()
                except Exception:  # noqa: BLE001
                    pass
                messagebox.showerror(
                    APP_TITLE,
                    "프로그램을 시작하는 중 문제가 발생했습니다.\n\n"
                    f"자세한 내용이 다음 파일에 저장되었습니다:\n{log_path}\n\n{tb}",
                )
                app.destroy()
            else:
                _err_root = tk.Tk()
                _err_root.withdraw()
                messagebox.showerror(
                    APP_TITLE,
                    "프로그램을 시작하는 중 문제가 발생했습니다.\n\n"
                    f"자세한 내용이 다음 파일에 저장되었습니다:\n{log_path}\n\n{tb}",
                )
                _err_root.destroy()
        except Exception:  # noqa: BLE001
            pass
        raise


if __name__ == "__main__":
    main()
