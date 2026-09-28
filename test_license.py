"""
Standalone regression test for core/license_client.py against a LOCAL test
license server (see cutline_license/server -- run with:
    ADMIN_API_KEY=test-admin-key-123 uvicorn main:app --host 127.0.0.1 --port 8811
before running this script).

Simulates multiple "devices" by monkeypatching get_device_fingerprint /
get_device_name, since everything here runs on one sandbox machine.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("CUTLINE_LICENSE_SERVER_URL", "http://127.0.0.1:8811")

from core import license_client as lc

ADMIN_URL = "http://127.0.0.1:8811"
ADMIN_KEY = "test-admin-key-123"

import requests


def admin_issue(license_type, seats=1, name="", email="", duration_days=None):
    r = requests.post(
        f"{ADMIN_URL}/admin/issue",
        json={
            "license_type": license_type, "owner_name": name, "owner_email": email,
            "seat_limit": seats, "duration_days": duration_days,
        },
        headers={"X-Admin-Key": ADMIN_KEY},
    )
    r.raise_for_status()
    return r.json()


def admin_issue_key(*a, **kw):
    return admin_issue(*a, **kw)["license_key"]


def admin_extend(key, duration_days=None):
    params = {"license_key": key}
    if duration_days is not None:
        params["duration_days"] = duration_days
    r = requests.post(f"{ADMIN_URL}/admin/extend", params=params, headers={"X-Admin-Key": ADMIN_KEY})
    r.raise_for_status()
    return r.json()


def admin_violations(key=None):
    params = {"license_key": key} if key else {}
    r = requests.get(f"{ADMIN_URL}/admin/violations", params=params, headers={"X-Admin-Key": ADMIN_KEY})
    r.raise_for_status()
    return r.json()["violations"]


def admin_error_reports(key=None):
    params = {"license_key": key} if key else {}
    r = requests.get(f"{ADMIN_URL}/admin/error_reports", params=params, headers={"X-Admin-Key": ADMIN_KEY})
    r.raise_for_status()
    return r.json()["error_reports"]


def admin_reactivation_code(key):
    r = requests.post(f"{ADMIN_URL}/admin/reactivation_code", params={"license_key": key}, headers={"X-Admin-Key": ADMIN_KEY})
    r.raise_for_status()
    return r.json()["reactivation_code"]


def as_device(name, fn, *a, **kw):
    orig_fp = lc.get_device_fingerprint
    orig_name = lc.get_device_name
    lc.get_device_fingerprint = lambda: f"fake-fingerprint-{name}"
    lc.get_device_name = lambda: f"fake-device-{name}"
    try:
        return fn(*a, **kw)
    finally:
        lc.get_device_fingerprint = orig_fp
        lc.get_device_name = orig_name


def use_cache_for(name, fn, *a, **kw):
    """Point the cache file at a name-specific path so each fake device has
    its own cache, mirroring separate real machines."""
    orig_cache_path = lc._cache_path
    fake_path = f"/tmp/license_cache_{name}.json"
    lc._cache_path = lambda: fake_path
    try:
        return fn(*a, **kw)
    finally:
        lc._cache_path = orig_cache_path


def full(name, fn, *a, **kw):
    return as_device(name, lambda: use_cache_for(name, fn, *a, **kw))


print("### 1) Personal license (seat_limit=1, 2026-09-12/56차: 이제 enterprise와 같은 하드 강제): "
      "2번째 기기가 접속하면 전체 잠금 + violations 기록")
personal_key = admin_issue_key("personal", seats=1, name="개인 김철수", email="kim@example.com")
r1 = full("laptop-A", lc.activate, personal_key)
assert r1.ok, r1
r2 = full("laptop-B", lc.activate, personal_key)
assert not r2.ok and r2.state == "seat_limit_exceeded", f"personal 2nd device should now be BLOCKED, got: {r2}"
viol = admin_violations(personal_key)
assert len(viol) == 1, f"expected exactly 1 violation recorded, got: {viol}"
assert viol[0]["seat_limit"] == 1 and viol[0]["active_device_count"] == 2
print("   OK:", r1.state, r2.state, "-- violation logged:", viol[0]["device_name"])

print("### 2) Enterprise license, seats=2: 2 devices OK, 3rd device triggers full lock")
ent_key = admin_issue_key("enterprise", seats=2, name="ACME", email="acme@example.com")
e1 = full("ent-A", lc.activate, ent_key)
assert e1.ok, e1
e2 = full("ent-B", lc.activate, ent_key)
assert e2.ok, e2
e3 = full("ent-C", lc.activate, ent_key)
assert not e3.ok and e3.state == "seat_limit_exceeded", f"3rd device should be rejected+locked, got: {e3}"
print("   OK: 3rd device rejected ->", e3.state, "-", e3.message)

print("### 3) Because the WHOLE license locked, device A (already active) should now ALSO see 'locked' on next check-in")
e1_again = full("ent-A", lc.check_license)
assert not e1_again.ok and e1_again.state == "locked", f"expected locked for original device too, got: {e1_again}"
print("   OK:", e1_again.state, "-", e1_again.message)

print("### 4) Reactivation code unlocks it again")
code = admin_reactivation_code(ent_key)
re1 = full("ent-A", lc.reactivate, ent_key, code)
assert re1.ok, re1
print("   OK:", re1.state)

print("### 5) A used reactivation code cannot be reused")
re2 = full("ent-B", lc.reactivate, ent_key, code)
assert not re2.ok and re2.state == "invalid_code", f"expected invalid_code, got: {re2}"
print("   OK:", re2.state)

print("### 6) Unknown license key is rejected")
bad = full("random", lc.activate, "CLS-PERS-ZZZZZ-ZZZZZ-ZZZZZ")
assert not bad.ok and bad.state == "invalid_key", bad
print("   OK:", bad.state)

print("### 7) Signature verification rejects a tampered token")
import json, base64
payload = {"license_key": "x", "license_type": "personal", "device_fingerprint": "y", "status": "active",
           "issued_at": "2020-01-01T00:00:00Z", "expires_at": "2999-01-01T00:00:00Z"}
fake_payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
fake_sig_b64 = base64.urlsafe_b64encode(b"not-a-real-signature-not-a-real-signature").decode()
forged = f"{fake_payload_b64}.{fake_sig_b64}"
assert lc._verify_token(forged) is None, "forged token must NOT verify!"
print("   OK: forged token correctly rejected")

print("### 8) Checksum: a freshly-issued key passes local format check; a single flipped character fails it")
chk_key = admin_issue_key("personal", seats=1, name="체크섬 테스트", email="chk@example.com")
assert lc._looks_like_valid_key(chk_key), f"freshly issued key should pass local format check: {chk_key}"
flipped_char = "A" if chk_key[-2] != "A" else "B"
typo_key = chk_key[:-2] + flipped_char + chk_key[-1]
assert not lc._looks_like_valid_key(typo_key), f"key with a flipped character should fail local format check: {typo_key}"
# and activate() should reject the typo'd key WITHOUT ever reaching the network
# (no server round trip needed to know a checksum is wrong)
typo_result = full("typo-device", lc.activate, typo_key)
assert not typo_result.ok and typo_result.state == "invalid_key", typo_result
print("   OK: valid key passes, single-character typo caught locally as invalid_key")

print("### 9) 2026-09-12(56차): duration_days -- a 1-day license issued 'in the past' (via extend to a")
print("    negative offset isn't allowed, so we issue normal and then move its expiry into the past directly)")
week_key = admin_issue_key("personal", seats=1, name="베타테스터", duration_days=14)
info = requests.get(f"{ADMIN_URL}/admin/licenses", headers={"X-Admin-Key": ADMIN_KEY})
info.raise_for_status()
lic_row = next(l for l in info.json()["licenses"] if l["key"] == week_key)
assert lic_row["expires_at"], f"issued license should have an expires_at set: {lic_row}"
still_valid = full("beta-device", lc.activate, week_key)
assert still_valid.ok, f"freshly issued 2-week license should activate fine well before its deadline: {still_valid}"
print(f"   OK: 2주짜리 라이선스 발급, expires_at={lic_row['expires_at']}, 지금 활성화 정상")

print("### 10) A license already past its expiry date is rejected with reason='expired'")
# Simplest reliable way to simulate "already expired" without waiting real days:
# issue a key, then flip its expires_at directly through the SERVER's own db
# module (same sqlite file the running test server reads/writes -- set
# CUTLINE_LICENSE_SERVER_DIR if that checkout lives somewhere other than the
# default path below).
expired_key = admin_issue_key("personal", seats=1, name="만료 테스트", duration_days=1)
os.environ.setdefault("DATA_DIR", os.environ.get("CUTLINE_LICENSE_TEST_DATA_DIR", ""))
sys.path.insert(0, os.environ.get("CUTLINE_LICENSE_SERVER_DIR", "/root/cutline_license/server"))
import db as license_db  # the SERVER's db module (separate from anything in cutline_studio) --
# must point at the SAME DATA_DIR the running test server was started with,
# or this edits a different (empty) database than the one /activate reads.
license_db.set_expires_at(expired_key, "2000-01-01T00:00:00Z")
expired_result = full("expired-device", lc.activate, expired_key)
assert not expired_result.ok and expired_result.state == "expired", f"expected 'expired', got: {expired_result}"
print("   OK:", expired_result.state, "-", expired_result.message)

print("### 11) /admin/extend can push a deadline back out, or remove it entirely")
ext1 = admin_extend(expired_key, duration_days=30)
assert ext1["expires_at"], ext1
revived = full("expired-device", lc.activate, expired_key)
assert revived.ok, f"license extended 30 days out should activate again: {revived}"
ext2 = admin_extend(expired_key)  # no duration_days -> permanent
assert ext2["expires_at"] is None, ext2
print("   OK: extend(+30일) unlocks it again, extend() with no args clears expiry back to permanent")

print("### 12) Client-side error reporting: report_error() posts to the server and shows up in admin/error_reports")
report_key = admin_issue_key("personal", seats=1, name="오류리포트 테스트")
full("err-device", lc.activate, report_key)  # give it a cached license_key to report against
ok = full("err-device", lc.report_error, "ValueError", "테스트용 가짜 오류", "Traceback (most recent call last):\n  ...", "자동 인식 중")
assert ok is True, f"report_error should return True on success, got: {ok}"
reports = admin_error_reports(report_key)
assert len(reports) == 1, f"expected exactly 1 error report, got: {reports}"
assert reports[0]["error_type"] == "ValueError" and "가짜 오류" in reports[0]["message"]
print("   OK: 오류 리포트가 서버에 정상 기록됨:", reports[0]["error_type"], "-", reports[0]["message"])

print("### 13) report_error() never raises even when there's no cached license key / network is unreachable")
import core.license_client as lc_mod
orig_url = lc_mod.LICENSE_SERVER_URL
lc_mod.LICENSE_SERVER_URL = "http://127.0.0.1:1"  # nothing listens here
try:
    result = full("unreachable-device", lc.report_error, "RuntimeError", "네트워크 없음 테스트")
    assert result is False, f"expected False (best-effort failure), got: {result}"
finally:
    lc_mod.LICENSE_SERVER_URL = orig_url
print("   OK: 서버에 연결할 수 없어도 예외 없이 조용히 실패(False)만 반환")

print()
print("ALL LICENSE TESTS PASSED")
