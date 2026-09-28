"""
CutLine Studio - license client.

This module ships INSIDE the built .exe (it has to -- the license check has
to run on the customer's machine). Because of that, it deliberately holds
no secret that would let someone forge a "valid" license: it only ever
holds the SERVER'S PUBLIC KEY, which is safe to embed (a public key can
verify signatures but cannot create new ones). All of the actual
enforcement logic (who is locked, how many seats a key has, issuing
reactivation codes) lives server-side, in the separate private repository
-- see cutline_license/README.md.

Honest limitation: no client-side check is un-crackable. A sufficiently
determined person can patch a compiled .exe to always report "valid". What
this system actually guarantees is: (1) every activation attempt is logged
server-side with a device fingerprint, IP, and timestamp -- so misuse is
traceable even if a particular install is later tampered with, and (2) an
enterprise license that legitimately checks in from more than its seat
limit gets the whole license locked centrally, which a normal (non-cracked)
customer install cannot work around. Treat this as a real deterrent and an
evidence trail for enforcement, not as unbreakable DRM.
"""

import os
import sys
import json
import base64
import socket
import hashlib
import platform
import datetime

try:
    import requests
except ImportError:
    requests = None

from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.exceptions import InvalidSignature

# ---------------------------------------------------------------- config

LICENSE_SERVER_URL = os.environ.get("CUTLINE_LICENSE_SERVER_URL", "https://cutline-license.onrender.com")

# Real production signing key's public half (2026-08-27) -- generated via
# cutline_license/server/generate_keys.py, matching the private key that's
# set as the SIGNING_KEY_PEM environment variable on the deployed server.
SERVER_PUBLIC_KEY_B64 = "HkTJSj3Ibu6rrospxO16GElfLTgSVrsCNIIkc5jPZs0="

APP_VENDOR_DIR_NAME = "CutLineStudio"
CACHE_FILENAME = "license_cache.json"
REQUEST_TIMEOUT_SEC = 8
# 2026-09-07: Render 무료/저가 플랜은 한동안 트래픽이 없으면 서버가 잠들고,
# 다음 요청이 와야 다시 깨어나는 콜드 스타트에 수십 초가 걸릴 수 있음 --
# 첫 시도(REQUEST_TIMEOUT_SEC)가 실패했을 때 이 값으로 한 번 더 참고
# 기다려본다(_post_with_wake_retry 참고).
COLD_START_TIMEOUT_SEC = 60

# Mirrors ALPHABET/_checksum_char in cutline_license/server/keygen.py -- kept
# here so a mistyped key can be caught locally, instantly, without a network
# round trip. This is a courtesy check only: the server is always the source
# of truth for whether a key was actually issued, so passing this check does
# not guarantee the key exists, and this never blocks anything the server
# itself wouldn't also reject. Keep in sync with keygen.py if that algorithm
# ever changes.
_KEY_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def _key_checksum_char(body14: str) -> str:
    total = sum((i + 1) * _KEY_ALPHABET.index(c) for i, c in enumerate(body14) if c in _KEY_ALPHABET)
    return _KEY_ALPHABET[total % len(_KEY_ALPHABET)]


def _looks_like_valid_key(license_key: str) -> bool:
    key = license_key.strip().upper()
    parts = key.split("-")
    if len(parts) != 5 or parts[0] != "CLS" or parts[1] not in ("PERS", "ENT"):
        return False
    body = "".join(parts[2:])
    if len(body) != 15 or any(c not in _KEY_ALPHABET for c in body):
        return False
    return _key_checksum_char(body[:14]) == body[14]


# ------------------------------------------------------------- fingerprint

def _windows_machine_guid():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as k:
            value, _ = winreg.QueryValueEx(k, "MachineGuid")
            return value
    except Exception:
        return None


def get_device_fingerprint() -> str:
    """A stable-per-machine identifier. Prefers the Windows MachineGuid
    (survives username changes, reinstalling this app, etc.); falls back to
    a MAC+hostname hash on other platforms (used for dev/testing)."""
    raw = None
    if platform.system() == "Windows":
        raw = _windows_machine_guid()
    if not raw:
        import uuid
        raw = f"{uuid.getnode()}:{platform.node()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def get_device_name() -> str:
    try:
        return platform.node() or socket.gethostname()
    except Exception:
        return "unknown-device"


# ------------------------------------------------------------------ cache

def _cache_dir():
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.path.expanduser("~/.config")
    d = os.path.join(base, APP_VENDOR_DIR_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def _cache_path():
    return os.path.join(_cache_dir(), CACHE_FILENAME)


def _save_cache(license_key: str, token: str):
    with open(_cache_path(), "w", encoding="utf-8") as f:
        json.dump({"license_key": license_key, "token": token}, f)


def _load_cache():
    try:
        with open(_cache_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def get_cached_license_key():
    """Best-effort peek at the last-entered license key, for prefilling a
    UI field. Returns "" if there is no cache yet."""
    cache = _load_cache()
    return (cache or {}).get("license_key", "") or ""


# ----------------------------------------------------------- verification

def _verify_token(token: str):
    """Returns the decoded payload dict if the signature is valid, else None."""
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload_bytes = base64.urlsafe_b64decode(payload_b64.encode("ascii"))
        signature = base64.urlsafe_b64decode(sig_b64.encode("ascii"))
        pub_key = ed25519.Ed25519PublicKey.from_public_bytes(
            base64.b64decode(SERVER_PUBLIC_KEY_B64)
        )
        pub_key.verify(signature, payload_bytes)
        return json.loads(payload_bytes.decode("utf-8"))
    except (InvalidSignature, ValueError, KeyError):
        return None


def _parse_iso(ts: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))


# -------------------------------------------------------------- high level

class LicenseResult:
    def __init__(self, ok, state, message="", payload=None):
        self.ok = ok
        self.state = state  # "valid" | "needs_activation" | "locked" | "network_error" | "invalid_key" |
        # "expired_offline" (오프라인 유예기간 초과) | "expired" (2026-09-12/56차: 라이선스 자체의
        # 유효기간이 지남 -- 서버가 판단, 오프라인 여부와 무관) | "seat_limit_exceeded" (2026-09-12/56차:
        # 이 기기가 접속해 허용 대수를 넘겨 전체 잠김) -- 뒤 두 가지는 서버가 보낸 reason을 그대로
        # 통과시키는 기존 코드 경로(activate/check_license의 data.get("reason", ...))로 이미 처리됨
        self.message = message
        self.payload = payload or {}

    def __repr__(self):
        return f"LicenseResult(ok={self.ok}, state={self.state!r}, message={self.message!r})"


def _post(path, json_body, timeout=REQUEST_TIMEOUT_SEC):
    if requests is None:
        return None
    try:
        resp = requests.post(f"{LICENSE_SERVER_URL}{path}", json=json_body, timeout=timeout)
        return resp
    except Exception:
        return None


def _post_with_wake_retry(path, json_body):
    """2026-09-07 피드백 대응: "라이선스 서버에 연결할 수 없고, 저장된 인증
    정보도 없습니다"가 첫 활성화 시도에서 떴음 -- 라이선스 서버(cutline-
    license.onrender.com)가 Render 무료/저가 플랜에 올라가 있어서, 한동안
    아무 요청이 없으면 서버가 잠들고(cold sleep) 다음 요청이 와야 그제서야
    다시 깨어나는데(cold start), 이 "깨어나는" 데 보통 수십 초가 걸릴 수
    있다. 예전엔 REQUEST_TIMEOUT_SEC(6초) 안에 응답이 없으면 곧바로
    "network_error"로 포기해버려서, 서버가 막 깨어나는 중인 흔한 상황에서도
    사용자가 직접 "활성화"를 여러 번 눌러야 했다. 이제 앱이 먼저 짧게
    시도해보고(서버가 이미 깨어있는 보통의 경우 빠르게 응답), 그게 실패하면
    한 번 더 훨씬 넉넉한 시간(COLD_START_TIMEOUT_SEC)을 주고 재시도한다 --
    진짜 인터넷 연결 자체가 끊긴 경우는 재시도도 어차피 실패하므로 손해가
    없고, 서버가 자고 있던 경우는 이 재시도로 대부분 성공한다."""
    resp = _post(path, json_body, timeout=REQUEST_TIMEOUT_SEC)
    if resp is not None:
        return resp
    return _post(path, json_body, timeout=COLD_START_TIMEOUT_SEC)


def activate(license_key: str) -> LicenseResult:
    """Call this when the user types in a license key for the first time
    (or to refresh an existing one). Always tries the live server."""
    if not _looks_like_valid_key(license_key):
        return LicenseResult(
            False, "invalid_key", "라이선스 키 형식이 올바르지 않습니다. 오타가 없는지 확인해주세요."
        )
    fingerprint = get_device_fingerprint()
    resp = _post_with_wake_retry(
        "/activate",
        {"license_key": license_key.strip(), "device_fingerprint": fingerprint, "device_name": get_device_name()},
    )
    if resp is None:
        return LicenseResult(False, "network_error", "라이선스 서버에 연결할 수 없습니다. 인터넷 연결을 확인해주세요.")
    if resp.status_code == 404:
        return LicenseResult(False, "invalid_key", "존재하지 않는 라이선스 키입니다.")
    data = resp.json()
    if not data.get("ok"):
        reason = data.get("reason", "locked")
        return LicenseResult(False, reason, data.get("message", "라이선스가 잠겨 있습니다."))
    token = data["token"]
    _save_cache(license_key.strip(), token)
    payload = _verify_token(token)
    return LicenseResult(True, "valid", "", payload)


def reactivate(license_key: str, reactivation_code: str) -> LicenseResult:
    if not _looks_like_valid_key(license_key):
        return LicenseResult(
            False, "invalid_key", "라이선스 키 형식이 올바르지 않습니다. 오타가 없는지 확인해주세요."
        )
    fingerprint = get_device_fingerprint()
    resp = _post_with_wake_retry(
        "/reactivate",
        {
            "license_key": license_key.strip(),
            "reactivation_code": reactivation_code.strip(),
            "device_fingerprint": fingerprint,
            "device_name": get_device_name(),
        },
    )
    if resp is None:
        return LicenseResult(False, "network_error", "라이선스 서버에 연결할 수 없습니다. 인터넷 연결을 확인해주세요.")
    if resp.status_code == 403:
        return LicenseResult(False, "invalid_code", "재활성화 코드가 올바르지 않거나 이미 사용되었습니다.")
    if resp.status_code == 404:
        return LicenseResult(False, "invalid_key", "존재하지 않는 라이선스 키입니다.")
    data = resp.json()
    token = data["token"]
    _save_cache(license_key.strip(), token)
    payload = _verify_token(token)
    return LicenseResult(True, "valid", "", payload)


def report_error(error_type: str, message: str, traceback_str: str = "", context: str = "") -> bool:
    """2026-09-12(56차) 피드백("발생한 오류를 프로그램이 자동적으로 나한테
    전달하는 게 필요해"): 예외가 발생한 곳(주로 gui.app의 각 except 블록)에서
    호출한다. 완전히 베스트에포트 -- 네트워크가 없거나, 아직 라이선스를
    입력하지 않은 상태거나, 서버가 응답하지 않아도 절대 예외를 던지지 않고
    조용히 False만 돌려준다(오류 처리 도중에 이 리포팅 자체가 또 다른 오류를
    일으켜서는 안 됨). 반환값은 테스트/디버깅 편의용일 뿐, 호출하는 쪽이
    이걸로 뭔가를 판단할 필요는 없다.

    라이선스 키가 아직 없으면(체험 중 활성화 전 등) 서버가 어차피 리포트를
    받아줄 근거(발급된 실제 키)가 없으므로 시도 자체를 건너뛴다."""
    try:
        cache = _load_cache()
        license_key = (cache or {}).get("license_key", "")
        if not license_key:
            return False
        resp = _post(
            "/report_error",
            {
                "license_key": license_key,
                "device_fingerprint": get_device_fingerprint(),
                "device_name": get_device_name(),
                "error_type": str(error_type or "")[:200],
                "message": str(message or "")[:2000],
                "traceback": str(traceback_str or "")[:8000],
                "context": str(context or "")[:500],
            },
        )
        return resp is not None and resp.status_code == 200
    except Exception:
        return False


def check_license() -> LicenseResult:
    """Call this on every app startup. Order of operations:
    1. Try a live check-in with the server (always wins if reachable --
       this is how a remote lock propagates to a device that's currently
       offline-but-then-comes-online).
    2. If the server is unreachable, fall back to the last cached, still
       signature-valid, still-not-expired token (short offline grace
       period, currently 3 days server-side) so the app still works on a
       plane/without wifi for a little while.
    3. If there's no usable cache either, the app must ask for a license
       key.

    Dev/test escape hatch: when running from SOURCE (never inside the
    built .exe -- `sys.frozen` is only ever True for a PyInstaller build)
    AND the CUTLINE_LICENSE_DEV_BYPASS env var is set, skip the check
    entirely. This exists purely so the headless regression-test suite can
    construct CutLineApp() without a live license server; it is a no-op
    for every real customer install because the frozen .exe ignores it
    unconditionally.
    """
    if not getattr(sys, "frozen", False) and os.environ.get("CUTLINE_LICENSE_DEV_BYPASS") == "1":
        return LicenseResult(True, "dev_bypass")

    cache = _load_cache()

    if cache and cache.get("license_key"):
        fingerprint = get_device_fingerprint()
        activate_body = {
            "license_key": cache["license_key"], "device_fingerprint": fingerprint, "device_name": get_device_name(),
        }
        resp = _post("/activate", activate_body)
        if resp is None:
            # 2026-09-07 피드백 대응: 서버가 응답하지 않을 때, 폴백할 수 있는
            # 유효한 오프라인 캐시가 이미 있으면 그걸로 빠르게 넘어가는 게
            # 낫다(Render 콜드 스타트일 때마다 앱 실행이 매번 수십 초씩
            # 늦어지면 안 됨). 폴백할 캐시가 없을 때만(이번에 실제로 겪은
            # "저장된 인증 정보도 없습니다" 상황) 서버가 깨어날 시간을 한 번
            # 더 넉넉히 주고 재시도한다.
            if _verify_token(cache.get("token", "")) is None:
                resp = _post("/activate", activate_body, timeout=COLD_START_TIMEOUT_SEC)
        if resp is not None:
            if resp.status_code == 404:
                return LicenseResult(False, "invalid_key", "라이선스 키를 다시 입력해주세요.")
            data = resp.json()
            if not data.get("ok"):
                return LicenseResult(False, data.get("reason", "locked"), data.get("message", "라이선스가 잠겨 있습니다."))
            token = data["token"]
            _save_cache(cache["license_key"], token)
            payload = _verify_token(token)
            return LicenseResult(True, "valid", "", payload)

        # Server unreachable -- fall back to cached token.
        payload = _verify_token(cache.get("token", ""))
        if payload is None:
            return LicenseResult(False, "network_error", "라이선스 서버에 연결할 수 없고, 저장된 인증 정보도 없습니다.")
        if payload.get("status") == "locked":
            return LicenseResult(False, "locked", "이 라이선스는 잠겨 있습니다.")
        expires_at = _parse_iso(payload["expires_at"])
        now = datetime.datetime.now(datetime.timezone.utc)
        if now > expires_at:
            return LicenseResult(
                False,
                "expired_offline",
                "오프라인 상태로 사용할 수 있는 기간이 지났습니다. 인터넷에 연결한 후 다시 실행해주세요.",
            )
        return LicenseResult(True, "valid", "", payload)

    return LicenseResult(False, "needs_activation", "라이선스 키를 입력해주세요.")
