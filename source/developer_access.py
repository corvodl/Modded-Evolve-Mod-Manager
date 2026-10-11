"""Local UI access gate; not an authorization boundary against app modification.

Only a salted, slow password verifier is distributed. Never persist an unlock
flag, log entered codes, or include a plaintext credential in source/tests.
"""
import hashlib
import hmac

_SALT = bytes.fromhex('c2e98ee31912f5b5f55a7ffd524e9010f63fba27d904c44e')
_VERIFIER = bytes.fromhex('3219a86118938f630ec4dacc5376a8c4d2927394bcaf1c2e44394fe550523daf')


def verify_code(code):
    if not isinstance(code, str) or not code or len(code) > 256:
        return False
    candidate = hashlib.pbkdf2_hmac('sha256', code.encode('utf-8'), _SALT, 240000)
    return hmac.compare_digest(candidate, _VERIFIER)
