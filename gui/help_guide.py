"""기능 설명서 창(2026-10-11, 멍푸: "각 기능에 대한 개념, 사용방법을 화면 초기화 옆에 클릭하면 해설이 나오게 해").

머리줄 "화면 초기화" 옆 "❔ 도움말"을 누르면 열린다. 기능마다 "무엇인가요"와 "이렇게 써요"를 쉬운 말로 짧게.
설명을 고칠 때는 아래 HELP_SECTIONS만 바꾸면 된다.
"""
from __future__ import annotations

import customtkinter as ctk

# (제목, 무엇인가요, [이렇게 써요 ...])
HELP_SECTIONS = [
    ("한눈에 보는 순서", "칼선을 만드는 기본 흐름이에요.", [
        "1. '찾아보기'로 도안 파일을 열어요(AI 추천, PNG·JPG도 돼요).",
        "2. '도무송 여부'에서 예/아니오를 골라요.",
        "3. '작업 종류'를 고르고 '자동으로 여러 개 인식 + 추가'를 눌러요.",
        "4. 미리보기에서 칼선을 확인하고, 필요하면 '칼선 수정'으로 고쳐요.",
        "5. '칼선 내보내기'로 저장해요(PNG 기본, SVG도 돼요).",
    ]),
    ("도안 파일", "칼선을 만들 그림 파일이에요.", [
        "'찾아보기'를 눌러 AI·PNG·JPG 파일을 열어요.",
        "AI 파일은 안에 든 인쇄용 그림을 그대로 꺼내 써요. 원본 파일은 바뀌지 않아요.",
    ]),
    ("작업 해상도", "그림을 얼마나 촘촘하게 읽을지 정해요.", [
        "DPI는 보통 300 그대로 두면 돼요.",
        "정밀도는 숫자가 클수록 칼선이 매끄럽지만 느려져요(기본 4).",
    ]),
    ("0. 도무송 여부", "시트에 네모·동그라미 같은 정해진 모양으로 자르는 칸(도무송)이 있는지 골라요.", [
        "'아니오, 없음': 모든 그림을 그림 모양대로 잘라요(무테).",
        "'예, 있음': 도무송 칸과 그림 모양 칸이 섞인 시트예요. ①②③ 버튼이 나와요.",
    ]),
    ("작업 종류", "칼선을 어떤 방식으로 만들지 정해요.", [
        "무테: 테두리 없이 그림 가장자리에 딱 맞춰 잘라요.",
        "유테: 그림 바깥에 흰 테두리를 두고 일정한 간격으로 잘라요.",
        "무테+유테 자동 생성: 그림마다 무테·유테를 알아서 골라요.",
        "무테/유테+도무송: 도무송 칸과 일반 칸이 섞인 시트용이에요(①②③ 순서로).",
        "도무송: 네모·원 같은 정해진 모양으로 잘라요('도무송 세부 옵션'에서 모양 선택).",
        "조각: 그림 하나의 실제 모양을 그대로 따라 잘라요(완칼). 간격은 '오프셋 간격'에서 정해요.",
        "키스컷: 마스킹테이프처럼 뒷면은 두고 앞면만 자르는 칼선이에요. 그림에서 고르게 띄운 칼선을 만들고 가까운 조각은 하나로 묶어요.",
    ]),
    ("자동으로 여러 개 인식 + 추가", "시트 안 그림을 찾아 한 번에 칼선을 만들어요.", [
        "작업 종류를 고른 뒤 누르면 돼요. 같은 그림이 반복되면 하나만 계산해 나머지에 똑같이 써요.",
        "그림 하나만 하고 싶으면 미리보기에서 드래그로 영역을 고르고 '이 영역 추가 + 미리보기'를 눌러요.",
    ]),
    ("①②③ 버튼 (도무송이 있을 때)", "도무송 칸과 일반 칸을 나눠서 만들어요.", [
        "① 도안 자동 인식: 시트 안 그림을 모두 찾아 표시해요.",
        "미리보기에서 도무송으로 자를 칸을 클릭해 표시해요.",
        "② 표시한 도무송 칼선 생성 → ③ 남은 칼선 생성 순서로 눌러요.",
    ]),
    ("롤 전체 테두리 추가 (마스킹테이프)", "키스컷 롤 전체를 두르는 바깥 테두리 선을 한 번 더해요.", [
        "작업 종류가 키스컷일 때만 눌러요. 간격은 '오프셋 간격'의 값을 써요.",
    ]),
    ("도무송 세부 옵션", "도무송 모양과 배경 분리 방법을 골라요.", [
        "재단 도형: 조각 스티커·직사각형·정사각형·타원형·정원형 중에서 골라요.",
        "'유색/복잡한 배경에서도 자동 분리'는 배경이 알록달록할 때 켜요.",
        "'같은 영역에 무테 칼선도 함께 추가'를 켜면 도무송과 무테 칼선을 같이 만들어요.",
    ]),
    ("오프셋 간격", "도무송·조각에서 세 가지 선의 간격(mm)이에요.", [
        "세이프티(초록): 중요한 그림이 이 안에 있어야 해요.",
        "칼선(빨강): 실제로 자르는 선이에요.",
        "블리딩(파랑): 인쇄가 밀려도 흰 틈이 안 보이게 그림을 더 칠하는 범위예요.",
    ]),
    ("유테/무테 간격", "그림과 칼선 사이 거리(mm)예요. 기본 1.2mm.", [
        "숫자를 키우면 칼선이 그림에서 더 멀어져요.",
    ]),
    ("누적", "만든 칼선이 한 파일에 계속 쌓여요.", [
        "'누적 초기화'를 누르면 만든 칼선을 모두 지워요(저장한 파일은 그대로).",
        "'원본 다시 보기'를 누르면 원본 그림으로 돌아가 영역을 더 고를 수 있어요.",
    ]),
    ("칼선 내보내기", "만든 칼선을 파일로 저장해요.", [
        "PNG: 원본과 같은 크기의 투명 배경에 마젠타 칼선만 그려요. 원본 위에 왼쪽 위만 맞춰 겹치면 끝이에요.",
        "SVG: 저장 창에서 형식을 SVG로 바꾸면 일러스트레이터에서 고칠 수 있는 선으로 저장돼요.",
        "저장 전에 칼선끼리 겹치거나 2mm보다 가까운 곳이 있으면 먼저 알려 줘요.",
    ]),
    ("화이트 레이어", "투명·홀로그램 스티커에서 그림 밑에 까는 흰색 바탕이에요.", [
        "'화이트 넣기' 체크: 켜면 화이트를 만들고 저장에 넣어요. 끄면 화면과 저장에서 빠져요.",
        "'적용된 모습': 그림이 화이트 위에 얹힌 모습이에요. 화면에선 화이트가 파란색으로 보여요(저장은 흰색).",
        "'화이트만': 그림을 숨기고 화이트만 보여 줘요.",
        "화이트 크기 조정: 숫자만큼 화이트를 안쪽으로 줄여요(0이면 그림 크기 그대로).",
        "칼선 간격 + '칼선 재설정': 그림 모양에서 이 거리만큼 띄운 칼선으로 다시 만들어요. 키스컷이면 지금 칼선을 바꾸고, 다른 종류면 칼선이 빠진 그림에만 더해요.",
        "저장하면 PNG는 '_화이트.png'가 따로, SVG는 같은 파일 안 '화이트레이어'에 들어가요.",
    ]),
    ("미리보기 도구", "오른쪽 미리보기 위 버튼들이에요.", [
        "확대·축소·원본크기: 미리보기 크기를 바꿔요.",
        "이동: 켜면 미리보기를 끌어서 움직여요. 다시 누르면 영역 고르기로 돌아와요.",
        "칼선 수정: 칼선을 클릭하면 점이 보여요. 점 끌기, 선 위 더블클릭으로 점 추가, Delete로 삭제, Ctrl+Z로 되돌리기.",
    ]),
    ("화면 초기화", "열었던 파일과 만든 칼선, 고른 설정을 처음 상태로 되돌려요.", [
        "컴퓨터에 저장한 파일은 지우지 않아요.",
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
