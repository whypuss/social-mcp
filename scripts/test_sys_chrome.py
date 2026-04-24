#!/usr/bin/env python3
"""Test system Chrome.app with Profile 3."""
import subprocess, time, urllib.request, json, os

SYS_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILE_DIR = os.path.expanduser("~/Library/Application Support/Google/Chrome")
PROFILE = "Profile 3"
PORT = 9333

print(f"Chrome: {SYS_CHROME}")
print(f"Exists: {os.path.exists(SYS_CHROME)}")
print(f"Profile: {PROFILE_DIR}/{PROFILE}")

# Use a fresh temp dir to avoid singleton lock
import tempfile
TEMP_DIR = tempfile.mkdtemp(prefix="social_mcp_chrome_")
print(f"Temp dir: {TEMP_DIR}")

cmd = [
    SYS_CHROME,
    f"--remote-debugging-port={PORT}",
    f"--user-data-dir={TEMP_DIR}",
    f"--profile-directory={PROFILE}",
    "--no-first-run",
    "--no-default-browser-check",
    "--headless=new",
    "--enable-unsafe-swiftshader",
    "--window-size=1280,720",
    "about:blank",
]
print(f"Starting Chrome...")
proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print(f"PID: {proc.pid}")

# Wait for Chrome
for i in range(20):
    time.sleep(1)
    try:
        req = urllib.request.Request(f"http://localhost:{PORT}/json/version", headers={"User-Agent": "Chrome-CDP"})
        with urllib.request.urlopen(req, timeout=2) as r:
            if r.status == 200:
                data = json.loads(r.read())
                print(f"Chrome started! {data.get('Browser')}")
                break
    except Exception as e:
        print(f"  {i+1}/20: waiting...")
else:
    print("FAILED: Chrome did not start")

proc.terminate()
proc.wait(timeout=5)
print("Done")
