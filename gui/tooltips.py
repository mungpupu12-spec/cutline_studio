"""마우스를 2초 이상 올려 두면 짧은 설명을 띄운다(2026-10-11, 멍푸: "프로그램 창에서 각각의
기능에 2초 이상 커서를 두면 10자 이내의 설명이 나오게 해"; 같은 날 "15자 이내로 간략하게 ... 문장
어색하지 않게" -> "~합니다/~입니다" 짧은 문장, 15자 이내).

위젯마다 따로 바인딩하지 않고 앱 전체에 <Enter>/<Leave>/<ButtonPress>를 한 번 걸어 둔다. CTk 버튼은 안쪽에
캔버스·라벨이 겹쳐 있어 위젯 하나에만 걸면 안쪽으로 들어가는 순간 Leave가 나서 설명이 사라지기 때문이다. 마우스가
올라간 위젯에서 부모 쪽으로 올라가며 설명이 등록된 가장 가까운 위젯을 찾는다.

설명 등록:
  - 버튼·체크박스는 화면 글자로 TIPS에서 찾는다.
  - 글자가 바뀌는 위젯(작업 종류 고르기 등)이나 숫자 칸은 만들 때 `_cl_tip_text`를 붙여 둔다.
"""
from __future__ import annotations

import tkinter as tk

TIP_DELAY_MS = 2000
MAX_TIP_LEN = 15

# 화면 글자 -> 설명
TIPS = {
    "화면 초기화": "처음 상태로 되돌립니다",
    "도움말": "기능 설명을 봅니다",
    "찾아보기": "도안 파일을 엽니다",
    "있음": "도무송 칸이 있는 시트입니다",
    "없음": "도무송 칸이 없는 시트입니다",
    "선택 영역 지우기": "드래그한 영역을 지웁니다",
    "원본 다시 보기": "원본 그림을 다시 봅니다",
    "자동으로 여러 개 인식 + 추가": "모든 그림에 칼선을 만듭니다",
    "① 도안 자동 인식 (표시하기)": "도안을 찾아 표시합니다",
    "② 표시한 도무송 칼선 생성": "도무송 칼선을 만듭니다",
    "③ 남은 칼선 생성": "나머지 칼선을 만듭니다",
    "롤 전체 테두리 추가": "롤 바깥 테두리를 그립니다",
    "배경이 복잡해도 그림만 분리": "배경을 빼고 그림만 찾습니다",
    "무테 칼선도 함께 추가": "무테 칼선을 함께 만듭니다",
    "선택 영역 추가": "선택 영역에 칼선을 만듭니다",
    "누적 초기화": "만든 칼선을 모두 지웁니다",
    "칼선 내보내기": "칼선을 파일로 저장합니다",
    "적용된 모습": "그림과 화이트를 함께 봅니다",
    "화이트만": "화이트만 봅니다",
    "⬜ 화이트 레이어 만들기": "흰색 바탕판을 만듭니다",
    "✂ 칼선 재설정": "칼선을 다시 만듭니다",
    "확대 +": "크게 봅니다",
    "축소 −": "작게 봅니다",
    "원본크기": "100% 크기로 봅니다",
    "🖐 이동": "끌어서 화면을 옮깁니다",
    "🖊 칼선 수정": "칼선 모양을 고칩니다",
    "완료": "수정을 마칩니다",
    "↶ 되돌리기": "한 단계 전으로 돌립니다",
    "삭제": "고른 점을 지웁니다",
    "뾰족/둥근 점": "점 모양을 바꿉니다",
    "같은 그림 모두 적용": "같은 그림도 함께 고칩니다",
}

# 숫자 칸·글자가 바뀌는 위젯처럼 만들 때 직접 붙이는 설명(테스트에서 길이를 함께 확인한다)
FIELD_TIPS = {
    "file": "열린 도안 파일 경로입니다",
    "dpi": "그림을 읽는 해상도입니다",
    "precision": "높을수록 칼선이 매끄럽습니다",
    "job_type": "칼선 만드는 방식을 고릅니다",
    "cutline_type": "자를 도형을 고릅니다",
    "safety": "안전선까지의 거리입니다",
    "cut": "칼선까지의 거리입니다",
    "bleed": "블리딩까지의 거리입니다",
    "style_margin": "그림과 칼선 사이 거리입니다",
    "white_on": "저장할 때 화이트를 넣습니다",
    "white_choke": "화이트를 안쪽으로 줄입니다",
    "white_cut": "화이트와 칼선의 거리입니다",
}


# 카드 제목 -> 설명(2026-10-11, 멍푸: "모든 타이틀에 2초 이상 마우스 커서 가져다 대면 한줄 설명")
SECTION_TIPS = {
    "도안 파일": "칼선을 만들 그림입니다",
    "작업 해상도": "그림을 읽는 정밀도입니다",
    "0. 도무송 여부": "도무송 칸이 있는지 고릅니다",
    "작업 종류": "칼선 만드는 방식입니다",
    "도무송 세부 옵션": "도무송 모양을 정합니다",
    "오프셋 간격": "세 선의 간격을 정합니다",
    "유테/무테 간격": "그림과 칼선 사이 거리입니다",
    "누적": "만든 칼선이 쌓이는 곳입니다",
    "화이트 레이어": "그림 밑 흰색 인쇄판입니다",
    "미리보기": "결과를 확인하는 화면입니다",
}


def set_tip(widget, text):
    """만들 때 설명을 붙여 둔다. install_tooltips가 나중에 등록한다."""
    widget._cl_tip_text = text
    return widget


class TooltipManager:
    def __init__(self, root, delay_ms: int = TIP_DELAY_MS):
        self.root = root
        self.delay_ms = delay_ms
        self._tips: dict = {}       # 위젯 경로 -> 설명
        self._current = None        # 지금 마우스가 올라간 등록 위젯 경로
        self._job = None
        self._win = None
        root.bind_all("<Enter>", self._on_enter, add="+")
        root.bind_all("<Leave>", self._on_leave, add="+")
        root.bind_all("<ButtonPress>", self._on_press, add="+")

    def add(self, widget, text):
        self._tips[str(widget)] = text

    def tip_for(self, widget):
        return self._tips.get(str(widget))

    def _owner(self, w):
        """w부터 부모 쪽으로 올라가며 설명이 등록된 위젯 경로를 찾는다."""
        if isinstance(w, str):
            try:
                w = self.root.nametowidget(w)
            except Exception:  # noqa: BLE001
                return None
        while w is not None:
            p = str(w)
            if p in self._tips:
                return p
            w = getattr(w, "master", None)
        return None

    def _on_enter(self, event):
        owner = self._owner(event.widget)
        if owner == self._current:
            return
        self._cancel()
        self._current = owner
        if owner is not None:
            self._job = self.root.after(self.delay_ms, self._show)

    def _on_leave(self, _event):
        try:
            x, y = self.root.winfo_pointerxy()
            under = self.root.winfo_containing(x, y)
        except Exception:  # noqa: BLE001
            under = None
        owner = self._owner(under) if under is not None else None
        if owner != self._current:
            self._cancel()
            self._current = None

    def _on_press(self, _event):
        self._cancel()

    def _cancel(self):
        if self._job is not None:
            try:
                self.root.after_cancel(self._job)
            except Exception:  # noqa: BLE001
                pass
            self._job = None
        if self._win is not None:
            try:
                self._win.destroy()
            except Exception:  # noqa: BLE001
                pass
            self._win = None

    def _show(self):
        self._job = None
        text = self._tips.get(self._current) if self._current else None
        if not text:
            return
        try:
            x, y = self.root.winfo_pointerxy()
        except Exception:  # noqa: BLE001
            x, y = 0, 0
        win = tk.Toplevel(self.root)
        win.wm_overrideredirect(True)
        try:
            win.attributes("-topmost", True)
        except Exception:  # noqa: BLE001
            pass
        tk.Label(win, text=text, bg="#2F3A36", fg="#FFFFFF", padx=8, pady=4,
                 font=("Malgun Gothic", 10)).pack()
        win.geometry(f"+{x + 14}+{y + 18}")
        self._win = win

    @property
    def visible_text(self):
        if self._win is None:
            return None
        for c in self._win.winfo_children():
            try:
                return c.cget("text")
            except Exception:  # noqa: BLE001
                pass
        return None


def install_tooltips(root, delay_ms: int = TIP_DELAY_MS, mgr: TooltipManager | None = None) -> TooltipManager:
    """화면을 다 만든 뒤 부른다. 설명이 붙은 위젯을 모두 등록한다. 화면을 다시 만들 때는 기존 mgr을 넘겨
    앱 전체 바인딩이 겹치지 않게 한다."""
    import customtkinter as ctk

    if mgr is None:
        mgr = TooltipManager(root, delay_ms)

    def walk(w):
        for c in w.winfo_children():
            text = getattr(c, "_cl_tip_text", None)
            if text is None and isinstance(c, (ctk.CTkButton, ctk.CTkCheckBox)):
                try:
                    text = TIPS.get(c.cget("text"))
                except Exception:  # noqa: BLE001
                    text = None
            if text:
                mgr.add(c, text)
            walk(c)

    walk(root)
    return mgr
