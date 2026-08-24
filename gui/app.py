"""
CutLine Studio - desktop prototype (Tkinter)

A minimal but complete desktop app:
  1. Open a raster (PNG/JPG) or vector (SVG) design file
  2. Set safety / cut / bleed offsets (mm) and working DPI
  3. Preview the generated cut lines over the artwork
  4. Export an Illustrator-ready SVG with labeled, colored layers

Run with:  python app.py
"""

import os
import sys
import threading
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.cutline_core import generate_cutlines, OffsetSpec
from core.svg_export import export_svg
from core.preview import render_preview

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

APP_TITLE = "CutLine Studio (prototype)"
PREVIEW_MAX_SIDE = 640


class CutLineApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("980x680")
        self.minsize(820, 560)

        self.input_path = tk.StringVar()
        self.is_vector = tk.BooleanVar(value=False)
        self.dpi = tk.DoubleVar(value=300.0)
        self.safety_mm = tk.DoubleVar(value=1.0)
        self.cut_mm = tk.DoubleVar(value=2.0)
        self.bleed_mm = tk.DoubleVar(value=3.0)
        self.status = tk.StringVar(value="파일을 선택하세요 (PNG / JPG / SVG)")

        self._last_result = None
        self._preview_photo = None  # keep a reference so Tk doesn't GC it

        self._build_layout()

    # ------------------------------------------------------------------
    def _build_layout(self):
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)

        # --- left: controls -------------------------------------------------
        controls = ttk.Frame(root, padding=(0, 0, 12, 0))
        controls.pack(side="left", fill="y")

        ttk.Label(controls, text="1. 도안 파일", font=("", 11, "bold")).pack(anchor="w")
        file_row = ttk.Frame(controls)
        file_row.pack(fill="x", pady=(4, 2))
        ttk.Entry(file_row, textvariable=self.input_path, width=28).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(file_row, text="찾아보기", command=self._choose_file).pack(
            side="left", padx=(6, 0)
        )
        ttk.Label(
            controls,
            text="투명 배경 PNG/JPG 또는 SVG 벡터 파일",
            foreground="#666",
        ).pack(anchor="w", pady=(0, 14))

        ttk.Label(controls, text="2. 작업 해상도", font=("", 11, "bold")).pack(anchor="w")
        dpi_row = ttk.Frame(controls)
        dpi_row.pack(fill="x", pady=(4, 14))
        ttk.Label(dpi_row, text="DPI:").pack(side="left")
        ttk.Spinbox(
            dpi_row, from_=72, to=1200, increment=1, textvariable=self.dpi, width=8
        ).pack(side="left", padx=(6, 0))

        ttk.Label(controls, text="3. 오프셋 간격 (mm, 도안 기준 바깥쪽)", font=("", 11, "bold")).pack(
            anchor="w"
        )
        self._offset_row(controls, "세이프티 (safety)", self.safety_mm, "#00CC44")
        self._offset_row(controls, "칼선 (cutting line)", self.cut_mm, "#FF0000")
        self._offset_row(controls, "블리딩 (bleeding)", self.bleed_mm, "#0000FF")

        ttk.Separator(controls).pack(fill="x", pady=14)

        self.generate_btn = ttk.Button(
            controls, text="칼선 생성 / 미리보기", command=self._on_generate
        )
        self.generate_btn.pack(fill="x", pady=(0, 8))

        self.export_btn = ttk.Button(
            controls, text="SVG로 내보내기", command=self._on_export, state="disabled"
        )
        self.export_btn.pack(fill="x")

        ttk.Separator(controls).pack(fill="x", pady=14)
        ttk.Label(
            controls,
            textvariable=self.status,
            wraplength=260,
            foreground="#333",
            justify="left",
        ).pack(anchor="w", fill="x")

        # --- right: preview ---------------------------------------------------
        preview_frame = ttk.Frame(root, relief="groove", borderwidth=1)
        preview_frame.pack(side="right", fill="both", expand=True)
        self.preview_label = ttk.Label(
            preview_frame, text="미리보기가 여기에 표시됩니다", anchor="center"
        )
        self.preview_label.pack(fill="both", expand=True)

    def _offset_row(self, parent, label, var, color):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=2)
        swatch = tk.Canvas(row, width=14, height=14, highlightthickness=0)
        swatch.create_rectangle(0, 0, 14, 14, fill=color, outline=color)
        swatch.pack(side="left", padx=(0, 6))
        ttk.Label(row, text=label, width=20).pack(side="left")
        ttk.Spinbox(
            row, from_=0.0, to=100.0, increment=0.5, textvariable=var, width=6
        ).pack(side="left")
        ttk.Label(row, text="mm").pack(side="left", padx=(4, 0))

    # ------------------------------------------------------------------
    def _choose_file(self):
        path = filedialog.askopenfilename(
            title="도안 파일 선택",
            filetypes=[
                ("지원 파일", "*.png *.jpg *.jpeg *.svg"),
                ("PNG/JPG", "*.png *.jpg *.jpeg"),
                ("SVG", "*.svg"),
                ("모든 파일", "*.*"),
            ],
        )
        if path:
            self.input_path.set(path)
            self.is_vector.set(path.lower().endswith(".svg"))
            self.status.set(f"불러옴: {os.path.basename(path)}")

    def _validate_inputs(self):
        path = self.input_path.get().strip()
        if not path or not os.path.isfile(path):
            messagebox.showwarning(APP_TITLE, "먼저 유효한 도안 파일을 선택하세요.")
            return None
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
        self.generate_btn.config(state="disabled")
        self.status.set("처리 중...")
        threading.Thread(target=self._run_generate, args=(path,), daemon=True).start()

    def _run_generate(self, path):
        try:
            offset_mm = OffsetSpec(
                safety_mm=self.safety_mm.get(),
                cut_mm=self.cut_mm.get(),
                bleed_mm=self.bleed_mm.get(),
            )
            result = generate_cutlines(
                image_path=path,
                is_vector=self.is_vector.get(),
                dpi=self.dpi.get(),
                offset_mm=offset_mm,
            )
            preview_png = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "_last_preview.png"
            )
            render_preview(
                result,
                preview_png,
                original_image_path=None if self.is_vector.get() else path,
            )
            self._last_result = result
            self.after(0, self._show_preview, preview_png)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.after(0, self._on_error, str(e))

    def _show_preview(self, preview_png):
        if Image is not None:
            img = Image.open(preview_png)
            w, h = img.size
            scale = min(PREVIEW_MAX_SIDE / w, PREVIEW_MAX_SIDE / h, 1.0)
            if scale < 1.0:
                img = img.resize((int(w * scale), int(h * scale)))
            self._preview_photo = ImageTk.PhotoImage(img)
            self.preview_label.config(image=self._preview_photo, text="")
        n_shapes = len(self._last_result.design.geoms)
        self.status.set(f"완료. 도안 {n_shapes}개 영역 감지됨. 내보내기 준비됨.")
        self.generate_btn.config(state="normal")
        self.export_btn.config(state="normal")

    def _on_error(self, message):
        messagebox.showerror(APP_TITLE, f"오류가 발생했습니다:\n{message}")
        self.status.set("오류 발생. 파일/설정을 확인하세요.")
        self.generate_btn.config(state="normal")

    def _on_export(self):
        if self._last_result is None:
            return
        default_name = os.path.splitext(os.path.basename(self.input_path.get()))[0] + "_cutlines.svg"
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
