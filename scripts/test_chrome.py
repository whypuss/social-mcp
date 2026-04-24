#!/usr/bin/env python3
"""
Test Chrome for Testing with ~/Library Profile 3.
"""
import subprocess
import time
import urllib.request
import json
import sys
import os

CFT = "/Users/whypuss/.agent-browser/browsers/chrome-147.0.7727.56/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
PROFILE_DIR = os.path.expanduser("~/Library/Application Support/Google/Chrome")
PROFILE = "Profile 3"
PORT = 9222

print(f"Chrome binary: {CFT}")
print(f"Exists: {os.path.exists(CFT)}")
print(f"Profile: {PROFILE_DIR}/{PROFILE}")

# Launch Chrome
cmd = [
    CFT,
    f"--remote-debugging-port={PORT}",
    f"--user-data-dir={PROFILE_DIR}",
    f"--profile-directory={PROFILE}",
    "--no-first-run",
    "--no-default-browser-check",
    "--headless=new",
    "--enable-unsafe-swiftshader",
    "--window-size=1280,720",
    "about:blank",
]
print(f"Launching: {' '.join(cmd[:3])} ...")
proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print(f"PID: {proc.pid}")

# Wait for Chrome to start
for i in range(20):
    time.sleep(1)
    try:
        req = urllib.request.Request(
            f"http://localhost:{PORT}/json/version",
            headers={"User-Agent": "Chrome-CDP-Client"}
        )
        with urllib.request.urlopen(req, timeout=2) as r:
            if r.status == 200:
                data = json.loads(r.read())
                print(f"Chrome started! Browser: {data.get('Browser')}")
                print(f"WebSocket URL: {data.get('webSocketDebuggerUrl', 'N/A')[:80]}")
                break
    except Exception as e:
        print(f"  Attempt {i+1}: waiting... ({e})")
else:
    print("Chrome did not start in 20 seconds")
    proc.terminate()
    sys.exit(1)

# Get cookies via CDP
try:
    tabs_url = urllib.request.urlopen(f"http://localhost:{PORT}/json", timeout=5)
    tabs = json.loads(tabs_url.read())
    ws_url = tabs[0].get("webSocketDebuggerUrl") if tabs else None
    print(f"Tab WebSocket: {ws_url[:80] if ws_url else 'None'}")
except Exception as e:
    print(f"Failed to get tabs: {e}")
    proc.terminate()
    sys.exit(1)

proc.terminate()
proc.wait(timeout=5)
print("Done")
