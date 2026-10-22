#!/usr/bin/env python3
"""Start a packaged (not selftest) app, wait for its page, then quit it as 退出 does.

    python3 desktop/scripts/smoke.py <app executable> [--kill]

--kill ends the app the hard way instead (a crash, Force Quit, Task Manager); the
server must still stop.

Its data goes to a fresh temporary folder. Exit code 0 when the server answered
within 60 seconds and the app and its server ended after /api/quit.
"""
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

PORTS = range(8787, 8807)


def up(port):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/status" % port, timeout=1) as resp:
            return resp.status == 200
    except OSError:
        return False


def answering(skip=()):
    """The first port, not in skip, with a Darkslide server on it."""
    return next((port for port in PORTS if port not in skip and up(port)), None)


def main():
    # Servers already running (the script version, say) are left alone.
    others = {port for port in PORTS if up(port)}
    env = dict(os.environ, DARKSLIDE_DATA=tempfile.mkdtemp(prefix="darkslide-smoke-"))
    started = time.monotonic()
    proc = subprocess.Popen([sys.argv[1]], env=env)
    try:
        port = None
        while time.monotonic() - started < 60 and proc.poll() is None and port is None:
            port = answering(others)
            time.sleep(0.25)
        if port is None:
            print("FAIL the server did not answer (app exit code %s)" % proc.poll())
            return 1
        print("PASS the server answered on %d after %.1fs" % (port, time.monotonic() - started))
        if "--kill" in sys.argv:
            # Ended without a chance to clean up, as a crash or Force Quit would.
            proc.kill()
            proc.wait()
            for _ in range(40):
                if not up(port):
                    break
                time.sleep(0.25)
            print("PASS the app was killed")
        else:
            req = urllib.request.Request("http://127.0.0.1:%d/api/quit" % port, data=b"{}", method="POST")
            urllib.request.urlopen(req, timeout=5).close()
            try:
                print("PASS the app quit (exit code %d)" % proc.wait(timeout=20))
            except subprocess.TimeoutExpired:
                print("FAIL the app still runs 20s after /api/quit")
                return 1
        time.sleep(1)
        if up(port):
            print("FAIL the server still answers")
            return 1
        print("PASS the server stopped")
        return 0
    finally:
        if proc.poll() is None:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
