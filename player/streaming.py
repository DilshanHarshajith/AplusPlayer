"""
Pure HLS playlist processing for a prepared PlaybackSession: deciding
whether a fetched body is an (encrypted) playlist and, if so, decrypting
it and rewriting its URLs to point at a given origin.

No Flask, no HTTP handling here — webapp/proxy.py does the actual
fetching from the CDN and wraps these as HTTP responses.
"""
import re

from . import config
from .crypto import decrypt_hex_payload


def rewrite_manifest(text: str, sess, origin: str) -> str:
    """Point CDN URLs back at `origin`; normalize EXT-X-KEY URIs."""
    if not sess.segments_encrypted:
        text = re.sub(r"^#EXT-X-KEY:.*\n?", "", text, flags=re.M)
    text = re.sub(
        r'(URI=")([^"]+)(")',
        lambda m: (m.group(1) + origin + "/api/proxy/video.key" + m.group(3)
                   if not m.group(2).startswith(origin) else m.group(0)),
        text)
    text = re.sub(
        r"https?://\S+",
        lambda m: (origin + m.group(0)[len(sess.base_url):]
                   if m.group(0).startswith(sess.base_url) else m.group(0)),
        text)
    return text


def decrypt_if_playlist(body_text: str, sess):
    """If `body_text` is a hex(AES(...)) playlist payload, decrypt it.
    Returns decrypted text, or None if it isn't a playlist at all."""
    if re.fullmatch(r"[0-9a-fA-F]+", body_text) and len(body_text) % 2 == 0:
        dec = decrypt_hex_payload(body_text, sess.playback_hash, config.KEY_RES)
        return dec if dec and dec.lstrip().startswith("#EXTM3U") else None
    if body_text.lstrip().startswith("#EXTM3U"):
        return body_text
    return None
