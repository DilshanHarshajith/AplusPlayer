"""
PlaybackSession: turns a lesson_id into a verified base_url + video AES key,
probing a real segment to confirm the key interpretation before playback
starts.
"""
import base64
import binascii
import re
import sys

import requests

from . import config
from .api import AplusAPI
from .crypto import aes_cbc_decrypt, decrypt_hex_payload, decrypt_playback_key, \
    unwrap_lesson_content

# Shared upstream HTTP session (CDN requests), separate from the GQL session.
_upstream = requests.Session()
_upstream.headers["User-Agent"] = config.UA


class PlaybackSession:
    """Resolve and verify the stream details for one lesson."""

    def __init__(self, api: AplusAPI, lesson_id: str):
        """Initialize a session for the given API client and lesson."""
        self.api = api
        self.lesson_id = lesson_id
        self.base_url = ""
        self.video_access_token = ""
        self.video_aes_key = b""
        self.segments_encrypted = True
        self.playback_hash = ""
        self.stream_url = ""

    def prepare(self):
        """Resolve lesson metadata and verify the video encryption key."""
        det = self.api.lesson_details(self.lesson_id)
        lp = det.get("link_params")
        if not lp:
            raise RuntimeError("no link_params for this lesson")
        lc = self.api.lesson_content(lp)
        crypted, ivs = lc["key"], lc["hash"]
        meta = unwrap_lesson_content(crypted, ivs, config.KEY_INFO)
        if not meta:
            raise RuntimeError("failed to decrypt lesson content")

        pbk = decrypt_playback_key(meta["playback_key"], meta["playback_sec"])
        self.playback_hash = meta["playback_hash"]
        self.video_access_token = meta.get("pass", "")
        self.stream_url = meta.get("stream_url") or ""
        self.base_url = meta["playback"].split("/playback")[0]

        # Stage 3 (VERIFIED against live CDN data): video AES-128 key =
        # first 16 UTF-8 bytes of the decrypted vk string, IV = 0.
        # (Full segment decrypts to 100% valid MPEG-TS packets with this.)
        vk = decrypt_hex_payload(pbk, self.playback_hash, config.KEY_RES)
        if not vk:
            raise RuntimeError("failed to decrypt video key")

        # Probe: verify against a real segment; also detects unencrypted media.
        self.video_aes_key = self._probe_video_key(vk)

    def fetch_upstream(self, path: str) -> bytes:
        """Fetch an authenticated resource relative to base_url — segments,
        playlists, anything under the lesson's CDN base URL. Public: used
        both internally (probing) and by the download module, which needs
        every segment rather than just the one the probe validates."""
        r = _upstream.get(
            self.base_url + path,
            headers={"Authorization": f"Bearer {self.video_access_token}"},
            timeout=120)
        if r.status_code != 200:
            raise RuntimeError(f"upstream {r.status_code} for {path}")
        return r.content

    def _fetch_and_decrypt_playlist(self, path: str) -> str:
        """Fetch a playlist at `path` and return its plaintext #EXTM3U text.

        Playlists are served either as plaintext (rare) or as
        hex(AES-CBC(base64(m3u8))) using KEY_RES — the same convention
        decrypt_hex_payload expects elsewhere in the client.
        """
        raw = self.fetch_upstream(path)
        body = raw.decode("utf-8", errors="replace").strip()
        if re.fullmatch(r"[0-9a-fA-F]+", body) and len(body) % 2 == 0:
            return decrypt_hex_payload(body, self.playback_hash, config.KEY_RES) or ""
        if body.startswith("#EXTM3U"):
            return body
        return ""

    def list_variant_segments(self) -> tuple[list[str], bytes]:
        """
        Fetch the master + first variant playlist and return every segment
        path (relative to base_url) plus the CBC IV to decrypt them with.

        Mirrors the playlist parsing `_probe_video_key` uses to validate
        just the first segment, but returns the full ordered list — used
        by the download module to pull an entire lesson.
        """
        master_text = self._fetch_and_decrypt_playlist("/playback")
        variants = [l.strip() for l in master_text.splitlines()
                    if l.strip() and not l.startswith("#")]
        if not variants:
            raise RuntimeError("master playlist has no variants")

        variant_text = self._fetch_and_decrypt_playlist("/" + variants[0])
        seg_names = [l.strip() for l in variant_text.splitlines()
                     if l.strip() and not l.startswith("#")]
        if not seg_names:
            raise RuntimeError("variant playlist has no segments")

        iv_m = re.search(r"IV=0[xX]([0-9a-fA-F]+)", variant_text)
        iv = bytes.fromhex(iv_m.group(1)[-32:].rjust(32, "0")) if iv_m \
            else bytes(16)

        var_dir = variants[0].rsplit("/", 1)[0] if "/" in variants[0] else ""
        seg_paths = [f"/{var_dir}/{name}" if var_dir else f"/{name}"
                     for name in seg_names]
        return seg_paths, iv

    @staticmethod
    def _looks_like_ts(data: bytes) -> bool:
        """MPEG-TS: 0x47 sync bytes every 188 bytes."""
        if len(data) < 376:
            return False
        return data[0] == 0x47 and data[188] == 0x47 \
            and data[376] == 0x47

    @staticmethod
    def _looks_like_mp4(data: bytes) -> bool:
        return len(data) > 12 and (
            b"ftyp" in data[:64] or b"styp" in data[:64])

    def _probe_video_key(self, vk: str) -> bytes:
        """
        Fetch master playlist + first variant + first segment; try key
        interpretations until one decrypts to a valid media container.
        Also detects unencrypted media.
        """
        try:
            enc_master = self.fetch_upstream("/playback")
        except (requests.RequestException, RuntimeError) as exc:
            print(f"[!] probe: cannot fetch /playback ({exc}); "
                  "falling back to utf8_16 key", file=sys.stderr)
            return vk.encode()[:16]

        body = enc_master.decode("utf-8", errors="replace").strip()
        if re.fullmatch(r"[0-9a-fA-F]+", body) and len(body) % 2 == 0:
            text = decrypt_hex_payload(body, self.playback_hash, config.KEY_RES) or ""
        elif body.startswith("#EXTM3U"):
            text = body
        else:
            text = ""

        # find first variant playlist (e.g. v0/playback)
        variants = [l.strip() for l in text.splitlines()
                    if l.strip() and not l.startswith("#")]
        print(f"[*] probe: master lists {len(variants)} variants",
              file=sys.stderr)
        if not variants:
            print("[!] probe: no variants; falling back to utf8_16 key",
                  file=sys.stderr)
            return vk.encode()[:16]

        try:
            enc_var = self.fetch_upstream("/" + variants[0])
        except (requests.RequestException, RuntimeError) as exc:
            print(f"[!] probe: variant fetch failed ({exc})", file=sys.stderr)
            return vk.encode()[:16]
        vbody = enc_var.decode("utf-8", errors="replace").strip()
        if re.fullmatch(r"[0-9a-fA-F]+", vbody) and len(vbody) % 2 == 0:
            vtext = decrypt_hex_payload(vbody, self.playback_hash,
                                        config.KEY_RES) or ""
        else:
            vtext = vbody

        seg_names = [l.strip() for l in vtext.splitlines()
                     if l.strip() and not l.startswith("#")]
        iv_m = re.search(r"IV=0[xX]([0-9a-fA-F]+)", vtext)
        iv = bytes.fromhex(iv_m.group(1)[-32:].rjust(32, "0")) if iv_m \
            else bytes(16)
        encrypted = "METHOD=AES-128" in vtext
        # segments are relative to the VARIANT directory
        var_dir = variants[0].rsplit("/", 1)[0] if "/" in variants[0] else ""
        seg_path = f"/{var_dir}/{seg_names[0]}" if var_dir \
            else f"/{seg_names[0]}"
        print(f"[*] probe: {len(seg_names)} segments, encrypted={encrypted}, "
              f"first={seg_path}", file=sys.stderr)

        if not seg_names:
            print("[!] probe: no segments found; falling back to utf8_16 key",
                  file=sys.stderr)
            return vk.encode()[:16]

        try:
            raw_seg = self.fetch_upstream(seg_path)
        except (requests.RequestException, RuntimeError) as exc:
            print(f"[!] probe: segment fetch failed ({exc})",
                  file=sys.stderr)
            return vk.encode()[:16]

        def valid(b):
            if len(b) < 376:
                return False
            if b[0] == 0x47 and b[188] == 0x47 and b[376] == 0x47:
                return True
            return b"ftyp" in b[:64] or b"styp" in b[:64]

        if not encrypted:
            if valid(raw_seg):
                print("[+] probe: segments are NOT encrypted at CDN level",
                      file=sys.stderr)
                self.segments_encrypted = False
                return b""
            print("[!] probe: unencrypted flag but segment unrecognized",
                  file=sys.stderr)

        candidates = [("utf8_16", vk.encode()[:16])]
        if len(vk.encode()) >= 32:
            candidates.append(("utf8_32", vk.encode()))
        if re.fullmatch(r"[0-9a-fA-F]{32}", vk):
            candidates.append(("hex", bytes.fromhex(vk)))
        try:
            b64 = base64.b64decode(vk, validate=True)
            if len(b64) == 16:
                candidates.append(("base64", b64))
        except (binascii.Error, ValueError):
            pass

        for label, key in candidates:
            if len(key) != 16:
                continue
            dec = aes_cbc_decrypt(key, iv, raw_seg)
            pad = dec[-1]
            if 1 <= pad <= 16 and dec[-pad:] == bytes([pad]) * pad:
                dec = dec[:-pad]
            if valid(dec):
                print(f"[+] probe: video key interpretation '{label}' "
                      "verified against real segment", file=sys.stderr)
                self.segments_encrypted = True
                return key

        print("[!] probe: no key interpretation produced valid media; "
              "serving segments as-is", file=sys.stderr)
        self.segments_encrypted = False
        return b""
