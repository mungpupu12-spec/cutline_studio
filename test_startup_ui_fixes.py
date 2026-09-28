"""
Non-interactive smoke test for two 2026-09-08(10차) UI fixes, driving the
real GUI class (gui.app.CutLineApp) headlessly under a virtual X display,
same pattern as test_accumulate.py/test_preview_zoom.py.

1. Tutorial slide 3 text spacing/line-wrap (피드백: "튜토리얼3번 슬라이드
   문장 간격과 줄 맞춰"): the caption used to be one long run-on string with
   an accidental extra space ("진행 하면") that tkinter's automatic
   wraplength-based word-wrap broke at an arbitrary, uneven point across 2
   lines. It's now pre-broken into 3 deliberate lines at natural phrase
   boundaries. This test verifies each explicit line actually fits within
   the dialog's wraplength on its own (so it renders as exactly those 3
   lines, not wrapped again into a 4th) and that the accidental double
   space is gone.

2. Startup "찾아보기" button briefly missing (피드백: "프로그램 시작하고
   도안 찾는 버튼이 사라졌다가 수 초 후에 나타나"): the left control panel
   is a CTkScrollableFrame built while the window is still self.withdraw()n
   (deliberately, to avoid a black-screen-on-launch issue from an earlier
   round) -- customtkinter only recomputes that frame's internal scroll
   region on its own <Configure> event, which may not reflect the window's
   real on-screen size yet at that point. The fix
   (_refresh_left_panel_scrollregion) forces that recalculation right after
   deiconify(). This test checks the method exists, does not raise when the
   panel exists, and is a safe no-op if the panel was never built (e.g. the
   license-gate-only screen).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("CUTLINE_LICENSE_DEV_BYPASS", "1")

from gui.app import CutLineApp, TUTORIAL_SLIDES


def check(label, cond):
    status = "OK" if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        raise SystemExit(1)


def test_tutorial_slide3_lines_fit_without_rewrapping():
    app = CutLineApp()
    app.withdraw()
    app.update_idletasks()

    check("튜토리얼 슬라이드가 최소 3개 이상 존재", len(TUTORIAL_SLIDES) >= 3)
    _fname, caption = TUTORIAL_SLIDES[2]
    check("3번 슬라이드 캡션에 어색한 이중 띄어쓰기('진행 하면')가 더 이상 없음", "진행 하면" not in caption)
    check("3번 슬라이드 캡션이 여러 줄로 미리 나뉘어 있음(자동 줄바꿈에만 의존하지 않음)", "\n" in caption)

    full = f"3. {caption}"
    font = app.font_body
    wraplength = 380  # 대화상자의 캡션 라벨에 실제로 쓰는 값 (gui/app.py 참고)
    for line in full.split("\n"):
        w = font.measure(line)
        check(
            f"줄 '{line}'이 wraplength({wraplength}px) 안에 들어가 다시 줄바꿈되지 않음 (측정폭={w}px)",
            w <= wraplength,
        )
    print("[OK] 튜토리얼 3번 슬라이드: 문장이 자연스러운 구간에서 미리 나뉘어, 들쭉날쭉한 자동 줄바꿈이 더 이상 생기지 않음")


def test_left_panel_scrollregion_refresh_is_safe_and_effective():
    app = CutLineApp()
    app.withdraw()

    check("왼쪽 조작 패널(_left_col)이 self에 보관되어 있음", hasattr(app, "_left_col"))
    canvas = app._left_col._parent_canvas

    # 정상 경로: 패널이 있을 때 예외 없이 스크롤 영역을 다시 계산.
    app._refresh_left_panel_scrollregion()
    region = canvas.cget("scrollregion")
    check("호출 후 scrollregion이 비어있지 않게 설정됨", bool(region.strip()))
    bbox = canvas.bbox("all")
    check("bbox('all')이 실제 내용 크기를 반영하는 사각형을 돌려줌", bbox is not None and bbox[3] > bbox[1])

    # 안전장치 경로: 왼쪽 패널이 아예 없는 상태(예: 라이선스 입력 화면만
    # 뜬 경우)에서 호출해도 죽지 않아야 함.
    del app._left_col
    app._refresh_left_panel_scrollregion()
    print("[OK] 왼쪽 패널 스크롤 영역 강제 재계산: 정상 동작하고, 패널이 없을 때도 안전하게 무시됨")


def main():
    test_tutorial_slide3_lines_fit_without_rewrapping()
    test_left_panel_scrollregion_refresh_is_safe_and_effective()
    print("\nAll startup-UI-fix checks passed.")


if __name__ == "__main__":
    main()
