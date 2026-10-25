"""기능 설명서 창(2026-10-11, 멍푸: "각 기능에 대한 개념, 사용방법을 화면 초기화 옆에 클릭하면 해설이 나오게 해").

머리줄 "화면 초기화" 옆 "도움말"을 누르면 열린다. 2026-10-11 문장 끝을 ~합니다/~입니다로 맞춤. 항목마다 두 줄 이내로 짧게(멍푸: "설명이 길어서 오히려 어려워"). 기능마다 한 줄 설명과 사용법을 자연스러운 문장으로 짧게(2026-10-11 멍푸: "ai 문체 사용하지 않고").
설명을 고칠 때는 아래 HELP_SECTIONS만 바꾸면 된다.
"""
from __future__ import annotations

import customtkinter as ctk

# (제목, 한 줄 설명, [사용법 ...])
HELP_SECTIONS = [
    ("작업 순서", "기본 순서는 다음과 같습니다.", [
        "1. 찾아보기로 도안 파일을 엽니다.",
        "2. 도무송 여부를 고릅니다.",
        "3. 작업 종류를 고르고 '자동으로 여러 개 인식 + 추가'를 누릅니다.",
        "4. 미리보기에서 칼선을 확인합니다. 고칠 곳은 '칼선 수정'으로 고칩니다.",
        "5. '완성 파일 내보내기'로 저장합니다.",
    ]),
    ("도안 파일", "칼선을 만들 그림 파일입니다.", [
        "AI 파일을 권장합니다. PNG, JPG도 열 수 있습니다.",
        "AI 파일을 열어도 원본 파일은 바뀌지 않습니다.",
    ]),
    ("작업 해상도", "그림을 읽는 해상도입니다.", [
        "DPI는 300 그대로 두면 됩니다.",
        "정밀도가 높을수록 칼선이 매끄럽지만 시간이 더 걸립니다. 기본은 4입니다.",
        "영역이 크면 속도를 위해 정밀도를 자동으로 낮춥니다.",
    ]),
    ("도무송 여부", "시트에 사각형, 원처럼 정해진 모양으로 자르는 칸이 있는지 고릅니다.", [
        "없음: 모든 그림을 그림 모양대로 자릅니다. 작업 종류가 무테로 정해집니다.",
        "있음: 도무송 칸과 일반 칸이 함께 있는 시트입니다. ①②③ 순서로 만듭니다.",
        "고른 뒤에도 작업 종류는 다시 바꿀 수 있습니다.",
    ]),
    ("작업 종류", "칼선을 만드는 방식입니다.", [
        "무테: 선화 없이 그린 그림입니다. 그림 가장자리를 따라 자릅니다.",
        "유테: 선화가 있는 그림입니다. 바깥 선을 따라 일정한 간격을 두고 자릅니다.",
        "무테+유테 자동 생성: 그림마다 무테인지 유테인지 프로그램이 판단합니다.",
        "무테/유테+도무송: 도무송 칸과 일반 칸이 함께 있는 시트에 씁니다.",
        "도무송: 사각형, 원 같은 정해진 모양으로 자릅니다.",
        "조각: 그림 하나를 모양 그대로 자릅니다. 간격은 오프셋 간격에서 정합니다.",
        "키스컷 (마스킹테이프): 겉면만 자르고, 가까운 조각은 하나로 묶습니다.",
    ]),
    ("자동으로 여러 개 인식 + 추가", "시트 안의 그림을 찾아 한 번에 칼선을 만듭니다.", [
        "같은 그림이 반복되면 하나만 만들고 나머지에 똑같이 적용합니다.",
        "하나만 만들 때는 드래그로 영역을 잡고 '선택 영역 추가'를 누릅니다.",
    ]),
    ("①②③ 버튼", "도무송이 있는 시트에서 씁니다.", [
        "① 도안 자동 인식을 누르면 시트 안 그림이 모두 표시됩니다.",
        "미리보기에서 도무송으로 자를 칸을 클릭합니다.",
        "② 표시한 도무송 칼선 생성, ③ 남은 칼선 생성 순서로 누릅니다.",
    ]),
    ("롤 전체 테두리 추가", "마스킹테이프 롤 바깥을 두르는 테두리 선을 추가합니다.", [
        "작업 종류가 키스컷일 때만 씁니다. 간격은 오프셋 간격 값을 씁니다.",
    ]),
    ("도무송 세부 옵션", "도무송 칼선의 모양을 고릅니다.", [
        "재단 도형: 조각 스티커, 직사각형, 정사각형, 타원형, 정원형 중에서 고릅니다.",
        "조각 스티커는 그림 모양대로, 나머지는 고른 도형으로 그림 안쪽을 자릅니다.",
        "무테 칼선도 함께 추가: 같은 영역에 무테 칼선을 하나 더 만듭니다.",
        "배경이 복잡해도 그림만 분리: 지금은 켜도 꺼도 결과가 같습니다.",
    ]),
    ("오프셋 간격", "조각, 도무송, 롤 테두리에 쓰는 선 간격(mm)입니다.", [
        "세이프티(초록): 재단이 밀려도 이 선 안쪽은 잘리지 않습니다.",
        "칼선(빨강): 실제로 자르는 선입니다.",
        "블리딩(파랑): 흰 틈이 생기지 않게 칼선 밖까지 더 인쇄하는 범위입니다.",
        "조각: 그림에서 바깥쪽으로 잰 거리입니다. 가장 안쪽 선은 최소 2mm입니다.",
        "도무송: 그림 가장자리에서 블리딩 값(최소 2mm)만큼 안쪽에 칼선이 생깁니다.",
    ]),
    ("유테/무테 간격", "그림과 칼선 사이 거리(mm)입니다. 기본은 1.2mm입니다.", [
        "무테, 유테, 키스컷에 적용합니다. 값을 키우면 칼선이 그림에서 멀어집니다.",
    ]),
    ("누적", "만든 칼선은 한 파일에 계속 쌓입니다.", [
        "누적 초기화: 만든 칼선을 모두 지웁니다. 이미 저장한 파일은 그대로입니다.",
        "원본 다시 보기: 원본 그림으로 돌아가 영역을 더 고를 수 있습니다.",
    ]),
    ("완성 파일 내보내기", "원본 사본에 칼선 레이어를 넣어 저장합니다. 원본은 그대로입니다.", [
        "AI 파일: 원본 사본에 '칼선' 레이어를 더한 AI로 저장합니다.",
        "PNG·JPG: 도안과 칼선을 레이어로 나눈 PDF로 저장합니다.",
        "칼선끼리 겹치거나 2mm보다 가까우면 저장 전에 알려 줍니다.",
    ]),
    ("칼선만 저장", "원본 그림 없이 칼선만 원본과 같은 크기로 저장합니다.", [
        "형식: AI(기본), PDF, SVG, PNG 중에서 고릅니다.",
        "화이트를 만들었으면 '화이트레이어'도 따로 넣습니다.",
        "원본과 왼쪽 위를 맞춰 겹치면 칼선 위치가 맞습니다.",
    ]),
    ("화이트 레이어", "투명 스티커 그림 밑에 까는 흰색 인쇄 판입니다.", [
        "화이트 넣기: 체크하면 저장 파일에 화이트가 들어갑니다.",
        "적용된 모습: 그림과 화이트를 함께 봅니다. 화이트는 파란색으로 보입니다.",
        "화이트만: 화이트만 봅니다.",
        "화이트 크기 조정: 값만큼 화이트를 안쪽으로 줄입니다.",
        "칼선 재설정: 화이트에서 칼선 간격만큼 떨어진 칼선을 다시 만듭니다.",
        "저장: AI·PDF는 '화이트레이어' 레이어에, PNG는 따로 저장됩니다.",
    ]),
    ("미리보기 도구", "미리보기 위쪽 버튼입니다.", [
        "확대, 축소, 원본크기: 미리보기 배율을 바꿉니다.",
        "이동: 켜고 끌면 화면이 움직입니다. 다시 누르면 꺼집니다.",
        "칼선 수정: 칼선을 클릭하고 점을 끌어 고칩니다. 선을 더블클릭하면 점이 생깁니다.",
    ]),
    ("화면 초기화", "열어 둔 파일, 만든 칼선, 설정을 처음 상태로 되돌립니다.", [
        "컴퓨터에 저장된 파일은 지우지 않습니다.",
    ]),
]


class HelpGuideMixin:
    def _show_help_guide(self):
        from gui.app import ACCENT, APP_TITLE, BG_APP, BG_CARD, BORDER, TEXT_PRIMARY, TEXT_SECONDARY

        win = getattr(self, "_help_win", None)
        if win is not None:
            try:
                if win.winfo_exists():
                    win.deiconify()
                    win.lift()
                    win.focus_force()
                    return
            except Exception:  # noqa: BLE001
                pass
        win = ctk.CTkToplevel(self)
        self._help_win = win
        win.title(f"{APP_TITLE} 도움말")
        try:
            win.configure(fg_color=BG_APP)
            win.transient(self)
            x = max(0, self.winfo_rootx() + 30)
            y = max(0, self.winfo_rooty() + 60)
            win.geometry(f"560x680+{x}+{y}")
        except Exception:  # noqa: BLE001
            pass
        ctk.CTkLabel(win, text="기능 설명", font=self.font_section, text_color=ACCENT,
                     anchor="w").pack(fill="x", padx=20, pady=(16, 6))
        body = ctk.CTkScrollableFrame(win, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        for title, what, hows in HELP_SECTIONS:
            card = ctk.CTkFrame(body, fg_color=BG_CARD, corner_radius=14, border_width=1, border_color=BORDER)
            card.pack(fill="x", padx=4, pady=5)
            ctk.CTkLabel(card, text=title, font=self.font_section, text_color=TEXT_PRIMARY,
                         anchor="w", justify="left").pack(fill="x", padx=14, pady=(10, 2))
            ctk.CTkLabel(card, text=what, font=self.font_body, text_color=TEXT_PRIMARY,
                         anchor="w", justify="left", wraplength=470).pack(fill="x", padx=14, pady=(0, 4))
            for h in hows:
                ctk.CTkLabel(card, text="· " + h, font=self.font_caption, text_color=TEXT_SECONDARY,
                             anchor="w", justify="left", wraplength=460).pack(fill="x", padx=18, pady=1)
            ctk.CTkFrame(card, fg_color="transparent", height=8).pack()
        self._btn_primary(win, "닫기", win.destroy).pack(fill="x", padx=20, pady=(4, 14))
        return win
