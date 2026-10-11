"""화이트 레이어 만들기 + 화이트 기준 칼선 보완(CutLineApp에 섞어 쓰는 mixin, 2026-10-05).

멍푸: "칼선을 보완하고 화이트 레이어를 만들어주는 기능을 추가할거야. 좋은 예는 키스컷 마테 좋은 예의
화이트 레이어를 기준으로 ... 만들어."

왼쪽 패널 "화이트 레이어" 카드:
  - [화이트 레이어 만들기]: 그림이 있는 자리에 흰색 잉크 판을 만든다(core.white_layer). 화면에서는 하늘색
    반투명으로 겹쳐 보인다. 그림 출처는 .ai 안 이미지의 투명도 -> 투명 배경 PNG의 알파 -> (꽉 찬 그림이면)
    지금까지 만든 칼선의 그림 실루엣 순서.
  - [화이트 기준으로 칼선 보완]:
      * 키스컷이거나 아직 칼선이 없으면: 좋은 예처럼 화이트에서 고르게 띄운 칼선으로 만든다(지금 칼선이
        있으면 바꿀지 먼저 묻는다).
      * 그 밖의 작업 종류: 지금 칼선은 그대로 두고, 칼선이 없는 그림 조각에만 칼선을 더한다. 옆 칼선과
        너무 가까워 자동으로 더하기 어려운 곳은 위치만 알려 준다.
  - 칼선 내보내기를 하면 화이트도 함께 저장된다(PNG는 "_화이트.png" 따로, SVG는 "화이트레이어" 그룹).
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
        self.white_status = tk.StringVar(value="화이트 없음")
        self._wl_busy = False

    # ---- 화면 --------------------------------------------------------------
    def _wl_build_section(self, parent):
        sec = self._section(
            parent, "화이트 레이어",
            "투명·홀로그램·크라프트 스티커의 흰색 잉크 판 · 실제 납품 파일(좋은 예) 기준",
        )
        ctk.CTkLabel(
            sec, textvariable=self.white_status, font=self.font_body, text_color="#111111",
            anchor="w", justify="left", wraplength=300,
        ).pack(fill="x", pady=(4, 4))
        self._make_number_field(sec, "화이트 줄이기", self.white_choke_mm, from_=0.0, to=1.0, increment=0.05)
        self._white_make_btn = self._btn_secondary(sec, "⬜ 화이트 레이어 만들기", self._on_make_white)
        self._white_make_btn.pack(fill="x", pady=(4, 6))
        self._make_number_field(sec, "칼선 간격", self.white_cut_offset_mm, from_=0.3, to=10.0,
                                increment=0.1)
        self._white_cut_btn = self._btn_secondary(sec, "✂ 화이트 기준으로 칼선 보완", self._on_white_cut)
        self._white_cut_btn.pack(fill="x", pady=(4, 2))
        return sec

    # ---- 상태 --------------------------------------------------------------
    def _wl_clear(self):
        from core.preview import set_white_overlay

        self._white_geom = None
        self._white_mask = None
        self._white_source = ""
        set_white_overlay(None)
        if hasattr(self, "white_status"):
            self.white_status.set("화이트 없음")

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
                                   ["화이트 레이어는 AI·PNG·JPG 도안에서 만들 수 있습니다."], kind="error")
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
                lines.append("흰 배경 같은 꽉 찬 그림은 먼저 칼선을 만들어야 그 그림 모양으로 화이트를 만듭니다.")
            self._show_note_dialog("화이트를 만들 수 없습니다", lines, kind="error")
            return
        self._white_mask, self._white_geom, self._white_source = mask, geom, source
        set_white_overlay(geom)
        src_txt = {"ai_alpha": "AI 안 그림의 투명도", "alpha": "그림의 투명 배경",
                   "design": "칼선을 만든 그림 모양"}.get(source, source)
        n = len(geom.geoms) if hasattr(geom, "geoms") else 1
        self.white_status.set(f"화이트 {n}조각 · 기준: {src_txt}\n화면에선 하늘색으로 보여요(저장은 흰색)")
        self._wl_rerender(path)
        self.status.set("화이트 레이어를 만들었습니다. 칼선 내보내기를 하면 함께 저장됩니다.")
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
                                   ["화이트 기준 칼선은 AI·PNG·JPG 도안에서 만들 수 있습니다."], kind="error")
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
                "칼선을 화이트 기준으로 바꿀까요?",
                [f"지금 만든 칼선 {len(self._accumulated)}개 영역을 지우고, 화이트에서 "
                 f"{float(self.white_cut_offset_mm.get()):.1f}mm 띄운 칼선으로 새로 만듭니다(키스컷 좋은 예 방식).",
                 "칼선끼리 2mm보다 가까워지는 조각은 하나로 합칩니다."],
            ):
                return
        if self._pe_active:
            self._pe_exit(rerender=False)
        self._wl_set_busy(True)
        self.status.set("화이트 기준 칼선을 만드는 중...")
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
        self.accum_status.set(f"누적 {n_items}개 영역")
        self.export_btn.configure(state="normal")
        n = len(cuts.geoms) if hasattr(cuts, "geoms") else 0
        if replace:
            self.status.set(f"화이트 기준 칼선 {n}개를 만들었습니다.")
            lines = [f"화이트에서 {off:.1f}mm 띄운 칼선 {n}개를 만들었습니다.",
                     "칼선끼리 2mm보다 가까워지는 조각은 하나로 합쳤습니다."]
        else:
            self.status.set(f"빠진 그림 {n_missing}곳 중 {n}곳에 칼선을 더했습니다.")
            lines = [f"칼선이 없던 그림 {n_missing}곳 중 {n}곳에 화이트 기준 칼선을 더했습니다(기존 칼선은 그대로)."]
            if not n_missing:
                lines = ["모든 그림에 이미 칼선이 있어 더할 곳이 없습니다."]
            for x, y in too_close[:8]:
                lines.append(f"옆 칼선과 너무 가까워 더하지 않은 곳: 가로 {x:.0f}mm, 세로 {y:.0f}mm -- 직접 확인해 주세요.")
        self._show_note_dialog("화이트 기준 칼선 보완", lines, kind="info")

    # ---- 내보내기 ------------------------------------------------------------
    def _wl_export_png(self, cut_png_path, dpi):
        """칼선 PNG 옆에 화이트 PNG(같은 크기, 흰색 면)를 저장. 저장한 경로(없으면 None)."""
        if self._white_geom is None or self._last_result is None:
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
        """멍푸: "칼선 종류에 키스컷 포함해서 칼선이 왜 제대로 인식 안 되는지 파악해" -- 키스컷 롤을 요소마다
        상자로 나눠 실루엣을 따로 따던 예전 방식은, 흩어진 반짝이·작은 별 묶음을 사각형으로 잡거나(실루엣 실패)
        칼선이 그림 안을 가로지르고, 좋은 예처럼 가까운 조각을 한 칼선으로 묶지 못했다(좋은 예 대조 67개 중 34~40개
        일치). 그림이 투명 배경(.ai 안 투명도 또는 PNG 알파)이면 좋은 예와 같은 방식 -- 그림 모양에서 고르게 띄운
        칼선, 가까운 조각은 합침 -- 으로 만든다(좋은 예와 60개 모두 일치). 꽉 찬 그림이면 False(예전 방식 그대로).
        작업 스레드에서 불린다."""
        from core.accumulate import combine_results
        from core.cutline_core import CutlineResult, OffsetSpec
        from core.preview import render_preview
        from gui.app import _work_file_dir

        try:
            ai_path = self._ai_source_path
            art, source = wl.art_mask_for_white(path, None, ai_path=ai_path)
            if source == "design" or not art.any():
                return False
            dpi = self._wl_image_dpi()
            off = max(0.1, float(self.white_cut_offset_mm.get()))
            mask = wl.build_white_mask(art, dpi)
            cuts = wl.cut_from_white_mask(mask, dpi, offset_mm=off)
            if cuts.is_empty:
                return False
            h, w = mask.shape
            item = CutlineResult(
                dpi=float(self.dpi.get()), width_px=w, height_px=h, design=wl.mask_to_geom(mask),
                offsets={"cut": cuts}, offset_mm=OffsetSpec(off, off, off),
                adjustments=[f"투명 배경 그림이라 그림 모양에서 {off:.1f}mm 띄운 키스컷 칼선 {len(cuts.geoms)}개를 "
                             "만들었습니다(가까운 조각은 하나로 합침 -- 키스컷 좋은 예 방식). 간격은 '화이트 레이어' "
                             "카드의 '칼선 간격'에서 바꿀 수 있습니다."],
            )
            self._accumulated.append(item)
            combined = combine_results(self._accumulated)
            png = os.path.join(_work_file_dir(), "_last_preview.png")
            render_preview(combined, png, original_image_path=path, async_save=True)
            self._last_result = combined
            self.after(0, self._show_preview, png, item)
            return True
        except Exception:  # noqa: BLE001 -- 실패하면 예전 방식으로
            traceback.print_exc()
            return False
