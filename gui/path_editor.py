"""미리보기 캔버스에서 칼선을 일러스트레이터 패스처럼 고치는 기능(CutLineApp에 섞어 쓰는 mixin).

2026-10-02(멍푸: "수정 하는 거 너무 어려워, 어도비 패스 기능 같은거로 만들어" -- 점 끌기·삭제와
어도비 핸들까지): 미리보기 위 "🖊 칼선 수정"을 누르면
  - 칼선을 클릭하면 파란 패스로 바뀌고 기준점(네모)이 보인다.
  - 기준점을 끌면 모양이 바뀐다. 기준점을 누르면 그 점의 방향 핸들(동그라미)이 보이고, 핸들을
    끌면 곡선이 휜다(부드러운 점은 반대쪽 핸들이 일직선으로 따라옴, Alt를 누른 채 끌면 한쪽만).
  - 선 위를 더블클릭하면 그 자리에 기준점이 생긴다(모양은 그대로).
  - 기준점을 더블클릭하면 뾰족한 점 <-> 부드러운 점.
  - 기준점을 고르고 Delete(또는 Backspace)를 누르면 점이 지워진다. 점을 고르지 않고 칼선만 골랐을
    때 Delete는 그 칼선 전체를 지운다.
  - 선(기준점 아닌 곳)을 잡고 끌면 칼선 전체가 움직인다. 방향키는 0.1mm씩(Shift는 1mm).
  - Ctrl+Z 되돌리기, Esc 선택 해제(한 번 더 누르면 수정 끝).
  - "같은 그림 모두 적용"이 켜져 있으면 반복된 같은 모양 칼선에도 똑같이 고친다.
고친 칼선은 바로 저장 대상(_accumulated)에 들어가고, SVG로 내보내면 같은 패스(곡선)로 써진다.
"""
from __future__ import annotations

import tkinter as tk
import traceback

import customtkinter as ctk
import numpy as np

from core import bezier_path as _bz

PE_SEL = "#0A84FF"       # 고른 패스(일러스트레이터 선택 색과 비슷한 파랑)
PE_CUT = "#EC008C"       # 다른 칼선(마젠타)
PE_HANDLE = "#0A84FF"
_MARGIN = 40             # core.preview.render_preview 기본 여백(미리보기 그림 안 원본 위치)


class PathEditorMixin:
    # ---- 켜고 끄기 ----------------------------------------------------------
    def _pe_init_state(self):
        self._pe_active = False
        self._pe_items = []          # [(항목 번호, 조각 번호)]
        self._pe_sel = None          # _pe_items 안 번호
        self._pe_path = None         # 고른 칼선의 BezierPath(작업본)
        self._pe_sel_node = None
        self._pe_drag = None
        self._pe_undo = []
        self._pe_dirty_since_press = False
        self._pe_path_dirty = False
        self._pe_pending_undo = None
        self._pe_apply_same = tk.BooleanVar(value=True)
        self._pe_toolbar = None
        self._pe_saved_image = None

    def _pe_build_button(self, parent, **style):
        self.pe_mode_btn = ctk.CTkButton(parent, text="🖊 칼선 수정", command=self._pe_toggle, **style)
        return self.pe_mode_btn

    def _pe_toggle(self):
        if self._pe_active:
            self._pe_exit()
        else:
            self._pe_enter()

    def _pe_enter(self):
        if not getattr(self, "_accumulated", None) or self._last_result is None:
            self._show_note_dialog(
                "먼저 칼선을 만들어 주세요",
                ["칼선 수정은 만들어진 칼선을 고치는 기능입니다. 자동 인식이나 영역 추가로 칼선을 만든 뒤 눌러 주세요."],
                kind="info",
            )
            return
        if getattr(self, "_pan_mode", False):
            self._on_toggle_pan_mode()
        try:
            from PIL import Image
            from core import image_cache

            W, H = int(self._last_result.width_px), int(self._last_result.height_px)
            base = Image.new("RGB", (W + 2 * _MARGIN, H + 2 * _MARGIN), (255, 255, 255))
            path = self.input_path.get().strip()
            art = image_cache.pil_open(path, "RGBA")
            base.paste(art, (_MARGIN, _MARGIN), art)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            self._show_note_dialog("칼선 수정을 시작할 수 없습니다", ["원본 그림을 다시 불러온 뒤 시도해 주세요."], kind="error")
            return
        self._pe_saved_image = self._preview_full_image
        self._pe_active = True
        self._pe_undo = []
        self._pe_sel = None
        self._pe_path = None
        self._pe_sel_node = None
        self._pe_collect_items()
        self._preview_full_image = base
        self._pe_show_toolbar(True)
        self.pe_mode_btn.configure(fg_color=self._pe_accent, text_color="#FFFFFF", hover_color=self._pe_accent_hover)
        self.preview_canvas.configure(cursor="arrow")
        self._render_preview()  # 그 뒤 _pe_redraw_all이 칼선을 벡터로 그림
        self.status.set(
            "칼선 수정: 칼선을 클릭하면 기준점이 보여요. 점 끌기=모양 · 선 위 더블클릭=점 추가 · "
            "점 더블클릭=뾰족/둥근 점 · Delete=점(또는 칼선) 삭제 · Ctrl+Z 되돌리기 · Esc 끝"
        )

    def _pe_exit(self, rerender: bool = True):
        if not self._pe_active:
            return
        self._pe_commit_working()
        self._pe_active = False
        self._pe_sel = None
        self._pe_path = None
        self._pe_sel_node = None
        self._pe_drag = None
        self._pe_show_toolbar(False)
        try:
            self.pe_mode_btn.configure(fg_color=self._pe_btn_fg, text_color=self._pe_btn_text, hover_color=self._pe_btn_hover)
        except Exception:  # noqa: BLE001
            pass
        self.preview_canvas.delete("pe")
        if not rerender:
            return
        try:
            import os
            from core.accumulate import combine_results
            from core.preview import open_rendered, render_preview

            combined = combine_results(self._accumulated)
            self._last_result = combined
            out = os.path.join(self._pe_work_dir(), "_last_preview.png")
            render_preview(combined, out, original_image_path=self.input_path.get().strip(), async_save=True)
            self._preview_full_image = open_rendered(out)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            self._preview_full_image = self._pe_saved_image
        self._render_preview()
        self.status.set("칼선 수정을 마쳤습니다. 고친 칼선은 내보내기에 그대로 들어갑니다.")

    def _pe_work_dir(self):
        try:
            from gui.app import _work_file_dir

            return _work_file_dir()
        except Exception:  # noqa: BLE001
            import tempfile

            return tempfile.gettempdir()

    def _pe_show_toolbar(self, show: bool):
        if self._pe_toolbar is None:
            return
        if show:
            self._pe_toolbar.pack(fill="x", padx=6, pady=(0, 8), before=self._pe_canvas_wrap)
        else:
            self._pe_toolbar.pack_forget()

    def _pe_build_toolbar(self, right_col, canvas_wrap, font, colors):
        """미리보기 위에 칼선 수정 중에만 보이는 도구 줄."""
        bg_card, accent_soft, text_primary, border, accent, accent_hover = colors
        self._pe_accent, self._pe_accent_hover = accent, accent_hover
        self._pe_btn_fg, self._pe_btn_text, self._pe_btn_hover = bg_card, text_primary, accent_soft
        self._pe_canvas_wrap = canvas_wrap
        bar = ctk.CTkFrame(right_col, fg_color=bg_card, corner_radius=14, border_width=1, border_color=border)
        style = dict(font=font, fg_color=bg_card, hover_color=accent_soft, text_color=text_primary,
                     border_width=1, border_color=border, corner_radius=12, height=28)
        # 창이 좁아도 버튼이 잘리지 않게 버튼 줄을 먼저(완료가 맨 앞), 설명은 아랫줄에(멍푸 PC 실측:
        # 한 줄로 두면 오른쪽 버튼이 창 밖으로 잘림)
        row = ctk.CTkFrame(bar, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=(6, 0))
        ctk.CTkButton(
            row, text="완료", width=60, command=self._pe_exit, font=font, fg_color=accent,
            hover_color=accent_hover, text_color="#FFFFFF", corner_radius=12, height=28,
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(row, text="↶ 되돌리기", width=84, command=self._pe_undo_last, **style).pack(side="left", padx=(0, 6))
        ctk.CTkButton(row, text="삭제", width=56, command=self._pe_delete_key, **style).pack(side="left", padx=(0, 6))
        ctk.CTkButton(row, text="뾰족/둥근 점", width=96, command=self._pe_toggle_selected_node, **style).pack(side="left", padx=(0, 8))
        ctk.CTkCheckBox(
            row, text="같은 그림 모두 적용", variable=self._pe_apply_same, font=font,
            text_color=text_primary, fg_color=accent, hover_color=accent_hover, checkbox_width=18, checkbox_height=18,
        ).pack(side="left")
        ctk.CTkLabel(
            bar, text="칼선 클릭 → 점 끌기 · 동그라미 핸들로 곡선(Alt=한쪽만) · 선 더블클릭=점 추가 · "
                      "점 더블클릭=뾰족/둥근 · Delete=점(점 안 고르면 칼선) 삭제 · Ctrl+Z",
            font=font, text_color=text_primary, anchor="w", justify="left", wraplength=520,
        ).pack(fill="x", padx=12, pady=(2, 6))
        self._pe_toolbar = bar

    # ---- 좌표 -----------------------------------------------------------------
    def _pe_scale(self):
        return self._display_scale or 1.0

    def _pe_to_canvas(self, x, y):
        s = self._pe_scale()
        return (x + _MARGIN) * s, (y + _MARGIN) * s

    def _pe_from_event(self, event):
        cx = self.preview_canvas.canvasx(event.x)
        cy = self.preview_canvas.canvasy(event.y)
        s = self._pe_scale()
        return cx / s - _MARGIN, cy / s - _MARGIN

    def _pe_tol(self, screen_px=8.0):
        return screen_px / self._pe_scale()

    # ---- 칼선 목록 --------------------------------------------------------------
    def _pe_collect_items(self):
        items = []
        for i, it in enumerate(self._accumulated):
            cut = it.offsets.get("cut") if it is not None else None
            if cut is None or cut.is_empty:
                continue
            for j, p in enumerate(self._pe_parts(cut)):
                items.append((i, j))
        self._pe_items = items

    @staticmethod
    def _pe_parts(cut):
        return [p for p in getattr(cut, "geoms", [cut]) if p.geom_type == "Polygon" and not p.is_empty]

    def _pe_poly(self, k):
        i, j = self._pe_items[k]
        parts = self._pe_parts(self._accumulated[i].offsets.get("cut"))
        return parts[j] if j < len(parts) else None

    def _pe_set_poly(self, i, j, poly):
        from shapely.geometry import MultiPolygon

        it = self._accumulated[i]
        parts = self._pe_parts(it.offsets.get("cut"))
        if poly is None:
            del parts[j]
        else:
            parts[j] = poly
        import dataclasses

        new_cut = MultiPolygon(parts) if parts else MultiPolygon()
        self._accumulated[i] = dataclasses.replace(it, offsets={**it.offsets, "cut": new_cut})

    # ---- 그리기 -----------------------------------------------------------------
    def _pe_redraw_all(self):
        if not self._pe_active:
            return
        c = self.preview_canvas
        c.delete("pe")
        for k in range(len(self._pe_items)):
            if k == self._pe_sel:
                continue
            P = self._pe_poly(k)
            if P is None:
                continue
            coords = []
            for x, y in P.exterior.coords:
                coords.extend(self._pe_to_canvas(x, y))
            if len(coords) >= 6:
                c.create_line(*coords, fill=PE_CUT, width=2, tags=("pe", "pe_cut"))
        self._pe_redraw_sel()

    def _pe_redraw_sel(self):
        c = self.preview_canvas
        c.delete("pe_sel")
        if not self._pe_active or self._pe_path is None or len(self._pe_path) == 0:
            return
        bp = self._pe_path
        step = max(0.5, 1.5 / self._pe_scale())
        coords = []
        for x, y in bp.flatten(step):
            coords.extend(self._pe_to_canvas(x, y))
        coords.extend(coords[:2])
        if len(coords) >= 6:
            c.create_line(*coords, fill=PE_SEL, width=2, tags=("pe", "pe_sel"))
        n = len(bp)
        sel = self._pe_sel_node
        handles = []
        if sel is not None and 0 <= sel < n:
            nd = bp.nodes[sel]
            handles += [(sel, "in", nd.h_in), (sel, "out", nd.h_out)]
            handles.append(((sel - 1) % n, "out", bp.nodes[(sel - 1) % n].h_out))
            handles.append(((sel + 1) % n, "in", bp.nodes[(sel + 1) % n].h_in))
        for idx, which, h in handles:
            anchor = bp.nodes[idx].p
            if np.linalg.norm(h - anchor) < 1e-6:
                continue
            ax, ay = self._pe_to_canvas(*anchor)
            hx, hy = self._pe_to_canvas(*h)
            c.create_line(ax, ay, hx, hy, fill=PE_HANDLE, width=1, tags=("pe", "pe_sel"))
            c.create_oval(hx - 4, hy - 4, hx + 4, hy + 4, fill=PE_HANDLE, outline="#FFFFFF", tags=("pe", "pe_sel"))
        for idx, nd in enumerate(bp.nodes):
            x, y = self._pe_to_canvas(*nd.p)
            r = 4
            fill = PE_SEL if idx == sel else "#FFFFFF"
            if nd.smooth:
                c.create_rectangle(x - r, y - r, x + r, y + r, fill=fill, outline=PE_SEL, width=1, tags=("pe", "pe_sel"))
            else:
                # 뾰족한 점은 마름모
                c.create_polygon(x, y - r - 1, x + r + 1, y, x, y + r + 1, x - r - 1, y,
                                 fill=fill, outline=PE_SEL, width=1, tags=("pe", "pe_sel"))

    # ---- 고르기 ------------------------------------------------------------------
    def _pe_select(self, k):
        self._pe_commit_working()
        self._pe_sel = k
        self._pe_sel_node = None
        self._pe_path = None
        self._pe_path_dirty = False
        if k is not None:
            P = self._pe_poly(k)
            tol = 0.1 * float(self.dpi.get() or 300.0) / 25.4  # 0.1mm
            self._pe_path = _bz.path_for_polygon(P, tol=max(0.8, tol)) if P is not None else None
        self._pe_redraw_all()

    def _pe_hit_cut(self, x, y):
        """(x, y)에 가장 가까운 칼선 번호(허용 거리 안), 없으면 None."""
        from shapely.geometry import Point

        q = Point(x, y)
        tol = self._pe_tol(8)
        best, bk = tol, None
        for k in range(len(self._pe_items)):
            P = self._pe_poly(k)
            if P is None:
                continue
            bx0, by0, bx1, by1 = P.bounds
            if x < bx0 - tol or x > bx1 + tol or y < by0 - tol or y > by1 + tol:
                continue
            d = P.exterior.distance(q)
            if d <= best:
                best, bk = d, k
        return bk

    def _pe_hit_node(self, x, y):
        bp = self._pe_path
        if bp is None:
            return None
        tol = self._pe_tol(7)
        q = np.array([x, y])
        best, bi = tol, None
        for i, nd in enumerate(bp.nodes):
            d = float(np.linalg.norm(nd.p - q))
            if d <= best:
                best, bi = d, i
        return bi

    def _pe_hit_handle(self, x, y):
        bp = self._pe_path
        sel = self._pe_sel_node
        if bp is None or sel is None:
            return None
        n = len(bp)
        q = np.array([x, y])
        tol = self._pe_tol(7)
        cands = [(sel, "in"), (sel, "out"), ((sel - 1) % n, "out"), ((sel + 1) % n, "in")]
        best, hit = tol, None
        for idx, which in cands:
            nd = bp.nodes[idx]
            h = nd.h_in if which == "in" else nd.h_out
            if np.linalg.norm(h - nd.p) < 1e-6:
                continue
            d = float(np.linalg.norm(h - q))
            if d <= best:
                best, hit = d, (idx, which)
        return hit

    # ---- 마우스 -----------------------------------------------------------------
    def _pe_on_press(self, event):
        x, y = self._pe_from_event(event)
        self._pe_dirty_since_press = False
        alt = bool(event.state & 0x20000) or bool(event.state & 0x0008)
        h = self._pe_hit_handle(x, y)
        if h is not None:
            self._pe_drag = {"kind": "handle", "idx": h[0], "which": h[1], "alt": alt}
            self._pe_snapshot_before()
            return
        ni = self._pe_hit_node(x, y)
        if ni is not None:
            self._pe_sel_node = ni
            self._pe_drag = {"kind": "node", "idx": ni, "last": (x, y)}
            self._pe_snapshot_before()
            self._pe_redraw_sel()
            return
        if self._pe_path is not None:
            seg, t, d = self._pe_path.nearest((x, y))
            if d <= self._pe_tol(7):
                self._pe_sel_node = None
                self._pe_drag = {"kind": "path", "last": (x, y)}
                self._pe_snapshot_before()
                self._pe_redraw_sel()
                return
        k = self._pe_hit_cut(x, y)
        self._pe_drag = None
        if k is not None:
            self._pe_select(k)
            self._pe_drag = {"kind": "path", "last": (x, y)}
            self._pe_snapshot_before()
        else:
            self._pe_select(None)

    def _pe_on_drag(self, event):
        d = self._pe_drag
        if d is None or self._pe_path is None:
            return
        x, y = self._pe_from_event(event)
        bp = self._pe_path
        if d["kind"] == "node":
            bp.move_node(d["idx"], x, y)
        elif d["kind"] == "handle":
            bp.move_handle(d["idx"], d["which"], x, y, break_smooth=d.get("alt", False))
        elif d["kind"] == "path":
            lx, ly = d["last"]
            bp.translate(x - lx, y - ly)
            d["last"] = (x, y)
        self._pe_dirty_since_press = True
        self._pe_path_dirty = True
        self._pe_redraw_sel()

    def _pe_on_release(self, _event):
        d = self._pe_drag
        self._pe_drag = None
        if d is None:
            return
        if self._pe_dirty_since_press:
            self._pe_commit_working(push_undo=True)
        else:
            self._pe_pending_undo = None

    def _pe_on_double(self, event):
        if not self._pe_active or self._pe_path is None:
            return
        x, y = self._pe_from_event(event)
        ni = self._pe_hit_node(x, y)
        self._pe_snapshot_before()
        self._pe_path_dirty = True
        if ni is not None:
            self._pe_path.toggle_smooth(ni)
            self._pe_sel_node = ni
        else:
            seg, t, dist = self._pe_path.nearest((x, y))
            if dist > self._pe_tol(8):
                self._pe_pending_undo = None
                return
            self._pe_sel_node = self._pe_path.insert_at(seg, t)
        self._pe_commit_working(push_undo=True)
        self._pe_redraw_sel()

    # ---- 키 ---------------------------------------------------------------------
    def _pe_key_ok(self):
        if not self._pe_active:
            return False
        try:
            w = self.focus_get()
            if isinstance(w, (tk.Entry, tk.Text)):
                return False
        except Exception:  # noqa: BLE001
            pass
        return True

    def _pe_delete_key(self, _event=None):
        if not self._pe_key_ok() or self._pe_sel is None:
            return
        self._pe_snapshot_before()
        if self._pe_sel_node is not None and self._pe_path is not None:
            if self._pe_path.delete(self._pe_sel_node):
                self._pe_path_dirty = True
                self._pe_sel_node = None
                self._pe_commit_working(push_undo=True)
                self._pe_redraw_sel()
            else:
                self._pe_pending_undo = None
                self.status.set("칼선에는 기준점이 3개 이상 있어야 해요. 칼선 전체를 지우려면 점을 고르지 말고 Delete를 누르세요.")
            return
        # 칼선 전체 삭제
        k = self._pe_sel
        i, j = self._pe_items[k]
        same = self._pe_same_shape_targets(self._pe_poly(k), k) if self._pe_apply_same.get() else []
        targets = [(i, j)] + [(ti, tj) for ti, tj, _q in same]
        # 같은 항목 안 여러 조각을 지울 때 번호가 밀리지 않게 뒤에서부터
        for ti, tj in sorted(targets, key=lambda t: (t[0], -t[1])):
            self._pe_set_poly(ti, tj, None)
        self._pe_push_undo()
        self._pe_sel = None
        self._pe_path = None
        self._pe_collect_items()
        self._pe_refresh_result()
        self._pe_redraw_all()
        self.status.set(f"칼선 {len(targets)}개를 지웠습니다(Ctrl+Z로 되돌리기).")

    def _pe_escape(self, _event=None):
        if not self._pe_key_ok():
            return
        if self._pe_sel_node is not None:
            self._pe_sel_node = None
            self._pe_redraw_sel()
        elif self._pe_sel is not None:
            self._pe_select(None)
        else:
            self._pe_exit()

    def _pe_arrow(self, dx, dy, big=False):
        if not self._pe_key_ok() or self._pe_path is None:
            return
        step = (1.0 if big else 0.1) * float(self.dpi.get() or 300.0) / 25.4
        self._pe_snapshot_before()
        self._pe_path_dirty = True
        if self._pe_sel_node is not None:
            nd = self._pe_path.nodes[self._pe_sel_node]
            self._pe_path.move_node(self._pe_sel_node, nd.p[0] + dx * step, nd.p[1] + dy * step)
        else:
            self._pe_path.translate(dx * step, dy * step)
        self._pe_commit_working(push_undo=True)
        self._pe_redraw_sel()

    def _pe_toggle_selected_node(self):
        if self._pe_path is None or self._pe_sel_node is None:
            self.status.set("먼저 칼선을 클릭하고 바꿀 기준점을 고르세요.")
            return
        self._pe_snapshot_before()
        self._pe_path.toggle_smooth(self._pe_sel_node)
        self._pe_path_dirty = True
        self._pe_commit_working(push_undo=True)
        self._pe_redraw_sel()

    def _pe_bind_keys(self):
        def guard(fn):
            def h(event):
                if self._pe_key_ok():
                    fn(event)
                    return "break"
                return None
            return h

        self.bind_all("<Delete>", guard(self._pe_delete_key), add="+")
        self.bind_all("<BackSpace>", guard(self._pe_delete_key), add="+")
        self.bind_all("<Control-z>", guard(lambda e: self._pe_undo_last()), add="+")
        self.bind_all("<Control-Z>", guard(lambda e: self._pe_undo_last()), add="+")
        self.bind_all("<Escape>", guard(self._pe_escape), add="+")
        for key, dx, dy in (("Left", -1, 0), ("Right", 1, 0), ("Up", 0, -1), ("Down", 0, 1)):
            self.bind_all(f"<{key}>", guard(lambda e, dx=dx, dy=dy: self._pe_arrow(dx, dy)), add="+")
            self.bind_all(f"<Shift-{key}>", guard(lambda e, dx=dx, dy=dy: self._pe_arrow(dx, dy, True)), add="+")
        self.preview_canvas.bind("<Double-Button-1>", self._pe_on_double, add="+")

    # ---- 저장(작업본 -> 칼선) ------------------------------------------------------
    def _pe_snapshot_before(self):
        """지금 바꾸기 직전 상태(이 칼선과 같은 그림 칼선들)를 되돌리기용으로 잡아 둔다."""
        if self._pe_sel is None:
            self._pe_pending_undo = None
            return
        self._pe_pending_undo = [(i, it.offsets.get("cut")) for i, it in enumerate(self._accumulated)]

    def _pe_push_undo(self):
        snap = getattr(self, "_pe_pending_undo", None)
        if snap is not None:
            self._pe_undo.append(snap)
            if len(self._pe_undo) > 60:
                self._pe_undo.pop(0)
        self._pe_pending_undo = None

    def _pe_commit_working(self, push_undo: bool = False):
        """작업 중인 패스를 칼선 도형으로 바꿔 넣는다(같은 그림 칼선에도)."""
        if not getattr(self, "_pe_active", False) or self._pe_sel is None or self._pe_path is None:
            return
        if not self._pe_path_dirty:
            if push_undo:
                self._pe_pending_undo = None
            return
        self._pe_path_dirty = False
        k = self._pe_sel
        old = self._pe_poly(k)
        if old is None:
            return
        new = self._pe_path.to_polygon(step=1.0)
        if new is None or new.is_empty or new.area < 4.0:
            if push_undo:
                self._pe_pending_undo = None
            return
        i, j = self._pe_items[k]
        targets = self._pe_same_shape_targets(old, k) if self._pe_apply_same.get() else []
        self._pe_set_poly(i, j, new)
        _bz.remember(new, self._pe_path)
        ox0, oy0, ox1, oy1 = old.bounds
        for ti, tj, q in targets:
            qx0, qy0, qx1, qy1 = q.bounds
            sx = (qx1 - qx0) / (ox1 - ox0) if ox1 > ox0 else 1.0
            sy = (qy1 - qy0) / (oy1 - oy0) if oy1 > oy0 else 1.0
            moved = self._pe_path.transformed(sx, sy, qx0 - sx * ox0, qy0 - sy * oy0)
            qn = moved.to_polygon(step=1.0)
            if qn is not None and not qn.is_empty:
                self._pe_set_poly(ti, tj, qn)
                _bz.remember(qn, moved)
        if push_undo:
            self._pe_push_undo()
        self._pe_refresh_result()
        if targets:
            self._pe_redraw_all()
            self.status.set(f"고친 모양을 같은 그림 칼선 {len(targets)}개에도 똑같이 적용했습니다.")

    def _pe_same_shape_targets(self, P0, k_self):
        """P0와 같은 모양(위치만 다르거나 아주 조금 크기가 다른 반복 칸) 칼선 목록 [(항목, 조각, 도형)]."""
        if P0 is None or P0.is_empty:
            return []
        from shapely.affinity import affine_transform

        out = []
        ox0, oy0, ox1, oy1 = P0.bounds
        ow, oh = ox1 - ox0, oy1 - oy0
        for k in range(len(self._pe_items)):
            if k == k_self:
                continue
            Q = self._pe_poly(k)
            if Q is None or Q.is_empty:
                continue
            if abs(Q.area - P0.area) > 0.04 * P0.area:
                continue
            qx0, qy0, qx1, qy1 = Q.bounds
            qw, qh = qx1 - qx0, qy1 - qy0
            if ow <= 0 or oh <= 0 or abs(qw - ow) > 0.04 * ow or abs(qh - oh) > 0.04 * oh:
                continue
            sx, sy = qw / ow, qh / oh
            M = affine_transform(P0, [sx, 0, 0, sy, qx0 - sx * ox0, qy0 - sy * oy0])
            try:
                iou = M.intersection(Q).area / max(1e-9, M.union(Q).area)
            except Exception:  # noqa: BLE001
                continue
            if iou >= 0.97:
                i, j = self._pe_items[k]
                out.append((i, j, Q))
        return out

    def _pe_undo_last(self):
        if not self._pe_active:
            return
        if not self._pe_undo:
            self.status.set("더 되돌릴 수정이 없습니다.")
            return
        import dataclasses

        snap = self._pe_undo.pop()
        n = min(len(snap), len(self._accumulated))
        for i in range(n):
            _, cut = snap[i]
            it = self._accumulated[i]
            self._accumulated[i] = dataclasses.replace(it, offsets={**it.offsets, "cut": cut})
        self._pe_collect_items()
        self._pe_refresh_result()
        keep = self._pe_sel if self._pe_sel is not None and self._pe_sel < len(self._pe_items) else None
        self._pe_sel = None
        self._pe_path = None
        self._pe_select(keep)
        self.status.set("되돌렸습니다.")

    def _pe_refresh_result(self):
        try:
            from core.accumulate import combine_results

            self._last_result = combine_results(self._accumulated)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
