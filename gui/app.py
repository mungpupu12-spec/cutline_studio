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
import threading
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.cutline_core import generate_cutlines, OffsetSpec
from core.cutline_types import CutlineType
from core.image_style import ImageStyle, DEFAULT_STYLE_MARGIN_MM
from core.interactive_cutline import (
    generate_cutline_auto,
    generate_cutline_by_style,
    generate_cutline_for_selection,
)
from core.svg_export import export_svg
from core.preview import render_preview
from core.guide import load_guide_from_ai
from core.sheet_layout import place_designs_into_sheet
from core.accumulate import combine_results
from core import license_client as lic

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

APP_TITLE = "CutLine Studio (prototype)"
PREVIEW_MAX_SIDE = 640


def _resource_dir():
    """프로젝트 루트 경로 -- 평소 소스에서 실행할 때와, PyInstaller로 만든
    .exe 안에서 실행될 때(2026-08-26, "exe 앱으로 만들어줘") 둘 다 올바르게
    동작해야 함. PyInstaller onefile 실행 시 실제 파일들은 매번 임시 폴더
    (`sys._MEIPASS`)에 풀리므로, 그 안에 --add-data로 함께 넣어둔 assets/
    같은 리소스를 찾으려면 `__file__` 대신 이 값을 기준으로 삼아야 함."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
TEXT_PRIMARY = "#0A0A0C"  # 거의 순검정
TEXT_SECONDARY = "#6E6E74"  # 그레이
ACCENT = "#0047AB"        # 브랜드 포인트 컬러 (코발트 블루)
ACCENT_HOVER = "#003682"
ACCENT_SOFT = "#E8F0FB"   # 보조 버튼 hover / 배경 강조용 아주 연한 코발트 톤
COLOR_SAFETY = "#16A44A"
COLOR_CUT = "#DC2626"
COLOR_BLEED = "#2563EB"
CANVAS_BG = "#EFEFF1"
FONT_FAMILY = "Malgun Gothic"

# 2026-08-26 추가 피드백: "폰트 0.8 포인트 키우고" -- 기존 폰트 크기 전부에
# 이 값을 더한 뒤 반올림(tkinter 폰트 크기는 정수만 받으므로).
FONT_SIZE_BUMP = 0.8


def _bumped(size):
    return round(size + FONT_SIZE_BUMP)


# 2026-08-26 추가 피드백: "버튼도 더 라운딩 넣고 크기 10mm 키워" -- 일반적인
# 모니터 기준(96dpi, 배율 100%)으로 10mm ≈ 37.8px 환산. 실제 클릭 대상인
# 1차/2차 버튼(가이드 선택/이 영역 추가/SVG로 내보내기 등)에 적용하고, 둥근
# 정도(corner_radius)도 함께 키움. +/- 스텝퍼처럼 작은 보조 컨트롤은 옆의
# 입력창과 비례가 깨지지 않도록 더 작은 폭으로만 키움.
MM_TO_PX_AT_96DPI = 96.0 / 25.4
BUTTON_SIZE_BUMP_PX = round(10 * MM_TO_PX_AT_96DPI)  # ~38px

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")


class CutLineApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        # 2026-08-26 피드백: "화면이 너무 크다" + "화면비율도 1:2로 맞춰"
        # (확인 결과: 창 전체를 가로로 넓은 2:1 비율로, 지금의 좌/우
        # 나란히 배치 구조는 그대로 유지) -- 기존 1180x800보다 작고 2:1
        # 비율인 1000x500으로 축소.
        self.geometry("1000x500")
        self.minsize(900, 450)
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
        self.font_caption = ctk.CTkFont(family=FONT_FAMILY, size=_bumped(11))
        self.font_button = ctk.CTkFont(family=FONT_FAMILY, size=_bumped(13), weight="bold")

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

        # 작업 종류(2026-08-26, 칼선 작업의 기본 원리 재정립): 도안 하나에
        # 외곽선 하나를 그 이미지의 실제 모양에 맞춰 만드는 것이 기본이고,
        # 그 "하나"가 무엇을 가리키는지에 따라 세 갈래로 나뉜다.
        #   완칼(FULL_CUT)   -- 이미지 단독 하나(또는 선택한 요소 하나)의
        #                       실제 실루엣을 그대로 따라가는 칼선.
        #   스티커(STICKER)  -- 도안 안에서 드래그로 고른 요소 하나의 칼선.
        #                       유테(테두리 있음)/무테(테두리 없음) 중 선택.
        #   도무송(DOMUSONG) -- 여백에 놓인 다른 이미지 등을 정해진 5개 도형
        #                       (사각형/정사각/타원형/정원/완칼) 중 하나로
        #                       재단.
        # 드래그 -> 옵션 선택 -> "이 영역 추가"를 반복하면, 그 각각의 칼선이
        # 전부 한 파일에 누적된다(아래 self._accumulated).
        self.job_type = tk.StringVar(value="STICKER")

        # 스티커(STICKER)일 때만 쓰는 세부 옵션: 자동 감지(드래그한 영역의
        # 내용물 모양만 보고 유테/무테를 스스로 판단 -- core.style_classify)
        # / 유테(테두리 외곽선이 있는 이미지 -- 실루엣 바깥쪽 1.5mm) / 무테
        # (테두리 외곽선이 없는 이미지 -- 셀 안쪽 1.5mm).
        self.image_style = tk.StringVar(value="AUTO")
        self.style_margin_mm = tk.DoubleVar(value=DEFAULT_STYLE_MARGIN_MM)

        # 도무송(DOMUSONG)일 때만 쓰는 세부 옵션: 5개 도형 중 선택. 완칼
        # (FULL_CUT) 잡타입은 이 값을 쓰지 않고 항상 실제 실루엣을 따라간다.
        self.cutline_type = tk.StringVar(value=CutlineType.RECTANGLE.name)
        # 유색/복잡한 배경에서도 선택 영역 안의 실제 도안만 자동으로 분리
        # (GrabCut). 끄면 드래그한 사각형 자체를 그대로 도형 크기 기준으로 씀.
        self.use_grabcut = tk.BooleanVar(value=True)

        # 정밀도(업샘플 배율) -- core.cutline_core.auto_supersample이 이미지/
        # 선택 영역이 커지면 이 값을 자동으로 낮춘다(처리 속도 보호). 여기서
        # 고르는 값은 "요청하는 상한"이지 무조건 그대로 쓰이는 값이 아님 --
        # 자동으로 낮아지면 완료 후 상태 메시지에 그대로 표시됨.
        self.precision = tk.IntVar(value=4)

        # 가이드 파일 + 여러 도안 -> 시트 합성 (core.guide/core.sheet_layout)
        self.guide_path = tk.StringVar()
        self._design_paths = []
        self.designs_status = tk.StringVar(value="선택된 도안 없음")
        # 기본 OFF -- 2026-08-26 피드백: "도안을 슬롯에 맞춰 몰래 축소하지
        # 않는다". 슬롯보다 큰 도안은 기본적으로 위반으로 보고되고, 자동
        # 축소를 원하면 이 체크박스를 직접 켜야 함(그리고 실제로 축소되면
        # 얼마나 줄었는지 항상 보고됨 -- core.sheet_layout의 resize_note).
        self.auto_fit_sheet = tk.BooleanVar(value=False)
        self.fit_margin_mm = tk.DoubleVar(value=0.0)

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
        self._selection_canvas_rect = None  # (x0,y0,x1,y1) in CANVAS px, while dragging
        self._selection_px = None  # (x0,y0,x1,y1) in ORIGINAL IMAGE px, once released
        self._drag_start = None
        self._selection_rect_id = None

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
            text_color=TEXT_SECONDARY, anchor="w", justify="left", wraplength=380,
        )
        status_label.pack(fill="x", pady=(0, 14))

        ctk.CTkLabel(inner, text="라이선스 키", font=self.font_caption, text_color=TEXT_SECONDARY, anchor="w").pack(fill="x")
        key_var = tk.StringVar(value=lic.get_cached_license_key())
        key_entry = ctk.CTkEntry(inner, textvariable=key_var, width=340, height=36, corner_radius=10)
        key_entry.pack(fill="x", pady=(2, 12))

        code_var = tk.StringVar()
        if result.state == "locked":
            ctk.CTkLabel(
                inner, text="재활성화 코드 (발급처에서 받은 코드)", font=self.font_caption,
                text_color=TEXT_SECONDARY, anchor="w",
            ).pack(fill="x")
            code_entry = ctk.CTkEntry(inner, textvariable=code_var, width=340, height=36, corner_radius=10)
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
        card.pack(fill="x", pady=(0, 14), padx=2)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=18, pady=16)
        ctk.CTkLabel(
            inner, text=title, font=self.font_section, text_color=TEXT_PRIMARY,
            anchor="w", justify="left",
        ).pack(fill="x")
        if subtitle:
            ctk.CTkLabel(
                inner, text=subtitle, font=self.font_caption, text_color=TEXT_SECONDARY,
                anchor="w", justify="left", wraplength=300,
            ).pack(fill="x", pady=(4, 0))
        return inner

    def _btn_primary(self, parent, text, command):
        """핵심 동작(추가/미리보기, 내보내기)에만 쓰는 강조 버튼. 2026-08-26
        피드백("버튼도 더 라운딩 넣고 크기 10mm 키워")에 맞춰 기존 44px
        높이에 BUTTON_SIZE_BUMP_PX(~10mm)를 더하고, 더 둥글게 처리."""
        return ctk.CTkButton(
            parent, text=text, command=command, font=self.font_button,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#FFFFFF",
            corner_radius=24, height=44 + BUTTON_SIZE_BUMP_PX,
        )

    def _btn_secondary(self, parent, text, command):
        """나머지 보조 동작에 쓰는 아웃라인 버튼. 같은 이유로 기존 36px
        높이에 BUTTON_SIZE_BUMP_PX를 더하고, 더 둥글게 처리."""
        return ctk.CTkButton(
            parent, text=text, command=command, font=self.font_body,
            fg_color="transparent", hover_color=ACCENT_SOFT, text_color=TEXT_PRIMARY,
            border_width=1, border_color=BORDER, corner_radius=20,
            height=36 + BUTTON_SIZE_BUMP_PX,
        )

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
            return bool(data.get("tutorial_seen"))
        except Exception:  # noqa: BLE001 -- 설정이 없거나 손상됐으면 그냥 다시 보여줌(안전한 쪽)
            return False

    def _save_tutorial_seen(self, value):
        import json

        path = self._tutorial_config_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"tutorial_seen": bool(value)}, f, ensure_ascii=False)
        except Exception:  # noqa: BLE001 -- 저장 실패해도 앱 동작에는 영향 없음(다음에도 다시 뜰 뿐)
            traceback.print_exc()

    def _show_tutorial_if_needed(self):
        if self._load_tutorial_seen():
            return
        self._open_tutorial_dialog()

    def _open_tutorial_dialog(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("CutLine Studio 사용법")
        dialog.geometry("560x600")
        dialog.resizable(False, False)
        try:
            dialog.configure(fg_color=BG_APP)
        except Exception:  # noqa: BLE001
            pass
        try:
            dialog.transient(self)
        except Exception:  # noqa: BLE001
            pass

        wrap = ctk.CTkFrame(dialog, fg_color=BG_CARD, corner_radius=16, border_width=1, border_color=BORDER)
        wrap.pack(fill="both", expand=True, padx=16, pady=16)

        ctk.CTkLabel(
            wrap, text="처음이신가요? 이렇게 쓰면 됩니다", font=self.font_section,
            text_color=TEXT_PRIMARY, anchor="w",
        ).pack(fill="x", padx=22, pady=(22, 6))

        steps = [
            "1. \"도안 파일\"에서 어도비 일러스트(.ai) 또는 PNG/JPG를 고릅니다.",
            "2. \"작업 종류\"에서 완칼 / 스티커 / 도무송 중 하나를 고릅니다.",
            "3. 완칼(전체 적용)이 아니라면, 오른쪽 미리보기에서 칼선을 만들 영역을 마우스로 드래그합니다.",
            "4. 스티커라면 유테/무테를, 도무송이라면 도형을 고릅니다.",
            "5. \"이 영역 추가 + 미리보기\"를 누르면 결과가 쌓입니다 -- 다른 영역도 같은 방식으로 반복해서 추가할 수 있습니다.",
            "6. 다 됐으면 \"SVG로 내보내기\"로 저장해서 일러스트레이터에서 엽니다.",
        ]
        for s in steps:
            ctk.CTkLabel(
                wrap, text=s, font=self.font_body, text_color=TEXT_PRIMARY, anchor="w",
                justify="left", wraplength=480,
            ).pack(fill="x", padx=22, pady=5)

        footer = ctk.CTkFrame(wrap, fg_color="transparent")
        footer.pack(fill="x", padx=22, pady=(16, 22), side="bottom")

        def _on_close():
            if self.tutorial_dont_show_again.get():
                self._save_tutorial_seen(True)
            dialog.destroy()

        ctk.CTkCheckBox(
            footer, text="다시 보지 않기", variable=self.tutorial_dont_show_again,
            font=self.font_body, text_color=TEXT_PRIMARY, fg_color=ACCENT, hover_color=ACCENT_HOVER,
        ).pack(side="left")
        self._btn_primary(footer, "확인", _on_close).pack(side="right")

        dialog.protocol("WM_DELETE_WINDOW", _on_close)
        try:
            dialog.grab_set()
        except Exception:  # noqa: BLE001
            pass

        self.update_idletasks()
        try:
            px, py = self.winfo_rootx(), self.winfo_rooty()
            pw, ph = self.winfo_width(), self.winfo_height()
            dw, dh = dialog.winfo_width(), dialog.winfo_height()
            dialog.geometry(f"+{px + max(0, (pw - dw) // 2)}+{py + max(0, (ph - dh) // 2)}")
        except Exception:  # noqa: BLE001 -- 위치 계산 실패해도 대화상자 자체는 뜸(장식일 뿐)
            pass

    # ------------------------------------------------------------------
    def _build_layout(self):
        # --- 상단 헤더 바 (브랜드 + 짧은 설명) ---------------------------------
        header_bar = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=0, height=68)
        header_bar.pack(side="top", fill="x")
        header_bar.pack_propagate(False)
        title_wrap = ctk.CTkFrame(header_bar, fg_color="transparent")
        title_wrap.pack(side="left", padx=24, pady=10)
        ctk.CTkLabel(
            title_wrap, text="CutLine Studio", font=self.font_title, text_color=TEXT_PRIMARY,
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_wrap, text="완칼 · 스티커 · 도무송 칼선 자동 생성",
            font=self.font_caption, text_color=TEXT_SECONDARY,
        ).pack(anchor="w")
        ctk.CTkFrame(header_bar, fg_color=BORDER, height=1, corner_radius=0).pack(
            side="bottom", fill="x"
        )

        # --- 본문: 왼쪽 스크롤 조작 패널 + 오른쪽 미리보기 카드 -------------------
        body = ctk.CTkFrame(self, fg_color=BG_APP, corner_radius=0)
        body.pack(side="top", fill="both", expand=True)

        # 조작 패널을 CTkScrollableFrame으로 감싼 게 이번 개편의 핵심 -- 섹션이
        # 몇 개가 되든, 창 높이가 얼마든, 스크롤바가 항상 생기므로 버튼이
        # 화면 밖으로 잘려서 눌리지 않는 문제 자체가 구조적으로 사라진다.
        left_col = ctk.CTkScrollableFrame(
            body, width=380, fg_color=BG_APP, corner_radius=0,
            scrollbar_button_color=BORDER, scrollbar_button_hover_color=TEXT_SECONDARY,
        )
        left_col.pack(side="left", fill="y", padx=(16, 8), pady=16)

        right_col = ctk.CTkFrame(
            body, fg_color=BG_CARD, corner_radius=16, border_width=1, border_color=BORDER,
        )
        right_col.pack(side="left", fill="both", expand=True, padx=(8, 16), pady=16)

        ctk.CTkLabel(
            right_col, text="미리보기", font=self.font_section, text_color=TEXT_PRIMARY, anchor="w",
        ).pack(fill="x", padx=20, pady=(18, 8))
        canvas_wrap = ctk.CTkFrame(right_col, fg_color=CANVAS_BG, corner_radius=12)
        canvas_wrap.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        self.preview_canvas = tk.Canvas(canvas_wrap, background=CANVAS_BG, highlightthickness=0)
        self.preview_canvas.pack(fill="both", expand=True, padx=2, pady=2)
        self._canvas_placeholder = self.preview_canvas.create_text(
            10, 10, anchor="nw", text="미리보기가 여기에 표시됩니다", fill=TEXT_SECONDARY
        )
        self.preview_canvas.bind("<ButtonPress-1>", self._on_canvas_press)
        self.preview_canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self.preview_canvas.bind("<ButtonRelease-1>", self._on_canvas_release)

        # ---- 0. (선택) 가이드 + 여러 도안 -> 시트 합성 --------------------------
        sec0 = self._section(
            left_col, "가이드 + 여러 도안 → 시트 합성",
            "선택 사항 -- 규격 시트에 여러 도안을 자동 배치합니다.",
        )
        guide_row = ctk.CTkFrame(sec0, fg_color="transparent")
        guide_row.pack(fill="x", pady=(10, 6))
        ctk.CTkEntry(
            guide_row, textvariable=self.guide_path, font=self.font_body, corner_radius=12,
            border_width=1, border_color=BORDER, fg_color="#FFFFFF", height=34,
        ).pack(side="left", fill="x", expand=True)
        self._btn_secondary(guide_row, "가이드 선택", self._choose_guide).pack(
            side="left", padx=(8, 0)
        )
        designs_row = ctk.CTkFrame(sec0, fg_color="transparent")
        designs_row.pack(fill="x", pady=(0, 6))
        self._btn_secondary(designs_row, "도안 여러 장 선택", self._choose_designs).pack(side="left")
        ctk.CTkLabel(
            designs_row, textvariable=self.designs_status, font=self.font_caption,
            text_color=TEXT_SECONDARY,
        ).pack(side="left", padx=(10, 0))
        fit_row = ctk.CTkFrame(sec0, fg_color="transparent")
        fit_row.pack(fill="x", pady=(4, 0))
        ctk.CTkCheckBox(
            fit_row, text="슬롯보다 크면 자동 축소(auto_fit)", variable=self.auto_fit_sheet,
            font=self.font_body, text_color=TEXT_PRIMARY, fg_color=ACCENT, hover_color=ACCENT_HOVER,
        ).pack(anchor="w")
        self._make_number_field(
            sec0, "여백", self.fit_margin_mm, unit="mm", from_=0.0, to=20.0, increment=0.5
        )
        ctk.CTkLabel(
            sec0,
            text="기본은 자동 축소 끔 -- 슬롯보다 큰 도안은 몰래 줄이지 않고 그대로 "
            "위반으로 보고합니다. 켜면 실제로 줄어든 만큼(전/후 mm)을 항상 보고합니다.",
            font=self.font_caption, text_color=TEXT_SECONDARY, anchor="w", justify="left",
            wraplength=300,
        ).pack(fill="x", pady=(2, 10))
        self._btn_secondary(sec0, "시트 합성 미리보기", self._on_build_sheet).pack(fill="x")

        # ---- 1. 도안 파일 -------------------------------------------------------
        sec1 = self._section(left_col, "도안 파일", "주 형식: 어도비 일러스트 (.ai) -- PNG/JPG도 가능")
        file_row = ctk.CTkFrame(sec1, fg_color="transparent")
        file_row.pack(fill="x", pady=(10, 6))
        ctk.CTkEntry(
            file_row, textvariable=self.input_path, font=self.font_body, corner_radius=12,
            border_width=1, border_color=BORDER, fg_color="#FFFFFF", height=34,
        ).pack(side="left", fill="x", expand=True)
        self._btn_secondary(file_row, "찾아보기", self._choose_file).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(
            sec1,
            text="어도비 일러스트(.ai, PDF 호환 저장)를 고르면 파일 안의 실제 인쇄용 이미지를 "
            "그대로 추출해서 씁니다(해상도 그대로, 다시 렌더링하지 않음). PNG/JPG는 투명 배경 "
            "또는 유색/복잡한 배경 모두 가능합니다.",
            font=self.font_caption, text_color=TEXT_SECONDARY, anchor="w", justify="left",
            wraplength=300,
        ).pack(fill="x")

        # ---- 2. 작업 해상도 ------------------------------------------------------
        sec2 = self._section(left_col, "작업 해상도")
        self._make_number_field(sec2, "DPI", self.dpi, unit="", from_=72, to=1200, increment=1)
        precision_row = ctk.CTkFrame(sec2, fg_color="transparent")
        precision_row.pack(fill="x", pady=(8, 0))
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
            precision_row, text="x (클수록 정밀·느림, 기본 4)",
            font=self.font_caption, text_color=TEXT_SECONDARY,
        ).pack(side="left", padx=(6, 0))
        ctk.CTkLabel(
            sec2,
            text="도안/선택 영역이 아주 크면 처리 속도 보호를 위해 이 값이 자동으로 "
            "낮아질 수 있고, 그러면 완료 메시지에 표시됩니다.",
            font=self.font_caption, text_color=TEXT_SECONDARY, anchor="w", justify="left",
            wraplength=300,
        ).pack(fill="x", pady=(6, 0))

        # ---- 3. 작업 종류 (칼선 기준) --------------------------------------------
        sec3 = self._section(left_col, "작업 종류", "칼선을 만들 기준을 고르세요.")
        for value, label in (
            ("FULL_CUT", "완칼 -- 이미지(또는 선택 요소) 하나의 실제 실루엣을 그대로"),
            ("STICKER", "스티커 -- 도안 안 요소 하나 선택, 유테/무테 중 선택"),
            ("DOMUSONG", "도무송 -- 선택 영역을 5개 도형 중 하나로 재단"),
        ):
            ctk.CTkRadioButton(
                sec3, text=label, value=value, variable=self.job_type,
                font=self.font_body, text_color=TEXT_PRIMARY, fg_color=ACCENT,
                hover_color=ACCENT_HOVER,
            ).pack(anchor="w", pady=4)
        ctk.CTkLabel(
            sec3,
            text="완칼은 아무 것도 드래그하지 않으면 이미지 전체에 적용됩니다. "
            "스티커/도무송은 오른쪽에서 요소를 드래그로 선택해야 합니다.",
            font=self.font_caption, text_color=TEXT_SECONDARY, anchor="w", justify="left",
            wraplength=300,
        ).pack(fill="x", pady=(4, 0))

        # ---- 4. 오프셋 간격 (완칼/도무송) -----------------------------------------
        sec4 = self._section(
            left_col, "오프셋 간격", "완칼/도무송, mm, 도안 기준 바깥쪽"
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

        # ---- 5. 스티커 세부 옵션 --------------------------------------------------
        sec5 = self._section(
            left_col, "스티커 세부 옵션", "작업 종류가 '스티커'일 때만 적용됩니다."
        )
        for value, label in (
            ("AUTO", "자동 감지 (드래그한 도안 모양을 보고 유테/무테 스스로 판단)"),
            ("LINE_ART", "유테 (테두리 외곽선이 있는 이미지 -- 실루엣 바깥쪽)"),
            ("BORDERLESS", "무테 (테두리 외곽선이 없는 이미지 -- 셀 안쪽)"),
        ):
            ctk.CTkRadioButton(
                sec5, text=label, value=value, variable=self.image_style,
                font=self.font_body, text_color=TEXT_PRIMARY, fg_color=ACCENT,
                hover_color=ACCENT_HOVER,
            ).pack(anchor="w", pady=4)
        self._make_number_field(
            sec5, "유테/무테 간격", self.style_margin_mm, unit="mm (기본 1.5)",
            from_=0.1, to=20.0, increment=0.1,
        )

        # ---- 6. 도무송 세부 옵션 --------------------------------------------------
        sec6 = self._section(
            left_col, "도무송 세부 옵션", "작업 종류가 '도무송'일 때만 적용됩니다."
        )
        for value, label in (
            (CutlineType.FULL_CUT.name, "완칼 (외곽 실루엣을 그대로)"),
            (CutlineType.RECTANGLE.name, "직사각형"),
            (CutlineType.SQUARE.name, "정사각형"),
            (CutlineType.ELLIPSE.name, "타원형"),
            (CutlineType.CIRCLE.name, "정원형"),
        ):
            ctk.CTkRadioButton(
                sec6, text=label, value=value, variable=self.cutline_type,
                font=self.font_body, text_color=TEXT_PRIMARY, fg_color=ACCENT,
                hover_color=ACCENT_HOVER,
            ).pack(anchor="w", pady=4)
        ctk.CTkCheckBox(
            sec6, text="유색/복잡한 배경에서도 자동 분리 (GrabCut)", variable=self.use_grabcut,
            font=self.font_body, text_color=TEXT_PRIMARY, fg_color=ACCENT, hover_color=ACCENT_HOVER,
        ).pack(anchor="w", pady=(8, 0))
        sel_btn_row = ctk.CTkFrame(sec6, fg_color="transparent")
        sel_btn_row.pack(fill="x", pady=(10, 0))
        self._btn_secondary(sel_btn_row, "선택 영역 지우기", self._clear_selection).pack(
            side="left", fill="x", expand=True, padx=(0, 6)
        )
        self._btn_secondary(sel_btn_row, "원본 다시 보기", self._show_source_again).pack(
            side="left", fill="x", expand=True
        )

        # ---- 7. 누적 ---------------------------------------------------------------
        sec7 = self._section(
            left_col, "누적", "여러 번 드래그해도 전부 한 파일에 쌓입니다."
        )
        ctk.CTkLabel(
            sec7, textvariable=self.accum_status, font=self.font_body, text_color=ACCENT,
            anchor="w",
        ).pack(fill="x", pady=(6, 10))
        self.generate_btn = self._btn_primary(sec7, "이 영역 추가 + 미리보기", self._on_generate)
        self.generate_btn.pack(fill="x", pady=(0, 8))
        self._btn_secondary(sec7, "누적 초기화 (모두 지우기)", self._on_reset_accumulation).pack(
            fill="x", pady=(0, 10)
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
            text_color=TEXT_SECONDARY, anchor="w", justify="left", wraplength=300,
        ).pack(fill="x", padx=18, pady=16)

    # ---- 가이드 + 여러 도안 -> 시트 합성 ------------------------------
    def _choose_guide(self):
        path = filedialog.askopenfilename(
            title="가이드 파일 선택 (.ai/PDF 호환)",
            filetypes=[("가이드 파일", "*.ai *.pdf"), ("모든 파일", "*.*")],
        )
        if path:
            self.guide_path.set(path)

    def _choose_designs(self):
        paths = filedialog.askopenfilenames(
            title="도안 파일 선택 (슬롯 순서대로 여러 장)",
            filetypes=[("PNG/JPG", "*.png *.jpg *.jpeg"), ("모든 파일", "*.*")],
        )
        if paths:
            self._design_paths = list(paths)
            self.designs_status.set(f"{len(paths)}장 선택됨")

    def _on_build_sheet(self):
        guide_path = self.guide_path.get().strip()
        if not guide_path or not os.path.isfile(guide_path):
            messagebox.showwarning(APP_TITLE, "먼저 가이드 파일을 선택하세요.")
            return
        if not self._design_paths:
            messagebox.showwarning(APP_TITLE, "도안 파일을 하나 이상 선택하세요.")
            return
        self.status.set("시트 합성 중...")
        threading.Thread(target=self._run_build_sheet, args=(guide_path,), daemon=True).start()

    def _run_build_sheet(self, guide_path):
        try:
            guide = load_guide_from_ai(guide_path)
            out_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "_sheet_composite.png"
            )
            composite_path, placements = place_designs_into_sheet(
                guide,
                self._design_paths,
                dpi=self.dpi.get(),
                out_path=out_path,
                auto_fit=self.auto_fit_sheet.get(),
                fit_margin_mm=self.fit_margin_mm.get(),
            )
            notes = []
            for p in placements:
                if p.violation:
                    notes.append(p.violation)
                if p.resize_note:
                    notes.append(p.resize_note)
            self.after(0, self._on_sheet_built, composite_path, notes)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.after(0, self._on_error, str(e))

    def _on_sheet_built(self, composite_path, notes):
        self.input_path.set(composite_path)
        self.is_vector.set(False)
        # 합성 결과는 특정 "원본 파일"이 없는 새 파일이므로, 내보내기 기본
        # 파일명은 이 합성 파일 이름 자체로 되돌림(_on_export 참고).
        self._export_basename = None
        self.status.set(f"시트 합성 완료: {os.path.basename(composite_path)}")
        self._clear_selection()
        self._on_reset_accumulation()  # 새 파일이므로 이전 파일의 누적 항목은 폐기
        try:
            self._load_source_preview(composite_path)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        if notes:
            joined = "\n".join(notes)
            self.status.set(
                f"시트 합성 완료: {os.path.basename(composite_path)}\n\n[슬롯 안내]\n{joined}"
            )
            messagebox.showinfo(APP_TITLE, joined)
        else:
            self.status.set(
                f"시트 합성 완료: {os.path.basename(composite_path)} -- 아래에서 이어서 "
                f"칼선 생성을 진행하세요 (합성 결과가 '1. 도안 파일'로 바로 들어갑니다)."
            )

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
            self.preview_canvas.config(width=PREVIEW_MAX_SIDE, height=200)
            self.preview_canvas.create_text(
                10, 10, anchor="nw", text="SVG는 영역 드래그 선택을 지원하지 않습니다.", fill=TEXT_SECONDARY
            )

    def _run_load_ai(self, ai_path):
        """어도비 일러스트(.ai) 파일 안의 실제 인쇄용 이미지를 추출해서
        내부 작업용 PNG로 저장 -- core.ai_import 참고. 파일이 크면 시간이
        걸릴 수 있어 기존 시트 합성/가이드 로딩과 같은 방식으로 백그라운드
        스레드에서 처리."""
        try:
            from core.ai_import import load_ai_as_raster

            raster_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "_ai_source_raster.png"
            )
            load_ai_as_raster(ai_path, raster_path, dpi=self.dpi.get())
            self.after(0, self._on_ai_loaded, ai_path, raster_path)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.after(0, self._on_error, f"어도비 일러스트 파일을 여는 중 오류:\n{e}")

    def _on_ai_loaded(self, ai_path, raster_path):
        self.input_path.set(raster_path)
        self.status.set(
            f"불러옴: {os.path.basename(ai_path)} -- 파일 안의 실제 인쇄용 이미지를 "
            f"추출해서 작업합니다 (해상도 그대로)."
        )
        if Image is not None:
            try:
                self._load_source_preview(raster_path)
            except Exception:  # noqa: BLE001
                traceback.print_exc()

    def _load_source_preview(self, path):
        """Show the raw (not-yet-processed) image on the canvas so the
        artist can drag a selection rectangle over one design BEFORE
        generating -- this is the "영역을 드래그 해서 생성" workflow."""
        img = Image.open(path).convert("RGBA")
        w, h = img.size
        scale = min(PREVIEW_MAX_SIDE / w, PREVIEW_MAX_SIDE / h, 1.0)
        self._display_scale = scale
        self._source_image = img
        disp = img if scale >= 1.0 else img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
        self._preview_photo = ImageTk.PhotoImage(disp)
        self.preview_canvas.delete("all")
        self.preview_canvas.config(width=disp.width, height=disp.height)
        self.preview_canvas.create_image(0, 0, anchor="nw", image=self._preview_photo)
        self._selection_rect_id = None

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

    # ---- drag-select on the canvas -----------------------------------
    def _clear_selection(self):
        self._selection_px = None
        self._drag_start = None
        if self._selection_rect_id is not None:
            self.preview_canvas.delete(self._selection_rect_id)
            self._selection_rect_id = None

    def _on_canvas_press(self, event):
        if self._source_image is None:
            return  # nothing loaded yet, or currently showing a rendered result/SVG
        self._drag_start = (event.x, event.y)
        if self._selection_rect_id is not None:
            self.preview_canvas.delete(self._selection_rect_id)
        self._selection_rect_id = self.preview_canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline=ACCENT, width=2, dash=(4, 2)
        )

    def _on_canvas_drag(self, event):
        if self._drag_start is None or self._selection_rect_id is None:
            return
        x0, y0 = self._drag_start
        self.preview_canvas.coords(self._selection_rect_id, x0, y0, event.x, event.y)

    def _on_canvas_release(self, event):
        if self._drag_start is None:
            return
        x0, y0 = self._drag_start
        x1, y1 = event.x, event.y
        self._drag_start = None
        cx0, cx1 = sorted((x0, x1))
        cy0, cy1 = sorted((y0, y1))
        if (cx1 - cx0) < 4 or (cy1 - cy0) < 4:
            # treated as an accidental click, not a real selection
            self._clear_selection()
            self.status.set("선택 영역이 너무 작아 취소됐습니다. 다시 드래그하세요.")
            return
        scale = self._display_scale
        self._selection_px = (cx0 / scale, cy0 / scale, cx1 / scale, cy1 / scale)
        self.status.set(
            "선택 영역이 지정됐습니다 -- 왼쪽에서 칼선 종류를 고르고 "
            "'이 영역 추가 + 미리보기'를 누르세요."
        )

    def _validate_inputs(self):
        path = self.input_path.get().strip()
        if not path or not os.path.isfile(path):
            messagebox.showwarning(APP_TITLE, "먼저 유효한 도안 파일을 선택하세요.")
            return None
        if self.job_type.get() == "STICKER":
            # 스티커(유테/무테)는 세이프티·칼선·블리딩을 아예 쓰지 않고
            # style_margin_mm 하나만 쓰므로, 그 셋의 순서 검사는 완칼/도무송
            # 잡타입일 때만 의미가 있음.
            return path
        vals = {
            "safety": self.safety_mm.get(),
            "cut": self.cut_mm.get(),
            "bleed": self.bleed_mm.get(),
        }
        if not (vals["safety"] < vals["cut"] < vals["bleed"]):
            proceed = messagebox.askyesno(
                APP_TITLE,
                "일반적으로 세이프티 < 칼선 < 블리딩 순서로 커집니다.\n"
                "현재 값은 이 순서가 아닙니다. 계속 진행할까요?",
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

    def _generate_one_item(self, path):
        """
        Build ONE CutlineResult from the CURRENT job type + sub-option +
        (if any) drag-selection -- does NOT touch self._accumulated. Called
        once per "이 영역 추가" press; the caller appends the result and
        recombines (see _run_generate).
        """
        job = self.job_type.get()

        if self.is_vector.get():
            # SVG 입력은 이 프로토타입에서 드래그 선택(스티커/도무송 세부
            # 요소 선택)을 지원하지 않음 -- 항상 벡터 전체를 완칼처럼 트레이싱.
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

        if job == "STICKER":
            if self._selection_px is None:
                raise ValueError(
                    "스티커는 도안 안 요소 하나를 드래그로 지정해야 합니다 -- 오른쪽 "
                    "이미지에서 영역을 먼저 선택하세요."
                )
            style = self.image_style.get()
            if style == "AUTO":
                return generate_cutline_auto(
                    image_path=path,
                    dpi=self.dpi.get(),
                    selection_px=self._selection_px,
                    margin_mm=self.style_margin_mm.get(),
                    supersample=self.precision.get(),
                )
            return generate_cutline_by_style(
                image_path=path,
                style=ImageStyle[style],
                dpi=self.dpi.get(),
                selection_px=self._selection_px,
                margin_mm=self.style_margin_mm.get(),
                supersample=self.precision.get(),
            )

        # DOMUSONG
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
        cutline_type = CutlineType[self.cutline_type.get()]
        return generate_cutline_for_selection(
            image_path=path,
            selection_px=self._selection_px,
            cutline_type=cutline_type,
            dpi=self.dpi.get(),
            offset_mm=offset_mm,
            use_grabcut=self.use_grabcut.get(),
            supersample=self.precision.get(),
        )

    def _run_generate(self, path):
        try:
            item_result = self._generate_one_item(path)
            self._accumulated.append(item_result)
            combined = combine_results(self._accumulated)
            preview_png = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "_last_preview.png"
            )
            render_preview(
                combined,
                preview_png,
                original_image_path=None if self.is_vector.get() else path,
            )
            self._last_result = combined
            self.after(0, self._show_preview, preview_png, item_result)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.after(0, self._on_error, str(e))

    def _show_preview(self, preview_png, item_result=None):
        self._clear_selection()
        self._source_image = None  # showing a rendered result now, not a raw source -- no new drag-select until a file is (re)loaded
        if Image is not None:
            img = Image.open(preview_png)
            w, h = img.size
            scale = min(PREVIEW_MAX_SIDE / w, PREVIEW_MAX_SIDE / h, 1.0)
            self._display_scale = scale
            if scale < 1.0:
                img = img.resize((int(w * scale), int(h * scale)))
            self._preview_photo = ImageTk.PhotoImage(img)
            self.preview_canvas.delete("all")
            self.preview_canvas.config(width=img.width, height=img.height)
            self.preview_canvas.create_image(0, 0, anchor="nw", image=self._preview_photo)

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
            if self.job_type.get() == "STICKER":
                self.style_margin_mm.set(adj.cut_mm)
            else:
                self.safety_mm.set(adj.safety_mm)
                self.cut_mm.set(adj.cut_mm)
                self.bleed_mm.set(adj.bleed_mm)
            status_text += "\n\n[방금 추가한 영역의 자동 조정]\n" + "\n".join(item_result.adjustments)
            messagebox.showinfo(APP_TITLE, "\n".join(item_result.adjustments))

        self.status.set(status_text)
        self.generate_btn.configure(state="normal")
        self.export_btn.configure(state="normal")

    def _on_reset_accumulation(self):
        self._accumulated = []
        self._last_result = None
        self.accum_status.set("누적된 영역 없음")
        self.export_btn.configure(state="disabled")
        self.status.set("누적된 영역을 모두 지웠습니다. 다시 드래그해서 시작하세요.")

    def _on_error(self, message):
        messagebox.showerror(APP_TITLE, f"오류가 발생했습니다:\n{message}")
        self.status.set("오류 발생. 파일/설정을 확인하세요.")
        self.generate_btn.configure(state="normal")

    def _on_export(self):
        if self._last_result is None:
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
            messagebox.showinfo(APP_TITLE, f"SVG로 저장했습니다:\n{out_path}")
        except Exception as e:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, f"저장 중 오류:\n{e}")


def main():
    app = CutLineApp()
    app.mainloop()


if __name__ == "__main__":
    main()
