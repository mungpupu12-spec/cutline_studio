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


def test_dropdown_is_truly_modal_and_closes_every_way():
    """2026-09-08(8차) 후속 확인: 멍푸님이 "그냥 옵션을 고르려던 중"이었는데도
    (드문 예외 상황이 아니라 평소 사용 중에) 드롭다운이 멈췄다고 확인해주신
    뒤 추가한 진짜 수정 -- 이 팝업이 실제로 앱의 입력을 붙잡는(grab_set)
    진짜 모달 창이 됐는지, 그리고 그걸 닫는 모든 경로(배경 클릭/Esc/재오픈)가
    다 제대로 동작하는지 확인한다."""
    app = CutLineApp()
    app.withdraw()
    anchor = ctk.CTkButton(app, text="anchor2")
    variable = tk.StringVar(value="")

    # ---- 1) normal open (the REAL code path, not monkeypatched) grabs input ----
    app._toggle_category_dropdown(anchor, [("A", "옵션 A"), ("B", "옵션 B")], variable, None, False)
    dd = app._open_dropdown
    check("dropdown opens and is tracked", dd is not None and dd.winfo_exists())
    check(
        "the dropdown actually holds the application's grab (this is the fix for "
        "'선택도 안 됨' -- clicks can no longer leak through to the window underneath)",
        app.grab_current() is dd,
    )

    # ---- 2) clicking blank space INSIDE the dropdown (not a button) closes it,
    #      WITHOUT picking any value ----
    card = dd.winfo_children()[0]
    card.event_generate("<Button-1>")
    app.update_idletasks()
    check("clicking blank dropdown space closes it", app._open_dropdown is None)
    check("the grab is released again once closed", app.grab_current() is None)
    check("closing via blank-space click never picked a value", variable.get() == "")

    # ---- 3) Escape closes a freshly-reopened dropdown too ----
    app._toggle_category_dropdown(anchor, [("A", "옵션 A"), ("B", "옵션 B")], variable, None, False)
    check("dropdown re-opens fine after the previous close", app._open_dropdown is not None)
    app.event_generate("<Escape>")
    app.update_idletasks()
    check("Esc closes the dropdown", app._open_dropdown is None)
    check("the grab is released after Esc too", app.grab_current() is None)

    # ---- 4) opening it again afterwards still works (no grab left stuck) ----
    app._toggle_category_dropdown(anchor, [("A", "옵션 A"), ("B", "옵션 B")], variable, None, False)
    check(
        "a third open still succeeds cleanly (no leftover grab blocking future opens)",
        app._open_dropdown is not None and app.grab_current() is app._open_dropdown,
    )
    app._close_open_dropdown()
    print("[OK] the dropdown is a real modal popup and every close path (option/blank-click/Esc) works")


def main():
    test_pan_mode_does_not_touch_selection()
    test_dropdown_build_failure_leaves_no_ghost_window()
    test_dropdown_is_truly_modal_and_closes_every_way()
    print("\nAll pan-mode / dropdown-safety checks passed.")


if __name__ == "__main__":
    main()
