"""설치본 자체 점검(2026-11-02, 저용량 설치 파일).

저장 공간이 부족한 고객 PC를 위해 설치 파일을 줄이면서(installer/slim_app.py), 빌드 폴더에서
안 쓰는 라이브러리 파일을 지운다. 그때 꼭 필요한 파일까지 지웠는지 확인할 방법이 필요하다.
특히 도안 자동 인식의 scikit-image 함수(core.multi_design의 _peak_local_max/_ski_watershed)는
실패하면 예외를 삼키고 다른 방법으로 넘어가기 때문에, 파일이 빠져도 프로그램은 멀쩡히 켜지고
칼선만 달라질 수 있다.

`컷라인 스튜디오.exe --selfcheck 결과.json [그림 ...]`(소스에서는 `python -m core.build_selfcheck
결과.json [그림 ...]`)으로 실행하면 창을 띄우지 않고
  1) 칼선 계산에 쓰는 라이브러리(OpenCV, scikit-image, SciPy, shapely, Pillow, PyMuPDF,
     cryptography)를 한 번씩 직접 써 보고
  2) 시험용 시트(코드로 그린 그림)와 넘겨준 그림마다 도안 자동 인식 결과를 요약(개수·좌표 지문)해
결과.json에 쓰고 끝난다. 소스와 줄인 설치본의 지문이 같으면 같은 칼선이 나온다는 뜻이다.
결과에는 그림 파일 이름을 남기지 않는다(순번만).
"""
import hashlib
import json
import os
import sys
import tempfile
import time
import traceback

# 소스와 설치본 결과를 비교할 때 꼭 "ok"여야 하는 항목
REQUIRED_LIBS = ("opencv", "skimage", "scipy", "split_touching", "pillow", "pymupdf", "shapely", "crypto")


def _fp(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def _poly_rows(polys) -> list:
    rows = []
    for p in polys or []:
        for q in getattr(p, "geoms", [p]):
            rows.append([round(float(v), 2) for v in q.bounds] + [round(float(q.area), 1)])
    return sorted(rows)


def _lib_checks() -> dict:
    import numpy as np

    out = {}

    def check(name, fn):
        try:
            out[name] = fn()
        except Exception as e:  # noqa: BLE001
            out[name] = f"FAIL {type(e).__name__}: {e}"

    def opencv():
        import cv2
        a = np.zeros((40, 40, 3), np.uint8)
        cv2.circle(a, (20, 20), 9, (255, 255, 255), -1)
        ok, buf = cv2.imencode(".png", a)
        b = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        return f"ok {int(b.sum())}"

    def skimage_():
        from skimage.feature import peak_local_max
        from skimage.segmentation import watershed
        a = np.zeros((40, 40), np.float32)
        a[10, 10] = 2.0
        a[30, 30] = 1.0
        pk = peak_local_max(a, min_distance=3, labels=np.ones((40, 40), np.int32), threshold_abs=0.5,
                            exclude_border=False)
        m = np.zeros((40, 40), np.int32)
        m[10, 10] = 1
        m[30, 30] = 2
        w = watershed(-a, markers=m, mask=np.ones((40, 40), bool))
        return f"ok peaks={len(pk)} labels={sorted(np.unique(w).tolist())}"

    def scipy_():
        from scipy import ndimage
        m = np.zeros((10, 10), np.int32)
        m[1:3, 1:3] = 1
        m[5:9, 5:9] = 2
        return f"ok {len(ndimage.find_objects(m))}"

    def split_touching():
        import cv2
        from core import multi_design as md
        mask = np.zeros((220, 340), np.uint8)
        cv2.circle(mask, (110, 110), 70, 255, -1)
        cv2.circle(mask, (230, 110), 70, 255, -1)
        markers, _origin = md._split_touching_blobs(mask, 100)
        parts = len(set(np.unique(markers).tolist()) - {0})
        return f"ok available={md._PEAK_LOCAL_MAX_AVAILABLE} parts={parts}"

    def pillow():
        import io
        from PIL import Image
        Image.init()
        im = Image.new("RGBA", (30, 20), (255, 0, 0, 128))
        sizes = []
        for fmt in ("PNG", "JPEG"):
            buf = io.BytesIO()
            (im.convert("RGB") if fmt == "JPEG" else im).save(buf, fmt)
            buf.seek(0)
            sizes.append(Image.open(buf).size)
        return f"ok {sizes}"

    def pymupdf():
        import fitz
        doc = fitz.open()
        page = doc.new_page(width=100, height=80)
        page.draw_rect(fitz.Rect(10, 10, 50, 50), color=(0, 0, 0))
        d2 = fitz.open("pdf", doc.tobytes())
        pix = d2[0].get_pixmap()
        return f"ok {pix.width}x{pix.height}"

    def shapely_():
        from shapely.geometry import box
        from shapely.ops import unary_union
        return f"ok {unary_union([box(0, 0, 2, 2), box(1, 1, 3, 3)]).area}"

    def crypto():
        from cryptography.hazmat.primitives.asymmetric import ed25519
        k = ed25519.Ed25519PrivateKey.generate()
        sig = k.sign(b"cutline")
        k.public_key().verify(sig, b"cutline")
        from core import license_client  # noqa: F401  (라이선스 확인 모듈이 그대로 불러와지는지)
        return "ok"

    def svg():
        import cairosvg  # noqa: F401  (SVG 원본 열기 -- 소스와 설치본이 같은지만 본다)
        return "ok"

    check("opencv", opencv)
    check("skimage", skimage_)
    check("scipy", scipy_)
    check("split_touching", split_touching)
    check("pillow", pillow)
    check("pymupdf", pymupdf)
    check("shapely", shapely_)
    check("crypto", crypto)
    check("svg", svg)
    return out


def make_test_sheet(path: str) -> None:
    """코드로 그린 시험용 시트: 흰 바탕에 여러 색 도형, 서로 붙은 원 두 개(쪼개기), 고리, 흰 셔츠 인물."""
    import cv2
    import numpy as np
    img = np.full((900, 1200, 3), 255, np.uint8)
    cv2.circle(img, (180, 180), 110, (60, 120, 220), -1)
    cv2.circle(img, (330, 180), 110, (60, 120, 220), -1)          # 붙은 원 두 개
    cv2.rectangle(img, (560, 80), (760, 300), (40, 160, 60), -1)
    cv2.circle(img, (980, 190), 110, (30, 30, 200), 18)           # 고리
    cv2.ellipse(img, (220, 640), (150, 90), 20, 0, 360, (200, 90, 160), -1)
    cv2.rectangle(img, (560, 520), (900, 820), (221, 238, 249), -1)  # 크림색 칸
    cv2.circle(img, (730, 600), 60, (90, 165, 230), -1)           # 머리
    cv2.rectangle(img, (680, 660), (780, 740), (255, 255, 255), -1)  # 흰 셔츠
    cv2.rectangle(img, (685, 740), (775, 800), (80, 130, 50), -1)    # 치마
    cv2.putText(img, "CUT 3", (950, 700), cv2.FONT_HERSHEY_SIMPLEX, 2.0, (0, 0, 0), 5)
    ok, buf = cv2.imencode(".png", img)
    with open(path, "wb") as f:
        f.write(buf.tobytes())


def _image_summary(path: str) -> dict:
    from PIL import Image
    from core import multi_design as md
    fails = {"peak": 0, "watershed": 0}
    calls = {"peak": 0, "watershed": 0}
    orig_peak, orig_ws = md._peak_local_max, md._ski_watershed

    def wrap(fn, key):
        if fn is None:
            return None

        def inner(*a, **k):
            calls[key] += 1
            try:
                return fn(*a, **k)
            except Exception:
                fails[key] += 1
                raise
        return inner

    md._peak_local_max, md._ski_watershed = wrap(orig_peak, "peak"), wrap(orig_ws, "watershed")
    t = time.time()
    try:
        with Image.open(path) as im:
            w, h = im.size
        boxes = [list(map(int, b)) for b in md.detect_design_bboxes_px(path)]
        flood = md.detect_elements_by_background_flood_px(path, (0, 0, w, h))
        flood_rows = _poly_rows(flood)
        return {
            "size": [w, h],
            "boxes": len(boxes), "boxes_fp": _fp(boxes),
            "flood": len(flood_rows), "flood_fp": _fp(flood_rows),
            "lib_calls": calls, "lib_failures": fails,
            "seconds": round(time.time() - t, 1),
        }
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-1500:]}
    finally:
        md._peak_local_max, md._ski_watershed = orig_peak, orig_ws


def run(images=()) -> dict:
    res = {"frozen": bool(getattr(sys, "frozen", False)), "python": sys.version.split()[0]}
    res["libs"] = _lib_checks()
    tmp = os.path.join(tempfile.gettempdir(), f"cutline_selfcheck_{os.getpid()}.png")
    try:
        make_test_sheet(tmp)
        res["test_sheet"] = _image_summary(tmp)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    res["images"] = [_image_summary(p) for p in images]
    res["modules"] = sorted(k for k in sys.modules if k.split(".")[0] in ("scipy", "skimage", "PIL", "cv2"))
    return res


def comparable(res: dict) -> dict:
    """소스와 설치본에서 같아야 하는 부분만(시간·파이썬 실행 위치 등은 뺌)."""
    def strip(s):
        return {k: v for k, v in (s or {}).items() if k not in ("seconds", "trace")}
    libs = {k: v for k, v in (res.get("libs") or {}).items() if k != "svg"}  # SVG는 PC의 cairo 설치에 따라 다를 수 있어 참고만
    return {"libs": libs, "test_sheet": strip(res.get("test_sheet")),
            "images": [strip(s) for s in res.get("images", [])]}


def problems(res: dict) -> list:
    """설치본 결과에서 바로 실패로 볼 것들."""
    out = [f"{k}: {res['libs'].get(k)}" for k in REQUIRED_LIBS if not str(res["libs"].get(k, "")).startswith("ok")]
    if "available=True" not in str(res["libs"].get("split_touching")):
        out.append("도안 쪼개기(scikit-image)를 쓸 수 없음")
    for i, s in enumerate([res.get("test_sheet")] + list(res.get("images", []))):
        if not s or "error" in s:
            out.append(f"그림 {i}: {s and s.get('error')}")
        elif any(s["lib_failures"].values()):
            out.append(f"그림 {i}: 라이브러리 실패 {s['lib_failures']}")
    return out


def run_cli(args) -> int:
    if not args:
        return 2
    out_path, images = args[0], list(args[1:])
    try:
        res = run(images)
    except Exception:  # noqa: BLE001
        res = {"fatal": traceback.format_exc()}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    return 0 if "fatal" not in res else 1


if __name__ == "__main__":
    sys.exit(run_cli(sys.argv[1:]))
