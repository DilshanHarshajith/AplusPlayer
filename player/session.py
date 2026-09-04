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


class PlaybackSessionData:
    """A serializable, worker-safe snapshot of a prepared PlaybackSession.

    Holds everything the HLS proxy and download paths need to talk to the
    CDN (base URL, per-lesson Bearer token, decrypted AES key, playlist
    hash) without the live AplusAPI / requests objects. It can be stored in
    the shared store (SQLite) and reconstructed in any gunicorn worker, so
    a lesson prepared in one worker can be served by another — that is what
    lets the app run behind multiple workers serving many users at once.

    All attributes are plain JSON-able values; the AES key is kept as bytes
    in memory but hex-encoded when serialized via to_dict/from_dict.
    """

    __slots__ = ("lesson_id", "base_url", "video_access_token",
                 "playback_hash", "video_aes_key", "segments_encrypted",
                 "stream_url")

    def __init__(self, lesson_id="", base_url="", video_access_token="",
                 playback_hash="", video_aes_key=b"", segments_encrypted=True,
                 stream_url=""):
        self.lesson_id = lesson_id
        self.base_url = base_url
        self.video_access_token = video_access_token
        self.playback_hash = playback_hash
        self.video_aes_key = video_aes_key
        self.segments_encrypted = segments_encrypted
        self.stream_url = stream_url

    @classmethod
    def from_session(cls, sess):
        """Snapshot a live, already-prepared PlaybackSession."""
        return cls(
            lesson_id=sess.lesson_id,
            base_url=sess.base_url,
            video_access_token=sess.video_access_token,
            playback_hash=sess.playback_hash,
            video_aes_key=sess.video_aes_key,
            segments_encrypted=sess.segments_encrypted,
            stream_url=sess.stream_url,
        )

    def to_dict(self) -> dict:
        """Return a JSON-safe dict for storage in the shared store."""
        return {
            "lesson_id": self.lesson_id,
            "base_url": self.base_url,
            "video_access_token": self.video_access_token,
            "playback_hash": self.playback_hash,
            "video_aes_key": self.video_aes_key.hex() if self.video_aes_key else None,
            "segments_encrypted": self.segments_encrypted,
            "stream_url": self.stream_url,
        }

    @classmethod
    def from_dict(cls, d) -> "PlaybackSessionData":
        """Rebuild a PlaybackSessionData from a to_dict() payload."""
        key = d.get("video_aes_key")
        return cls(
            lesson_id=d.get("lesson_id", ""),
            base_url=d.get("base_url", ""),
            video_access_token=d.get("video_access_token", ""),
            playback_hash=d.get("playback_hash", ""),
            video_aes_key=bytes.fromhex(key) if key else b"",
            segments_encrypted=d.get("segments_encrypted", True),
            stream_url=d.get("stream_url", ""),
        )

    def fetch_upstream(self, path: str) -> bytes:
        """Fetch an authenticated resource relative to base_url."""
        r = _upstream.get(
            self.base_url + path,
            headers={"Authorization": f"Bearer {self.video_access_token}"},
            timeout=120)
        if r.status_code != 200:
            raise RuntimeError(f"upstream {r.status_code} for {path}")
        return r.content

    def _fetch_and_decrypt_playlist(self, path: str) -> str:
        """Fetch a playlist and return its plaintext #EXTM3U text."""
        raw = self.fetch_upstream(path)
        body = raw.decode("utf-8", errors="replace").strip()
        if re.fullmatch(r"[0-9a-fA-F]+", body) and len(body) % 2 == 0:
            return decrypt_hex_payload(body, self.playback_hash, config.KEY_RES) or ""
        if body.startswith("#EXTM3U"):
            return body
        return ""

    def _resolution_to_quality_name(self, resolution: str) -> str:
        """Convert resolution string to standard quality name (e.g., '1920x1080' -> '1080p')."""
        if not resolution or resolution == "Unknown":
            return "Unknown"

        try:
            width, height = map(int, resolution.split('x'))
            return f"{height}p"
        except (ValueError, AttributeError):
            return "Unknown"

    def list_qualities(self) -> list[dict]:
        """Return available quality variants from the master playlist.

        Each variant is returned as a dict with 'id' and 'label' keys.
        """
        master_text = self._fetch_and_decrypt_playlist("/playback")
        lines = master_text.splitlines()

        qualities = []
        current_variant = None

        for line in lines:
            line = line.strip()
            if line.startswith("#EXT-X-STREAM-INF"):
                # Parse quality info from the stream inf line
                # Format: #EXT-X-STREAM-INF:BANDWIDTH=XXX,RESOLUTION=XXXxXXX
                bandwidth_match = re.search(r"BANDWIDTH=(\d+)", line)
                resolution_match = re.search(r"RESOLUTION=(\d+x\d+)", line)

                bandwidth = int(bandwidth_match.group(1)) if bandwidth_match else 0
                resolution = resolution_match.group(1) if resolution_match else "Unknown"

                current_variant = {
                    "bandwidth": bandwidth,
                    "resolution": resolution
                }
            elif line and not line.startswith("#") and current_variant:
                # This is the variant URL line
                variant_id = line
                quality_name = self._resolution_to_quality_name(current_variant['resolution'])
                label = f"{current_variant['resolution']} ({quality_name})"
                qualities.append({
                    "id": variant_id,
                    "label": label,
                    "bandwidth": current_variant["bandwidth"],
                    "resolution": current_variant["resolution"]
                })
                current_variant = None

        # Sort by bandwidth (lowest quality first to match HLS.js expectations)
        qualities.sort(key=lambda x: x["bandwidth"])

        return qualities

    def list_variant_segments(self, quality: str = "auto") -> tuple[list[str], bytes]:
        """Return every segment path (relative to base_url) plus the CBC IV.

        Mirrors the playlist parsing PlaybackSession._probe_video_key uses to
        validate just the first segment, but returns the full ordered list —
        used by the download module to pull an entire lesson.

        Args:
            quality: The quality variant to use. If "auto", uses the first (highest) variant.
                    Otherwise should be a variant path like "v0/playback".
        """
        master_text = self._fetch_and_decrypt_playlist("/playback")
        variants = [l.strip() for l in master_text.splitlines()
                    if l.strip() and not l.startswith("#")]
        if not variants:
            raise RuntimeError("master playlist has no variants")

        # Select variant based on quality parameter
        if quality == "auto":
            selected_variant = variants[0]
        else:
            # Try to find the requested quality variant
            if quality in variants:
                selected_variant = quality
            else:
                # Fallback to first variant if quality not found
                selected_variant = variants[0]

        variant_text = self._fetch_and_decrypt_playlist("/" + selected_variant)
        seg_names = [l.strip() for l in variant_text.splitlines()
                     if l.strip() and not l.startswith("#")]
        if not seg_names:
            raise RuntimeError("variant playlist has no segments")

        iv_m = re.search(r"IV=0[xX]([0-9a-fA-F]+)", variant_text)
        iv = bytes.fromhex(iv_m.group(1)[-32:].rjust(32, "0")) if iv_m \
            else bytes(16)

        var_dir = selected_variant.rsplit("/", 1)[0] if "/" in selected_variant else ""
        seg_paths = [f"/{var_dir}/{name}" if var_dir else f"/{name}"
                     for name in seg_names]
        return seg_paths, iv


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

    def to_data(self) -> PlaybackSessionData:
        """Return a serializable snapshot for the shared store.

        The proxy and download paths work from this snapshot rather than the
        live object, so a lesson prepared in one gunicorn worker can be served
        by any other (and by any number of concurrent users).
        """
        return PlaybackSessionData.from_session(self)

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
