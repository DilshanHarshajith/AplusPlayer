"""
AplusPlayer — unofficial client (personal use, own account only).

Package layout:
  config.py          Constants (endpoints, device model, crypto key IDs, UA)
  crypto.py          AES-CBC + OTP decrypt routines used by the vendor app
  api.py             GraphQL client (login, courses, lesson details/content)
  session.py         PlaybackSession — resolves + verifies the video AES key
  streaming.py       Playlist decrypt/rewrite logic used by the HLS proxy
  download_engine.py Segment fetch/decrypt/remux logic used by downloads

Pure site/API interaction and lesson processing — no Flask, no HTTP
routing. The `webapp/` package's blueprints (`proxy.py`, `downloads.py`,
etc.) are thin HTTP wrappers that call into this package for everything.
"""

__version__ = "1.0.0"
