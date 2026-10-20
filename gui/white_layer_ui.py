"""화이트 레이어 만들기 + 화이트 기준 칼선 보완(CutLineApp에 섞어 쓰는 mixin, 2026-10-05).

멍푸: "칼선을 보완하고 화이트 레이어를 만들어주는 기능을 추가할거야. 좋은 예는 키스컷 마테 좋은 예의
화이트 레이어를 기준으로 ... 만들어."

왼쪽 패널 "화이트 레이어" 카드:
  - [화이트 레이어 만들기]: 그림이 있는 자리에 흰색 잉크 판을 만든다(core.white_layer). 화면에서는 파란색으로
    보인다(저장은 흰색). 그림 출처는 .ai 안 이미지의 투명도 -> 투명 배경 PNG의 알파 -> (꽉 찬 그림이면)
    지금까지 만든 칼선의 그림 실루엣 순서.
  - [화이트 기준으로 칼선 보완]:
      * 키스컷이거나 아직 칼선이 없으면: 좋은 예처럼 화이트에서 고르게 띄운 칼선으로 만든다(지금 칼선이
        있으면 바꿀지 먼저 묻는다).
      * 그 밖의 작업 종류: 지금 칼선은 그대로 두고, 칼선이 없는 그림 조각에만 칼선을 더한다. 옆 칼선과
        너무 가까워 자동으로 더하기 어려운 곳은 위치만 알려 준다.
  - 완성 파일 내보내기(예전 이름 칼선 내보내기)를 하면 화이트도 함께 저장된다(PNG는 "_화이트.png" 따로, SVG는 "화이트레이어" 그룹).
"""
from __future__ import annotations

import os
import threading
import tkinter as tk
import traceback

import customtkinter as ctk

from core import white_layer as wl


class WhiteLayerMixin:
    def _wl_init_state(self):
        self._white_geom = None        # 화이트(이미지 픽셀 좌표 MultiPolygon)
        self._white_mask = None        # 같은 화이트의 픽셀 마스크(칼선 계산용)
        self._white_source = ""
        self._ai_source_path = None    # .ai로 열었으면 그 원본 경로(안 이미지의 투명도를 읽음)
        self.white_choke_mm = tk.DoubleVar(value=wl.DEFAULT_CHOKE_MM)
        self.white_cut_offset_mm = tk.DoubleVar(value=wl.DEFAULT_CUT_OFFSET_MM)
        self.white_status = tk.StringVar(value="화이트 넣기")
        # 2026-10-11(멍푸: "표시한 문구 체크 표기 할 수 있게 해"): "화이트 없음" 글자 대신 체크칸 --
        # 켜면 화이트를 만들고(이미 있으면 다시 보이게) 저장에 넣고, 끄면 화면·저장에서 뺀다.
        self.white_on = tk.BooleanVar(value=False)
        self.white_view = tk.StringVar(value="적용된 모습")
        self._wl_busy = False

    # ---- 화면 --------------------------------------------------------------
    def _wl_build_section(self, parent):
        sec = self._section(
            parent, "화이트 레이어",
            "투명 스티커 그림 밑에 까는 흰색 바탕입니다.",
        )
        from gui.app import ACCENT, ACCENT_HOVER, TEXT_PRIMARY
        from gui.tooltips import FIELD_TIPS

        self._white_check = self._wrap_checkbox(ctk.CTkCheckBox(
            sec, textvariable=self.white_status, variable=self.white_on, command=self._on_white_check,
            font=self.font_body, text_color=TEXT_PRIMARY, fg_color=ACCENT, hover_color=ACCENT_HOVER,
        ))
        self._white_check._cl_tip_text = FIELD_TIPS["white_on"]
        self._white_check.pack(anchor="w", pady=(4, 4))
        # 보기: 그림이 화이트 위에 얹힌 모습 / 화이트만(2026-10-11)
        self._white_view_btn = ctk.CTkSegmentedButton(
            sec, values=["적용된 모습", "화이트만"], variable=self.white_view, command=self._on_white_view,
            font=self.font_body, selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
        )
        self._white_view_btn.pack(fill="x", pady=(2, 6))
        self._make_number_field(sec, "화이트 크기 조정", self.white_choke_mm, from_=0.0, to=1.0, increment=0.05,
                                tip=FIELD_TIPS["white_choke"])
        self._white_make_btn = self._btn_secondary(sec, "⬜ 화이트 레이어 만들기", self._on_make_white)
        self._white_make_btn.pack(fill="x", pady=(4, 6))
        self._make_number_field(sec, "칼선 간격", self.white_cut_offset_mm, from_=0.3, to=10.0,
                                increment=0.1, tip=FIELD_TIPS["white_cut"])
        self._white_cut_btn = self._btn_secondary(sec, "✂ 칼선 재설정", self._on_white_cut)
        self._white_cut_btn.pack(fill="x", pady=(4, 2))
        return sec

    # ---- 상태 --------------------------------------------------------------
    def _wl_clear(self):
        from core.preview import set_white_overlay

        self._white_geom = None
        self._white_mask = None
        self._white_source = ""
        set_white_overlay(None)
        from core.preview import set_white_only

        set_white_only(False)
        if hasattr(self, "white_view"):
            self.white_view.set("적용된 모습")
        if hasattr(self, "white_status"):
            self.white_status.set("화이트 넣기")
        if hasattr(self, "white_on"):
            self.white_on.set(False)

    def _on_white_view(self, _value=None):
        from core.preview import set_white_only

        only = self.white_view.get() == "화이트만"
        set_white_only(only)
        if self._wl_active_geom() is None:
            if only:
                self.status.set("화이트를 먼저 만들어 주세요(체크칸 또는 '화이트 레이어 만들기').")
            return
        self.status.set("화이트만 보고 있어요(그림 숨김)." if only else "그림이 화이트 위에 얹힌 모습이에요.")
        path = self.input_path.get()
        if path and os.path.exists(path):
            self._wl_rerender(path)

    def _wl_active_geom(self):
        """저장·화면에 쓸 화이트(체크를 끄면 None)."""
        return self._white_geom if (self._white_geom is not None and self.white_on.get()) else None

    def _on_white_check(self):
        from core.preview import set_white_overlay

        if self.white_on.get():
            if self._white_geom is None:
                self.white_on.set(False)   # 다 만들어지면 켜짐(_wl_made)
                self._on_make_white()
                return
            set_white_overlay(self._white_geom)
            self.status.set("화이트를 다시 넣었습니다. 완성 파일을 내보내면 함께 저장됩니다.")
        else:
            set_white_overlay(None)
            self.status.set("화이트를 뺐어요. 저장할 때 화이트는 안 들어가요.")
        path = self.input_path.get()
        if path and os.path.exists(path) and getattr(self, "_preview_full_image", None) is not None:
            self._wl_rerender(path)

    def _wl_image_dpi(self):
        """작업 이미지 1px의 실제 해상도(dpi) -- mm 값을 픽셀로 바꿀 때."""
        placement = getattr(self, "_ai_placement", None)
        if placement and placement.get("pt_per_px"):
            return 72.0 / float(placement["pt_per_px"])
        d = getattr(self, "_source_raster_dpi", None)
        if d:
            return float(d)
        try:
            return float(self.dpi.get())
        except Exception:  # noqa: BLE001
            return 300.0

    def _wl_set_busy(self, busy):
        self._wl_busy = busy
        st = "disabled" if busy else "normal"
        for b in (getattr(self, "_white_make_btn", None), getattr(self, "_white_cut_btn", None)):
            if b is not None:
                b.configure(state=st)

    # ---- 화이트 만들기 ------------------------------------------------------
    def _on_make_white(self):
        if self._wl_busy:
            return
        path = self.input_path.get()
        if not path or not os.path.exists(path) or self.is_vector.get():
            self._show_note_dialog("도안 파일을 먼저 열어 주세요",
                                   ["AI·PNG·JPG 파일에서 만들 수 있어요."], kind="error")
            return
        self._wl_set_busy(True)
        self.status.set("화이트 레이어를 만드는 중...")
        threading.Thread(target=self._wl_run_make, args=(path, None, self._wl_make_args()), daemon=True).start()

    def _wl_make_args(self):
        """계산에 필요한 화면 값을 메인 스레드에서 미리 읽어 둔다(작업 스레드에서 Tk 변수를 읽지 않게)."""
        try:
            choke = max(0.0, float(self.white_choke_mm.get()))
        except Exception:  # noqa: BLE001
            choke = 0.0
        return {"dpi": self._wl_image_dpi(), "choke": choke, "items": list(self._accumulated),
                "ai": self._ai_source_path}

    def _wl_compute(self, path, args=None):
        """화이트 계산. Returns (mask, geom, source)."""
        args = args or self._wl_make_args()
        art, source = wl.art_mask_for_white(path, args["items"], ai_path=args["ai"])
        if not art.any():
            return None, None, source
        mask = wl.build_white_mask(art, args["dpi"], choke_mm=args["choke"])
        return mask, wl.mask_to_geom(mask), source

    def _wl_run_make(self, path, then, args):
        try:
            mask, geom, source = self._wl_compute(path, args)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._report_exception_to_server("화이트 레이어 만드는 중")
            self.after(0, self._wl_failed, str(e))
            return
        self.after(0, self._wl_made, path, mask, geom, source, then)

    def _wl_failed(self, msg):
        self._wl_set_busy(False)
        self.status.set("화이트 레이어를 만들지 못했습니다.")
        self._show_note_dialog("화이트 레이어를 만들지 못했습니다", [msg], kind="error")

    def _wl_made(self, path, mask, geom, source, then=None):
        from core.preview import set_white_overlay

        self._wl_set_busy(False)
        if geom is None or geom.is_empty:
            self.status.set("화이트를 깔 그림을 찾지 못했습니다.")
            lines = ["그림 영역을 찾지 못했습니다."]
            if source == "design":
                lines.append("배경이 있는 그림은 칼선을 먼저 만든 뒤에 눌러 주세요.")
            self._show_note_dialog("화이트를 만들 수 없습니다", lines, kind="error")
            return
        self._white_mask, self._white_geom, self._white_source = mask, geom, source
        set_white_overlay(geom)
        n = len(geom.geoms) if hasattr(geom, "geoms") else 1
        self.white_on.set(True)
        self.white_status.set(f"화이트 넣기 ({n}조각) · 화면은 파란색, 저장은 흰색")
        self._wl_rerender(path)
        self.status.set("화이트 레이어를 만들었습니다. 완성 파일을 내보내면 함께 저장됩니다.")
        if then:
            then()

    def _wl_rerender(self, path):
        """화이트를 겹쳐 미리보기를 다시 그림(확대 배율은 그대로)."""
        from core.cutline_core import CutlineResult, OffsetSpec
        from core.preview import open_rendered, render_preview
        from gui.app import _work_file_dir

        res = self._last_result
        if res is None:
            from PIL import Image

            with Image.open(path) as im:
                w, h = im.size
            from shapely.geometry import MultiPolygon

            res = CutlineResult(dpi=float(self.dpi.get()), width_px=w, height_px=h, design=MultiPolygon(),
                                offsets={}, offset_mm=OffsetSpec(0, 0, 0))
        png = os.path.join(_work_file_dir(), "_last_preview.png")
        render_preview(res, png, original_image_path=path, async_save=True)
        zoom = getattr(self, "_preview_zoom", 1.0)
        self._preview_full_image = open_rendered(png)
        self._source_image = None
        self._preview_zoom = zoom
        self._render_preview()

    # ---- 화이트 기준 칼선 보완 ----------------------------------------------------
    def _on_white_cut(self):
        if self._wl_busy:
            return
        path = self.input_path.get()
        if not path or not os.path.exists(path) or self.is_vector.get():
            self._show_note_dialog("도안 파일을 먼저 열어 주세요",
                                   ["AI·PNG·JPG 파일에서 할 수 있어요."], kind="error")
            return
        if self._white_mask is None:
            # 화이트가 아직 없으면 먼저 만들고 이어서
            self._wl_set_busy(True)
            self.status.set("화이트 레이어를 먼저 만드는 중...")
            threading.Thread(target=self._wl_run_make, args=(path, self._on_white_cut, self._wl_make_args()),
                             daemon=True).start()
            return
        replace = (self.job_type.get() == "MASKING_TAPE") or not self._accumulated
        if replace and self._accumulated:
            if not self._show_confirm_dialog(
                "칼선을 다시 만들까요?",
                [f"지금 칼선을 지우고, 그림에서 {float(self.white_cut_offset_mm.get()):.1f}mm 떨어진 칼선으로 새로 만들어요.",
                 "가까이 붙은 조각은 칼선 하나로 묶어요."],
            ):
                return
        if self._pe_active:
            self._pe_exit(rerender=False)
        self._wl_set_busy(True)
        self.status.set("칼선을 다시 만드는 중...")
        args = {"dpi": self._wl_image_dpi(), "off": max(0.1, float(self.white_cut_offset_mm.get())),
                "work_dpi": float(self.dpi.get()), "items": list(self._accumulated)}
        threading.Thread(target=self._wl_run_cut, args=(path, replace, args), daemon=True).start()

    def _wl_run_cut(self, path, replace, args):
        from core.accumulate import combine_results
        from core.cutline_core import CutlineResult, OffsetSpec
        from shapely.geometry import MultiPolygon

        try:
            dpi, off, work_dpi = args["dpi"], args["off"], args["work_dpi"]
            h, w = self._white_mask.shape
            if replace:
                cuts = wl.cut_from_white_mask(self._white_mask, dpi, offset_mm=off)
                note = f"화이트 기준 칼선 {len(cuts.geoms)}개(화이트에서 {off:.1f}mm)"
                too_close, n_missing = [], 0
                items = [CutlineResult(dpi=work_dpi, width_px=w, height_px=h, design=self._white_geom,
                                       offsets={"cut": cuts}, offset_mm=OffsetSpec(off, off, off),
                                       adjustments=[note])]
            else:
                existing = combine_results(args["items"]).offsets.get("cut")
                added, too_close, n_missing = wl.supplement_missing_cuts(
                    existing, self._white_mask, self._white_geom, dpi, offset_mm=off)
                items = list(args["items"])
                if not added.is_empty:
                    parts = [g for g in self._white_geom.geoms if g.intersects(added)]
                    items.append(CutlineResult(dpi=work_dpi, width_px=w, height_px=h,
                                               design=MultiPolygon(parts),
                                               offsets={"cut": added}, offset_mm=OffsetSpec(off, off, off),
                                               adjustments=[f"화이트 기준으로 빠진 칼선 {len(added.geoms)}개 추가"]))
                cuts = added
            combined = combine_results(items)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._report_exception_to_server("화이트 기준 칼선 만드는 중")
            self.after(0, self._wl_failed, str(e))
            return
        self.after(0, self._wl_cut_done, path, items, combined, replace, cuts, too_close, n_missing, args["off"])

    def _wl_cut_done(self, path, items, combined, replace, cuts, too_close, n_missing, off):
        self._wl_set_busy(False)
        self._accumulated = items
        self._last_result = combined
        self._wl_rerender(path)
        n_items = len(self._accumulated)
        self.accum_status.set(f"{n_items}개 영역이 쌓였습니다.")
        self.export_btn.configure(state="normal")
        n = len(cuts.geoms) if hasattr(cuts, "geoms") else 0
        if replace:
            self.status.set(f"칼선 {n}개를 다시 만들었어요.")
            lines = [f"그림에서 {off:.1f}mm 떨어진 칼선 {n}개를 만들었어요.",
                     "가까이 붙은 조각은 칼선 하나로 묶었어요."]
        else:
            self.status.set(f"칼선이 없던 그림 {n}곳에 칼선을 더했어요.")
            lines = [f"칼선이 없던 그림 {n_missing}곳 중 {n}곳에 칼선을 더했어요(원래 칼선은 그대로)."]
            if not n_missing:
                lines = ["모든 그림에 칼선이 이미 있어요."]
            for x, y in too_close[:8]:
                lines.append(f"옆 칼선과 너무 가까워 그대로 둔 곳: 가로 {x:.0f}mm, 세로 {y:.0f}mm -- 확인해 주세요.")
        self._show_note_dialog("칼선 재설정", lines, kind="info")

    # ---- 내보내기 ------------------------------------------------------------
    def _wl_export_png(self, cut_png_path, dpi):
        """칼선 PNG 옆에 화이트 PNG(같은 크기, 흰색 면)를 저장. 저장한 경로(없으면 None)."""
        if self._wl_active_geom() is None or self._last_result is None:
            return None
        from core.png_export import export_white_png

        base, ext = os.path.splitext(cut_png_path)
        if base.endswith("_cutlines"):
            base = base[: -len("_cutlines")]
        out = base + "_화이트.png"
        export_white_png(self._white_geom, self._last_result.width_px, self._last_result.height_px, out,
                         dpi=dpi or self._wl_image_dpi())
        return out

    # ---- 키스컷 자동 인식(2026-10-11) -------------------------------------------
    def _wl_kisscut_auto(self, path) -> bool:
        """키스컷 자동(예전 이름 그대로 -- _shape_cut_auto 참고)."""
        return self._shape_cut_auto(path)

    # 그림 모양 기준 칼선을 쓰는 작업 종류. 투명 배경 그림은 키스컷·유테·무테(2026-10-20 무테 추가, 자동 생성은 예전 방식),
    # 흰 테두리 스티커 시트는 넷 다(흰 테두리 바깥선이 곧 자를 선).
    SHAPE_CUT_ALPHA_JOBS = ("MASKING_TAPE", "LINE_ART", "BORDERLESS")
    SHAPE_CUT_STICKER_JOBS = ("MASKING_TAPE", "LINE_ART", "BORDERLESS", "AUTO_STYLE")

    def _shape_cut_auto(self, path) -> bool:
        """그림 모양 기준으로 칼선을 한 번에 만든다. 못 쓰는 그림이면 False(예전 방식으로). 작업 스레드에서 불린다.

        2026-10-11(멍푸: "칼선 종류에 키스컷 포함해서 칼선이 왜 제대로 인식 안 되는지 파악해"): 키스컷 롤을 요소마다
        상자로 나눠 실루엣을 따로 따던 예전 방식은, 흩어진 반짝이·작은 별 묶음을 사각형으로 잡거나 칼선이 그림 안을
        가로지르고 가까운 조각을 한 칼선으로 묶지 못했다. 투명 배경 그림이면 좋은 예 방식 -- 그림 모양에서 고르게
        띄운 칼선, 가까운 조각은 합침 -- 으로 만든다(좋은 예와 60개 모두 일치).
        2026-10-12(멍푸: "png 칼선 오류 고쳐", "칼선이 체크 된건 다 만들어져야해 배경색이 진하고 화려해도 정확한
        칼선을 따야해"): 실제 PNG 두 장으로 확인 --
          - 투명 배경 PNG + 유테: 연한 하늘색 털 같은 밝은 부분을 배경으로 보고 칼선이 그림 안을 가로질렀다.
            -> 유테도 투명도로 그림 모양을 잡고 유테 간격만큼 띄운다.
          - 파란 그러데이션·격자·반짝이 배경 위 흰 테두리 스티커: 배경을 한 가지 색으로 보지 못해 칼선 3개만
            나왔다(스티커 6개 이상). -> 흰 테두리 스티커를 찾아 테두리 바깥선을 칼선으로(core.white_layer.
            sticker_border_mask). 가까운 칼선(2mm 미만)은 합친다."""
        from core.accumulate import combine_results
        from core.cutline_core import CutlineResult, OffsetSpec
        from core.preview import render_preview
        from gui.app import _work_file_dir

        try:
            job = self.job_type.get()
            if job not in self.SHAPE_CUT_STICKER_JOBS:
                return False
            dpi = self._wl_image_dpi()
            kind, mask = wl.shape_cut_source(path, dpi, ai_path=self._ai_source_path)
            if kind is None or (kind != "sticker" and job not in self.SHAPE_CUT_ALPHA_JOBS):
                return False
            separate = None          # 가까운 칼선을 합치지 않은 결과(고를 수 있을 때만)
            n_close = 0
            if kind == "sticker":
                off = 0.0
                cuts = wl.cut_from_white_mask(mask, dpi, offset_mm=0.0)
                separate = wl.cut_from_white_mask(mask, dpi, offset_mm=0.0, merge_gap_mm=0.0)
                msg = "흰 테두리 스티커 {n}개에 칼선을 만들었습니다(흰 테두리 바깥선)."
            elif job == "BORDERLESS":
                off = max(0.5, float(self.style_margin_mm.get()))
                mask = wl.build_white_mask(mask, dpi)
                cuts, n_thin = wl.borderless_cut_from_mask(mask, dpi, inset_mm=off)
                msg = (f"무테 칼선 {{n}}개를 만들었습니다(그림 안쪽 {off:.1f}mm). "
                       "간격은 '유테/무테 간격'에서 바꿀 수 있습니다.")
                if n_thin:
                    msg += f" 너무 가는 그림 {n_thin}개는 안쪽으로 자를 수 없어 칼선을 만들지 않았습니다."
            else:
                mask = wl.build_white_mask(mask, dpi)
                if job == "MASKING_TAPE":
                    off = max(0.1, float(self.white_cut_offset_mm.get()))
                    msg = (f"키스컷 칼선 {{n}}개를 만들었습니다(그림에서 {off:.1f}mm). "
                           "간격은 아래 '화이트 레이어'의 '칼선 간격'에서 바꿀 수 있습니다.")
                else:
                    off = max(0.1, float(self.style_margin_mm.get()))
                    msg = (f"유테 칼선 {{n}}개를 만들었습니다(그림에서 {off:.1f}mm). "
                           "간격은 '유테/무테 간격'에서 바꿀 수 있습니다.")
                    separate = wl.cut_from_white_mask(mask, dpi, offset_mm=off, merge_gap_mm=0.0)
                cuts = wl.cut_from_white_mask(mask, dpi, offset_mm=off)
            if cuts.is_empty:
                return False
            if separate is not None and len(separate.geoms) > len(cuts.geoms):
                n_close = wl.count_close_pairs(separate, dpi, gap_mm=wl.DEFAULT_MERGE_GAP_MM)
            h, w = mask.shape
            design = wl.mask_to_geom(mask)

            def _finish(chosen, note=""):
                item = CutlineResult(
                    dpi=float(self.dpi.get()), width_px=w, height_px=h, design=design,
                    offsets={"cut": chosen}, offset_mm=OffsetSpec(off, off, off),
                )
                # 안내 문구는 adjustments(자동 조정)가 아니라 상태 줄에 -- adjustments에 넣으면 _show_preview가
                # "자동 조정 안내" 창을 띄우고 이 칼선의 간격(스티커는 0)을 유테/무테 간격 칸에 덮어써서, 다음
                # 그림의 유테 간격이 0이 됐다(2026-10-12 멍푸 PC에서 확인: 스티커판 다음 투명판 유테가 0.1mm).
                done_msg = "자동 인식 완료: " + msg.format(n=len(chosen.geoms)) + note
                self._accumulated.append(item)
                combined = combine_results(self._accumulated)
                png = os.path.join(_work_file_dir(), "_last_preview.png")
                render_preview(combined, png, original_image_path=path, async_save=True)
                self._last_result = combined
                return png, item, done_msg

            if n_close:
                # 2026-10-20(멍푸: "테두리 사이가 2mm보다 가까운 스티커를 ... 합칠지, 따로 자를지 선택 사항으로
                # 팝업 뜨기"): 고르는 창은 화면 스레드에서 띄우고, 고른 뒤 칼선을 넣는다.
                def _ask():
                    merge = self._show_confirm_dialog(
                        "가까운 칼선이 있습니다",
                        [f"칼선 사이가 {wl.DEFAULT_MERGE_GAP_MM:g}mm보다 가까운 곳이 {n_close}군데 있습니다.",
                         "하나로 합치기: 가까운 그림을 칼선 하나로 묶습니다. 사이의 좁은 틈도 함께 잘립니다.",
                         "따로 자르기: 그림마다 칼선을 따로 둡니다. 칼선이 가까워 재단이 밀리면 겹칠 수 있습니다."],
                        yes_text="하나로 합치기", no_text="따로 자르기",
                    )
                    chosen = cuts if merge else separate
                    note = "" if merge else f" 가까운 칼선 {n_close}군데는 따로 두었습니다."
                    png, item, done_msg = _finish(chosen, note)
                    self._show_preview(png, item)
                    self.status.set(done_msg)

                self.after(0, _ask)
                return True

            png, item, done_msg = _finish(cuts)

            def _show():
                self._show_preview(png, item)
                self.status.set(done_msg)

            self.after(0, _show)
            return True
        except Exception:  # noqa: BLE001 -- 실패하면 예전 방식으로
            traceback.print_exc()
            return False
