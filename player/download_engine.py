"""
Core lesson-download engine: fetches every HLS segment for a prepared
PlaybackSession, decrypts it, and (optionally) remuxes the result to MP4
via ffmpeg.

No Flask, no progress-store, no HTTP here — webapp/downloads.py wraps
this as a streamed HTTP response with live progress polling and
cancellation, via the `on_progress` / `is_cancelled` callbacks below.
"""
import os
import shutil
import subprocess
import sys

from .crypto import aes_cbc_decrypt


def decrypt_segment(sess, raw: bytes, iv: bytes) -> bytes:
    """Decrypt one media segment and strip PKCS#7 padding."""
    if not sess.segments_encrypted or not sess.video_aes_key:
        return raw
    dec = aes_cbc_decrypt(sess.video_aes_key, iv, raw)
    if dec:
        pad = dec[-1]
        if 1 <= pad <= 16 and dec[-pad:] == bytes([pad]) * pad:
            dec = dec[:-pad]
    return dec


def download_and_remux(sess, seg_paths, iv: bytes, temp_ts: str, temp_mp4: str,
                       *, is_cancelled=None, on_progress=None):
    """
    Fetch + decrypt every segment into `temp_ts`, then remux to `temp_mp4`
    via ffmpeg if it's on PATH.

    `is_cancelled()` is polled between segments; `on_progress(**fields)` is
    called with progress updates (status/progress/message) as work happens.

    Returns the path of the final playable file (`temp_mp4` if the remux
    ran and succeeded, otherwise `temp_ts`), or None if cancelled partway
    through — the caller is responsible for cleaning up the partial file
    in that case.
    """
    is_cancelled = is_cancelled or (lambda: False)
    on_progress = on_progress or (lambda **_fields: None)
    total = len(seg_paths)

    with open(temp_ts, "wb") as out:
        for i, seg_path in enumerate(seg_paths, start=1):
            if is_cancelled():
                return None

            raw = sess.fetch_upstream(seg_path)
            out.write(decrypt_segment(sess, raw, iv))

            progress = int((i / total) * 80) + 5  # 5-85% downloading
            on_progress(progress=progress,
                        message=f"Downloading segment {i}/{total} ({progress}%)")

    on_progress(status="processing", progress=90,
                message="Processing video file...")

    final_file = temp_ts
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        on_progress(progress=92, message="Converting to MP4...")
        result = subprocess.run(
            [ffmpeg_path, "-y", "-i", temp_ts, "-c", "copy", temp_mp4],
            capture_output=True, text=True, check=False)
        if result.returncode == 0 and os.path.exists(temp_mp4):
            final_file = temp_mp4
            on_progress(progress=95)
        else:
            print(f"[!] ffmpeg remux failed, sending .ts: "
                  f"{result.stderr[-500:]}", file=sys.stderr)

    return final_file
