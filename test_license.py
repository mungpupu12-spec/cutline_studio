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


def admin_issue(license_type, seats=1, name="", email=""):
    r = requests.post(
        f"{ADMIN_URL}/admin/issue",
        json={"license_type": license_type, "owner_name": name, "owner_email": email, "seat_limit": seats},
        headers={"X-Admin-Key": ADMIN_KEY},
    )
    r.raise_for_status()
    return r.json()["license_key"]


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


print("### 1) Personal license: activate from 2 different devices -- both should succeed (soft, logged only)")
personal_key = admin_issue("personal", seats=1, name="개인 김철수", email="kim@example.com")
r1 = full("laptop-A", lc.activate, personal_key)
assert r1.ok, r1
r2 = full("laptop-B", lc.activate, personal_key)
assert r2.ok, f"personal 2nd device should NOT be blocked, got: {r2}"
print("   OK:", r1.state, r2.state)

print("### 2) Enterprise license, seats=2: 2 devices OK, 3rd device triggers full lock")
ent_key = admin_issue("enterprise", seats=2, name="ACME", email="acme@example.com")
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
chk_key = admin_issue("personal", seats=1, name="체크섬 테스트", email="chk@example.com")
assert lc._looks_like_valid_key(chk_key), f"freshly issued key should pass local format check: {chk_key}"
flipped_char = "A" if chk_key[-2] != "A" else "B"
typo_key = chk_key[:-2] + flipped_char + chk_key[-1]
assert not lc._looks_like_valid_key(typo_key), f"key with a flipped character should fail local format check: {typo_key}"
# and activate() should reject the typo'd key WITHOUT ever reaching the network
# (no server round trip needed to know a checksum is wrong)
typo_result = full("typo-device", lc.activate, typo_key)
assert not typo_result.ok and typo_result.state == "invalid_key", typo_result
print("   OK: valid key passes, single-character typo caught locally as invalid_key")

print()
print("ALL LICENSE TESTS PASSED")
