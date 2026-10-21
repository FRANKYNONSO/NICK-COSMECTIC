#!/usr/bin/env python3
"""Install the Nick Cosmetics Beauty Advisor chat (storefront only).

Run from the storefront folder (apps\\ecommerce):
    python apply_advisor_chat.py
or point at it:
    python apply_advisor_chat.py --root "C:\\Dev\\nick-cosmetics-python-platform\\apps\\ecommerce"
To remove it again:
    python apply_advisor_chat.py --undo

What it does
- backs up every existing file it changes into _backup_before_advisor_chat_<time>
- adds 6 new files (chat logic, Gemini helper, chat turn, route, page, script)
- makes 3 small edits: main.py (register the chat route), static/css/style.css
  (adds the chat styles at the end) and templates/base.html (adds a nav link
  only if there is no advisor link yet)
- does NOT touch the inventory/POS app, the cart, checkout, the engine, the
  advisor catalog or the old wizard
- checks every Python file compiles; if anything fails it restores the backup
"""
import argparse
import base64
import datetime
import hashlib
import json
import os
import py_compile
import shutil
import sys

DEFAULT_ROOT = r"C:\Dev\nick-cosmetics-python-platform\apps\ecommerce"
BACKUP_PREFIX = "_backup_before_advisor_chat_"
MARK_CSS = "/* ---------- Beauty Advisor chat ---------- */"

FILES = {
    'advisor_flow.py': {
        "sha256": '0c9b1e09eefcd7a448571aa01fbc0063776f8e3a8b398549afca89de49a21e48',
        "b64": (
            'IiIiQmVhdXR5IGFkdmlzb3IgY2hhdCBmbG93IGxvZ2ljIChwdXJlIFB5dGhvbjogbm8gbmV0d29yaywgbm8gd2ViIGZyYW1ld29y'
            'aykuCgpXaGF0IGxpdmVzIGhlcmUKLSBjYXRlZ29yaWVzIGJ1aWx0IGxpdmUgZnJvbSB0aGUgc2hvcCdzIG93biBjYXRhbG9nIChl'
            'bmFibGVkICsgbGlzdGVkICsgaW4gc3RvY2spCi0gYSBmcmVlLCBydWxlcy1iYXNlZCByZWFkZXIgZm9yIHR5cGVkIG1lc3NhZ2Vz'
            'ICh3b3JrcyB3aXRoIG5vIEFJIGF0IGFsbCkKLSB0aGUgcXVlc3Rpb24gZmxvdyBwZXIga2luZCBvZiBjYXRlZ29yeSAoc2tpbmNh'
            'cmUgLyBmcmFncmFuY2UgLyBvdGhlcikKLSB0aGUgc2FmZXR5IHRleHQgYW5kIHRoZSByZWQtZmxhZyBjaGVjayAod2UgbmV2ZXIg'
            'ZGlhZ25vc2UpCi0gc21hbGwgaW4tbWVtb3J5IGxpbWl0ZXJzIChwZXIgdmlzaXRvciwgYW5kIGEgc2hvcC13aWRlIEFJIGRhaWx5'
            'IGNhcCkKClRoZSBzaG9ydGxpc3Qgb2YgcHJvZHVjdHMgaXMgQUxXQVlTIG1hZGUgYnkgYWR2aXNvcl9lbmdpbmUuIE5vdGhpbmcg'
            'aW4gdGhpcyBmaWxlCihhbmQgbm90aGluZyB0aGUgQUkgc2F5cykgY2FuIGFkZCBhIHByb2R1Y3QgdGhhdCB0aGUgZW5naW5lIGRp'
            'ZCBub3QgcGljay4KIiIiCmltcG9ydCByZQppbXBvcnQgdGltZQppbXBvcnQgZGF0ZXRpbWUKCmltcG9ydCBhZHZpc29yX2VuZ2lu'
            'ZSBhcyBlbmdpbmUKClNLSU5fVFlQRVMgPSAoImRyeSIsICJvaWx5IiwgImNvbWJpbmF0aW9uIiwgIm5vcm1hbCIsICJzZW5zaXRp'
            'dmUiKQpDT05DRVJOUyA9ICgKICAgICJkcnluZXNzIiwgIm9pbCBjb250cm9sIiwgImRhcmsgc3BvdHMiLCAiYWNuZSIsICJzdW4g'
            'cHJvdGVjdGlvbiIsCiAgICAiYnJpZ2h0ZW5pbmciLCAic2Vuc2l0aXZlIHNraW4iLCAiYmFieSBjYXJlIiwKKQpTQ0VOVFMgPSAo'
            'InN3ZWV0IiwgImZyZXNoIiwgImZydWl0eSIsICJmbG9yYWwiLCAid29vZHkiLCAibXVza3kiLCAidmFuaWxsYSIsICJzdHJvbmci'
            'LCAibGlnaHQiKQpPQ0NBU0lPTlMgPSAoImV2ZXJ5ZGF5IiwgIm9mZmljZSIsICJkYXRlIG5pZ2h0IiwgInBhcnR5IiwgInNwZWNp'
            'YWwgb2NjYXNpb24iKQoKRElTQ0xBSU1FUiA9ICgKICAgICJUaGVzZSByZWNvbW1lbmRhdGlvbnMgYXJlIGZvciBjb3NtZXRpYyBh'
            'bmQgc2tpbmNhcmUgZ3VpZGFuY2Ugb25seSBhbmQgYXJlIG5vdCBhICIKICAgICJtZWRpY2FsIGRpYWdub3Npcy4gSWYgeW91IGhh'
            'dmUgYSBwZXJzaXN0ZW50LCBwYWluZnVsLCBzZXZlcmUgb3Igd29yc2VuaW5nIHNraW4gIgogICAgImNvbmRpdGlvbiwgcGxlYXNl'
            'IGNvbnN1bHQgYSBxdWFsaWZpZWQgaGVhbHRoY2FyZSBwcm9mZXNzaW9uYWwgb3IgZGVybWF0b2xvZ2lzdC4iCikKTUVESUNBTF9S'
            'RVBMWSA9ICgKICAgICJJIGNhbid0IGRpYWdub3NlIHNraW4gY29uZGl0aW9ucywgYW5kIHdoYXQgeW91IGRlc2NyaWJlIG1heSBu'
            'ZWVkIGEgcHJvZmVzc2lvbmFsLiAiCiAgICAiUGxlYXNlIHNlZSBhIGRvY3RvciwgcGhhcm1hY2lzdCBvciBkZXJtYXRvbG9naXN0'
            'IGFib3V0IGl0LiBJZiB5b3UgbGlrZSwgSSBjYW4gc3RpbGwgIgogICAgInN1Z2dlc3QgZ2VudGxlIGV2ZXJ5ZGF5IHByb2R1Y3Rz'
            'IGZyb20gb3VyIHNob3AuIgopCgpfU0tJTl9LSU5EX1dPUkRTID0gKAogICAgImxvdGlvbiIsICJjcmVhbSIsICJvaWwiLCAic29h'
            'cCIsICJzZXJ1bSIsICJjbGVhbnNlciIsICJ0b25lciIsICJtb2lzdHVyIiwgInN1bnNjcmVlbiIsCiAgICAic2NydWIiLCAibWFz'
            'ayIsICJ3YXNoIiwgImJhbG0iLCAiYnV0dGVyIiwgImZhY2UiLCAiYm9keSIsICJza2luIiwgImV4Zm9saWF0IiwKKQpfRlJBR1JB'
            'TkNFX1dPUkRTID0gKCJwZXJmdW1lIiwgImZyYWdyYW5jZSIsICJzcHJheSIsICJtaXN0IiwgImNvbG9nbmUiLCAicGFyZnVtIikK'
            'Cl9NRURJQ0FMX1dPUkRTID0gKAogICAgImVjemVtYSIsICJpbmZlY3Rpb24iLCAiaW5mZWN0ZWQiLCAicmFzaCIsICJmdW5nYWwi'
            'LCAiZnVuZ3VzIiwgInJpbmd3b3JtIiwgInB1cyIsCiAgICAiYmxlZWRpbmciLCAiY2FuY2VyIiwgIm1vbGUiLCAic3dvbGxlbiIs'
            'ICJzd2VsbGluZyIsICJzaGluZ2xlcyIsICJwc29yaWFzaXMiLAogICAgImJ1cm5pbmciLCAiYmxpc3RlciIsICJvcGVuIHdvdW5k'
            'IiwgImx1bXAiLCAicGFpbmZ1bCIsICJrZWVwcyBnZXR0aW5nIHdvcnNlIiwKKQoKIyAtLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0t'
            'LS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tIGNhdGVnb3JpZXMKCgpkZWYgY2F0ZWdvcnlfbGFiZWwoZW50'
            'cnkpOgogICAgIiIiQ3VzdG9tZXItZmFjaW5nIGNhdGVnb3J5IG5hbWUgZm9yIG9uZSBjYXRhbG9nIGVudHJ5LiBJdCBjb21lcyBm'
            'cm9tIHRoZQogICAgaW52ZW50b3J5IGNhdGVnb3J5IHdoZW4gdGhlIHNob3AgaGFzIG9uZSwgZWxzZSBmcm9tIHRoZSBwcm9kdWN0'
            'IHR5cGUuIiIiCiAgICByYXcgPSAoZW50cnkuZ2V0KCJpbnZlbnRvcnlfY2F0ZWdvcnkiKSBvciBlbnRyeS5nZXQoInByb2R1Y3Rf'
            'dHlwZSIpIG9yICIiKS5zdHJpcCgpCiAgICByZXR1cm4gcmF3WzoxXS51cHBlcigpICsgcmF3WzE6XSBpZiByYXcgZWxzZSAiIgoK'
            'CmRlZiBjYXRlZ29yeV9zbHVnKGxhYmVsKToKICAgIHJldHVybiByZS5zdWIociJbXmEtejAtOV0rIiwgIi0iLCAobGFiZWwgb3Ig'
            'IiIpLmxvd2VyKCkpLnN0cmlwKCItIikKCgpkZWYgY2F0ZWdvcnlfa2luZChsYWJlbCk6CiAgICBsb3cgPSAobGFiZWwgb3IgIiIp'
            'Lmxvd2VyKCkKICAgIGlmIGFueSh3IGluIGxvdyBmb3IgdyBpbiBfRlJBR1JBTkNFX1dPUkRTKToKICAgICAgICByZXR1cm4gImZy'
            'YWdyYW5jZSIKICAgIGlmIGFueSh3IGluIGxvdyBmb3IgdyBpbiBfU0tJTl9LSU5EX1dPUkRTKToKICAgICAgICByZXR1cm4gInNr'
            'aW4iCiAgICByZXR1cm4gIm90aGVyIgoKCmRlZiBhdmFpbGFibGVfY2F0ZWdvcmllcyhwcm9kdWN0cywgc3RvY2ssIG1ldGEpOgog'
            'ICAgIiIiW3tzbHVnLGxhYmVsLGtpbmQsY291bnR9XSBmb3IgY2F0ZWdvcmllcyB0aGF0IGhhdmUgYXQgbGVhc3Qgb25lIGVuYWJs'
            'ZWQsCiAgICBsaXN0ZWQsIGluLXN0b2NrIHByb2R1Y3QgcmlnaHQgbm93LiBOb3RoaW5nIGlzIGhhcmQtY29kZWQuIiIiCiAgICBz'
            'ZWVuID0ge30KICAgIGZvciBwIGluIHByb2R1Y3RzOgogICAgICAgIHBpZCA9IHN0cihwLmdldCgiaWQiKSBvciBwLmdldCgicHJv'
            'ZHVjdF9pZCIpIG9yICIiKQogICAgICAgIGVudHJ5ID0gbWV0YS5nZXQocGlkKQogICAgICAgIGlmIG5vdCBlbnRyeToKICAgICAg'
            'ICAgICAgY29udGludWUKICAgICAgICB0cnk6CiAgICAgICAgICAgIHByaWNlID0gZmxvYXQocC5nZXQoInByaWNlX25haXJhIikg'
            'b3IgcC5nZXQoInByaWNlIikgb3IgMCkKICAgICAgICBleGNlcHQgKFR5cGVFcnJvciwgVmFsdWVFcnJvcik6CiAgICAgICAgICAg'
            'IHByaWNlID0gMAogICAgICAgIGlmIHByaWNlIDw9IDAgb3IgKHN0b2NrLmdldChwaWQpIG9yIDApIDw9IDA6CiAgICAgICAgICAg'
            'IGNvbnRpbnVlCiAgICAgICAgbGFiZWwgPSBjYXRlZ29yeV9sYWJlbChlbnRyeSkKICAgICAgICBzbHVnID0gY2F0ZWdvcnlfc2x1'
            'ZyhsYWJlbCkKICAgICAgICBpZiBub3Qgc2x1ZzoKICAgICAgICAgICAgY29udGludWUKICAgICAgICByb3cgPSBzZWVuLnNldGRl'
            'ZmF1bHQoc2x1ZywgeyJzbHVnIjogc2x1ZywgImxhYmVsIjogbGFiZWwsICJraW5kIjogY2F0ZWdvcnlfa2luZChsYWJlbCksICJj'
            'b3VudCI6IDB9KQogICAgICAgIHJvd1siY291bnQiXSArPSAxCiAgICByZXR1cm4gc29ydGVkKHNlZW4udmFsdWVzKCksIGtleT1s'
            'YW1iZGEgcjogKC1yWyJjb3VudCJdLCByWyJsYWJlbCJdLmxvd2VyKCkpKQoKCmRlZiBtZXRhX2Zvcl9jYXRlZ29yeShtZXRhLCBz'
            'bHVnKToKICAgIHJldHVybiB7cGlkOiBlIGZvciBwaWQsIGUgaW4gbWV0YS5pdGVtcygpIGlmIGNhdGVnb3J5X3NsdWcoY2F0ZWdv'
            'cnlfbGFiZWwoZSkpID09IHNsdWd9CgoKIyAtLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0t'
            'LS0tLS0tLS0tLS0tLS0tIHN0YXRlCkZJRUxEUyA9IHsKICAgICJza2luIjogKCJza2luX3R5cGUiLCAiY29uY2VybnMiLCAiYnVk'
            'Z2V0IiksCiAgICAiZnJhZ3JhbmNlIjogKCJzY2VudCIsICJvY2Nhc2lvbiIsICJidWRnZXQiKSwKICAgICJvdGhlciI6ICgiYnVk'
            'Z2V0IiwpLAp9CgoKZGVmIG5ld19zdGF0ZSgpOgogICAgcmV0dXJuIHsiY2F0ZWdvcnkiOiBOb25lLCAic2tpbl90eXBlIjogTm9u'
            'ZSwgImNvbmNlcm5zIjogTm9uZSwgInNjZW50IjogTm9uZSwKICAgICAgICAgICAgIm9jY2FzaW9uIjogTm9uZSwgImJ1ZGdldF9t'
            'aW4iOiBOb25lLCAiYnVkZ2V0X21heCI6IE5vbmUsICJkb25lIjogW119CgoKZGVmIGNsZWFuX3N0YXRlKHJhdywgdmFsaWRfc2x1'
            'Z3MpOgogICAgIiIiVGhlIHNlc3Npb24gaXMgb3VycywgYnV0IHN0aWxsIHRyZWF0IGl0IGFzIHVudHJ1c3RlZCBvbiByZWFkLiIi'
            'IgogICAgcyA9IG5ld19zdGF0ZSgpCiAgICBpZiBub3QgaXNpbnN0YW5jZShyYXcsIGRpY3QpOgogICAgICAgIHJldHVybiBzCiAg'
            'ICBpZiByYXcuZ2V0KCJjYXRlZ29yeSIpIGluIHZhbGlkX3NsdWdzOgogICAgICAgIHNbImNhdGVnb3J5Il0gPSByYXdbImNhdGVn'
            'b3J5Il0KICAgIGlmIHJhdy5nZXQoInNraW5fdHlwZSIpIGluIFNLSU5fVFlQRVMgKyAoInVua25vd24iLCk6CiAgICAgICAgc1si'
            'c2tpbl90eXBlIl0gPSByYXdbInNraW5fdHlwZSJdCiAgICBpZiBpc2luc3RhbmNlKHJhdy5nZXQoImNvbmNlcm5zIiksIGxpc3Qp'
            'OgogICAgICAgIHNbImNvbmNlcm5zIl0gPSBbYyBmb3IgYyBpbiByYXdbImNvbmNlcm5zIl0gaWYgYyBpbiBDT05DRVJOU11bOjhd'
            'CiAgICBpZiBpc2luc3RhbmNlKHJhdy5nZXQoInNjZW50IiksIGxpc3QpOgogICAgICAgIHNbInNjZW50Il0gPSBbYyBmb3IgYyBp'
            'biByYXdbInNjZW50Il0gaWYgYyBpbiBTQ0VOVFNdWzo0XQogICAgaWYgcmF3LmdldCgib2NjYXNpb24iKSBpbiBPQ0NBU0lPTlM6'
            'CiAgICAgICAgc1sib2NjYXNpb24iXSA9IHJhd1sib2NjYXNpb24iXQogICAgZm9yIGsgaW4gKCJidWRnZXRfbWluIiwgImJ1ZGdl'
            'dF9tYXgiKToKICAgICAgICB2ID0gcmF3LmdldChrKQogICAgICAgIGlmIGlzaW5zdGFuY2UodiwgKGludCwgZmxvYXQpKSBhbmQg'
            'MCA8PSB2IDw9IDEwXzAwMF8wMDA6CiAgICAgICAgICAgIHNba10gPSBmbG9hdCh2KQogICAgaWYgaXNpbnN0YW5jZShyYXcuZ2V0'
            'KCJkb25lIiksIGxpc3QpOgogICAgICAgIHNbImRvbmUiXSA9IFtkIGZvciBkIGluIHJhd1siZG9uZSJdIGlmIGQgaW4gKCJza2lu'
            'X3R5cGUiLCAiY29uY2VybnMiLCAic2NlbnQiLCAib2NjYXNpb24iLCAiYnVkZ2V0IildWzo1XQogICAgcmV0dXJuIHMKCgpkZWYg'
            'bmV4dF9maWVsZChzdGF0ZSwga2luZCk6CiAgICBmb3IgZiBpbiBGSUVMRFMuZ2V0KGtpbmQsICgiYnVkZ2V0IiwpKToKICAgICAg'
            'ICBpZiBmIG5vdCBpbiBzdGF0ZVsiZG9uZSJdOgogICAgICAgICAgICByZXR1cm4gZgogICAgcmV0dXJuIE5vbmUKCgojIC0tLS0t'
            'LS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0gYnVkZ2V0CkJVREdFVF9D'
            'SElQUyA9IFsKICAgICgiQW55IGJ1ZGdldCIsICJhbnkiKSwKICAgICgiVW5kZXIgXHUyMEE2MywwMDAiLCAidW5kZXJfMzAwMCIp'
            'LAogICAgKCJcdTIwQTYzLDAwMCB0byBcdTIwQTY4LDAwMCIsICIzMDAwXzgwMDAiKSwKICAgICgiXHUyMEE2OCwwMDAgdG8gXHUy'
            'MEE2MjAsMDAwIiwgIjgwMDBfMjAwMDAiKSwKICAgICgiT3ZlciBcdTIwQTYyMCwwMDAiLCAib3Zlcl8yMDAwMCIpLApdCkJVREdF'
            'VF9SQU5HRVMgPSB7CiAgICAiYW55IjogKE5vbmUsIE5vbmUpLCAidW5kZXJfMzAwMCI6ICgwLCAzMDAwKSwgIjMwMDBfODAwMCI6'
            'ICgzMDAwLCA4MDAwKSwKICAgICI4MDAwXzIwMDAwIjogKDgwMDAsIDIwMDAwKSwgIm92ZXJfMjAwMDAiOiAoMjAwMDAsIE5vbmUp'
            'LAp9CgpfTlVNID0gciIoPzpcdTIwQTZ8bnxuZ24pP1xzKihcZHsxLDN9KD86LFxkezN9KSt8XGQrKD86XC5cZCspPylccyooa3xt'
            'KT8iCgoKZGVmIF90b19uYWlyYShudW0sIHN1ZmZpeCk6CiAgICB2ID0gZmxvYXQoc3RyKG51bSkucmVwbGFjZSgiLCIsICIiKSkK'
            'ICAgIGlmIHN1ZmZpeCA9PSAiayI6CiAgICAgICAgdiAqPSAxMDAwCiAgICBlbGlmIHN1ZmZpeCA9PSAibSI6CiAgICAgICAgdiAq'
            'PSAxXzAwMF8wMDAKICAgIHJldHVybiB2CgoKZGVmIHBhcnNlX2J1ZGdldCh0ZXh0KToKICAgICIiIlJlYWQgJ3VuZGVyIDUwMDAn'
            'LCAnYmVsb3cgM2snLCAnMzAwMCB0byA4MDAwJywgJ2Fyb3VuZCAxMCwwMDAnIGZyb20gZnJlZQogICAgdGV4dC4gUmV0dXJucyAo'
            'bWluLCBtYXgpIG9yIE5vbmUuIE9ubHkgY2xlYXIgbW9uZXkgcGhyYXNlcyBjb3VudC4iIiIKICAgIHQgPSAodGV4dCBvciAiIiku'
            'bG93ZXIoKS5yZXBsYWNlKCJcdTIwYTYiLCAiIG4gIikKICAgIG0gPSByZS5zZWFyY2gociIoPzpiZXR3ZWVuXHMrKT8iICsgX05V'
            'TSArIHIiXHMqKD86dG98LXxhbmQpXHMqIiArIF9OVU0sIHQpCiAgICBpZiBtOgogICAgICAgIGEgPSBfdG9fbmFpcmEobS5ncm91'
            'cCgxKSwgbS5ncm91cCgyKSBvciBtLmdyb3VwKDQpKQogICAgICAgIGIgPSBfdG9fbmFpcmEobS5ncm91cCgzKSwgbS5ncm91cCg0'
            'KSkKICAgICAgICBsbywgaGkgPSBzb3J0ZWQoKGEsIGIpKQogICAgICAgIGlmIGhpID49IDEwMDoKICAgICAgICAgICAgcmV0dXJu'
            'IChsbywgaGkpCiAgICBtID0gcmUuc2VhcmNoKHIiKD86dW5kZXJ8YmVsb3d8bGVzcyB0aGFufG1heCg/OmltdW0pP3x3aXRoaW58'
            'bm90IG1vcmUgdGhhbnx1cCB0b3xhdCBtb3N0KVxzKiIgKyBfTlVNLCB0KQogICAgaWYgbToKICAgICAgICB2ID0gX3RvX25haXJh'
            'KG0uZ3JvdXAoMSksIG0uZ3JvdXAoMikpCiAgICAgICAgaWYgdiA+PSAxMDA6CiAgICAgICAgICAgIHJldHVybiAoMCwgdikKICAg'
            'IG0gPSByZS5zZWFyY2gociIoPzpvdmVyfGFib3ZlfG1vcmUgdGhhbnxhdCBsZWFzdHxmcm9tKVxzKiIgKyBfTlVNLCB0KQogICAg'
            'aWYgbToKICAgICAgICB2ID0gX3RvX25haXJhKG0uZ3JvdXAoMSksIG0uZ3JvdXAoMikpCiAgICAgICAgaWYgdiA+PSAxMDA6CiAg'
            'ICAgICAgICAgIHJldHVybiAodiwgTm9uZSkKICAgIG0gPSByZS5zZWFyY2gociIoPzpidWRnZXR8YXJvdW5kfGFib3V0fHJvdWdo'
            'bHkpXHMqKD86aXN8b2YpP1xzKiIgKyBfTlVNLCB0KQogICAgaWYgbToKICAgICAgICB2ID0gX3RvX25haXJhKG0uZ3JvdXAoMSks'
            'IG0uZ3JvdXAoMikpCiAgICAgICAgaWYgdiA+PSAxMDA6CiAgICAgICAgICAgIHJldHVybiAoMCwgdikKICAgIHJldHVybiBOb25l'
            'CgoKIyAtLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tIGZyZWUg'
            'dGV4dCByZWFkZXIKX1NLSU5fUEhSQVNFUyA9IHsKICAgICJkcnkiOiAoImRyeSBza2luIiwgIm15IHNraW4gaXMgZHJ5IiwgImRy'
            'eSBmYWNlIiwgImZsYWt5IiwgInRpZ2h0IHNraW4iKSwKICAgICJvaWx5IjogKCJvaWx5IiwgImdyZWFzeSIsICJzaGlueSBmYWNl'
            'IiwgInNoaW55IHNraW4iKSwKICAgICJjb21iaW5hdGlvbiI6ICgiY29tYmluYXRpb24iLCksCiAgICAibm9ybWFsIjogKCJub3Jt'
            'YWwgc2tpbiIsKSwKICAgICJzZW5zaXRpdmUiOiAoInNlbnNpdGl2ZSIsKSwKfQpfQ09OQ0VSTl9QSFJBU0VTID0gewogICAgImRh'
            'cmsgc3BvdHMiOiAoImRhcmsgc3BvdCIsICJkYXJrIG1hcmsiLCAiaHlwZXJwaWdtZW50IiwgInBpZ21lbnRhdGlvbiIsICJkaXNj'
            'b2xvciIsICJkaXNjb2xvdXIiKSwKICAgICJhY25lIjogKCJhY25lIiwgInBpbXBsZSIsICJicmVha291dCIsICJicmVhayBvdXQi'
            'LCAiYmxlbWlzaCIpLAogICAgImRyeW5lc3MiOiAoImRyeSIsICJmbGFreSIsICJkcnluZXNzIiksCiAgICAib2lsIGNvbnRyb2wi'
            'OiAoIm9pbHkiLCAib2lsIGNvbnRyb2wiLCAiZ3JlYXN5IiwgInNoaW55IiksCiAgICAic3VuIHByb3RlY3Rpb24iOiAoInN1bnNj'
            'cmVlbiIsICJzdW4gcHJvdGVjdGlvbiIsICJzcGYiLCAic3VuIGJsb2NrIiwgInN1bmJsb2NrIiksCiAgICAiYnJpZ2h0ZW5pbmci'
            'OiAoImJyaWdodGVuIiwgImdsb3ciLCAiZXZlbiB0b25lIiwgInVuZXZlbiIsICJkdWxsIiwgInJhZGlhbnQiLCAiYnJpZ2h0Iiks'
            'CiAgICAic2Vuc2l0aXZlIHNraW4iOiAoInNlbnNpdGl2ZSIsICJpcnJpdGF0IiwgInJlZG5lc3MiLCAicmVkZGlzaCIpLAogICAg'
            'ImJhYnkgY2FyZSI6ICgiYmFieSIsICJpbmZhbnQiLCAibmV3Ym9ybiIsICJ0b2RkbGVyIiksCn0KX1NDRU5UX1BIUkFTRVMgPSB7'
            'czogKHMsKSBmb3IgcyBpbiBTQ0VOVFN9Cl9TQ0VOVF9QSFJBU0VTWyJmcnVpdHkiXSA9ICgiZnJ1aXR5IiwgImZydWl0IikKX09D'
            'Q19QSFJBU0VTID0gewogICAgImV2ZXJ5ZGF5IjogKCJldmVyeWRheSIsICJldmVyeSBkYXkiLCAiZGFpbHkiLCAiY2FzdWFsIiks'
            'CiAgICAib2ZmaWNlIjogKCJvZmZpY2UiLCAid29yayIpLAogICAgImRhdGUgbmlnaHQiOiAoImRhdGUgbmlnaHQiLCAiZGF0ZSIp'
            'LAogICAgInBhcnR5IjogKCJwYXJ0eSIsICJjbHViIiwgIm5pZ2h0IG91dCIpLAogICAgInNwZWNpYWwgb2NjYXNpb24iOiAoInNw'
            'ZWNpYWwgb2NjYXNpb24iLCAid2VkZGluZyIsICJldmVudCIsICJmb3JtYWwiKSwKfQpfU0hPV19XT1JEUyA9ICgic2hvdyBtZSIs'
            'ICJyZWNvbW1lbmQiLCAianVzdCBzaG93IiwgIndoYXQgZG8geW91IGhhdmUiLCAid2hhdCBkbyB5b3Ugc3VnZ2VzdCIsICJzdWdn'
            'ZXN0IikKCgpkZWYgX2hhcyhsb3csIHBocmFzZSk6CiAgICAiIiJQaHJhc2Ugc3RhcnRzIGF0IGEgd29yZCBib3VuZGFyeSwgc28g'
            'J2RyeScgZG9lcyBub3QgZmlyZSBpbnNpZGUgJ2xhdW5kcnknLiIiIgogICAgcmV0dXJuIHJlLnNlYXJjaChyIlxiIiArIHJlLmVz'
            'Y2FwZShwaHJhc2UpLCBsb3cpIGlzIG5vdCBOb25lCgoKZGVmIGhhc19tZWRpY2FsX2ZsYWcodGV4dCk6CiAgICBsb3cgPSAodGV4'
            'dCBvciAiIikubG93ZXIoKQogICAgcmV0dXJuIGFueSh3IGluIGxvdyBmb3IgdyBpbiBfTUVESUNBTF9XT1JEUykKCgpkZWYgcmVh'
            'ZF90ZXh0KHRleHQsIGNhdGVnb3JpZXMpOgogICAgIiIiUnVsZXMtYmFzZWQgdW5kZXJzdGFuZGluZyBvZiBhIHR5cGVkIG1lc3Nh'
            'Z2UuIFJldHVybnMgYSBkaWN0IHdpdGggb25seQogICAgd2hhdCB3YXMgY2xlYXJseSBzYWlkLiBUaGlzIGlzIHRoZSBGUkVFIHBh'
            'dGg7IHRoZSBBSSBpcyBvbmx5IGEgaGVscGVyIGZvcgogICAgbWVzc2FnZXMgdGhpcyBjYW5ub3QgcmVhZC4iIiIKICAgIGxvdyA9'
            'ICIgIiArIHJlLnN1YihyIlxzKyIsICIgIiwgKHRleHQgb3IgIiIpLmxvd2VyKCkpICsgIiAiCiAgICBvdXQgPSB7fQogICAgZm9y'
            'IGMgaW4gY2F0ZWdvcmllczoKICAgICAgICB3b3JkcyA9IHtjWyJsYWJlbCJdLmxvd2VyKCksIGNbImxhYmVsIl0ubG93ZXIoKS5y'
            'c3RyaXAoInMiKX0KICAgICAgICBpZiBhbnkocmUuc2VhcmNoKHIiXGIiICsgcmUuZXNjYXBlKHcpICsgciJzP1xiIiwgbG93KSBm'
            'b3IgdyBpbiB3b3JkcyBpZiB3KToKICAgICAgICAgICAgb3V0WyJjYXRlZ29yeSJdID0gY1sic2x1ZyJdCiAgICAgICAgICAgIGJy'
            'ZWFrCiAgICBpZiAiY2F0ZWdvcnkiIG5vdCBpbiBvdXQ6CiAgICAgICAgIyBmcmllbmRseSBzeW5vbnltcyAtPiB3aGF0ZXZlciBj'
            'YXRlZ29yeSBuYW1lIGNvbnRhaW5zIHRoZSB3b3JkCiAgICAgICAgc3luID0geyJwZXJmdW1lIjogX0ZSQUdSQU5DRV9XT1JEUywg'
            'ImJvZHkgc3ByYXkiOiAoInNwcmF5IiwgIm1pc3QiKSwgImNyZWFtIjogKCJjcmVhbSIsKSwKICAgICAgICAgICAgICAgIm1vaXN0'
            'dXJpemVyIjogKCJsb3Rpb24iLCAiY3JlYW0iLCAibW9pc3R1ciIpLCAibW9pc3R1cmlzZXIiOiAoImxvdGlvbiIsICJjcmVhbSIs'
            'ICJtb2lzdHVyIil9CiAgICAgICAgZm9yIHdvcmQsIG5lZWRsZXMgaW4gc3luLml0ZW1zKCk6CiAgICAgICAgICAgIGlmIHdvcmQg'
            'aW4gbG93OgogICAgICAgICAgICAgICAgZm9yIGMgaW4gY2F0ZWdvcmllczoKICAgICAgICAgICAgICAgICAgICBpZiBhbnkobiBp'
            'biBjWyJsYWJlbCJdLmxvd2VyKCkgZm9yIG4gaW4gbmVlZGxlcyk6CiAgICAgICAgICAgICAgICAgICAgICAgIG91dFsiY2F0ZWdv'
            'cnkiXSA9IGNbInNsdWciXQogICAgICAgICAgICAgICAgICAgICAgICBicmVhawogICAgICAgICAgICBpZiAiY2F0ZWdvcnkiIGlu'
            'IG91dDoKICAgICAgICAgICAgICAgIGJyZWFrCiAgICBmb3Igc2tpbiwgcGhyYXNlcyBpbiBfU0tJTl9QSFJBU0VTLml0ZW1zKCk6'
            'CiAgICAgICAgaWYgYW55KF9oYXMobG93LCBwKSBmb3IgcCBpbiBwaHJhc2VzKToKICAgICAgICAgICAgb3V0WyJza2luX3R5cGUi'
            'XSA9IHNraW4KICAgICAgICAgICAgYnJlYWsKICAgIGNvbmMgPSBbayBmb3IgaywgcGggaW4gX0NPTkNFUk5fUEhSQVNFUy5pdGVt'
            'cygpIGlmIGFueShfaGFzKGxvdywgcCkgZm9yIHAgaW4gcGgpXQogICAgaWYgY29uYzoKICAgICAgICBvdXRbImNvbmNlcm5zIl0g'
            'PSBjb25jCiAgICBzYyA9IFtrIGZvciBrLCBwaCBpbiBfU0NFTlRfUEhSQVNFUy5pdGVtcygpIGlmIGFueShyZS5zZWFyY2gociJc'
            'YiIgKyByZS5lc2NhcGUocCkgKyByIlxiIiwgbG93KSBmb3IgcCBpbiBwaCldCiAgICBpZiBzYzoKICAgICAgICBvdXRbInNjZW50'
            'Il0gPSBzY1s6NF0KICAgIGZvciBvY2MsIHBoIGluIF9PQ0NfUEhSQVNFUy5pdGVtcygpOgogICAgICAgIGlmIGFueShyZS5zZWFy'
            'Y2gociJcYiIgKyByZS5lc2NhcGUocCkgKyByIlxiIiwgbG93KSBmb3IgcCBpbiBwaCk6CiAgICAgICAgICAgIG91dFsib2NjYXNp'
            'b24iXSA9IG9jYwogICAgICAgICAgICBicmVhawogICAgYiA9IHBhcnNlX2J1ZGdldCh0ZXh0KQogICAgaWYgYjoKICAgICAgICBv'
            'dXRbImJ1ZGdldCJdID0gYgogICAgaWYgYW55KHcgaW4gbG93IGZvciB3IGluIF9TSE9XX1dPUkRTKToKICAgICAgICBvdXRbInNo'
            'b3dfbm93Il0gPSBUcnVlCiAgICBpZiBoYXNfbWVkaWNhbF9mbGFnKHRleHQpOgogICAgICAgIG91dFsibWVkaWNhbCJdID0gVHJ1'
            'ZQogICAgcmV0dXJuIG91dAoKCmRlZiBhcHBseV9yZWFkaW5nKHN0YXRlLCByZWFkaW5nLCBraW5kX29mPU5vbmUpOgogICAgIiIi'
            'Rm9sZCBhIHJlYWRpbmcgKGZyb20gcnVsZXMgb3IgZnJvbSB0aGUgQUksIGFscmVhZHkgdmFsaWRhdGVkKSBpbnRvIHRoZQogICAg'
            'c3RhdGUuIFNraW4sIHNjZW50IGFuZCBidWRnZXQgZmFjdHMgYXJlIGtlcHQgZXZlbiBiZWZvcmUgYSBjYXRlZ29yeSBpcwogICAg'
            'Y2hvc2VuLCBzbyBub3RoaW5nIHRoZSBjdXN0b21lciBzYWlkIGlzIGxvc3QuIFJldHVybnMgVHJ1ZSBpZiBhbnl0aGluZyBjaGFu'
            'Z2VkLiIiIgogICAgY2hhbmdlZCA9IEZhbHNlCiAgICBjYXQgPSByZWFkaW5nLmdldCgiY2F0ZWdvcnkiKQogICAgaWYgY2F0IGFu'
            'ZCBjYXQgIT0gc3RhdGVbImNhdGVnb3J5Il06CiAgICAgICAgc3RhdGUudXBkYXRlKG5ld19zdGF0ZSgpKQogICAgICAgIHN0YXRl'
            'WyJjYXRlZ29yeSJdID0gY2F0CiAgICAgICAgY2hhbmdlZCA9IFRydWUKICAgIHNrID0gcmVhZGluZy5nZXQoInNraW5fdHlwZSIp'
            'CiAgICBpZiBzayBpbiBTS0lOX1RZUEVTICsgKCJ1bmtub3duIiwpOgogICAgICAgIHN0YXRlWyJza2luX3R5cGUiXSA9IHNrCiAg'
            'ICAgICAgX21hcmsoc3RhdGUsICJza2luX3R5cGUiKQogICAgICAgIGNoYW5nZWQgPSBUcnVlCiAgICBjcyA9IFtjIGZvciBjIGlu'
            'IChyZWFkaW5nLmdldCgiY29uY2VybnMiKSBvciBbXSkgaWYgYyBpbiBDT05DRVJOU10KICAgIGlmIGNzOgogICAgICAgIHN0YXRl'
            'WyJjb25jZXJucyJdID0gY3MKICAgICAgICBfbWFyayhzdGF0ZSwgImNvbmNlcm5zIikKICAgICAgICBjaGFuZ2VkID0gVHJ1ZQog'
            'ICAgc2MgPSBbYyBmb3IgYyBpbiAocmVhZGluZy5nZXQoInNjZW50Iikgb3IgW10pIGlmIGMgaW4gU0NFTlRTXQogICAgaWYgc2M6'
            'CiAgICAgICAgc3RhdGVbInNjZW50Il0gPSBzYwogICAgICAgIF9tYXJrKHN0YXRlLCAic2NlbnQiKQogICAgICAgIGNoYW5nZWQg'
            'PSBUcnVlCiAgICBvYyA9IHJlYWRpbmcuZ2V0KCJvY2Nhc2lvbiIpCiAgICBpZiBvYyBpbiBPQ0NBU0lPTlM6CiAgICAgICAgc3Rh'
            'dGVbIm9jY2FzaW9uIl0gPSBvYwogICAgICAgIF9tYXJrKHN0YXRlLCAib2NjYXNpb24iKQogICAgICAgIGNoYW5nZWQgPSBUcnVl'
            'CiAgICBiID0gcmVhZGluZy5nZXQoImJ1ZGdldCIpCiAgICBpZiBiOgogICAgICAgIHN0YXRlWyJidWRnZXRfbWluIl0sIHN0YXRl'
            'WyJidWRnZXRfbWF4Il0gPSBiWzBdLCBiWzFdCiAgICAgICAgX21hcmsoc3RhdGUsICJidWRnZXQiKQogICAgICAgIGNoYW5nZWQg'
            'PSBUcnVlCiAgICByZXR1cm4gY2hhbmdlZAoKCmRlZiBfbWFyayhzdGF0ZSwgZmllbGQpOgogICAgaWYgZmllbGQgbm90IGluIHN0'
            'YXRlWyJkb25lIl06CiAgICAgICAgc3RhdGVbImRvbmUiXS5hcHBlbmQoZmllbGQpCgoKIyAtLS0tLS0tLS0tLS0tLS0tLS0tLS0t'
            'LS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tIGVuZ2luZSBicmlkZ2UKZGVmIGVuZ2luZV9hbnN3ZXJz'
            'KHN0YXRlKToKICAgIHJldHVybiB7CiAgICAgICAgInNraW5fdHlwZSI6IHN0YXRlLmdldCgic2tpbl90eXBlIikgb3IgInVua25v'
            'd24iLAogICAgICAgICJjb25jZXJucyI6IHN0YXRlLmdldCgiY29uY2VybnMiKSBvciBbXSwKICAgICAgICAicHJvZHVjdF90eXBl'
            'IjogImFueSIsCiAgICAgICAgImJ1ZGdldF9taW4iOiBzdGF0ZS5nZXQoImJ1ZGdldF9taW4iKSBvciAwLAogICAgICAgICJidWRn'
            'ZXRfbWF4Ijogc3RhdGUuZ2V0KCJidWRnZXRfbWF4IiksCiAgICAgICAgInN1bl9leHBvc3VyZSI6ICJ1bmtub3duIiwKICAgICAg'
            'ICAic3dlYXRpbmciOiAidW5rbm93biIsCiAgICB9CgoKZGVmIHBpY2tfcHJvZHVjdHMoc3RhdGUsIHByb2R1Y3RzLCBzdG9jaywg'
            'bWV0YSwgbGltaXQ9NCk6CiAgICAiIiJFbmdpbmUgc2hvcnRsaXN0IGZvciB0aGUgY2hvc2VuIGNhdGVnb3J5LiBSZXR1cm5zIHRo'
            'ZSBlbmdpbmUgcmVzdWx0IGRpY3QuIiIiCiAgICBzbHVnID0gc3RhdGUuZ2V0KCJjYXRlZ29yeSIpCiAgICBzdWIgPSBtZXRhX2Zv'
            'cl9jYXRlZ29yeShtZXRhLCBzbHVnKQogICAgcmVzID0gZW5naW5lLnJlY29tbWVuZChlbmdpbmVfYW5zd2VycyhzdGF0ZSksIHBy'
            'b2R1Y3RzLCBzdG9jaywgc3ViLCBOb25lLCBsaW1pdD01MCkKICAgIGl0ZW1zID0gcmVzWyJpdGVtcyJdCiAgICBraW5kID0gY2F0'
            'ZWdvcnlfa2luZChuZXh0KChjYXRlZ29yeV9sYWJlbChlKSBmb3IgZSBpbiBzdWIudmFsdWVzKCkpLCAiIikpCiAgICBpZiBraW5k'
            'ID09ICJmcmFncmFuY2UiOgogICAgICAgIGl0ZW1zID0gX3JlcmFua19mcmFncmFuY2UoaXRlbXMsIHN1Yiwgc3RhdGUpCiAgICBy'
            'ZXNbIml0ZW1zIl0gPSBpdGVtc1s6bGltaXRdCiAgICByZXR1cm4gcmVzCgoKZGVmIF9yZXJhbmtfZnJhZ3JhbmNlKGl0ZW1zLCBz'
            'dWIsIHN0YXRlKToKICAgIHdhbnRfc2NlbnQgPSBzZXQoc3RhdGUuZ2V0KCJzY2VudCIpIG9yIFtdKQogICAgd2FudF9vY2MgPSBz'
            'dGF0ZS5nZXQoIm9jY2FzaW9uIikKICAgIG91dCA9IFtdCiAgICBmb3IgaXQgaW4gaXRlbXM6CiAgICAgICAgZSA9IHN1Yi5nZXQo'
            'aXRbInByb2R1Y3RfaWQiXSwge30pCiAgICAgICAgYm9udXMgPSAwLjAKICAgICAgICBoYXZlID0gW3N0cih4KS5sb3dlcigpIGZv'
            'ciB4IGluIChlLmdldCgic2NlbnRfZmFtaWx5Iikgb3IgW10pXQogICAgICAgIGhpdCA9IHNvcnRlZCh3YW50X3NjZW50ICYgc2V0'
            'KGhhdmUpKQogICAgICAgIGlmIGhpdDoKICAgICAgICAgICAgYm9udXMgKz0gMiAqIGxlbihoaXQpCiAgICAgICAgICAgIGl0WyJy'
            'ZWFzb25zIl0gPSBbIkxpc3RlZCBhcyAlcy4iICUgIiwgIi5qb2luKGhpdCldICsgaXRbInJlYXNvbnMiXQogICAgICAgIG9jY3Mg'
            'PSBbc3RyKHgpLmxvd2VyKCkgZm9yIHggaW4gKGUuZ2V0KCJvY2Nhc2lvbnMiKSBvciBbXSldCiAgICAgICAgaWYgd2FudF9vY2Mg'
            'YW5kIHdhbnRfb2NjIGluIG9jY3M6CiAgICAgICAgICAgIGJvbnVzICs9IDIKICAgICAgICAgICAgaXRbInJlYXNvbnMiXSA9IFsi'
            'TGlzdGVkIGZvciAlcyB1c2UuIiAlIHdhbnRfb2NjXSArIGl0WyJyZWFzb25zIl0KICAgICAgICBpdFsic2NvcmUiXSA9IHJvdW5k'
            'KGl0WyJzY29yZSJdICsgYm9udXMsIDIpCiAgICAgICAgb3V0LmFwcGVuZChpdCkKICAgIG91dC5zb3J0KGtleT1sYW1iZGEgYzog'
            'KC1jWyJzY29yZSJdLCBjWyJwcmljZV9uYWlyYSJdLCBjWyJuYW1lIl0ubG93ZXIoKSkpCiAgICByZXR1cm4gb3V0CgoKIyAtLS0t'
            'LS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tIHF1ZXN0aW9ucwpkZWYg'
            'cXVlc3Rpb25fZm9yKGZpZWxkLCBzdGF0ZSk6CiAgICAiIiIodGV4dCwgY2hpcHMtc3BlYykgZm9yIHRoZSBuZXh0IHF1ZXN0aW9u'
            'LiBjaGlwcy1zcGVjOiBkaWN0IG9yIE5vbmUuIiIiCiAgICBpZiBmaWVsZCA9PSAic2tpbl90eXBlIjoKICAgICAgICBvcHRzID0g'
            'WygiRHJ5IiwgImRyeSIpLCAoIk9pbHkiLCAib2lseSIpLCAoIkNvbWJpbmF0aW9uIiwgImNvbWJpbmF0aW9uIiksCiAgICAgICAg'
            'ICAgICAgICAoIk5vcm1hbCIsICJub3JtYWwiKSwgKCJTZW5zaXRpdmUiLCAic2Vuc2l0aXZlIiksICgiTm90IHN1cmUiLCAidW5r'
            'bm93biIpXQogICAgICAgIHJldHVybiAoIldoYXQgaXMgeW91ciBza2luIHR5cGU/IElmIHlvdSBhcmUgbm90IHN1cmUsIHRoYXQg'
            'aXMgZmluZS4iLAogICAgICAgICAgICAgICAgeyJmaWVsZCI6ICJza2luX3R5cGUiLCAibXVsdGkiOiBGYWxzZSwgIm9wdGlvbnMi'
            'OiBvcHRzfSkKICAgIGlmIGZpZWxkID09ICJjb25jZXJucyI6CiAgICAgICAgb3B0cyA9IFsoIkRhcmsgc3BvdHMiLCAiZGFyayBz'
            'cG90cyIpLCAoIkFjbmUiLCAiYWNuZSIpLCAoIkRyeW5lc3MiLCAiZHJ5bmVzcyIpLAogICAgICAgICAgICAgICAgKCJPaWwgY29u'
            'dHJvbCIsICJvaWwgY29udHJvbCIpLCAoIlVuZXZlbiB0b25lIG9yIGR1bGxuZXNzIiwgImJyaWdodGVuaW5nIiksCiAgICAgICAg'
            'ICAgICAgICAoIlN1biBwcm90ZWN0aW9uIiwgInN1biBwcm90ZWN0aW9uIiksICgiU2Vuc2l0aXZlIHNraW4iLCAic2Vuc2l0aXZl'
            'IHNraW4iKSwKICAgICAgICAgICAgICAgICgiQmFieSBjYXJlIiwgImJhYnkgY2FyZSIpXQogICAgICAgIHJldHVybiAoIldoYXQg'
            'd291bGQgeW91IGxpa2UgaGVscCB3aXRoPyBQaWNrIGFsbCB0aGF0IGFwcGx5LiIsCiAgICAgICAgICAgICAgICB7ImZpZWxkIjog'
            'ImNvbmNlcm5zIiwgIm11bHRpIjogVHJ1ZSwgIm9wdGlvbnMiOiBvcHRzfSkKICAgIGlmIGZpZWxkID09ICJzY2VudCI6CiAgICAg'
            'ICAgb3B0cyA9IFsocy5jYXBpdGFsaXplKCksIHMpIGZvciBzIGluIFNDRU5UU10KICAgICAgICByZXR1cm4gKCJXaGljaCBzY2Vu'
            'dHMgZG8geW91IGxpa2U/IFBpY2sgYW55LiIsIHsiZmllbGQiOiAic2NlbnQiLCAibXVsdGkiOiBUcnVlLCAib3B0aW9ucyI6IG9w'
            'dHN9KQogICAgaWYgZmllbGQgPT0gIm9jY2FzaW9uIjoKICAgICAgICBvcHRzID0gWyhvLmNhcGl0YWxpemUoKSwgbykgZm9yIG8g'
            'aW4gT0NDQVNJT05TXQogICAgICAgIHJldHVybiAoIldoZW4gd2lsbCB5b3Ugd2VhciBpdD8iLCB7ImZpZWxkIjogIm9jY2FzaW9u'
            'IiwgIm11bHRpIjogRmFsc2UsICJvcHRpb25zIjogb3B0c30pCiAgICBpZiBmaWVsZCA9PSAiYnVkZ2V0IjoKICAgICAgICByZXR1'
            'cm4gKCJXaGF0IGlzIHlvdXIgYnVkZ2V0PyIsIHsiZmllbGQiOiAiYnVkZ2V0IiwgIm11bHRpIjogRmFsc2UsCiAgICAgICAgICAg'
            'ICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIm9wdGlvbnMiOiBbKGEsIGIpIGZvciBhLCBiIGluIEJVREdFVF9DSElQU119'
            'KQogICAgcmV0dXJuICgiIiwgTm9uZSkKCgpkZWYgYXBwbHlfY2hvaWNlKHN0YXRlLCBmaWVsZCwgdmFsdWVzLCBraW5kX29mKToK'
            'ICAgICIiIkFwcGx5IGEgdGFwcGVkIGNoaXAgYW5zd2VyLiB2YWx1ZXM6IGxpc3Qgb2Ygc3RyaW5ncy4gJ3NraXAnIG1hcmtzIHRo'
            'ZQogICAgcXVlc3Rpb24gYXMgYW5zd2VyZWQgd2l0aCBub3RoaW5nLiIiIgogICAgdmFsdWVzID0gW3N0cih2KSBmb3IgdiBpbiAo'
            'dmFsdWVzIG9yIFtdKV1bOjhdCiAgICBpZiBub3QgdmFsdWVzIG9yIHZhbHVlcyA9PSBbInNraXAiXToKICAgICAgICBfbWFyayhz'
            'dGF0ZSwgZmllbGQpCiAgICAgICAgcmV0dXJuIFRydWUKICAgIGlmIGZpZWxkID09ICJza2luX3R5cGUiIGFuZCB2YWx1ZXNbMF0g'
            'aW4gU0tJTl9UWVBFUyArICgidW5rbm93biIsKToKICAgICAgICBzdGF0ZVsic2tpbl90eXBlIl0gPSB2YWx1ZXNbMF0KICAgIGVs'
            'aWYgZmllbGQgPT0gImNvbmNlcm5zIjoKICAgICAgICBzdGF0ZVsiY29uY2VybnMiXSA9IFt2IGZvciB2IGluIHZhbHVlcyBpZiB2'
            'IGluIENPTkNFUk5TXQogICAgZWxpZiBmaWVsZCA9PSAic2NlbnQiOgogICAgICAgIHN0YXRlWyJzY2VudCJdID0gW3YgZm9yIHYg'
            'aW4gdmFsdWVzIGlmIHYgaW4gU0NFTlRTXQogICAgZWxpZiBmaWVsZCA9PSAib2NjYXNpb24iIGFuZCB2YWx1ZXNbMF0gaW4gT0ND'
            'QVNJT05TOgogICAgICAgIHN0YXRlWyJvY2Nhc2lvbiJdID0gdmFsdWVzWzBdCiAgICBlbGlmIGZpZWxkID09ICJidWRnZXQiIGFu'
            'ZCB2YWx1ZXNbMF0gaW4gQlVER0VUX1JBTkdFUzoKICAgICAgICBzdGF0ZVsiYnVkZ2V0X21pbiJdLCBzdGF0ZVsiYnVkZ2V0X21h'
            'eCJdID0gQlVER0VUX1JBTkdFU1t2YWx1ZXNbMF1dCiAgICBlbHNlOgogICAgICAgIHJldHVybiBGYWxzZQogICAgX21hcmsoc3Rh'
            'dGUsIGZpZWxkKQogICAgcmV0dXJuIFRydWUKCgpkZWYgc3VtbWFyeV9saW5lKHN0YXRlLCBsYWJlbCk6CiAgICBiaXRzID0gW2xh'
            'YmVsLmxvd2VyKCldCiAgICBpZiBzdGF0ZS5nZXQoInNraW5fdHlwZSIpIGFuZCBzdGF0ZVsic2tpbl90eXBlIl0gIT0gInVua25v'
            'd24iOgogICAgICAgIGJpdHMuYXBwZW5kKCIlcyBza2luIiAlIHN0YXRlWyJza2luX3R5cGUiXSkKICAgIGlmIHN0YXRlLmdldCgi'
            'Y29uY2VybnMiKToKICAgICAgICBiaXRzLmFwcGVuZCgiZm9yICIgKyAiLCAiLmpvaW4oc3RhdGVbImNvbmNlcm5zIl0pKQogICAg'
            'aWYgc3RhdGUuZ2V0KCJidWRnZXRfbWF4Iik6CiAgICAgICAgYml0cy5hcHBlbmQoInVwIHRvIFx1MjBBNns6LC4wZn0iLmZvcm1h'
            'dChzdGF0ZVsiYnVkZ2V0X21heCJdKSkKICAgIHJldHVybiAiLCAiLmpvaW4oYml0cykKCgojIC0tLS0tLS0tLS0tLS0tLS0tLS0t'
            'LS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0gbGltaXRlcnMKX2hpdHMgPSB7fQpfYWlfaXAgPSB7'
            'fQpfYWlfZ2xvYmFsID0geyJkYXkiOiBOb25lLCAibiI6IDB9CgoKZGVmIGNoYXRfcmF0ZV9saW1pdGVkKGNsaWVudF9pZCwgbGlt'
            'aXQ9NjAsIHdpbmRvdz02MDAsIG5vdz1Ob25lKToKICAgIG5vdyA9IHRpbWUudGltZSgpIGlmIG5vdyBpcyBOb25lIGVsc2Ugbm93'
            'CiAgICBsc3QgPSBbdCBmb3IgdCBpbiBfaGl0cy5nZXQoY2xpZW50X2lkLCBbXSkgaWYgbm93IC0gdCA8IHdpbmRvd10KICAgIGlm'
            'IGxlbihsc3QpID49IGxpbWl0OgogICAgICAgIF9oaXRzW2NsaWVudF9pZF0gPSBsc3QKICAgICAgICByZXR1cm4gVHJ1ZQogICAg'
            'bHN0LmFwcGVuZChub3cpCiAgICBfaGl0c1tjbGllbnRfaWRdID0gbHN0CiAgICBpZiBsZW4oX2hpdHMpID4gNTAwMDogICMga2Vl'
            'cCBtZW1vcnkgYm91bmRlZAogICAgICAgIGZvciBrIGluIGxpc3QoX2hpdHMpWzoxMDAwXToKICAgICAgICAgICAgX2hpdHMucG9w'
            'KGssIE5vbmUpCiAgICByZXR1cm4gRmFsc2UKCgpkZWYgX3RvZGF5KG5vdz1Ob25lKToKICAgIHJldHVybiBkYXRldGltZS5kYXRl'
            'dGltZS51dGNmcm9tdGltZXN0YW1wKHRpbWUudGltZSgpIGlmIG5vdyBpcyBOb25lIGVsc2Ugbm93KS5zdHJmdGltZSgiJVktJW0t'
            'JWQiKQoKCmRlZiBhaV9hbGxvd2VkKGNsaWVudF9pZCwgcGVyX3Zpc2l0b3IsIGRhaWx5X2NhcCwgbm93PU5vbmUpOgogICAgIiIi'
            'VHJ1ZSBpZiB0aGlzIHZpc2l0b3IgYW5kIHRoZSB3aG9sZSBzaG9wIHN0aWxsIGhhdmUgZnJlZSBBSSBjYWxscyB0b2RheS4KICAg'
            'IENvdW50aW5nIGhhcHBlbnMgaW4gYWlfc3BlbmQoKTsgdGhpcyBvbmx5IGNoZWNrcy4iIiIKICAgIGRheSA9IF90b2RheShub3cp'
            'CiAgICBpZiBfYWlfZ2xvYmFsWyJkYXkiXSAhPSBkYXk6CiAgICAgICAgX2FpX2dsb2JhbFsiZGF5Il0sIF9haV9nbG9iYWxbIm4i'
            'XSA9IGRheSwgMAogICAgICAgIF9haV9pcC5jbGVhcigpCiAgICBpZiBfYWlfZ2xvYmFsWyJuIl0gPj0gZGFpbHlfY2FwOgogICAg'
            'ICAgIHJldHVybiBGYWxzZQogICAgcmV0dXJuIF9haV9pcC5nZXQoY2xpZW50X2lkLCAwKSA8IHBlcl92aXNpdG9yCgoKZGVmIGFp'
            'X3NwZW5kKGNsaWVudF9pZCwgbm93PU5vbmUpOgogICAgZGF5ID0gX3RvZGF5KG5vdykKICAgIGlmIF9haV9nbG9iYWxbImRheSJd'
            'ICE9IGRheToKICAgICAgICBfYWlfZ2xvYmFsWyJkYXkiXSwgX2FpX2dsb2JhbFsibiJdID0gZGF5LCAwCiAgICAgICAgX2FpX2lw'
            'LmNsZWFyKCkKICAgIF9haV9nbG9iYWxbIm4iXSArPSAxCiAgICBfYWlfaXBbY2xpZW50X2lkXSA9IF9haV9pcC5nZXQoY2xpZW50'
            'X2lkLCAwKSArIDEK'
        ),
    },
    'advisor_gemini.py': {
        "sha256": '4071e318fda91a12e601f5a5f57b29d48c05360dda21858cd8d08c299cd0624e',
        "b64": (
            'IiIiT3B0aW9uYWwgR29vZ2xlIEdlbWluaSBoZWxwZXIgZm9yIHRoZSBCZWF1dHkgQWR2aXNvciAoc2VydmVyIHNpZGUgb25seSku'
            'CgpEZXNpZ24gcnVsZXMKLSBHRU1JTklfQVBJX0tFWSBpcyBPUFRJT05BTCBhbmQgcmVhZCBvbmx5IGZyb20gdGhlIGVudmlyb25t'
            'ZW50LiBXaXRoIG5vIGtleSwKICBldmVyeSBmdW5jdGlvbiByZXR1cm5zIE5vbmUgYW5kIHRoZSBhZHZpc29yIHJ1bnMgb24gaXRz'
            'IGZyZWUgYnVpbHQtaW4gbG9naWMuCi0gUGxhaW4gUkVTVCBvdmVyIGh0dHB4IChhbHJlYWR5IGEgZGVwZW5kZW5jeSkuIE5vIGV4'
            'dHJhIHBhY2thZ2UgdG8gaW5zdGFsbC4KLSBOb3RoaW5nIGhlcmUgcmFpc2VzLiBBbnkgZXJyb3IsIHRpbWVvdXQgb3IgbGltaXQg'
            'aGl0IHJldHVybnMgTm9uZS4KLSBBZnRlciBhIHJhdGUtbGltaXQgKEhUVFAgNDI5KSBvciBzZXJ2ZXIgZXJyb3IgdGhlIGhlbHBl'
            'ciBzd2l0Y2hlcyBpdHNlbGYgb2ZmCiAgZm9yIGEgd2hpbGUsIHNvIHRoZSBzaG9wIGlzIG5vdCBoYW1tZXJpbmcgR29vZ2xlIGFu'
            'ZCBjdXN0b21lcnMgYXJlIG5vdCBrZXB0CiAgd2FpdGluZy4gVGhlIGFkdmlzb3Igc2ltcGx5IGNhcnJpZXMgb24gd2l0aG91dCB0'
            'aGUgQUkuCi0gVGhlIEFJIG5ldmVyIGNob29zZXMgcHJvZHVjdHMuIEl0ICgxKSByZWFkcyBhIHR5cGVkIG1lc3NhZ2UgaW50byBh'
            'IGZldyBhbGxvd2VkCiAgZmllbGRzIGFuZCAoMikgcmV3b3JkcyB0aGUgcmVhc29ucyBmb3IgcHJvZHVjdHMgdGhlIGVuZ2luZSBh'
            'bHJlYWR5IHBpY2tlZC4KICBCb3RoIG91dHB1dHMgYXJlIHZhbGlkYXRlZCBhZ2FpbnN0IGFsbG93LWxpc3RzIGJ5IHRoZSBjYWxs'
            'ZXIuCi0gTm8gcGhvdG9zIGFyZSBzZW50LiBPbmx5IHRoZSBjdXN0b21lcidzIHR5cGVkIHNlbnRlbmNlIChtYXggMzAwIGNoYXJh'
            'Y3RlcnMpCiAgYW5kIG91ciBvd24gcHJvZHVjdCBmYWN0cyBsZWF2ZSB0aGUgc2VydmVyLgoiIiIKaW1wb3J0IGpzb24KaW1wb3J0'
            'IG9zCmltcG9ydCByZQppbXBvcnQgdGltZQoKaW1wb3J0IGFkdmlzb3JfZXh0cmFzIGFzIGV4dHJhcwoKREVGQVVMVF9NT0RFTCA9'
            'ICJnZW1pbmktMy41LWZsYXNoLWxpdGUiCkVORFBPSU5UID0gImh0dHBzOi8vZ2VuZXJhdGl2ZWxhbmd1YWdlLmdvb2dsZWFwaXMu'
            'Y29tL3YxYmV0YS9tb2RlbHMvJXM6Z2VuZXJhdGVDb250ZW50IgpfYmxvY2tlZF91bnRpbCA9IDAuMAoKCmRlZiBjb25maWd1cmVk'
            'KCk6CiAgICByZXR1cm4gYm9vbChvcy5lbnZpcm9uLmdldCgiR0VNSU5JX0FQSV9LRVkiLCAiIikuc3RyaXAoKSkKCgpkZWYgcGF1'
            'c2VkKG5vdz1Ob25lKToKICAgIHJldHVybiAodGltZS50aW1lKCkgaWYgbm93IGlzIE5vbmUgZWxzZSBub3cpIDwgX2Jsb2NrZWRf'
            'dW50aWwKCgpkZWYgX3BhdXNlKHNlY29uZHMsIG5vdz1Ob25lKToKICAgIGdsb2JhbCBfYmxvY2tlZF91bnRpbAogICAgX2Jsb2Nr'
            'ZWRfdW50aWwgPSAodGltZS50aW1lKCkgaWYgbm93IGlzIE5vbmUgZWxzZSBub3cpICsgc2Vjb25kcwoKCmRlZiBfcGFydHNfdGV4'
            'dChkYXRhKToKICAgIHRyeToKICAgICAgICBwYXJ0cyA9IGRhdGFbImNhbmRpZGF0ZXMiXVswXVsiY29udGVudCJdWyJwYXJ0cyJd'
            'CiAgICBleGNlcHQgKEtleUVycm9yLCBJbmRleEVycm9yLCBUeXBlRXJyb3IpOgogICAgICAgIHJldHVybiAiIgogICAgcmV0dXJu'
            'ICIiLmpvaW4ocC5nZXQoInRleHQiLCAiIikgZm9yIHAgaW4gcGFydHMgaWYgaXNpbnN0YW5jZShwLCBkaWN0KSBhbmQgbm90IHAu'
            'Z2V0KCJ0aG91Z2h0IikpCgoKYXN5bmMgZGVmIF9jYWxsKGh0dHB4X21vZHVsZSwgcHJvbXB0LCBzY2hlbWEsIHRpbWVvdXQ9OC4w'
            'KToKICAgICIiIlJldHVybiBwYXJzZWQgSlNPTiAoZGljdCkgb3IgTm9uZS4gTmV2ZXIgcmFpc2VzLiIiIgogICAga2V5ID0gb3Mu'
            'ZW52aXJvbi5nZXQoIkdFTUlOSV9BUElfS0VZIiwgIiIpLnN0cmlwKCkKICAgIGlmIG5vdCBrZXkgb3IgcGF1c2VkKCk6CiAgICAg'
            'ICAgcmV0dXJuIE5vbmUKICAgIG1vZGVsID0gb3MuZW52aXJvbi5nZXQoIkdFTUlOSV9NT0RFTCIsIERFRkFVTFRfTU9ERUwpLnN0'
            'cmlwKCkgb3IgREVGQVVMVF9NT0RFTAogICAgYm9keSA9IHsKICAgICAgICAiY29udGVudHMiOiBbeyJwYXJ0cyI6IFt7InRleHQi'
            'OiBwcm9tcHR9XX1dLAogICAgICAgICJnZW5lcmF0aW9uQ29uZmlnIjogewogICAgICAgICAgICAicmVzcG9uc2VGb3JtYXQiOiB7'
            'InRleHQiOiB7Im1pbWVUeXBlIjogImFwcGxpY2F0aW9uL2pzb24iLCAic2NoZW1hIjogc2NoZW1hfX0sCiAgICAgICAgfSwKICAg'
            'IH0KICAgIHRyeToKICAgICAgICBhc3luYyB3aXRoIGh0dHB4X21vZHVsZS5Bc3luY0NsaWVudCh0aW1lb3V0PXRpbWVvdXQpIGFz'
            'IGNsaWVudDoKICAgICAgICAgICAgciA9IGF3YWl0IGNsaWVudC5wb3N0KAogICAgICAgICAgICAgICAgRU5EUE9JTlQgJSBtb2Rl'
            'bCwKICAgICAgICAgICAgICAgIGhlYWRlcnM9eyJ4LWdvb2ctYXBpLWtleSI6IGtleSwgIkNvbnRlbnQtVHlwZSI6ICJhcHBsaWNh'
            'dGlvbi9qc29uIn0sCiAgICAgICAgICAgICAgICBqc29uPWJvZHksCiAgICAgICAgICAgICkKICAgICAgICBpZiByLnN0YXR1c19j'
            'b2RlID09IDQyOToKICAgICAgICAgICAgX3BhdXNlKDE1ICogNjApICAjIGZyZWUgZGFpbHkgb3IgcGVyLW1pbnV0ZSBsaW1pdCBy'
            'ZWFjaGVkCiAgICAgICAgICAgIHJldHVybiBOb25lCiAgICAgICAgaWYgci5zdGF0dXNfY29kZSA+PSA1MDA6CiAgICAgICAgICAg'
            'IF9wYXVzZSgyICogNjApCiAgICAgICAgICAgIHJldHVybiBOb25lCiAgICAgICAgaWYgci5zdGF0dXNfY29kZSA+PSA0MDA6CiAg'
            'ICAgICAgICAgIF9wYXVzZSg2MCkgICMgYmFkIGtleSAvIGJhZCByZXF1ZXN0OiBkbyBub3QgcmV0cnkgb24gZXZlcnkgbWVzc2Fn'
            'ZQogICAgICAgICAgICByZXR1cm4gTm9uZQogICAgICAgIHRleHQgPSBfcGFydHNfdGV4dChyLmpzb24oKSkKICAgICAgICB0ZXh0'
            'ID0gcmUuc3ViKHIiXmBgYCg/Ompzb24pP3xgYGAkIiwgIiIsIHRleHQuc3RyaXAoKSwgZmxhZ3M9cmUuTSkuc3RyaXAoKQogICAg'
            'ICAgIGRhdGEgPSBqc29uLmxvYWRzKHRleHQpIGlmIHRleHQgZWxzZSBOb25lCiAgICAgICAgcmV0dXJuIGRhdGEgaWYgaXNpbnN0'
            'YW5jZShkYXRhLCBkaWN0KSBlbHNlIE5vbmUKICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgcmV0dXJuIE5vbmUKCgojIC0t'
            'LS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0gMS4gcmVhZCBhIHR5cGVkIG1lc3Nh'
            'Z2UKZGVmIF9leHRyYWN0X3NjaGVtYShzbHVncywgc2tpbl90eXBlcywgY29uY2VybnMsIHNjZW50cywgb2NjYXNpb25zKToKICAg'
            'IHJldHVybiB7CiAgICAgICAgInR5cGUiOiAib2JqZWN0IiwKICAgICAgICAicHJvcGVydGllcyI6IHsKICAgICAgICAgICAgImNh'
            'dGVnb3J5IjogeyJ0eXBlIjogInN0cmluZyIsICJlbnVtIjogbGlzdChzbHVncykgKyBbIm5vbmUiXX0sCiAgICAgICAgICAgICJz'
            'a2luX3R5cGUiOiB7InR5cGUiOiAic3RyaW5nIiwgImVudW0iOiBsaXN0KHNraW5fdHlwZXMpICsgWyJ1bmtub3duIiwgIm5vbmUi'
            'XX0sCiAgICAgICAgICAgICJjb25jZXJucyI6IHsidHlwZSI6ICJhcnJheSIsICJpdGVtcyI6IHsidHlwZSI6ICJzdHJpbmciLCAi'
            'ZW51bSI6IGxpc3QoY29uY2VybnMpfX0sCiAgICAgICAgICAgICJzY2VudCI6IHsidHlwZSI6ICJhcnJheSIsICJpdGVtcyI6IHsi'
            'dHlwZSI6ICJzdHJpbmciLCAiZW51bSI6IGxpc3Qoc2NlbnRzKX19LAogICAgICAgICAgICAib2NjYXNpb24iOiB7InR5cGUiOiAi'
            'c3RyaW5nIiwgImVudW0iOiBsaXN0KG9jY2FzaW9ucykgKyBbIm5vbmUiXX0sCiAgICAgICAgICAgICJidWRnZXRfbWF4X25haXJh'
            'IjogeyJ0eXBlIjogWyJpbnRlZ2VyIiwgIm51bGwiXX0sCiAgICAgICAgICAgICJtZWRpY2FsX2NvbmNlcm4iOiB7InR5cGUiOiAi'
            'Ym9vbGVhbiJ9LAogICAgICAgIH0sCiAgICAgICAgInJlcXVpcmVkIjogWyJjYXRlZ29yeSIsICJza2luX3R5cGUiLCAiY29uY2Vy'
            'bnMiLCAic2NlbnQiLCAib2NjYXNpb24iLAogICAgICAgICAgICAgICAgICAgICAiYnVkZ2V0X21heF9uYWlyYSIsICJtZWRpY2Fs'
            'X2NvbmNlcm4iXSwKICAgIH0KCgpkZWYgYnVpbGRfZXh0cmFjdF9wcm9tcHQobWVzc2FnZSwgY2F0ZWdvcmllcyk6CiAgICBsYWJl'
            'bHMgPSAiLCAiLmpvaW4oIiVzICglcykiICUgKGNbInNsdWciXSwgY1sibGFiZWwiXSkgZm9yIGMgaW4gY2F0ZWdvcmllcykKICAg'
            'IHJldHVybiAoCiAgICAgICAgIllvdSByZWFkIG9uZSBtZXNzYWdlIGZyb20gYSBjdXN0b21lciBvZiBhIGNvc21ldGljcyBzaG9w'
            'IGFuZCBmaWxsIGEgZm9ybS4gIgogICAgICAgICJVc2UgT05MWSB3aGF0IHRoZSBjdXN0b21lciBjbGVhcmx5IHNhaWQuIElmIHNv'
            'bWV0aGluZyB3YXMgbm90IHNhaWQsIHVzZSAnbm9uZScsICIKICAgICAgICAiJ3Vua25vd24nIG9yIGFuIGVtcHR5IGxpc3QuIE5l'
            'dmVyIGd1ZXNzLiBOZXZlciBkaWFnbm9zZS4gIgogICAgICAgICJTZXQgbWVkaWNhbF9jb25jZXJuIHRvIHRydWUgb25seSBpZiB0'
            'aGV5IGRlc2NyaWJlIGFuIGluZmVjdGlvbiwgcmFzaCwgZWN6ZW1hLCAiCiAgICAgICAgIndvdW5kLCBzZXZlcmUgcGFpbiBvciBh'
            'IHNpbWlsYXIgbWVkaWNhbCBwcm9ibGVtLiAiCiAgICAgICAgImNhdGVnb3J5IG11c3QgYmUgb25lIG9mIHRoZXNlIHNob3AgY2F0'
            'ZWdvcmllcyBvciAnbm9uZSc6ICVzLiAiCiAgICAgICAgImJ1ZGdldF9tYXhfbmFpcmEgaXMgYSBudW1iZXIgaW4gTmlnZXJpYW4g'
            'bmFpcmEgb3IgbnVsbC5cblxuIgogICAgICAgICJDdXN0b21lciBtZXNzYWdlOiAlcyIgJSAobGFiZWxzLCBqc29uLmR1bXBzKHN0'
            'cihtZXNzYWdlKVs6MzAwXSkpCiAgICApCgoKYXN5bmMgZGVmIHJlYWRfbWVzc2FnZShodHRweF9tb2R1bGUsIG1lc3NhZ2UsIGNh'
            'dGVnb3JpZXMsIHNraW5fdHlwZXMsIGNvbmNlcm5zLCBzY2VudHMsIG9jY2FzaW9ucyk6CiAgICAiIiJSZXR1cm4gYSByZWFkaW5n'
            'IGRpY3QgKHNhbWUgc2hhcGUgYXMgYWR2aXNvcl9mbG93LnJlYWRfdGV4dCkgb3IgTm9uZS4iIiIKICAgIGlmIG5vdCBjYXRlZ29y'
            'aWVzOgogICAgICAgIHJldHVybiBOb25lCiAgICBzbHVncyA9IFtjWyJzbHVnIl0gZm9yIGMgaW4gY2F0ZWdvcmllc10KICAgIGRh'
            'dGEgPSBhd2FpdCBfY2FsbChodHRweF9tb2R1bGUsIGJ1aWxkX2V4dHJhY3RfcHJvbXB0KG1lc3NhZ2UsIGNhdGVnb3JpZXMpLAog'
            'ICAgICAgICAgICAgICAgICAgICAgIF9leHRyYWN0X3NjaGVtYShzbHVncywgc2tpbl90eXBlcywgY29uY2VybnMsIHNjZW50cywg'
            'b2NjYXNpb25zKSkKICAgIGlmIG5vdCBkYXRhOgogICAgICAgIHJldHVybiBOb25lCiAgICBvdXQgPSB7fQogICAgaWYgZGF0YS5n'
            'ZXQoImNhdGVnb3J5IikgaW4gc2x1Z3M6CiAgICAgICAgb3V0WyJjYXRlZ29yeSJdID0gZGF0YVsiY2F0ZWdvcnkiXQogICAgaWYg'
            'ZGF0YS5nZXQoInNraW5fdHlwZSIpIGluIHNraW5fdHlwZXMgKyAoInVua25vd24iLCk6CiAgICAgICAgb3V0WyJza2luX3R5cGUi'
            'XSA9IGRhdGFbInNraW5fdHlwZSJdCiAgICBjcyA9IFtjIGZvciBjIGluIChkYXRhLmdldCgiY29uY2VybnMiKSBvciBbXSkgaWYg'
            'YyBpbiBjb25jZXJuc10KICAgIGlmIGNzOgogICAgICAgIG91dFsiY29uY2VybnMiXSA9IGNzWzo4XQogICAgc2MgPSBbYyBmb3Ig'
            'YyBpbiAoZGF0YS5nZXQoInNjZW50Iikgb3IgW10pIGlmIGMgaW4gc2NlbnRzXQogICAgaWYgc2M6CiAgICAgICAgb3V0WyJzY2Vu'
            'dCJdID0gc2NbOjRdCiAgICBpZiBkYXRhLmdldCgib2NjYXNpb24iKSBpbiBvY2Nhc2lvbnM6CiAgICAgICAgb3V0WyJvY2Nhc2lv'
            'biJdID0gZGF0YVsib2NjYXNpb24iXQogICAgYiA9IGRhdGEuZ2V0KCJidWRnZXRfbWF4X25haXJhIikKICAgIGlmIGlzaW5zdGFu'
            'Y2UoYiwgKGludCwgZmxvYXQpKSBhbmQgbm90IGlzaW5zdGFuY2UoYiwgYm9vbCkgYW5kIDEwMCA8PSBiIDw9IDEwXzAwMF8wMDA6'
            'CiAgICAgICAgb3V0WyJidWRnZXQiXSA9ICgwLCBmbG9hdChiKSkKICAgIGlmIGRhdGEuZ2V0KCJtZWRpY2FsX2NvbmNlcm4iKSBp'
            'cyBUcnVlOgogICAgICAgIG91dFsibWVkaWNhbCJdID0gVHJ1ZQogICAgcmV0dXJuIG91dAoKCiMgLS0tLS0tLS0tLS0tLS0tLS0t'
            'LS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLSAyLiByZXdvcmQgcmVhc29ucwpfUkVBU09OX1NDSEVNQSA9IHsK'
            'ICAgICJ0eXBlIjogIm9iamVjdCIsCiAgICAicHJvcGVydGllcyI6IHsKICAgICAgICAiaXRlbXMiOiB7CiAgICAgICAgICAgICJ0'
            'eXBlIjogImFycmF5IiwKICAgICAgICAgICAgIml0ZW1zIjogewogICAgICAgICAgICAgICAgInR5cGUiOiAib2JqZWN0IiwKICAg'
            'ICAgICAgICAgICAgICJwcm9wZXJ0aWVzIjogeyJwcm9kdWN0X2lkIjogeyJ0eXBlIjogInN0cmluZyJ9LCAicmVhc29uIjogeyJ0'
            'eXBlIjogInN0cmluZyJ9fSwKICAgICAgICAgICAgICAgICJyZXF1aXJlZCI6IFsicHJvZHVjdF9pZCIsICJyZWFzb24iXSwKICAg'
            'ICAgICAgICAgfSwKICAgICAgICB9CiAgICB9LAogICAgInJlcXVpcmVkIjogWyJpdGVtcyJdLAp9CgoKYXN5bmMgZGVmIHJld29y'
            'ZF9yZWFzb25zKGh0dHB4X21vZHVsZSwgc2hvcnRsaXN0LCBhbnN3ZXJzKToKICAgICIiIlJldHVybiBhIEpTT04gc3RyaW5nIHsi'
            'aXRlbXMiOlt7cHJvZHVjdF9pZCwgcmVhc29ufV19IG9yIE5vbmUuIFRoZQogICAgY2FsbGVyIG11c3QgdmFsaWRhdGUgaXQgd2l0'
            'aCBhZHZpc29yX2VuZ2luZS52YWxpZGF0ZV9sbG1fb3V0cHV0LiIiIgogICAgaWYgbm90IHNob3J0bGlzdDoKICAgICAgICByZXR1'
            'cm4gTm9uZQogICAgc3lzdGVtLCB1c2VyID0gZXh0cmFzLmJ1aWxkX2xsbV9wcm9tcHQoc2hvcnRsaXN0LCBhbnN3ZXJzKQogICAg'
            'ZGF0YSA9IGF3YWl0IF9jYWxsKGh0dHB4X21vZHVsZSwgc3lzdGVtICsgIlxuXG4iICsgdXNlciwgX1JFQVNPTl9TQ0hFTUEpCiAg'
            'ICByZXR1cm4ganNvbi5kdW1wcyhkYXRhKSBpZiBkYXRhIGVsc2UgTm9uZQo='
        ),
    },
    'advisor_turn.py': {
        "sha256": 'c1ef07dcd67f3f95b85542e5ef9ae95c589b27cb69a01ff4112e1070b22ceb02',
        "b64": (
            'IiIiT25lIEJlYXV0eSBBZHZpc29yIGNoYXQgdHVybi4gTm8gd2ViIGZyYW1ld29yayBpbiBoZXJlLCBzbyBpdCBjYW4gYmUgdGVz'
            'dGVkCm9uIGl0cyBvd24uIFRoZSByb3V0ZXIgKHJvdXRlcnMvYWR2aXNvcl9jaGF0LnB5KSBvbmx5IGFkZHMgSFRUUCBhcm91bmQg'
            'aXQuCgpJbnB1dCA6IHRoZSBicm93c2VyJ3MgcmVxdWVzdCBib2R5LCB0aGUgdmlzaXRvcidzIHNlc3Npb24gZGljdCwgdGhlIGxp'
            'dmUKICAgICAgICBwcm9kdWN0IGxpc3QgKyBzdG9jaywgYW5kIHRoZSBhZHZpc29yIGNhdGFsb2cgZW50cmllcy4KT3V0cHV0OiBh'
            'IEpTT04tcmVhZHkgZGljdDogbWVzc2FnZXMsIG9wdGlvbmFsIGNoaXBzLCBvcHRpb25hbCBwcm9kdWN0IGNhcmRzLgoKU2FmZXR5'
            'IGJ5IGNvbnN0cnVjdGlvbgotIFByb2R1Y3RzIHNob3duIGFyZSBvbmx5IHRob3NlIHBpY2tlZCBieSBhZHZpc29yX2VuZ2luZSAo'
            'ZW5hYmxlZCwgYWN0aXZlLAogIHByaWNlZCwgaW4gc3RvY2spLiBUaGUgQUkgY2Fubm90IGFkZCBvciBjaGFuZ2UgYSBwcm9kdWN0'
            'LgotIFRoZSBBSSBpcyBvcHRpb25hbCBhbmQgbGltaXRlZCAocGVyIHZpc2l0b3IgYW5kIHNob3Atd2lkZSBlYWNoIGRheSkuCi0g'
            'VGV4dHMgc2hvd24gdG8gdGhlIGN1c3RvbWVyIGNvbWUgZnJvbSB0aGlzIGZpbGUgb3IgZnJvbSB0aGUgdmFsaWRhdGVkCiAgcGVy'
            'LXByb2R1Y3QgcmVhc29ucywgbmV2ZXIgZnJvbSBmcmVlIEFJIHByb3NlLgoiIiIKaW1wb3J0IG9zCgppbXBvcnQgYWR2aXNvcl9l'
            'bmdpbmUgYXMgZW5naW5lCmltcG9ydCBhZHZpc29yX2Zsb3cgYXMgZmxvdwppbXBvcnQgYWR2aXNvcl9nZW1pbmkgYXMgZ2VtaW5p'
            'CgpHUkVFVElORyA9ICgKICAgICJIaSwgSSBhbSB0aGUgTmljayBDb3NtZXRpY3MgQmVhdXR5IEFkdmlzb3IuIFRlbGwgbWUgd2hh'
            'dCB5b3UgYXJlIGxvb2tpbmcgZm9yLCAiCiAgICAib3IgcGljayBhIGNhdGVnb3J5IGJlbG93LiBJIG9ubHkgc3VnZ2VzdCBwcm9k'
            'dWN0cyB0aGF0IHdlIGhhdmUgaW4gc3RvY2sgcmlnaHQgbm93LiIKKQoKCmRlZiBfaW50X2VudihuYW1lLCBkZWZhdWx0KToKICAg'
            'IHRyeToKICAgICAgICByZXR1cm4gbWF4KDAsIGludChvcy5lbnZpcm9uLmdldChuYW1lLCBkZWZhdWx0KSkpCiAgICBleGNlcHQg'
            'KFR5cGVFcnJvciwgVmFsdWVFcnJvcik6CiAgICAgICAgcmV0dXJuIGRlZmF1bHQKCgpkZWYgX2NoaXBzKHNwZWMsIHNraXBwYWJs'
            'ZT1GYWxzZSk6CiAgICBpZiBub3Qgc3BlYzoKICAgICAgICByZXR1cm4gTm9uZQogICAgcmV0dXJuIHsKICAgICAgICAiZmllbGQi'
            'OiBzcGVjWyJmaWVsZCJdLAogICAgICAgICJtdWx0aSI6IHNwZWNbIm11bHRpIl0sCiAgICAgICAgInNraXBwYWJsZSI6IHNraXBw'
            'YWJsZSwKICAgICAgICAib3B0aW9ucyI6IFt7ImxhYmVsIjogYSwgInZhbHVlIjogYn0gZm9yIGEsIGIgaW4gc3BlY1sib3B0aW9u'
            'cyJdXSwKICAgIH0KCgpkZWYgX2NhdGVnb3J5X2NoaXBzKGNhdHMpOgogICAgcmV0dXJuIHsKICAgICAgICAiZmllbGQiOiAiY2F0'
            'ZWdvcnkiLCAibXVsdGkiOiBGYWxzZSwgInNraXBwYWJsZSI6IEZhbHNlLAogICAgICAgICJvcHRpb25zIjogW3sibGFiZWwiOiBj'
            'WyJsYWJlbCJdLCAidmFsdWUiOiBjWyJzbHVnIl19IGZvciBjIGluIGNhdHNdLAogICAgfQoKCmRlZiBfcmVwbHkobWVzc2FnZXMs'
            'IGNoaXBzPU5vbmUsIGl0ZW1zPU5vbmUsIGRpc2NsYWltZXI9RmFsc2UsIGFpX3VzZWQ9RmFsc2UpOgogICAgcmV0dXJuIHsib2si'
            'OiBUcnVlLCAibWVzc2FnZXMiOiBtZXNzYWdlcywgImNoaXBzIjogY2hpcHMsICJpdGVtcyI6IGl0ZW1zIG9yIFtdLAogICAgICAg'
            'ICAgICAiZGlzY2xhaW1lciI6IGRpc2NsYWltZXIsICJhaV91c2VkIjogYWlfdXNlZH0KCgphc3luYyBkZWYgaGFuZGxlKGJvZHks'
            'IHNlc3Npb24sIHZpc2l0b3JfaWQsIHByb2R1Y3RzLCBzdG9jaywgbWV0YSwgaHR0cHhfbW9kdWxlKToKICAgIGJvZHkgPSBib2R5'
            'IGlmIGlzaW5zdGFuY2UoYm9keSwgZGljdCkgZWxzZSB7fQogICAgYWN0aW9uID0gc3RyKGJvZHkuZ2V0KCJhY3Rpb24iKSBvciAi'
            'IikKICAgIGNhdHMgPSBmbG93LmF2YWlsYWJsZV9jYXRlZ29yaWVzKHByb2R1Y3RzLCBzdG9jaywgbWV0YSkKICAgIGJ5X3NsdWcg'
            'PSB7Y1sic2x1ZyJdOiBjIGZvciBjIGluIGNhdHN9CiAgICBzdGF0ZSA9IGZsb3cuY2xlYW5fc3RhdGUoc2Vzc2lvbi5nZXQoImFk'
            'dl9jaGF0IiksIHNldChieV9zbHVnKSkKICAgIGtpbmRfb2YgPSBsYW1iZGEgc2x1ZzogYnlfc2x1Z1tzbHVnXVsia2luZCJdIGlm'
            'IHNsdWcgaW4gYnlfc2x1ZyBlbHNlICJvdGhlciIgICMgbm9xYTogRTczMQoKICAgIGlmIG5vdCBjYXRzOgogICAgICAgIHJldHVy'
            'biBfcmVwbHkoWyJPdXIgYWR2aXNvciBoYXMgbm90aGluZyBpbiBzdG9jayB0byBzdWdnZXN0IHJpZ2h0IG5vdy4gIgogICAgICAg'
            'ICAgICAgICAgICAgICAgICJQbGVhc2UgYnJvd3NlIHRoZSBzaG9wLCBvciBhc2sgdXMgaW4gc3RvcmUuIl0pCgogICAgaWYgYWN0'
            'aW9uIGluICgic3RhcnQiLCAicmVzZXQiKToKICAgICAgICBzZXNzaW9uWyJhZHZfY2hhdCJdID0gZmxvdy5uZXdfc3RhdGUoKQog'
            'ICAgICAgIHJldHVybiBfcmVwbHkoW0dSRUVUSU5HXSwgY2hpcHM9X2NhdGVnb3J5X2NoaXBzKGNhdHMpKQoKICAgIG1lc3NhZ2Vz'
            'ID0gW10KICAgIHNob3dfbm93ID0gRmFsc2UKICAgIGFpX3VzZWQgPSBGYWxzZQogICAgbWVkaWNhbCA9IEZhbHNlCgogICAgaWYg'
            'YWN0aW9uID09ICJjaG9vc2UiOgogICAgICAgIGZpZWxkID0gc3RyKGJvZHkuZ2V0KCJmaWVsZCIpIG9yICIiKQogICAgICAgIHZh'
            'bHVlcyA9IGJvZHkuZ2V0KCJ2YWx1ZXMiKQogICAgICAgIGlmIG5vdCBpc2luc3RhbmNlKHZhbHVlcywgbGlzdCk6CiAgICAgICAg'
            'ICAgIHZhbHVlcyA9IFtib2R5LmdldCgidmFsdWUiKV0gaWYgYm9keS5nZXQoInZhbHVlIikgaXMgbm90IE5vbmUgZWxzZSBbXQog'
            'ICAgICAgIHZhbHVlcyA9IFtzdHIodikgZm9yIHYgaW4gdmFsdWVzXVs6OF0KICAgICAgICBpZiBmaWVsZCA9PSAibmV4dCI6CiAg'
            'ICAgICAgICAgIGlmIHZhbHVlcyBhbmQgdmFsdWVzWzBdID09ICJyZXN0YXJ0IjoKICAgICAgICAgICAgICAgIHNlc3Npb25bImFk'
            'dl9jaGF0Il0gPSBmbG93Lm5ld19zdGF0ZSgpCiAgICAgICAgICAgICAgICByZXR1cm4gX3JlcGx5KFtHUkVFVElOR10sIGNoaXBz'
            'PV9jYXRlZ29yeV9jaGlwcyhjYXRzKSkKICAgICAgICAgICAgc3RhdGUgPSBmbG93Lm5ld19zdGF0ZSgpCiAgICAgICAgICAgIHNl'
            'c3Npb25bImFkdl9jaGF0Il0gPSBzdGF0ZQogICAgICAgICAgICByZXR1cm4gX3JlcGx5KFsiV2hpY2ggY2F0ZWdvcnkgd291bGQg'
            'eW91IGxpa2UgdG8gbG9vayBhdD8iXSwgY2hpcHM9X2NhdGVnb3J5X2NoaXBzKGNhdHMpKQogICAgICAgIGlmIGZpZWxkID09ICJj'
            'YXRlZ29yeSI6CiAgICAgICAgICAgIHNsdWcgPSB2YWx1ZXNbMF0gaWYgdmFsdWVzIGVsc2UgIiIKICAgICAgICAgICAgaWYgc2x1'
            'ZyBub3QgaW4gYnlfc2x1ZzoKICAgICAgICAgICAgICAgIHJldHVybiBfcmVwbHkoWyJQbGVhc2UgcGljayBvbmUgb2YgdGhlIGNh'
            'dGVnb3JpZXMgYmVsb3cuIl0sIGNoaXBzPV9jYXRlZ29yeV9jaGlwcyhjYXRzKSkKICAgICAgICAgICAgc3RhdGVbImNhdGVnb3J5'
            'Il0gPSBzbHVnCiAgICAgICAgICAgIG1lc3NhZ2VzLmFwcGVuZCgiR29vZCBjaG9pY2UuIikKICAgICAgICBlbHNlOgogICAgICAg'
            'ICAgICBpZiBub3Qgc3RhdGVbImNhdGVnb3J5Il06CiAgICAgICAgICAgICAgICByZXR1cm4gX3JlcGx5KFsiTGV0IHVzIHN0YXJ0'
            'IHdpdGggYSBjYXRlZ29yeS4iXSwgY2hpcHM9X2NhdGVnb3J5X2NoaXBzKGNhdHMpKQogICAgICAgICAgICBpZiBub3QgZmxvdy5h'
            'cHBseV9jaG9pY2Uoc3RhdGUsIGZpZWxkLCB2YWx1ZXMsIGtpbmRfb2YpOgogICAgICAgICAgICAgICAgbmYgPSBmbG93Lm5leHRf'
            'ZmllbGQoc3RhdGUsIGtpbmRfb2Yoc3RhdGVbImNhdGVnb3J5Il0pKQogICAgICAgICAgICAgICAgdGV4dCwgc3BlYyA9IGZsb3cu'
            'cXVlc3Rpb25fZm9yKG5mLCBzdGF0ZSkgaWYgbmYgZWxzZSAoIiIsIE5vbmUpCiAgICAgICAgICAgICAgICByZXR1cm4gX3JlcGx5'
            'KFsiSSBkaWQgbm90IHVuZGVyc3RhbmQgdGhhdCBjaG9pY2UuICIgKyB0ZXh0XSwgY2hpcHM9X2NoaXBzKHNwZWMsIFRydWUpKQog'
            'ICAgZWxpZiBhY3Rpb24gPT0gInRleHQiOgogICAgICAgIHRleHQgPSAiIi5qb2luKGNoIGZvciBjaCBpbiBzdHIoYm9keS5nZXQo'
            'InRleHQiKSBvciAiIikgaWYgY2guaXNwcmludGFibGUoKSlbOjMwMF0uc3RyaXAoKQogICAgICAgIGlmIG5vdCB0ZXh0OgogICAg'
            'ICAgICAgICByZXR1cm4gX3JlcGx5KFsiVHlwZSBhIG1lc3NhZ2UsIG9yIHBpY2sgb25lIG9mIHRoZSBvcHRpb25zLiJdLAogICAg'
            'ICAgICAgICAgICAgICAgICAgICAgIGNoaXBzPV9jYXRlZ29yeV9jaGlwcyhjYXRzKSBpZiBub3Qgc3RhdGVbImNhdGVnb3J5Il0g'
            'ZWxzZSBOb25lKQogICAgICAgIHJlYWRpbmcgPSBmbG93LnJlYWRfdGV4dCh0ZXh0LCBjYXRzKQogICAgICAgIHVuZGVyc3Rvb2Qg'
            'PSBhbnkoayBpbiByZWFkaW5nIGZvciBrIGluCiAgICAgICAgICAgICAgICAgICAgICAgICAoImNhdGVnb3J5IiwgInNraW5fdHlw'
            'ZSIsICJjb25jZXJucyIsICJzY2VudCIsICJvY2Nhc2lvbiIsICJidWRnZXQiLCAibWVkaWNhbCIpKQogICAgICAgIGlmIG5vdCB1'
            'bmRlcnN0b29kIGFuZCBnZW1pbmkuY29uZmlndXJlZCgpIGFuZCBub3QgZ2VtaW5pLnBhdXNlZCgpIGFuZCBmbG93LmFpX2FsbG93'
            'ZWQoCiAgICAgICAgICAgICAgICB2aXNpdG9yX2lkLCBfaW50X2VudigiQURWSVNPUl9BSV9QRVJfVklTSVRPUiIsIDEyKSwgX2lu'
            'dF9lbnYoIkFEVklTT1JfQUlfREFJTFlfQ0FQIiwgMTUwKSk6CiAgICAgICAgICAgIGZsb3cuYWlfc3BlbmQodmlzaXRvcl9pZCkK'
            'ICAgICAgICAgICAgYWlfcmVhZCA9IGF3YWl0IGdlbWluaS5yZWFkX21lc3NhZ2UoCiAgICAgICAgICAgICAgICBodHRweF9tb2R1'
            'bGUsIHRleHQsIGNhdHMsIGZsb3cuU0tJTl9UWVBFUywgZmxvdy5DT05DRVJOUywgZmxvdy5TQ0VOVFMsIGZsb3cuT0NDQVNJT05T'
            'KQogICAgICAgICAgICBpZiBhaV9yZWFkOgogICAgICAgICAgICAgICAgcmVhZGluZyA9IHsqKnJlYWRpbmcsICoqYWlfcmVhZH0K'
            'ICAgICAgICAgICAgICAgIGFpX3VzZWQgPSBUcnVlCiAgICAgICAgICAgICAgICB1bmRlcnN0b29kID0gYW55KGsgaW4gYWlfcmVh'
            'ZCBmb3IgayBpbgogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAoImNhdGVnb3J5IiwgInNraW5fdHlwZSIsICJjb25j'
            'ZXJucyIsICJzY2VudCIsICJvY2Nhc2lvbiIsICJidWRnZXQiLCAibWVkaWNhbCIpKQogICAgICAgIGlmIHJlYWRpbmcuZ2V0KCJt'
            'ZWRpY2FsIik6CiAgICAgICAgICAgIG1lZGljYWwgPSBUcnVlCiAgICAgICAgICAgIG1lc3NhZ2VzLmFwcGVuZChmbG93Lk1FRElD'
            'QUxfUkVQTFkpCiAgICAgICAgaWYgbm90IHVuZGVyc3Rvb2QgYW5kIG5vdCByZWFkaW5nLmdldCgic2hvd19ub3ciKToKICAgICAg'
            'ICAgICAgbmYgPSBmbG93Lm5leHRfZmllbGQoc3RhdGUsIGtpbmRfb2Yoc3RhdGVbImNhdGVnb3J5Il0pKSBpZiBzdGF0ZVsiY2F0'
            'ZWdvcnkiXSBlbHNlIE5vbmUKICAgICAgICAgICAgaWYgbmY6CiAgICAgICAgICAgICAgICBxdGV4dCwgc3BlYyA9IGZsb3cucXVl'
            'c3Rpb25fZm9yKG5mLCBzdGF0ZSkKICAgICAgICAgICAgICAgIHJldHVybiBfcmVwbHkoWyJJIGRpZCBub3QgcXVpdGUgY2F0Y2gg'
            'dGhhdC4gIiArIHF0ZXh0XSwgY2hpcHM9X2NoaXBzKHNwZWMsIFRydWUpKQogICAgICAgICAgICByZXR1cm4gX3JlcGx5KFsiSSBk'
            'aWQgbm90IHF1aXRlIGNhdGNoIHRoYXQuIFRlbGwgbWUgdGhlIHByb2R1Y3QgYW5kIHlvdXIgc2tpbiBuZWVkcywgIgogICAgICAg'
            'ICAgICAgICAgICAgICAgICAgICAib3IgcGljayBhIGNhdGVnb3J5LiJdLCBjaGlwcz1fY2F0ZWdvcnlfY2hpcHMoY2F0cykpCiAg'
            'ICAgICAgZmxvdy5hcHBseV9yZWFkaW5nKHN0YXRlLCByZWFkaW5nLCBraW5kX29mKQogICAgICAgIHNob3dfbm93ID0gYm9vbChy'
            'ZWFkaW5nLmdldCgic2hvd19ub3ciKSkKICAgICAgICBpZiBzdGF0ZVsiY2F0ZWdvcnkiXSBhbmQgbm90IG1lZGljYWw6CiAgICAg'
            'ICAgICAgIG1lc3NhZ2VzLmFwcGVuZCgiR290IGl0LiIpCiAgICBlbHNlOgogICAgICAgIHJldHVybiBfcmVwbHkoW0dSRUVUSU5H'
            'XSwgY2hpcHM9X2NhdGVnb3J5X2NoaXBzKGNhdHMpKQoKICAgICMgLS0tLSBkZWNpZGUgd2hhdCBjb21lcyBuZXh0CiAgICBpZiBu'
            'b3Qgc3RhdGVbImNhdGVnb3J5Il06CiAgICAgICAgc2Vzc2lvblsiYWR2X2NoYXQiXSA9IHN0YXRlCiAgICAgICAgbWVzc2FnZXMu'
            'YXBwZW5kKCJXaGljaCBjYXRlZ29yeSB3b3VsZCB5b3UgbGlrZSB0byBsb29rIGF0PyIpCiAgICAgICAgcmV0dXJuIF9yZXBseSht'
            'ZXNzYWdlcywgY2hpcHM9X2NhdGVnb3J5X2NoaXBzKGNhdHMpLCBhaV91c2VkPWFpX3VzZWQpCgogICAga2luZCA9IGtpbmRfb2Yo'
            'c3RhdGVbImNhdGVnb3J5Il0pCiAgICBuZiA9IGZsb3cubmV4dF9maWVsZChzdGF0ZSwga2luZCkKICAgIGlmIG5mIGFuZCBub3Qg'
            'c2hvd19ub3c6CiAgICAgICAgc2Vzc2lvblsiYWR2X2NoYXQiXSA9IHN0YXRlCiAgICAgICAgcXRleHQsIHNwZWMgPSBmbG93LnF1'
            'ZXN0aW9uX2ZvcihuZiwgc3RhdGUpCiAgICAgICAgbWVzc2FnZXMuYXBwZW5kKHF0ZXh0KQogICAgICAgIHJldHVybiBfcmVwbHko'
            'bWVzc2FnZXMsIGNoaXBzPV9jaGlwcyhzcGVjLCBuZiAhPSAiYnVkZ2V0IiksIGFpX3VzZWQ9YWlfdXNlZCwgZGlzY2xhaW1lcj1t'
            'ZWRpY2FsKQoKICAgICMgLS0tLSByZWNvbW1lbmQKICAgIHNlc3Npb25bImFkdl9jaGF0Il0gPSBzdGF0ZQogICAgbGFiZWwgPSBi'
            'eV9zbHVnW3N0YXRlWyJjYXRlZ29yeSJdXVsibGFiZWwiXQogICAgcmVzdWx0ID0gZmxvdy5waWNrX3Byb2R1Y3RzKHN0YXRlLCBw'
            'cm9kdWN0cywgc3RvY2ssIG1ldGEpCiAgICBpdGVtcyA9IHJlc3VsdFsiaXRlbXMiXQogICAgYWZ0ZXJfY2hpcHMgPSB7ImZpZWxk'
            'IjogIm5leHQiLCAibXVsdGkiOiBGYWxzZSwgInNraXBwYWJsZSI6IEZhbHNlLAogICAgICAgICAgICAgICAgICAgIm9wdGlvbnMi'
            'OiBbeyJsYWJlbCI6ICJBbm90aGVyIGNhdGVnb3J5IiwgInZhbHVlIjogImNhdGVnb3J5In0sCiAgICAgICAgICAgICAgICAgICAg'
            'ICAgICAgICAgICB7ImxhYmVsIjogIlN0YXJ0IG92ZXIiLCAidmFsdWUiOiAicmVzdGFydCJ9XX0KICAgIGlmIG5vdCBpdGVtczoK'
            'ICAgICAgICBtZXNzYWdlcy5hcHBlbmQocmVzdWx0LmdldCgibWVzc2FnZSIpIG9yICJJIGNvdWxkIG5vdCBmaW5kIGEgbWF0Y2gg'
            'cmlnaHQgbm93LiIpCiAgICAgICAgaWYgc3RhdGUuZ2V0KCJidWRnZXRfbWF4Iikgb3Igc3RhdGUuZ2V0KCJidWRnZXRfbWluIik6'
            'CiAgICAgICAgICAgIHdpZGUgPSBkaWN0KHN0YXRlLCBidWRnZXRfbWluPU5vbmUsIGJ1ZGdldF9tYXg9Tm9uZSkKICAgICAgICAg'
            'ICAgYWx0ID0gZmxvdy5waWNrX3Byb2R1Y3RzKHdpZGUsIHByb2R1Y3RzLCBzdG9jaywgbWV0YSwgbGltaXQ9NTApWyJpdGVtcyJd'
            'CiAgICAgICAgICAgIGlmIGFsdDoKICAgICAgICAgICAgICAgIGxvdyA9IG1pbihhWyJwcmljZV9uYWlyYSJdIGZvciBhIGluIGFs'
            'dCkKICAgICAgICAgICAgICAgIG1lc3NhZ2VzLmFwcGVuZCgiVGhlIGxvd2VzdC1wcmljZWQgb3B0aW9uIGluIHN0b2NrIGluIHRo'
            'aXMgY2F0ZWdvcnkgaXMgXHUyMEE2ezosLjBmfSwgc28gYSBoaWdoZXIgYnVkZ2V0IHdvdWxkIGhlbHAuIi5mb3JtYXQobG93KSkK'
            'ICAgICAgICBtZXNzYWdlcy5hcHBlbmQoIllvdSBjYW4gY2hhbmdlIHlvdXIgYW5zd2VycywgdHJ5IGFub3RoZXIgY2F0ZWdvcnks'
            'IG9yIGJyb3dzZSB0aGUgd2hvbGUgc2hvcC4iKQogICAgICAgIHJldHVybiBfcmVwbHkobWVzc2FnZXMsIGNoaXBzPWFmdGVyX2No'
            'aXBzLCBhaV91c2VkPWFpX3VzZWQsIGRpc2NsYWltZXI9bWVkaWNhbCkKCiAgICBhbnN3ZXJzID0gZmxvdy5lbmdpbmVfYW5zd2Vy'
            'cyhzdGF0ZSkKICAgIGlmIGdlbWluaS5jb25maWd1cmVkKCkgYW5kIG5vdCBnZW1pbmkucGF1c2VkKCkgYW5kIGZsb3cuYWlfYWxs'
            'b3dlZCgKICAgICAgICAgICAgdmlzaXRvcl9pZCwgX2ludF9lbnYoIkFEVklTT1JfQUlfUEVSX1ZJU0lUT1IiLCAxMiksIF9pbnRf'
            'ZW52KCJBRFZJU09SX0FJX0RBSUxZX0NBUCIsIDE1MCkpOgogICAgICAgIGZsb3cuYWlfc3BlbmQodmlzaXRvcl9pZCkKICAgICAg'
            'ICByYXcgPSBhd2FpdCBnZW1pbmkucmV3b3JkX3JlYXNvbnMoaHR0cHhfbW9kdWxlLCBpdGVtcywgYW5zd2VycykKICAgICAgICBp'
            'ZiByYXc6CiAgICAgICAgICAgIG5hbWVzID0gW3AuZ2V0KCJuYW1lIikgZm9yIHAgaW4gcHJvZHVjdHNdCiAgICAgICAgICAgIGdv'
            'b2QgPSBlbmdpbmUudmFsaWRhdGVfbGxtX291dHB1dChyYXcsIGl0ZW1zLCBuYW1lcykKICAgICAgICAgICAgZm9yIGl0IGluIGl0'
            'ZW1zOgogICAgICAgICAgICAgICAgaWYgaXRbInByb2R1Y3RfaWQiXSBpbiBnb29kOgogICAgICAgICAgICAgICAgICAgIGl0WyJy'
            'ZWFzb25zIl0gPSBbZ29vZFtpdFsicHJvZHVjdF9pZCJdXV0KICAgICAgICAgICAgICAgICAgICBhaV91c2VkID0gVHJ1ZQoKICAg'
            'IGJ5X2lkID0ge3N0cihwLmdldCgiaWQiKSk6IHAgZm9yIHAgaW4gcHJvZHVjdHN9CiAgICBjYXJkcyA9IFtdCiAgICBmb3IgaXQg'
            'aW4gaXRlbXM6CiAgICAgICAgcCA9IGJ5X2lkLmdldChpdFsicHJvZHVjdF9pZCJdLCB7fSkKICAgICAgICBjYXJkcy5hcHBlbmQo'
            'ewogICAgICAgICAgICAicHJvZHVjdF9pZCI6IGl0WyJwcm9kdWN0X2lkIl0sCiAgICAgICAgICAgICJuYW1lIjogaXRbIm5hbWUi'
            'XSwKICAgICAgICAgICAgInNpemUiOiBpdC5nZXQoInNpemUiKSwKICAgICAgICAgICAgInByaWNlX25haXJhIjogaXRbInByaWNl'
            'X25haXJhIl0sCiAgICAgICAgICAgICJyZWFzb25zIjogaXRbInJlYXNvbnMiXVs6M10sCiAgICAgICAgICAgICJkZXRhaWxzX29u'
            'X2ZpbGUiOiBpdFsiZGV0YWlsc19vbl9maWxlIl0sCiAgICAgICAgICAgICJpbWFnZV91cmwiOiBwLmdldCgiaW1hZ2VfdXJsIiks'
            'CiAgICAgICAgICAgICJ1cmwiOiAiL3Byb2R1Y3QvIiArIGl0WyJwcm9kdWN0X2lkIl0sCiAgICAgICAgICAgICJpbl9zdG9jayI6'
            'IFRydWUsCiAgICAgICAgfSkKICAgIGlmIGFueShjWyJkZXRhaWxzX29uX2ZpbGUiXSBmb3IgYyBpbiBjYXJkcyk6CiAgICAgICAg'
            'bWVzc2FnZXMuYXBwZW5kKCJIZXJlIGFyZSBteSBwaWNrcyBmb3IgJXMuIEFsbCBvZiB0aGVtIGFyZSBpbiBzdG9jayBub3cuIiAl'
            'IGZsb3cuc3VtbWFyeV9saW5lKHN0YXRlLCBsYWJlbCkpCiAgICBlbHNlOgogICAgICAgICMgQmUgaG9uZXN0OiB3aXRoIG5vIHNr'
            'aW4gb3IgaW5ncmVkaWVudCBmYWN0cyBvbiBmaWxlLCBwcm9kdWN0cyBhcmUgb25seQogICAgICAgICMgZmlsdGVyZWQgYnkgY2F0'
            'ZWdvcnksIHN0b2NrIGFuZCBidWRnZXQsIG5vdCBtYXRjaGVkIHRvIHNraW4gbmVlZHMuCiAgICAgICAgbWVzc2FnZXMuYXBwZW5k'
            'KAogICAgICAgICAgICAiSGVyZSBpcyB3aGF0IHdlIGhhdmUgaW4gc3RvY2sgaW4gJXMlcy4gV2UgaGF2ZSBub3QgYWRkZWQgc2tp'
            'biBkZXRhaWxzIGZvciB0aGVzZSBwcm9kdWN0cyAiCiAgICAgICAgICAgICJ5ZXQsIHNvIEkgY2Fubm90IHNheSB3aGljaCBvbmUg'
            'c3VpdHMgeW91ciBza2luIGJlc3QuIFBsZWFzZSBjaGVjayBlYWNoIHByb2R1Y3QncyBsYWJlbC4iCiAgICAgICAgICAgICUgKGxh'
            'YmVsLCAiIHdpdGhpbiB5b3VyIGJ1ZGdldCIgaWYgKHN0YXRlLmdldCgiYnVkZ2V0X21heCIpIG9yIHN0YXRlLmdldCgiYnVkZ2V0'
            'X21pbiIpKSBlbHNlICIiKSkKICAgIHJldHVybiBfcmVwbHkobWVzc2FnZXMsIGNoaXBzPWFmdGVyX2NoaXBzLCBpdGVtcz1jYXJk'
            'cywgYWlfdXNlZD1haV91c2VkLAogICAgICAgICAgICAgICAgICBkaXNjbGFpbWVyPShraW5kID09ICJza2luIikgb3IgbWVkaWNh'
            'bCkK'
        ),
    },
    'routers/advisor_chat.py': {
        "sha256": '03614a6cdb8077300f52f39f0e76c447f8d704dddee9273760efad6014795372',
        "b64": (
            'IiIiQmVhdXR5IEFkdmlzb3IgY2hhdCByb3V0ZXMgKHN0b3JlZnJvbnQgb25seTsgbmV2ZXIgdG91Y2hlcyBQT1Mgb3IgaW52ZW50'
            'b3J5KS4KCkdFVCAgL2Fkdmlzb3IgICAgICAgICAgdGhlIGNoYXQgcGFnZQpHRVQgIC9hZHZpc29yL2NsYXNzaWMgIHRoZSBvcmln'
            'aW5hbCA3LXN0ZXAgd2l6YXJkIChzdGlsbCB3b3Jrcywgc3RpbGwgZnJlZSkKUE9TVCAvYWR2aXNvci9jaGF0ICAgICBvbmUgY2hh'
            'dCB0dXJuLCBKU09OIGluIC8gSlNPTiBvdXQKClRoaXMgcm91dGVyIGlzIGluY2x1ZGVkIEJFRk9SRSByb3V0ZXJzLmFkdmlzb3Ig'
            'aW4gbWFpbi5weSwgc28gaXRzIC9hZHZpc29yCnBhZ2UgaXMgdGhlIG9uZSBzZXJ2ZWQuIFRoZSB3aXphcmQncyBvd24gUE9TVCAv'
            'YWR2aXNvci9yZWNvbW1lbmQgaXMgdW50b3VjaGVkLgpBbnkgZmFpbHVyZSBoZXJlIHJldHVybnMgYSBwbGFpbiBtZXNzYWdlOyBi'
            'cm93c2luZywgY2FydCBhbmQgY2hlY2tvdXQgYXJlCm5ldmVyIGFmZmVjdGVkLgoiIiIKaW1wb3J0IHNlY3JldHMKCmltcG9ydCBo'
            'dHRweApmcm9tIGZhc3RhcGkgaW1wb3J0IEFQSVJvdXRlciwgUmVxdWVzdApmcm9tIGZhc3RhcGkucmVzcG9uc2VzIGltcG9ydCBI'
            'VE1MUmVzcG9uc2UsIEpTT05SZXNwb25zZQpmcm9tIGZhc3RhcGkudGVtcGxhdGluZyBpbXBvcnQgSmluamEyVGVtcGxhdGVzCgpp'
            'bXBvcnQgYWR2aXNvcl9mbG93IGFzIGZsb3cKaW1wb3J0IGFkdmlzb3JfdHVybiBhcyB0dXJuCmZyb20gYXBpX2NsaWVudCBpbXBv'
            'cnQgYXBpX2NsaWVudApmcm9tIGNvbmZpZyBpbXBvcnQgc2V0dGluZ3MKZnJvbSByb3V0ZXJzLmFkdmlzb3IgaW1wb3J0IF9sb2Fk'
            'X21ldGEgICMgc2FtZSBjYXRhbG9nIGZpbGUgYW5kIHJlbG9hZCBsb2dpYyBhcyB0aGUgd2l6YXJkCgpyb3V0ZXIgPSBBUElSb3V0'
            'ZXIoKQp0ZW1wbGF0ZXMgPSBKaW5qYTJUZW1wbGF0ZXMoZGlyZWN0b3J5PSJ0ZW1wbGF0ZXMiKQoKCmRlZiBfaXAocmVxdWVzdDog'
            'UmVxdWVzdCkgLT4gc3RyOgogICAgcmV0dXJuICJhZHZjaGF0OiIgKyAocmVxdWVzdC5jbGllbnQuaG9zdCBpZiByZXF1ZXN0LmNs'
            'aWVudCBlbHNlICJ1bmtub3duIikKCgpkZWYgX3Zpc2l0b3JfaWQocmVxdWVzdDogUmVxdWVzdCkgLT4gc3RyOgogICAgdmlkID0g'
            'cmVxdWVzdC5zZXNzaW9uLmdldCgiYWR2X3ZpZCIpCiAgICBpZiBub3QgdmlkOgogICAgICAgIHZpZCA9IHNlY3JldHMudG9rZW5f'
            'aGV4KDgpCiAgICAgICAgcmVxdWVzdC5zZXNzaW9uWyJhZHZfdmlkIl0gPSB2aWQKICAgIHJldHVybiB2aWQKCgpAcm91dGVyLmdl'
            'dCgiL2Fkdmlzb3IiLCByZXNwb25zZV9jbGFzcz1IVE1MUmVzcG9uc2UpCmFzeW5jIGRlZiBhZHZpc29yX2NoYXRfcGFnZShyZXF1'
            'ZXN0OiBSZXF1ZXN0KToKICAgIHJldHVybiB0ZW1wbGF0ZXMuVGVtcGxhdGVSZXNwb25zZSgKICAgICAgICByZXF1ZXN0LCAiYWR2'
            'aXNvcl9jaGF0Lmh0bWwiLAogICAgICAgIHsic2l0ZV9uYW1lIjogc2V0dGluZ3MuU0lURV9OQU1FLCAiZGlzY2xhaW1lciI6IGZs'
            'b3cuRElTQ0xBSU1FUn0sCiAgICApCgoKQHJvdXRlci5nZXQoIi9hZHZpc29yL2NsYXNzaWMiLCByZXNwb25zZV9jbGFzcz1IVE1M'
            'UmVzcG9uc2UpCmFzeW5jIGRlZiBhZHZpc29yX2NsYXNzaWNfcGFnZShyZXF1ZXN0OiBSZXF1ZXN0KToKICAgIHJldHVybiB0ZW1w'
            'bGF0ZXMuVGVtcGxhdGVSZXNwb25zZShyZXF1ZXN0LCAiYWR2aXNvci5odG1sIiwgeyJzaXRlX25hbWUiOiBzZXR0aW5ncy5TSVRF'
            'X05BTUV9KQoKCkByb3V0ZXIucG9zdCgiL2Fkdmlzb3IvY2hhdCIpCmFzeW5jIGRlZiBhZHZpc29yX2NoYXQocmVxdWVzdDogUmVx'
            'dWVzdCk6CiAgICAjIENvYXJzZSBsaW1pdCBwZXIgbmV0d29yayBhZGRyZXNzIChnZW5lcm91czogbWFueSBwaG9uZXMgc2hhcmUg'
            'b25lIGFkZHJlc3MpLgogICAgaWYgZmxvdy5jaGF0X3JhdGVfbGltaXRlZChfaXAocmVxdWVzdCksIGxpbWl0PTI0MCwgd2luZG93'
            'PTYwMCk6CiAgICAgICAgcmV0dXJuIEpTT05SZXNwb25zZSh7Im9rIjogRmFsc2UsICJtZXNzYWdlIjogIlRvbyBtYW55IHJlcXVl'
            'c3RzLiBQbGVhc2Ugd2FpdCBhIGZldyBtaW51dGVzIGFuZCB0cnkgYWdhaW4uIn0sCiAgICAgICAgICAgICAgICAgICAgICAgICAg'
            'ICBzdGF0dXNfY29kZT00MjkpCiAgICB0cnk6CiAgICAgICAgYm9keSA9IGF3YWl0IHJlcXVlc3QuanNvbigpCiAgICBleGNlcHQg'
            'RXhjZXB0aW9uOgogICAgICAgIHJldHVybiBKU09OUmVzcG9uc2UoeyJvayI6IEZhbHNlLCAibWVzc2FnZSI6ICJTb21ldGhpbmcg'
            'd2VudCB3cm9uZy4gUGxlYXNlIHRyeSBhZ2Fpbi4ifSwgc3RhdHVzX2NvZGU9NDAwKQogICAgIyBUaWdodGVyIGxpbWl0IHBlciB2'
            'aXNpdG9yIHNlc3Npb24uCiAgICB2aWQgPSBfdmlzaXRvcl9pZChyZXF1ZXN0KQogICAgaWYgZmxvdy5jaGF0X3JhdGVfbGltaXRl'
            'ZCgidjoiICsgdmlkLCBsaW1pdD04MCwgd2luZG93PTYwMCk6CiAgICAgICAgcmV0dXJuIEpTT05SZXNwb25zZSh7Im9rIjogRmFs'
            'c2UsICJtZXNzYWdlIjogIllvdSBhcmUgc2VuZGluZyBtZXNzYWdlcyB2ZXJ5IHF1aWNrbHkuIFBsZWFzZSB3YWl0IGEgbW9tZW50'
            'LiJ9LAogICAgICAgICAgICAgICAgICAgICAgICAgICAgc3RhdHVzX2NvZGU9NDI5KQogICAgdHJ5OgogICAgICAgIHByb2R1Y3Rz'
            'ID0gYXdhaXQgYXBpX2NsaWVudC5saXN0X3Byb2R1Y3RzX3dpdGhfc3RvY2soKQogICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAg'
            'ICByZXR1cm4gSlNPTlJlc3BvbnNlKAogICAgICAgICAgICB7Im9rIjogRmFsc2UsICJtZXNzYWdlIjogIldlIGNvdWxkIG5vdCBj'
            'aGVjayB0aGUgc2hvcCByaWdodCBub3cuIFBsZWFzZSB0cnkgYWdhaW4gc2hvcnRseSwgb3IgYnJvd3NlIHRoZSBzaG9wLiJ9LAog'
            'ICAgICAgICAgICBzdGF0dXNfY29kZT01MDIsCiAgICAgICAgKQogICAgc3RvY2sgPSB7c3RyKHAuZ2V0KCJpZCIpKTogaW50KHAu'
            'Z2V0KCJzdG9ja19xdHkiKSBvciAwKSBmb3IgcCBpbiBwcm9kdWN0c30KICAgIHRyeToKICAgICAgICBkYXRhID0gYXdhaXQgdHVy'
            'bi5oYW5kbGUoYm9keSwgcmVxdWVzdC5zZXNzaW9uLCB2aWQsIHByb2R1Y3RzLCBzdG9jaywgX2xvYWRfbWV0YSgpLCBodHRweCkK'
            'ICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgcmV0dXJuIEpTT05SZXNwb25zZSh7Im9rIjogRmFsc2UsICJtZXNzYWdlIjog'
            'IlNvbWV0aGluZyB3ZW50IHdyb25nIG9uIG91ciBzaWRlLiBQbGVhc2UgdHJ5IGFnYWluLiJ9LAogICAgICAgICAgICAgICAgICAg'
            'ICAgICAgICAgc3RhdHVzX2NvZGU9NTAwKQogICAgcmV0dXJuIEpTT05SZXNwb25zZShkYXRhKQo='
        ),
    },
    'templates/advisor_chat.html': {
        "sha256": 'ddbda0592f39f615c5f5a903eb92bca3e34db3dea0007d4aebe4adb82eb8b351',
        "b64": (
            'eyUgZXh0ZW5kcyAiYmFzZS5odG1sIiAlfQp7JSBibG9jayB0aXRsZSAlfUJlYXV0eSBBZHZpc29yIHwge3sgc2l0ZV9uYW1lIH19'
            'eyUgZW5kYmxvY2sgJX0KeyUgYmxvY2sgY29udGVudCAlfQo8c2VjdGlvbiBjbGFzcz0iYmMiIGlkPSJiYy1yb290Ij4KICA8aGVh'
            'ZGVyIGNsYXNzPSJiYy1oZWFkIj4KICAgIDxoMT5CZWF1dHkgQWR2aXNvcjwvaDE+CiAgICA8cCBjbGFzcz0iYmMtc3ViIj5UZWxs'
            'IHVzIHdoYXQgeW91IG5lZWQuIFdlIHN1Z2dlc3QgcHJvZHVjdHMgZnJvbSBvdXIgb3duIHNob3AgdGhhdCBhcmUgaW4gc3RvY2sg'
            'dG9kYXkuPC9wPgogICAgPHAgY2xhc3M9ImJjLW5vdGUiPnt7IGRpc2NsYWltZXIgfX08L3A+CiAgPC9oZWFkZXI+CgogIDxkaXYg'
            'Y2xhc3M9ImJjLXBhbmVsIj4KICAgIDxkaXYgY2xhc3M9ImJjLWxvZyIgaWQ9ImJjLWxvZyIgcm9sZT0ibG9nIiBhcmlhLWxpdmU9'
            'InBvbGl0ZSIgYXJpYS1sYWJlbD0iQ29udmVyc2F0aW9uIHdpdGggdGhlIEJlYXV0eSBBZHZpc29yIj48L2Rpdj4KCiAgICA8ZGl2'
            'IGNsYXNzPSJiYy1jaGlwcyIgaWQ9ImJjLWNoaXBzIiBoaWRkZW4+PC9kaXY+CgogICAgPGZvcm0gY2xhc3M9ImJjLWNvbXBvc2Vy'
            'IiBpZD0iYmMtZm9ybSIgYXV0b2NvbXBsZXRlPSJvZmYiPgogICAgICA8bGFiZWwgY2xhc3M9ImJjLXNyIiBmb3I9ImJjLWlucHV0'
            'Ij5Zb3VyIG1lc3NhZ2U8L2xhYmVsPgogICAgICA8aW5wdXQgdHlwZT0idGV4dCIgaWQ9ImJjLWlucHV0IiBtYXhsZW5ndGg9IjMw'
            'MCIgcGxhY2Vob2xkZXI9IkZvciBleGFtcGxlOiBvaWx5IHNraW4sIGRhcmsgc3BvdHMsIGxvdGlvbiB1bmRlciA1LDAwMCIgZW50'
            'ZXJrZXloaW50PSJzZW5kIj4KICAgICAgPGJ1dHRvbiB0eXBlPSJzdWJtaXQiIGNsYXNzPSJidG4tcHJpbWFyeSBiYy1zZW5kIiBp'
            'ZD0iYmMtc2VuZCI+U2VuZDwvYnV0dG9uPgogICAgPC9mb3JtPgogIDwvZGl2PgoKICA8cCBjbGFzcz0iYmMtZm9vdCI+CiAgICA8'
            'YnV0dG9uIHR5cGU9ImJ1dHRvbiIgY2xhc3M9ImJjLWxpbmsiIGlkPSJiYy1yZXN0YXJ0Ij5TdGFydCBvdmVyPC9idXR0b24+CiAg'
            'ICA8YSBjbGFzcz0iYmMtbGluayIgaHJlZj0iL2Fkdmlzb3IvY2xhc3NpYyI+UHJlZmVyIHF1aWNrIHF1ZXN0aW9ucz8gVXNlIHRo'
            'ZSBzdGVwLWJ5LXN0ZXAgdmVyc2lvbjwvYT4KICAgIDxhIGNsYXNzPSJiYy1saW5rIiBocmVmPSIvc2hvcCI+QnJvd3NlIHRoZSB3'
            'aG9sZSBzaG9wPC9hPgogIDwvcD4KPC9zZWN0aW9uPgo8c2NyaXB0IHNyYz0iL3N0YXRpYy9qcy9hZHZpc29yX2NoYXQuanMiPjwv'
            'c2NyaXB0Pgp7JSBlbmRibG9jayAlfQo='
        ),
    },
    'static/js/advisor_chat.js': {
        "sha256": '1d29c462ddd4273bd658ae49c69685b9e80122e6277b5b263e1c910a313a26f6',
        "b64": (
            'LyogQmVhdXR5IEFkdmlzb3IgY2hhdC4gUGxhaW4gSlMsIG5vIGJ1aWxkIHN0ZXAuIFRleHQgZnJvbSB0aGUgc2VydmVyIGlzIG9u'
            'bHkKICAgZXZlciBpbnNlcnRlZCB3aXRoIHRleHRDb250ZW50IChuZXZlciBpbm5lckhUTUwpLiAqLwooZnVuY3Rpb24gKCkgewog'
            'ICd1c2Ugc3RyaWN0JzsKCiAgdmFyIE5BSVJBID0gJ1x1MjBBNic7CgogIGZ1bmN0aW9uIGZvcm1hdE5haXJhKG4pIHsKICAgIHZh'
            'ciB2ID0gTWF0aC5yb3VuZChOdW1iZXIobikgfHwgMCk7CiAgICByZXR1cm4gTkFJUkEgKyBTdHJpbmcodikucmVwbGFjZSgvXEIo'
            'Pz0oXGR7M30pKyg/IVxkKSkvZywgJywnKTsKICB9CgogIHZhciByb290ID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2JjLXJv'
            'b3QnKTsKICBpZiAoIXJvb3QpIHsKICAgIGlmICh0eXBlb2YgbW9kdWxlICE9PSAndW5kZWZpbmVkJykgeyBtb2R1bGUuZXhwb3J0'
            'cyA9IHsgZm9ybWF0TmFpcmE6IGZvcm1hdE5haXJhIH07IH0KICAgIHJldHVybjsKICB9CgogIHZhciBsb2cgPSBkb2N1bWVudC5n'
            'ZXRFbGVtZW50QnlJZCgnYmMtbG9nJyk7CiAgdmFyIGNoaXBzQm94ID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2JjLWNoaXBz'
            'Jyk7CiAgdmFyIGZvcm0gPSBkb2N1bWVudC5nZXRFbGVtZW50QnlJZCgnYmMtZm9ybScpOwogIHZhciBpbnB1dCA9IGRvY3VtZW50'
            'LmdldEVsZW1lbnRCeUlkKCdiYy1pbnB1dCcpOwogIHZhciBzZW5kQnRuID0gZG9jdW1lbnQuZ2V0RWxlbWVudEJ5SWQoJ2JjLXNl'
            'bmQnKTsKICB2YXIgYnVzeSA9IGZhbHNlOwoKICBmdW5jdGlvbiBlbCh0YWcsIGNscywgdGV4dCkgewogICAgdmFyIG4gPSBkb2N1'
            'bWVudC5jcmVhdGVFbGVtZW50KHRhZyk7CiAgICBpZiAoY2xzKSB7IG4uY2xhc3NOYW1lID0gY2xzOyB9CiAgICBpZiAodGV4dCAh'
            'PT0gdW5kZWZpbmVkICYmIHRleHQgIT09IG51bGwpIHsgbi50ZXh0Q29udGVudCA9IHRleHQ7IH0KICAgIHJldHVybiBuOwogIH0K'
            'CiAgLy8gVGhlIHBhZ2UgaXRzZWxmIHNjcm9sbHMgKG5vIGlubmVyIHNjcm9sbCBib3gsIHdoaWNoIGlzIGF3a3dhcmQgb24gcGhv'
            'bmVzKS4KICB2YXIgcmVkdWNlTW90aW9uID0gd2luZG93Lm1hdGNoTWVkaWEgJiYgd2luZG93Lm1hdGNoTWVkaWEoJyhwcmVmZXJz'
            'LXJlZHVjZWQtbW90aW9uOiByZWR1Y2UpJykubWF0Y2hlczsKICBmdW5jdGlvbiBzY3JvbGxUbyhub2RlLCBibG9jaykgewogICAg'
            'aWYgKG5vZGUgJiYgbm9kZS5zY3JvbGxJbnRvVmlldykgewogICAgICBub2RlLnNjcm9sbEludG9WaWV3KHsgYmxvY2s6IGJsb2Nr'
            'LCBiZWhhdmlvcjogcmVkdWNlTW90aW9uID8gJ2F1dG8nIDogJ3Ntb290aCcgfSk7CiAgICB9CiAgfQogIGZ1bmN0aW9uIHRvQm90'
            'dG9tKCkgeyAvKiBoYW5kbGVkIHBlciByZXNwb25zZSBpbiByZW5kZXIoKSAqLyB9CgogIGZ1bmN0aW9uIGFkZE1lc3NhZ2Uocm9s'
            'ZSwgdGV4dCkgewogICAgdmFyIHJvdyA9IGVsKCdkaXYnLCAnYmMtbXNnIGJjLScgKyByb2xlKTsKICAgIGlmIChyb2xlID09PSAn'
            'Ym90JykgeyByb3cuYXBwZW5kQ2hpbGQoZWwoJ3NwYW4nLCAnYmMtbWFyaycpKTsgfQogICAgcm93LmFwcGVuZENoaWxkKGVsKCdw'
            'JywgJ2JjLWJ1YmJsZScsIHRleHQpKTsKICAgIGxvZy5hcHBlbmRDaGlsZChyb3cpOwogICAgdG9Cb3R0b20oKTsKICB9CgogIGZ1'
            'bmN0aW9uIGFkZE5vdGUodGV4dCkgewogICAgbG9nLmFwcGVuZENoaWxkKGVsKCdwJywgJ2JjLWlubGluZS1ub3RlJywgdGV4dCkp'
            'OwogICAgdG9Cb3R0b20oKTsKICB9CgogIHZhciB0eXBpbmdFbCA9IG51bGw7CiAgZnVuY3Rpb24gc2hvd1R5cGluZyhvbikgewog'
            'ICAgaWYgKG9uICYmICF0eXBpbmdFbCkgewogICAgICB0eXBpbmdFbCA9IGVsKCdkaXYnLCAnYmMtbXNnIGJjLWJvdCBiYy10eXBp'
            'bmcnKTsKICAgICAgdHlwaW5nRWwuYXBwZW5kQ2hpbGQoZWwoJ3NwYW4nLCAnYmMtbWFyaycpKTsKICAgICAgdmFyIGRvdHMgPSBl'
            'bCgncCcsICdiYy1idWJibGUnKTsKICAgICAgZG90cy5zZXRBdHRyaWJ1dGUoJ2FyaWEtbGFiZWwnLCAnVGhlIGFkdmlzb3IgaXMg'
            'dHlwaW5nJyk7CiAgICAgIGRvdHMuYXBwZW5kQ2hpbGQoZWwoJ2knKSk7IGRvdHMuYXBwZW5kQ2hpbGQoZWwoJ2knKSk7IGRvdHMu'
            'YXBwZW5kQ2hpbGQoZWwoJ2knKSk7CiAgICAgIHR5cGluZ0VsLmFwcGVuZENoaWxkKGRvdHMpOwogICAgICBsb2cuYXBwZW5kQ2hp'
            'bGQodHlwaW5nRWwpOwogICAgICB0b0JvdHRvbSgpOwogICAgfSBlbHNlIGlmICghb24gJiYgdHlwaW5nRWwpIHsKICAgICAgdHlw'
            'aW5nRWwucGFyZW50Tm9kZS5yZW1vdmVDaGlsZCh0eXBpbmdFbCk7CiAgICAgIHR5cGluZ0VsID0gbnVsbDsKICAgIH0KICB9Cgog'
            'IGZ1bmN0aW9uIHNldEJ1c3kob24pIHsKICAgIGJ1c3kgPSBvbjsKICAgIHNlbmRCdG4uZGlzYWJsZWQgPSBvbjsKICAgIEFycmF5'
            'LnByb3RvdHlwZS5mb3JFYWNoLmNhbGwoY2hpcHNCb3gucXVlcnlTZWxlY3RvckFsbCgnYnV0dG9uJyksIGZ1bmN0aW9uIChiKSB7'
            'IGIuZGlzYWJsZWQgPSBvbjsgfSk7CiAgfQoKICAvLyAtLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0t'
            'LS0tLS0tLS0tLS0tLS0tLS0gY2FydAogIGZ1bmN0aW9uIGJ1bXBDYXJ0Q291bnQoKSB7CiAgICB2YXIgbGluayA9IGRvY3VtZW50'
            'LnF1ZXJ5U2VsZWN0b3IoJy5jYXJ0LWxpbmsnKTsKICAgIGlmICghbGluaykgeyByZXR1cm47IH0KICAgIHZhciBjID0gbGluay5x'
            'dWVyeVNlbGVjdG9yKCcuY2FydC1jb3VudCcpOwogICAgaWYgKGMpIHsgYy50ZXh0Q29udGVudCA9IFN0cmluZygocGFyc2VJbnQo'
            'Yy50ZXh0Q29udGVudCwgMTApIHx8IDApICsgMSk7IHJldHVybjsgfQogICAgYyA9IGVsKCdzcGFuJywgJ2NhcnQtY291bnQnLCAn'
            'MScpOwogICAgbGluay5hcHBlbmRDaGlsZChjKTsKICB9CgogIGZ1bmN0aW9uIGFkZFRvQ2FydChpdGVtLCBidG4sIHN0YXR1cykg'
            'ewogICAgYnRuLmRpc2FibGVkID0gdHJ1ZTsKICAgIGJ0bi50ZXh0Q29udGVudCA9ICdBZGRpbmcuLi4nOwogICAgdmFyIGJvZHkg'
            'PSBuZXcgVVJMU2VhcmNoUGFyYW1zKCk7CiAgICBib2R5LmFwcGVuZCgncHJvZHVjdF9pZCcsIGl0ZW0ucHJvZHVjdF9pZCk7CiAg'
            'ICBib2R5LmFwcGVuZCgncXVhbnRpdHknLCAnMScpOwogICAgZmV0Y2goJy9jYXJ0L2FkZCcsIHsgbWV0aG9kOiAnUE9TVCcsIGJv'
            'ZHk6IGJvZHksIGNyZWRlbnRpYWxzOiAnc2FtZS1vcmlnaW4nIH0pCiAgICAgIC50aGVuKGZ1bmN0aW9uIChyKSB7CiAgICAgICAg'
            'Ly8gVGhlIGNhcnQgcm91dGUgcmVkaXJlY3RzIHRvIC9jYXJ0IG9uIHN1Y2Nlc3MgYW5kIGJhY2sgdG8gdGhlIHByb2R1Y3QKICAg'
            'ICAgICAvLyBwYWdlIHdoZW4gdGhlIGl0ZW0gaGFzIGp1c3QgZ29uZSBvdXQgb2Ygc3RvY2suCiAgICAgICAgdmFyIG9rID0gL1wv'
            'Y2FydFwvPyQvLnRlc3Qoci51cmwuc3BsaXQoJz8nKVswXSk7CiAgICAgICAgaWYgKG9rKSB7CiAgICAgICAgICBidG4udGV4dENv'
            'bnRlbnQgPSAnQWRkZWQnOwogICAgICAgICAgc3RhdHVzLnRleHRDb250ZW50ID0gJyc7CiAgICAgICAgICB2YXIgYSA9IGVsKCdh'
            'JywgJ2JjLWxpbmsnLCAnVmlldyBjYXJ0Jyk7CiAgICAgICAgICBhLmhyZWYgPSAnL2NhcnQnOwogICAgICAgICAgc3RhdHVzLmFw'
            'cGVuZENoaWxkKGEpOwogICAgICAgICAgYnVtcENhcnRDb3VudCgpOwogICAgICAgIH0gZWxzZSB7CiAgICAgICAgICBidG4udGV4'
            'dENvbnRlbnQgPSAnT3V0IG9mIHN0b2NrJzsKICAgICAgICAgIHN0YXR1cy50ZXh0Q29udGVudCA9ICdTb3JyeSwgc29tZW9uZSBq'
            'dXN0IGJvdWdodCB0aGUgbGFzdCBvbmUuJzsKICAgICAgICB9CiAgICAgIH0pCiAgICAgIC5jYXRjaChmdW5jdGlvbiAoKSB7CiAg'
            'ICAgICAgYnRuLmRpc2FibGVkID0gZmFsc2U7CiAgICAgICAgYnRuLnRleHRDb250ZW50ID0gJ0FkZCB0byBjYXJ0JzsKICAgICAg'
            'ICBzdGF0dXMudGV4dENvbnRlbnQgPSAnQ291bGQgbm90IGFkZCBpdC4gUGxlYXNlIHRyeSBhZ2Fpbi4nOwogICAgICB9KTsKICB9'
            'CgogIC8vIC0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLSBjYXJkcwog'
            'IGZ1bmN0aW9uIGFkZENhcmRzKGl0ZW1zKSB7CiAgICB2YXIgd3JhcCA9IGVsKCdkaXYnLCAnYmMtY2FyZHMnKTsKICAgIGl0ZW1z'
            'LmZvckVhY2goZnVuY3Rpb24gKGl0KSB7CiAgICAgIHZhciBjYXJkID0gZWwoJ2FydGljbGUnLCAnYmMtY2FyZCcpOwogICAgICBp'
            'ZiAoaXQuaW1hZ2VfdXJsKSB7CiAgICAgICAgdmFyIGltZyA9IGVsKCdpbWcnKTsKICAgICAgICBpbWcuc3JjID0gaXQuaW1hZ2Vf'
            'dXJsOwogICAgICAgIGltZy5hbHQgPSBpdC5uYW1lOwogICAgICAgIGltZy5sb2FkaW5nID0gJ2xhenknOwogICAgICAgIGNhcmQu'
            'YXBwZW5kQ2hpbGQoaW1nKTsKICAgICAgfSBlbHNlIHsKICAgICAgICBjYXJkLmFwcGVuZENoaWxkKGVsKCdkaXYnLCAnYmMtY2Fy'
            'ZC1waCcpKTsKICAgICAgfQogICAgICB2YXIgYm9keSA9IGVsKCdkaXYnLCAnYmMtY2FyZC1ib2R5Jyk7CiAgICAgIGJvZHkuYXBw'
            'ZW5kQ2hpbGQoZWwoJ2gzJywgbnVsbCwgaXQubmFtZSArIChpdC5zaXplID8gJyAoJyArIGl0LnNpemUgKyAnKScgOiAnJykpKTsK'
            'ICAgICAgdmFyIG1ldGEgPSBlbCgncCcsICdiYy1jYXJkLW1ldGEnKTsKICAgICAgbWV0YS5hcHBlbmRDaGlsZChlbCgnc3Bhbics'
            'ICdwcmljZScsIGZvcm1hdE5haXJhKGl0LnByaWNlX25haXJhKSkpOwogICAgICBtZXRhLmFwcGVuZENoaWxkKGVsKCdzcGFuJywg'
            'J3N0b2NrIGluJywgJ0luIHN0b2NrJykpOwogICAgICBib2R5LmFwcGVuZENoaWxkKG1ldGEpOwogICAgICB2YXIgdWwgPSBlbCgn'
            'dWwnLCAnYmMtcmVhc29ucycpOwogICAgICAoaXQucmVhc29ucyB8fCBbXSkuZm9yRWFjaChmdW5jdGlvbiAocikgeyB1bC5hcHBl'
            'bmRDaGlsZChlbCgnbGknLCBudWxsLCByKSk7IH0pOwogICAgICBib2R5LmFwcGVuZENoaWxkKHVsKTsKICAgICAgdmFyIGFjdGlv'
            'bnMgPSBlbCgnZGl2JywgJ2JjLWNhcmQtYWN0aW9ucycpOwogICAgICB2YXIgdmlldyA9IGVsKCdhJywgJ2JjLXZpZXcnLCAnVmll'
            'dyBwcm9kdWN0Jyk7CiAgICAgIHZpZXcuaHJlZiA9IGl0LnVybDsKICAgICAgdmFyIGFkZCA9IGVsKCdidXR0b24nLCAnYnRuLXBy'
            'aW1hcnkgYmMtYWRkJywgJ0FkZCB0byBjYXJ0Jyk7CiAgICAgIGFkZC50eXBlID0gJ2J1dHRvbic7CiAgICAgIHZhciBzdGF0dXMg'
            'PSBlbCgnc3BhbicsICdiYy1hZGQtc3RhdHVzJyk7CiAgICAgIHN0YXR1cy5zZXRBdHRyaWJ1dGUoJ2FyaWEtbGl2ZScsICdwb2xp'
            'dGUnKTsKICAgICAgYWRkLmFkZEV2ZW50TGlzdGVuZXIoJ2NsaWNrJywgZnVuY3Rpb24gKCkgeyBhZGRUb0NhcnQoaXQsIGFkZCwg'
            'c3RhdHVzKTsgfSk7CiAgICAgIGFjdGlvbnMuYXBwZW5kQ2hpbGQodmlldyk7CiAgICAgIGFjdGlvbnMuYXBwZW5kQ2hpbGQoYWRk'
            'KTsKICAgICAgYm9keS5hcHBlbmRDaGlsZChhY3Rpb25zKTsKICAgICAgYm9keS5hcHBlbmRDaGlsZChzdGF0dXMpOwogICAgICBj'
            'YXJkLmFwcGVuZENoaWxkKGJvZHkpOwogICAgICB3cmFwLmFwcGVuZENoaWxkKGNhcmQpOwogICAgfSk7CiAgICBsb2cuYXBwZW5k'
            'Q2hpbGQod3JhcCk7CiAgICB0b0JvdHRvbSgpOwogIH0KCiAgLy8gLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0t'
            'LS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tIGNoaXBzCiAgZnVuY3Rpb24gY2xlYXJDaGlwcygpIHsKICAgIGNoaXBzQm94LnRleHRD'
            'b250ZW50ID0gJyc7CiAgICBjaGlwc0JveC5oaWRkZW4gPSB0cnVlOwogIH0KCiAgZnVuY3Rpb24gcmVuZGVyQ2hpcHMoc3BlYykg'
            'ewogICAgY2xlYXJDaGlwcygpOwogICAgaWYgKCFzcGVjIHx8ICFzcGVjLm9wdGlvbnMgfHwgIXNwZWMub3B0aW9ucy5sZW5ndGgp'
            'IHsgcmV0dXJuOyB9CiAgICBjaGlwc0JveC5oaWRkZW4gPSBmYWxzZTsKICAgIHZhciBwaWNrZWQgPSBbXTsKCiAgICBmdW5jdGlv'
            'biBjaG9vc2UodmFsdWVzLCBzaG93bkxhYmVscykgewogICAgICBpZiAoYnVzeSkgeyByZXR1cm47IH0KICAgICAgY2xlYXJDaGlw'
            'cygpOwogICAgICBhZGRNZXNzYWdlKCd1c2VyJywgc2hvd25MYWJlbHMubGVuZ3RoID8gc2hvd25MYWJlbHMuam9pbignLCAnKSA6'
            'ICdTa2lwJyk7CiAgICAgIHNlbmQoeyBhY3Rpb246ICdjaG9vc2UnLCBmaWVsZDogc3BlYy5maWVsZCwgdmFsdWVzOiB2YWx1ZXMg'
            'fSk7CiAgICB9CgogICAgc3BlYy5vcHRpb25zLmZvckVhY2goZnVuY3Rpb24gKG8pIHsKICAgICAgdmFyIGIgPSBlbCgnYnV0dG9u'
            'JywgJ2JjLWNoaXAnLCBvLmxhYmVsKTsKICAgICAgYi50eXBlID0gJ2J1dHRvbic7CiAgICAgIGlmIChzcGVjLm11bHRpKSB7CiAg'
            'ICAgICAgYi5zZXRBdHRyaWJ1dGUoJ2FyaWEtcHJlc3NlZCcsICdmYWxzZScpOwogICAgICAgIGIuYWRkRXZlbnRMaXN0ZW5lcign'
            'Y2xpY2snLCBmdW5jdGlvbiAoKSB7CiAgICAgICAgICB2YXIgaSA9IHBpY2tlZC5pbmRleE9mKG8udmFsdWUpOwogICAgICAgICAg'
            'aWYgKGkgPT09IC0xKSB7IHBpY2tlZC5wdXNoKG8udmFsdWUpOyB9IGVsc2UgeyBwaWNrZWQuc3BsaWNlKGksIDEpOyB9CiAgICAg'
            'ICAgICBiLmNsYXNzTGlzdC50b2dnbGUoJ2lzLW9uJywgaSA9PT0gLTEpOwogICAgICAgICAgYi5zZXRBdHRyaWJ1dGUoJ2FyaWEt'
            'cHJlc3NlZCcsIGkgPT09IC0xID8gJ3RydWUnIDogJ2ZhbHNlJyk7CiAgICAgICAgICBkb25lLmRpc2FibGVkID0gcGlja2VkLmxl'
            'bmd0aCA9PT0gMDsKICAgICAgICB9KTsKICAgICAgfSBlbHNlIHsKICAgICAgICBiLmFkZEV2ZW50TGlzdGVuZXIoJ2NsaWNrJywg'
            'ZnVuY3Rpb24gKCkgeyBjaG9vc2UoW28udmFsdWVdLCBbby5sYWJlbF0pOyB9KTsKICAgICAgfQogICAgICBjaGlwc0JveC5hcHBl'
            'bmRDaGlsZChiKTsKICAgIH0pOwoKICAgIHZhciBkb25lID0gbnVsbDsKICAgIGlmIChzcGVjLm11bHRpKSB7CiAgICAgIGRvbmUg'
            'PSBlbCgnYnV0dG9uJywgJ2JjLWNoaXAgYmMtY2hpcC1nbycsICdEb25lJyk7CiAgICAgIGRvbmUudHlwZSA9ICdidXR0b24nOwog'
            'ICAgICBkb25lLmRpc2FibGVkID0gdHJ1ZTsKICAgICAgZG9uZS5hZGRFdmVudExpc3RlbmVyKCdjbGljaycsIGZ1bmN0aW9uICgp'
            'IHsKICAgICAgICB2YXIgbGFiZWxzID0gW107CiAgICAgICAgQXJyYXkucHJvdG90eXBlLmZvckVhY2guY2FsbChjaGlwc0JveC5x'
            'dWVyeVNlbGVjdG9yQWxsKCcuYmMtY2hpcC5pcy1vbicpLCBmdW5jdGlvbiAoeCkgeyBsYWJlbHMucHVzaCh4LnRleHRDb250ZW50'
            'KTsgfSk7CiAgICAgICAgY2hvb3NlKHBpY2tlZC5zbGljZSgpLCBsYWJlbHMpOwogICAgICB9KTsKICAgICAgY2hpcHNCb3guYXBw'
            'ZW5kQ2hpbGQoZG9uZSk7CiAgICB9CiAgICBpZiAoc3BlYy5za2lwcGFibGUpIHsKICAgICAgdmFyIHNraXAgPSBlbCgnYnV0dG9u'
            'JywgJ2JjLWNoaXAgYmMtY2hpcC1xdWlldCcsICdTa2lwJyk7CiAgICAgIHNraXAudHlwZSA9ICdidXR0b24nOwogICAgICBza2lw'
            'LmFkZEV2ZW50TGlzdGVuZXIoJ2NsaWNrJywgZnVuY3Rpb24gKCkgeyBjaG9vc2UoWydza2lwJ10sIFtdKTsgfSk7CiAgICAgIGNo'
            'aXBzQm94LmFwcGVuZENoaWxkKHNraXApOwogICAgfQogIH0KCiAgLy8gLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0t'
            'LS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tIHRhbGtpbmcgdG8gdGhlIHNlcnZlcgogIGZ1bmN0aW9uIHJlbmRlcihkYXRhKSB7'
            'CiAgICB2YXIgYmVmb3JlID0gbG9nLmxhc3RFbGVtZW50Q2hpbGQ7CiAgICAoZGF0YS5tZXNzYWdlcyB8fCBbXSkuZm9yRWFjaChm'
            'dW5jdGlvbiAobSkgeyBhZGRNZXNzYWdlKCdib3QnLCBtKTsgfSk7CiAgICBpZiAoZGF0YS5pdGVtcyAmJiBkYXRhLml0ZW1zLmxl'
            'bmd0aCkgeyBhZGRDYXJkcyhkYXRhLml0ZW1zKTsgfQogICAgaWYgKGRhdGEuZGlzY2xhaW1lcikgewogICAgICBhZGROb3RlKCdD'
            'b3NtZXRpYyBndWlkYW5jZSBvbmx5LCBub3QgYSBtZWRpY2FsIGRpYWdub3Npcy4gU2VlIGEgZG9jdG9yIG9yIHBoYXJtYWNpc3Qg'
            'Zm9yIGEgc2tpbiBwcm9ibGVtIHRoYXQgaXMgcGFpbmZ1bCwgc2V2ZXJlIG9yIGRvZXMgbm90IGdvIGF3YXkuJyk7CiAgICB9CiAg'
            'ICByZW5kZXJDaGlwcyhkYXRhLmNoaXBzKTsKICAgIHZhciBmaXJzdCA9IGJlZm9yZSA/IGJlZm9yZS5uZXh0RWxlbWVudFNpYmxp'
            'bmcgOiBsb2cuZmlyc3RFbGVtZW50Q2hpbGQ7CiAgICBpZiAoZGF0YS5pdGVtcyAmJiBkYXRhLml0ZW1zLmxlbmd0aCkgewogICAg'
            'ICBzY3JvbGxUbyhmaXJzdCwgJ3N0YXJ0Jyk7ICAgICAgICAgIC8vIHNob3cgdGhlIHN0YXJ0IG9mIHRoZSBwaWNrcwogICAgfSBl'
            'bHNlIGlmICghY2hpcHNCb3guaGlkZGVuKSB7CiAgICAgIHNjcm9sbFRvKGNoaXBzQm94LCAnbmVhcmVzdCcpOyAgICAgLy8ga2Vl'
            'cCB0aGUgb3B0aW9ucyBpbiB2aWV3CiAgICB9IGVsc2UgewogICAgICBzY3JvbGxUbyhmb3JtLCAnbmVhcmVzdCcpOwogICAgfQog'
            'IH0KCiAgZnVuY3Rpb24gc2VuZChib2R5KSB7CiAgICBzZXRCdXN5KHRydWUpOwogICAgc2hvd1R5cGluZyh0cnVlKTsKICAgIGZl'
            'dGNoKCcvYWR2aXNvci9jaGF0JywgewogICAgICBtZXRob2Q6ICdQT1NUJywKICAgICAgaGVhZGVyczogeyAnQ29udGVudC1UeXBl'
            'JzogJ2FwcGxpY2F0aW9uL2pzb24nIH0sCiAgICAgIGNyZWRlbnRpYWxzOiAnc2FtZS1vcmlnaW4nLAogICAgICBib2R5OiBKU09O'
            'LnN0cmluZ2lmeShib2R5KQogICAgfSkudGhlbihmdW5jdGlvbiAocikgewogICAgICByZXR1cm4gci5qc29uKCkudGhlbihmdW5j'
            'dGlvbiAoZCkgeyByZXR1cm4geyBzdGF0dXM6IHIuc3RhdHVzLCBkYXRhOiBkIH07IH0pOwogICAgfSkudGhlbihmdW5jdGlvbiAo'
            'cmVzKSB7CiAgICAgIHNob3dUeXBpbmcoZmFsc2UpOwogICAgICBzZXRCdXN5KGZhbHNlKTsKICAgICAgaWYgKHJlcy5kYXRhICYm'
            'IHJlcy5kYXRhLm9rKSB7IHJlbmRlcihyZXMuZGF0YSk7IHJldHVybjsgfQogICAgICBhZGRNZXNzYWdlKCdib3QnLCAocmVzLmRh'
            'dGEgJiYgcmVzLmRhdGEubWVzc2FnZSkgfHwgJ1NvbWV0aGluZyB3ZW50IHdyb25nLiBQbGVhc2UgdHJ5IGFnYWluLicpOwogICAg'
            'fSkuY2F0Y2goZnVuY3Rpb24gKCkgewogICAgICBzaG93VHlwaW5nKGZhbHNlKTsKICAgICAgc2V0QnVzeShmYWxzZSk7CiAgICAg'
            'IGFkZE1lc3NhZ2UoJ2JvdCcsICdXZSBjb3VsZCBub3QgcmVhY2ggdGhlIHNob3AgcmlnaHQgbm93LiBQbGVhc2UgdHJ5IGFnYWlu'
            'IHNob3J0bHksIG9yIGJyb3dzZSB0aGUgc2hvcC4nKTsKICAgIH0pOwogIH0KCiAgZm9ybS5hZGRFdmVudExpc3RlbmVyKCdzdWJt'
            'aXQnLCBmdW5jdGlvbiAoZSkgewogICAgZS5wcmV2ZW50RGVmYXVsdCgpOwogICAgdmFyIHRleHQgPSBpbnB1dC52YWx1ZS50cmlt'
            'KCk7CiAgICBpZiAoIXRleHQgfHwgYnVzeSkgeyByZXR1cm47IH0KICAgIGlucHV0LnZhbHVlID0gJyc7CiAgICBjbGVhckNoaXBz'
            'KCk7CiAgICBhZGRNZXNzYWdlKCd1c2VyJywgdGV4dCk7CiAgICBzZW5kKHsgYWN0aW9uOiAndGV4dCcsIHRleHQ6IHRleHQgfSk7'
            'CiAgfSk7CgogIGRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCdiYy1yZXN0YXJ0JykuYWRkRXZlbnRMaXN0ZW5lcignY2xpY2snLCBm'
            'dW5jdGlvbiAoKSB7CiAgICBpZiAoYnVzeSkgeyByZXR1cm47IH0KICAgIGxvZy50ZXh0Q29udGVudCA9ICcnOwogICAgY2xlYXJD'
            'aGlwcygpOwogICAgc2VuZCh7IGFjdGlvbjogJ3N0YXJ0JyB9KTsKICB9KTsKCiAgc2VuZCh7IGFjdGlvbjogJ3N0YXJ0JyB9KTsK'
            'fSkoKTsK'
        ),
    },
}

CSS_B64 = (
    'Ci8qIC0tLS0tLS0tLS0gQmVhdXR5IEFkdmlzb3IgY2hhdCAtLS0tLS0tLS0tICovCgouYmMgeyBtYXgtd2lkdGg6IDc2MHB4OyBt'
    'YXJnaW46IDAgYXV0bzsgcGFkZGluZzogMzZweCAwIDU2cHg7IH0KCi5iYy1oZWFkIGgxIHsgZm9udC1zaXplOiAycmVtOyBsaW5l'
    'LWhlaWdodDogMS4xNTsgbWFyZ2luLWJvdHRvbTogOHB4OyB9Ci5iYy1zdWIgeyBjb2xvcjogdmFyKC0tY29sb3ItaW5rLXNvZnQp'
    'OyBtYXgtd2lkdGg6IDU2Y2g7IH0KLmJjLW5vdGUgewogIG1hcmdpbi10b3A6IDE0cHg7CiAgcGFkZGluZzogMTBweCAxNHB4Owog'
    'IGJvcmRlci1sZWZ0OiAzcHggc29saWQgdmFyKC0tY29sb3ItZ29sZCk7CiAgYmFja2dyb3VuZDogdmFyKC0tY29sb3Itc3VyZmFj'
    'ZSk7CiAgY29sb3I6IHZhcigtLWNvbG9yLWluay1zb2Z0KTsKICBmb250LXNpemU6IDAuODRyZW07CiAgbGluZS1oZWlnaHQ6IDEu'
    'NTsKICBtYXgtd2lkdGg6IDY4Y2g7Cn0KCi5iYy1wYW5lbCB7CiAgbWFyZ2luLXRvcDogMjJweDsKICBiYWNrZ3JvdW5kOiB2YXIo'
    'LS1jb2xvci1zdXJmYWNlKTsKICBib3JkZXI6IDFweCBzb2xpZCB2YXIoLS1jb2xvci1ib3JkZXIpOwogIGJvcmRlci1yYWRpdXM6'
    'IDE2cHg7CiAgYm94LXNoYWRvdzogdmFyKC0tc2hhZG93LWNhcmQpOwogIG92ZXJmbG93OiBoaWRkZW47Cn0KCi5iYy1sb2cgewog'
    'IHBhZGRpbmc6IDIycHggMjBweCAxNHB4OwogIGRpc3BsYXk6IGZsZXg7CiAgZmxleC1kaXJlY3Rpb246IGNvbHVtbjsKICBnYXA6'
    'IDE0cHg7Cn0KLmJjLW1zZywgLmJjLWNhcmRzLCAuYmMtaW5saW5lLW5vdGUgeyBzY3JvbGwtbWFyZ2luLXRvcDogODhweDsgfQou'
    'YmMtY2hpcHMsIC5iYy1jb21wb3NlciB7IHNjcm9sbC1tYXJnaW4tYm90dG9tOiAxNnB4OyB9CgouYmMtbXNnIHsgZGlzcGxheTog'
    'ZmxleDsgYWxpZ24taXRlbXM6IGZsZXgtc3RhcnQ7IGdhcDogMTBweDsgbWF4LXdpZHRoOiAxMDAlOyB9Ci5iYy1tc2cgLmJjLWJ1'
    'YmJsZSB7IG1hcmdpbjogMDsgbGluZS1oZWlnaHQ6IDEuNTU7IGZvbnQtc2l6ZTogMC45N3JlbTsgb3ZlcmZsb3ctd3JhcDogYW55'
    'd2hlcmU7IH0KCi5iYy1ib3QgLmJjLWJ1YmJsZSB7IGNvbG9yOiB2YXIoLS1jb2xvci1pbmspOyBtYXgtd2lkdGg6IDYyY2g7IH0K'
    'LmJjLXVzZXIgeyBqdXN0aWZ5LWNvbnRlbnQ6IGZsZXgtZW5kOyB9Ci5iYy11c2VyIC5iYy1idWJibGUgewogIGJhY2tncm91bmQ6'
    'IHZhcigtLWNvbG9yLWJsdXNoKTsKICBjb2xvcjogdmFyKC0tY29sb3ItcHJpbWFyeS1kYXJrKTsKICBwYWRkaW5nOiA5cHggMTVw'
    'eDsKICBib3JkZXItcmFkaXVzOiAxOHB4IDE4cHggNHB4IDE4cHg7CiAgbWF4LXdpZHRoOiA4MCU7Cn0KCi5iYy1tYXJrIHsKICBm'
    'bGV4OiAwIDAgYXV0bzsKICB3aWR0aDogMjZweDsKICBoZWlnaHQ6IDI2cHg7CiAgbWFyZ2luLXRvcDogMXB4OwogIGJvcmRlci1y'
    'YWRpdXM6IDUwJSA1MCUgNTAlIDhweDsKICBiYWNrZ3JvdW5kOiB2YXIoLS1jb2xvci1wcmltYXJ5KTsKfQoKLmJjLXR5cGluZyAu'
    'YmMtYnViYmxlIHsgZGlzcGxheTogaW5saW5lLWZsZXg7IGdhcDogNHB4OyBwYWRkaW5nOiA4cHggMDsgfQouYmMtdHlwaW5nIGkg'
    'ewogIHdpZHRoOiA2cHg7IGhlaWdodDogNnB4OyBib3JkZXItcmFkaXVzOiA1MCU7CiAgYmFja2dyb3VuZDogdmFyKC0tY29sb3It'
    'aW5rLXNvZnQpOwogIG9wYWNpdHk6IDAuMzU7CiAgYW5pbWF0aW9uOiBiYy1wdWxzZSAxcyBpbmZpbml0ZSBlYXNlLWluLW91dDsK'
    'fQouYmMtdHlwaW5nIGk6bnRoLWNoaWxkKDIpIHsgYW5pbWF0aW9uLWRlbGF5OiAwLjE1czsgfQouYmMtdHlwaW5nIGk6bnRoLWNo'
    'aWxkKDMpIHsgYW5pbWF0aW9uLWRlbGF5OiAwLjNzOyB9CkBrZXlmcmFtZXMgYmMtcHVsc2UgeyA1MCUgeyBvcGFjaXR5OiAxOyB9'
    'IH0KCi5iYy1pbmxpbmUtbm90ZSB7CiAgbWFyZ2luOiAwIDAgMCAzNnB4OwogIGNvbG9yOiB2YXIoLS1jb2xvci1pbmstc29mdCk7'
    'CiAgZm9udC1zaXplOiAwLjgycmVtOwogIGxpbmUtaGVpZ2h0OiAxLjU7CiAgbWF4LXdpZHRoOiA2MGNoOwp9CgovKiBwcm9kdWN0'
    'IGNhcmRzIGluc2lkZSB0aGUgY29udmVyc2F0aW9uICovCi5iYy1jYXJkcyB7IGRpc3BsYXk6IGdyaWQ7IGdhcDogMTJweDsgbWFy'
    'Z2luLWxlZnQ6IDM2cHg7IH0KCi5iYy1jYXJkIHsKICBkaXNwbGF5OiBncmlkOwogIGdyaWQtdGVtcGxhdGUtY29sdW1uczogOTZw'
    'eCAxZnI7CiAgZ2FwOiAxNHB4OwogIHBhZGRpbmc6IDEycHg7CiAgYmFja2dyb3VuZDogdmFyKC0tY29sb3ItYmcpOwogIGJvcmRl'
    'cjogMXB4IHNvbGlkIHZhcigtLWNvbG9yLWJvcmRlcik7CiAgYm9yZGVyLXJhZGl1czogdmFyKC0tcmFkaXVzKTsKfQouYmMtY2Fy'
    'ZCBpbWcsIC5iYy1jYXJkLXBoIHsKICB3aWR0aDogOTZweDsKICBoZWlnaHQ6IDk2cHg7CiAgb2JqZWN0LWZpdDogY292ZXI7CiAg'
    'Ym9yZGVyLXJhZGl1czogOHB4OwogIGJhY2tncm91bmQ6IHZhcigtLWNvbG9yLWJsdXNoKTsKfQouYmMtY2FyZCBoMyB7IGZvbnQt'
    'c2l6ZTogMS4wMnJlbTsgbGluZS1oZWlnaHQ6IDEuMzsgbWFyZ2luLWJvdHRvbTogNHB4OyB9Ci5iYy1jYXJkLW1ldGEgeyBkaXNw'
    'bGF5OiBmbGV4OyBmbGV4LXdyYXA6IHdyYXA7IGFsaWduLWl0ZW1zOiBiYXNlbGluZTsgZ2FwOiA0cHggMTJweDsgbWFyZ2luLWJv'
    'dHRvbTogNnB4OyB9Ci5iYy1jYXJkLW1ldGEgLnByaWNlIHsgbWFyZ2luOiAwOyB9Ci5iYy1jYXJkLW1ldGEgLnN0b2NrIHsgbWFy'
    'Z2luOiAwOyBmb250LXNpemU6IDAuODJyZW07IH0KLmJjLXJlYXNvbnMgeyBtYXJnaW46IDAgMCAxMHB4IDE4cHg7IGZvbnQtc2l6'
    'ZTogMC44OHJlbTsgY29sb3I6IHZhcigtLWNvbG9yLWluay1zb2Z0KTsgbGluZS1oZWlnaHQ6IDEuNTsgfQouYmMtcmVhc29ucyBs'
    'aSArIGxpIHsgbWFyZ2luLXRvcDogMnB4OyB9Ci5iYy1jYXJkLWFjdGlvbnMgeyBkaXNwbGF5OiBmbGV4OyBmbGV4LXdyYXA6IHdy'
    'YXA7IGFsaWduLWl0ZW1zOiBjZW50ZXI7IGdhcDogOHB4IDE2cHg7IH0KLmJjLXZpZXcgeyBmb250LXNpemU6IDAuOXJlbTsgZm9u'
    'dC13ZWlnaHQ6IDYwMDsgY29sb3I6IHZhcigtLWNvbG9yLXByaW1hcnkpOyB9Ci5iYy12aWV3OmhvdmVyIHsgY29sb3I6IHZhcigt'
    'LWNvbG9yLXByaW1hcnktZGFyayk7IH0KLmJjLWNhcmQgLmJjLWFkZCB7IHBhZGRpbmc6IDhweCAxOHB4OyBmb250LXNpemU6IDAu'
    'ODZyZW07IH0KLmJjLWNhcmQgLmJjLWFkZDpkaXNhYmxlZCB7IG9wYWNpdHk6IDAuNjU7IGN1cnNvcjogZGVmYXVsdDsgfQouYmMt'
    'YWRkLXN0YXR1cyB7IGRpc3BsYXk6IGJsb2NrOyBtYXJnaW4tdG9wOiA2cHg7IGZvbnQtc2l6ZTogMC44NHJlbTsgY29sb3I6IHZh'
    'cigtLWNvbG9yLWluay1zb2Z0KTsgfQouYmMtYWRkLXN0YXR1czplbXB0eSB7IGRpc3BsYXk6IG5vbmU7IH0KCi8qIG9wdGlvbiBj'
    'aGlwcyAqLwouYmMtY2hpcHMgewogIGRpc3BsYXk6IGZsZXg7CiAgZmxleC13cmFwOiB3cmFwOwogIGdhcDogOHB4OwogIHBhZGRp'
    'bmc6IDEwcHggMjBweCAxNnB4OwogIGJvcmRlci10b3A6IDFweCBzb2xpZCB2YXIoLS1jb2xvci1ib3JkZXIpOwogIGJhY2tncm91'
    'bmQ6IHZhcigtLWNvbG9yLWJnKTsKfQouYmMtY2hpcCB7CiAgcGFkZGluZzogOHB4IDE1cHg7CiAgYm9yZGVyOiAxcHggc29saWQg'
    'dmFyKC0tY29sb3ItcHJpbWFyeSk7CiAgYm9yZGVyLXJhZGl1czogMjBweDsKICBiYWNrZ3JvdW5kOiB2YXIoLS1jb2xvci1zdXJm'
    'YWNlKTsKICBjb2xvcjogdmFyKC0tY29sb3ItcHJpbWFyeSk7CiAgZm9udC1mYW1pbHk6IHZhcigtLWZvbnQtYm9keSk7CiAgZm9u'
    'dC1zaXplOiAwLjlyZW07CiAgZm9udC13ZWlnaHQ6IDUwMDsKICBjdXJzb3I6IHBvaW50ZXI7CiAgdHJhbnNpdGlvbjogYmFja2dy'
    'b3VuZCAwLjE1cyBlYXNlLCBjb2xvciAwLjE1cyBlYXNlOwp9Ci5iYy1jaGlwOmhvdmVyOm5vdCg6ZGlzYWJsZWQpIHsgYmFja2dy'
    'b3VuZDogdmFyKC0tY29sb3ItYmx1c2gpOyB9Ci5iYy1jaGlwLmlzLW9uIHsgYmFja2dyb3VuZDogdmFyKC0tY29sb3ItcHJpbWFy'
    'eSk7IGNvbG9yOiAjZmZmOyB9Ci5iYy1jaGlwOmRpc2FibGVkIHsgb3BhY2l0eTogMC41NTsgY3Vyc29yOiBkZWZhdWx0OyB9Ci5i'
    'Yy1jaGlwLWdvIHsgYmFja2dyb3VuZDogdmFyKC0tY29sb3ItcHJpbWFyeSk7IGNvbG9yOiAjZmZmOyB9Ci5iYy1jaGlwLWdvOmhv'
    'dmVyOm5vdCg6ZGlzYWJsZWQpIHsgYmFja2dyb3VuZDogdmFyKC0tY29sb3ItcHJpbWFyeS1kYXJrKTsgfQouYmMtY2hpcC1xdWll'
    'dCB7IGJvcmRlci1jb2xvcjogdmFyKC0tY29sb3ItYm9yZGVyKTsgY29sb3I6IHZhcigtLWNvbG9yLWluay1zb2Z0KTsgfQoKLyog'
    'Y29tcG9zZXIgKi8KLmJjLWNvbXBvc2VyIHsKICBkaXNwbGF5OiBmbGV4OwogIGdhcDogMTBweDsKICBwYWRkaW5nOiAxMnB4IDE2'
    'cHg7CiAgYm9yZGVyLXRvcDogMXB4IHNvbGlkIHZhcigtLWNvbG9yLWJvcmRlcik7CiAgYmFja2dyb3VuZDogdmFyKC0tY29sb3It'
    'c3VyZmFjZSk7Cn0KLmJjLWNvbXBvc2VyIGlucHV0IHsKICBmbGV4OiAxIDEgYXV0bzsKICBtaW4td2lkdGg6IDA7CiAgcGFkZGlu'
    'ZzogMTFweCAxNnB4OwogIGJvcmRlcjogMXB4IHNvbGlkIHZhcigtLWNvbG9yLWJvcmRlcik7CiAgYm9yZGVyLXJhZGl1czogMjRw'
    'eDsKICBiYWNrZ3JvdW5kOiB2YXIoLS1jb2xvci1iZyk7CiAgY29sb3I6IHZhcigtLWNvbG9yLWluayk7CiAgZm9udC1mYW1pbHk6'
    'IHZhcigtLWZvbnQtYm9keSk7CiAgZm9udC1zaXplOiAxcmVtOwp9Ci5iYy1jb21wb3NlciBpbnB1dDpmb2N1cyB7IGJvcmRlci1j'
    'b2xvcjogdmFyKC0tY29sb3ItcHJpbWFyeSk7IH0KLmJjLXNlbmQgeyBwYWRkaW5nOiAxMHB4IDI0cHg7IH0KLmJjLXNlbmQ6ZGlz'
    'YWJsZWQgeyBvcGFjaXR5OiAwLjY7IGN1cnNvcjogZGVmYXVsdDsgfQoKLmJjLWZvb3QgeyBkaXNwbGF5OiBmbGV4OyBmbGV4LXdy'
    'YXA6IHdyYXA7IGdhcDogOHB4IDIycHg7IG1hcmdpbi10b3A6IDE2cHg7IGZvbnQtc2l6ZTogMC45cmVtOyB9Ci5iYy1saW5rIHsK'
    'ICBiYWNrZ3JvdW5kOiBub25lOwogIGJvcmRlcjogbm9uZTsKICBwYWRkaW5nOiAwOwogIGNvbG9yOiB2YXIoLS1jb2xvci1wcmlt'
    'YXJ5KTsKICBmb250OiBpbmhlcml0OwogIHRleHQtZGVjb3JhdGlvbjogdW5kZXJsaW5lOwogIHRleHQtdW5kZXJsaW5lLW9mZnNl'
    'dDogM3B4OwogIGN1cnNvcjogcG9pbnRlcjsKfQouYmMtbGluazpob3ZlciB7IGNvbG9yOiB2YXIoLS1jb2xvci1wcmltYXJ5LWRh'
    'cmspOyB9CgouYmMtc3IgewogIHBvc2l0aW9uOiBhYnNvbHV0ZTsgd2lkdGg6IDFweDsgaGVpZ2h0OiAxcHg7IG92ZXJmbG93OiBo'
    'aWRkZW47CiAgY2xpcDogcmVjdCgwIDAgMCAwKTsgd2hpdGUtc3BhY2U6IG5vd3JhcDsKfQoKQG1lZGlhIChtYXgtd2lkdGg6IDU2'
    'MHB4KSB7CiAgLmJjIHsgcGFkZGluZy10b3A6IDI0cHg7IH0KICAuYmMtaGVhZCBoMSB7IGZvbnQtc2l6ZTogMS42cmVtOyB9CiAg'
    'LmJjLWxvZyB7IHBhZGRpbmc6IDE2cHggMTRweCAxMHB4OyB9CiAgLmJjLWNhcmRzLCAuYmMtaW5saW5lLW5vdGUgeyBtYXJnaW4t'
    'bGVmdDogMDsgfQogIC5iYy1jYXJkIHsgZ3JpZC10ZW1wbGF0ZS1jb2x1bW5zOiA3MnB4IDFmcjsgZ2FwOiAxMHB4OyB9CiAgLmJj'
    'LWNhcmQgaW1nLCAuYmMtY2FyZC1waCB7IHdpZHRoOiA3MnB4OyBoZWlnaHQ6IDcycHg7IH0KICAuYmMtY2hpcHMgeyBwYWRkaW5n'
    'OiAxMHB4IDE0cHggMTRweDsgfQogIC5iYy1jb21wb3NlciB7IHBhZGRpbmc6IDEwcHggMTJweDsgfQogIC5iYy1zZW5kIHsgcGFk'
    'ZGluZzogMTBweCAxOHB4OyB9Cn0KCkBtZWRpYSAocHJlZmVycy1yZWR1Y2VkLW1vdGlvbjogcmVkdWNlKSB7CiAgLmJjLXR5cGlu'
    'ZyBpIHsgYW5pbWF0aW9uOiBub25lOyBvcGFjaXR5OiAwLjY7IH0KfQo='
)

REQUIRED = [
    "main.py", "api_client.py", "config.py", "advisor_engine.py", "advisor_extras.py",
    os.path.join("routers", "advisor.py"), os.path.join("templates", "base.html"),
    os.path.join("static", "css", "style.css"),
]


def say(msg):
    print(msg)


def die(msg):
    print("\nSTOPPED: " + msg)
    print("Nothing was left half-changed." )
    sys.exit(1)


def find_root(arg):
    for cand in [arg, os.getcwd(), DEFAULT_ROOT]:
        if cand and os.path.isfile(os.path.join(cand, "main.py")) and os.path.isdir(os.path.join(cand, "routers")):
            return os.path.abspath(cand)
    die("Could not find the storefront folder (the one with main.py and routers). "
        "Run this from apps\\ecommerce or use --root \"<path>\".")


def rd(path):
    with open(path, "rb") as fh:
        return fh.read()


def wr(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def nl_of(data):
    return b"\r\n" if b"\r\n" in data else b"\n"


def newest_backup(root):
    items = sorted(d for d in os.listdir(root)
                   if d.startswith(BACKUP_PREFIX) and os.path.isfile(os.path.join(root, d, "manifest.json")))
    return os.path.join(root, items[-1]) if items else None


def undo(root):
    bk = newest_backup(root)
    if not bk or not os.path.isfile(os.path.join(bk, "manifest.json")):
        die("No advisor-chat backup found, nothing to undo.")
    man = json.load(open(os.path.join(bk, "manifest.json"), encoding="utf-8"))
    for rel in man["restored"]:
        shutil.copy2(os.path.join(bk, rel.replace("/", os.sep)), os.path.join(root, rel.replace("/", os.sep)))
        say("restored  " + rel)
    for rel in man["created"]:
        p = os.path.join(root, rel.replace("/", os.sep))
        if os.path.isfile(p):
            os.remove(p)
            say("removed   " + rel)
    os.replace(os.path.join(bk, "manifest.json"), os.path.join(bk, "manifest.used.json"))
    say("\nUndo complete. Restart the storefront.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=None)
    ap.add_argument("--undo", action="store_true")
    args = ap.parse_args()
    root = find_root(args.root)
    say("Storefront folder: " + root)
    if args.undo:
        undo(root)
        return

    for rel in REQUIRED:
        if not os.path.isfile(os.path.join(root, rel)):
            die("Missing expected file: " + rel + ". Is this the right folder?")
    if os.path.isdir(os.path.join(root, "..", "..", "apps", "pos")) and os.path.abspath(
            os.path.join(root, "..", "..", "apps", "pos")) == os.path.abspath(root):
        die("This looks like the POS folder. The advisor belongs only to the e-commerce storefront.")

    if os.path.isfile(os.path.join(root, "advisor_flow.py")) or b"advisor_chat" in rd(os.path.join(root, "main.py")):
        die("The Beauty Advisor chat is already installed here. To install a newer version, "
            "run:  python apply_advisor_chat.py --undo   and then run this installer again.")

    # ---- verify embedded files (guards against a damaged download)
    decoded = {}
    for rel, d in FILES.items():
        data = base64.b64decode(d["b64"])
        if hashlib.sha256(data).hexdigest() != d["sha256"]:
            die("The installer file is damaged (checksum failed for %s). Download it again." % rel)
        decoded[rel] = data
    css = base64.b64decode(CSS_B64)

    # ---- plan the three edits in memory first, so a surprise stops us before any change
    main_path = os.path.join(root, "main.py")
    mp = rd(main_path)
    mp_new = mp
    if b"advisor_chat" not in mp:
        nl = nl_of(mp)
        a_imp = b"from routers import storefront, cart, checkout, advisor"
        a_inc = b"app.include_router(advisor.router)"
        if mp.count(a_imp) != 1 or mp.count(a_inc) != 1:
            die("main.py does not look as expected (the routers import line or the "
                "advisor include line was not found exactly once). Send me main.py.")
        mp_new = mp.replace(a_imp, a_imp + b", advisor_chat")
        mp_new = mp_new.replace(a_inc, b"app.include_router(advisor_chat.router)" + nl + a_inc)

    css_path = os.path.join(root, "static", "css", "style.css")
    cp = rd(css_path)
    cp_new = cp
    if MARK_CSS.encode() not in cp:
        nl = nl_of(cp)
        block = css.replace(b"\n", nl)
        cp_new = cp.rstrip() + nl + block

    base_path = os.path.join(root, "templates", "base.html")
    bp = rd(base_path)
    bp_new = bp
    if b'href="/advisor' not in bp:
        anchor = b'<a href="/shop">Shop</a>'
        if bp.count(anchor) == 1:
            bp_new = bp.replace(anchor, anchor + (b"\r\n" if b"\r\n" in bp else b"\n") +
                                b'                    <a href="/advisor">Beauty Advisor</a>')
        else:
            say("note: could not place a nav link automatically (the Shop link was not found once). "
                "The advisor still works at /advisor.")

    # ---- backup, then write
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bk, n = os.path.join(root, BACKUP_PREFIX + stamp), 1
    while os.path.exists(bk):
        n += 1
        bk = os.path.join(root, BACKUP_PREFIX + stamp + "_" + str(n))
    os.makedirs(bk)
    restored, created = [], []
    for rel, old, new in [("main.py", mp, mp_new), ("static/css/style.css", cp, cp_new),
                          ("templates/base.html", bp, bp_new)]:
        if old != new:
            dst = os.path.join(bk, rel.replace("/", os.sep))
            wr(dst, old)
            restored.append(rel)
    for rel in FILES:
        p = os.path.join(root, rel.replace("/", os.sep))
        if os.path.isfile(p):
            wr(os.path.join(bk, rel.replace("/", os.sep)), rd(p))
            restored.append(rel)
        else:
            created.append(rel)
    json.dump({"restored": restored, "created": created}, open(os.path.join(bk, "manifest.json"), "w", encoding="utf-8"))

    try:
        for rel, data in decoded.items():
            wr(os.path.join(root, rel.replace("/", os.sep)), data)
            say("wrote     " + rel)
        for path, new, old, rel in [(main_path, mp_new, mp, "main.py"),
                                    (css_path, cp_new, cp, "static/css/style.css"),
                                    (base_path, bp_new, bp, "templates/base.html")]:
            if new != old:
                wr(path, new)
                say("edited    " + rel)
        for rel in list(FILES) + ["main.py"]:
            if rel.endswith(".py"):
                py_compile.compile(os.path.join(root, rel.replace("/", os.sep)), doraise=True)
        say("checked   all Python files compile")
    except Exception as exc:  # restore everything and stop
        say("\nSomething failed: %r. Restoring your files..." % (exc,))
        undo(root)
        die("The install was rolled back.")

    say("\nDone. Backup folder: " + os.path.basename(bk))
    say("\nNext steps")
    say("1. Restart the storefront, then open http://127.0.0.1:<port>/advisor")
    say("2. The chat works now with NO key (free built-in logic).")
    say("3. To turn on Gemini for typed messages (optional, free tier), in PowerShell:")
    say('     setx GEMINI_API_KEY "your-key-from-aistudio.google.com"')
    say("   then close and reopen the terminal and restart the storefront.")
    say("   Optional: setx ADVISOR_AI_DAILY_CAP 150   (shop-wide AI calls per day)")
    say("             setx ADVISOR_AI_PER_VISITOR 12  (AI calls per visitor per day)")
    say("             setx GEMINI_MODEL gemini-3.5-flash-lite")
    say("4. The old wizard is still at /advisor/classic.")
    say("To remove everything:  python apply_advisor_chat.py --undo")


if __name__ == "__main__":
    main()
