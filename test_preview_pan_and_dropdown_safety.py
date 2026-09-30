"""
Non-interactive smoke test for two 2026-09-08(8차) fixes, driving the real
GUI class (gui.app.CutLineApp) headlessly under a virtual X display, same
pattern as test_accumulate.py/test_preview_zoom.py.

1. Pan (hand-drag) mode for the zoom feature (피드백: "확대 축소 기능에
   손바닥 모양의 이동할 수 있는 기능 추가"): toggling "🖐 이동" must swap the
   canvas's left-click-drag behavior from "draw a selection rectangle" to
   "pan the view" (tk.Canvas.scan_mark/scan_dragto) WITHOUT altering
   self._selection_px, and toggling back off must restore normal
   drag-to-select exactly as before -- this is the actual risk the new mode
   introduces (both actions share the same physical gesture: left-button
   press + drag).

2. Ghost-dropdown crash-proofing (피드백: "옵션 자리 이탈 사라지지 않아서
   강제 종료"): the custom category-picker popup (_toggle_category_dropdown,
   used for 무테/유테/도무송 등) is an overrideredirect+topmost CTkToplevel
   with no window chrome. Before this fix, if anything threw an exception
   while building/positioning it, the exception could propagate out before
   self._open_dropdown was ever set to point at it -- leaving a borderless,
   always-on-top popup on screen that the app's own tracking no longer knew
   about, and that _close_open_dropdown() could therefore never close (a
   "ghost window" only a force-quit could remove). The fix wraps the whole
   build/position step in try/except and destroys the half-built popup on
   any failure. This test simulates that failure deterministically (by
   monkeypatching the build step to raise) and verifies no window is left
   behind and app state cleanly reports "nothing open".

3. The actual root-cause fix, added after 멍푸님 confirmed this happened
   during completely normal use (not a rare exception): the popup never
   grabbed input, so on Windows a click meant for one of its option buttons
   could land on the window underneath instead -- looking "stuck" (position
   frozen, doesn't disappear) AND unresponsive (picking an option does
   nothing), which matches exactly what she described. The fix makes the
   popup a real modal window (lift + focus_force + grab_set) so clicks can
   no longer leak through, and adds a background-click-to-close handler
   (since a local grab can prevent the old "click anywhere else" detection
   from reaching other windows) alongside the Esc-to-close from fix #2.
   This test verifies the dropdown actually holds app.grab_current(), that
   closing it (via blank-space click, via Esc, or normally) always releases
   that grab again, and that it can be reopened cleanly afterwards.

Uses ONLY the pre-existing public demo fixture samples/ring.png -- pure
GUI/plumbing check, not an image-recognition quality check, per the
standing "다른 이미지 생성 금지" rule.
"""
import os
import sys
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import customtkinter as ctk

from gui.app import CutLineApp

SAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples")


def check(label, cond):
    status = "OK" if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        raise SystemExit(1)


class _FakeEvent:
    def __init__(self, x, y):
        self.x = x
        self.y = y


def test_pan_mode_does_not_touch_selection():
    app = CutLineApp()
    app.withdraw()
    ring_path = os.path.join(SAMPLES, "ring.png")
    app.input_path.set(ring_path)
    app.is_vector.set(False)
    app._load_source_preview(ring_path)

    check("pan mode starts off", app._pan_mode is False)

    # ---- normal (pan mode OFF) drag still creates a selection as before ----
    app._on_canvas_press(_FakeEvent(10, 10))
    app._on_canvas_drag(_FakeEvent(80, 60))
    app._on_canvas_release(_FakeEvent(80, 60))
    check("normal drag (pan off) sets a selection", app._selection_px is not None)
    baseline_selection = app._selection_px

    # ---- turn pan mode on ----
    app._on_toggle_pan_mode()
    check("pan mode is now on", app._pan_mode is True)
    check("canvas cursor switches to the hand cursor", app.preview_canvas.cget("cursor") == "hand2")

    # A left-press+drag+release while panning must NOT create/alter a
    # selection -- it should be entirely absorbed by scan_mark/scan_dragto.
    app._on_canvas_press(_FakeEvent(200, 200))
    app._on_canvas_drag(_FakeEvent(150, 130))
    app._on_canvas_release(_FakeEvent(150, 130))
    check(
        "panning leaves the previous selection completely untouched",
        app._selection_px == baseline_selection,
    )
    check("panning never sets a pending drag-start", app._drag_start is None)

    # ---- turn pan mode back off: normal drag-to-select must work again ----
    app._on_toggle_pan_mode()
    check("pan mode is off again", app._pan_mode is False)
    check("canvas cursor restored to default", app.preview_canvas.cget("cursor") == "")

    app._on_canvas_press(_FakeEvent(20, 20))
    app._on_canvas_drag(_FakeEvent(120, 90))
    app._on_canvas_release(_FakeEvent(120, 90))
    check(
        "drag-to-select works again after turning pan mode back off",
        app._selection_px is not None and app._selection_px != baseline_selection,
    )
    print("[OK] pan mode toggles cleanly without ever corrupting selection state")


def test_dropdown_build_failure_leaves_no_ghost_window():
    app = CutLineApp()
    app.withdraw()

    children_before = set(app.winfo_children())

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated failure while building/positioning the dropdown")

    app._build_and_position_dropdown = _boom  # monkeypatch this instance only

    anchor = ctk.CTkButton(app, text="anchor")
    variable = tk.StringVar(value="")
    # Should NOT raise out of this call -- the try/except in
    # _toggle_category_dropdown must swallow the simulated failure.
    app._toggle_category_dropdown(anchor, [("A", "A"), ("B", "B")], variable, None, False)

    check("no dropdown is left registered after a failed build", app._open_dropdown is None)
    check("no dropdown anchor is left registered either", app._dropdown_anchor is None)

    children_after = set(app.winfo_children()) - {anchor}
    check(
        "no leftover (ghost) Toplevel window remains after the simulated failure",
        children_after == children_before,
    )
    print("[OK] a failure while building the dropdown destroys it instead of leaving a stuck ghost window")


def test_dropdown_opens_inline_inside_its_card_and_closes_every_way():
    """2026-09-29(멍푸: "옵션 창 위치 벗어나는 공백 없애고 칸에 맞게 스크롤방식으로
    선택형 모두 변경"): 옵션 목록은 이제 따로 뜨는 팝업 창이 아니라, 버튼 바로 아래
    같은 카드 안에 펼쳐진다 -- 새 창이 생기지 않고(유령 창/위치 이탈 불가), 폭이
    카드 폭을 넘지 않으며, 옵션이 많으면 그 칸 안에서 스크롤. 고르기/다시 누르기/Esc로
    닫힌다. (예전 8차 수정의 "팝업이 입력을 붙잡는지(grab)" 검사는 팝업 창 자체가
    없어져 의미가 없어짐.)"""
    app = CutLineApp()
    app.withdraw()
    card = ctk.CTkFrame(app, width=300)
    card.pack(fill="x")
    anchor = ctk.CTkButton(card, text="anchor2")
    anchor.pack(fill="x")
    variable = tk.StringVar(value="")
    toplevels_before = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)]
    many = [(str(i), f"옵션 {i}") for i in range(8)]

    # ---- 1) opens inline, right under the button, in the same card ----
    app._toggle_category_dropdown(anchor, many, variable, None, False)
    dd = app._open_dropdown
    check("dropdown opens and is tracked", dd is not None and dd.winfo_exists())
    check("the list lives inside the same card as its button (no separate popup window)",
          dd.master is anchor.master)
    check("no new Toplevel window was created",
          [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)] == toplevels_before)
    packed = card.pack_slaves()
    check("the list is placed directly below its button", packed.index(dd) == packed.index(anchor) + 1)
    app.update_idletasks()
    check("the list is never wider than its card", dd.winfo_reqwidth() <= max(card.winfo_width(), card.winfo_reqwidth()))
    check("8 options scroll inside a fixed-height box",
          isinstance(app._open_dropdown_body, ctk.CTkScrollableFrame))

    # ---- 2) pressing the same button again just closes it ----
    app._toggle_category_dropdown(anchor, many, variable, None, False)
    check("pressing the button again closes it", app._open_dropdown is None and not dd.winfo_exists())

    # ---- 3) picking an option sets the value and closes ----
    app._toggle_category_dropdown(anchor, [("A", "옵션 A"), ("B", "옵션 B")], variable, None, False)
    buttons = [w for w in app._open_dropdown.winfo_children()[0].winfo_children() if isinstance(w, ctk.CTkButton)]
    buttons[1].invoke()
    check("picking an option sets the value", variable.get() == "B")
    check("picking an option closes the list", app._open_dropdown is None)

    # ---- 4) Esc closes too, and it can be reopened cleanly ----
    app._toggle_category_dropdown(anchor, [("A", "옵션 A"), ("B", "옵션 B")], variable, None, True)
    app.event_generate("<Escape>")
    app.update_idletasks()
    check("Esc closes the list", app._open_dropdown is None)
    app._toggle_category_dropdown(anchor, [("A", "옵션 A"), ("B", "옵션 B")], variable, None, True)
    opts = [w.cget("text") for w in app._open_dropdown.winfo_children()[0].winfo_children()
            if isinstance(w, ctk.CTkButton)]
    check("'✕ 선택 해제' is the LAST row so option positions never shift", opts[-1] == "✕ 선택 해제" and opts[0] == "옵션 A")
    app._close_open_dropdown()
    print("[OK] the option list opens inline in its card, scrolls in place, and every close path works")


def main():
    test_pan_mode_does_not_touch_selection()
    test_dropdown_build_failure_leaves_no_ghost_window()
    test_dropdown_opens_inline_inside_its_card_and_closes_every_way()
    print("\nAll pan-mode / dropdown-safety checks passed.")


if __name__ == "__main__":
    main()
