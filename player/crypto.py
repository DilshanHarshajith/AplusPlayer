"""
Replicates the app's decrypt chain locally (AES-256-CBC + byte-subtraction
OTP). Secrets are the constants embedded in @axoten/aplus-common-addon
(see config.KEY_INFO / config.KEY_RES).
"""
import base64
import binascii
import json
import sys

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def aes_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """Decrypt bytes using AES-CBC."""
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return dec.update(data) + dec.finalize()


def decrypt_hex_payload(hex_crypted: str, hex_iv: str, key_str: str):
    """App's decryptLessonContent/decryptVideoKey/decryptPlayback pattern."""
    if not hex_crypted or not hex_iv:
        return None
    try:
        key = key_str.encode("utf-8")
        iv = bytes.fromhex(hex_iv)
        raw = aes_cbc_decrypt(key, iv, bytes.fromhex(hex_crypted))
        pad = raw[-1]
        if 1 <= pad <= 16:
            raw = raw[:-pad]
        text = raw.decode("utf-8")
    except (binascii.Error, TypeError, ValueError) as exc:
        print(f"[crypto] decrypt failed: {exc}", file=sys.stderr)
        return None
    # inner layer is usually base64; tolerate payloads without it
    try:
        return base64.b64decode(text, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return text


def decrypt_playback_key(enc_key: list, otp: list) -> str:
    """Byte-subtraction OTP routine from VideoProvider.decryptPlaybackKey."""
    rounds = otp[-1] + otp[len(otp) // 2]
    k = 0
    buf = list(enc_key)
    for _ in range(rounds):
        for i, value in enumerate(buf):
            buf[i] = (value - otp[k % len(otp)]) & 0xFF
            k += 1
    return bytes(buf).decode("utf-8", errors="replace")


def unwrap_lesson_content(crypted: str, ivs: str, key_info: str):
    """Stage 1: lesson content payload -> playback metadata dict."""
    plaintext = decrypt_hex_payload(crypted, ivs, key_info)
    if not plaintext:
        return None
    return json.loads(plaintext)  # plaintext is a JSON string


# Alias kept for clarity: stage-1 output is a dict.
unwrap_lesson_content_dict = unwrap_lesson_content
