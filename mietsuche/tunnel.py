"""Zugang von außen über ngrok, wenn die App auf einem Laptop läuft."""

from __future__ import annotations

import atexit
import logging
import shutil
import subprocess
import sys
import threading
import time

log = logging.getLogger("mietsuche")


def ngrok_command(domain: str, port: int) -> list[str] | None:
    exe = shutil.which("ngrok")
    if exe is None:
        return None
    url = domain if domain.startswith("https://") else f"https://{domain}"
    return [exe, "http", str(port), f"--url={url}", "--log=stdout"]


def start_ngrok(domain: str, port: int) -> None:
    """Startet ngrok und hält es am Laufen, solange die App läuft."""
    cmd = ngrok_command(domain, port)
    if cmd is None:
        log.error("ngrok ist nicht installiert, die App ist nur lokal erreichbar.")
        return
    # Unter Windows kein eigenes Konsolenfenster öffnen.
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

    def run() -> None:
        while True:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=flags)
            atexit.register(proc.terminate)
            log.info("ngrok gestartet: %s", cmd[3])
            proc.wait()
            log.warning("ngrok beendet (Code %s), Neustart in 30 Sekunden", proc.returncode)
            time.sleep(30)

    threading.Thread(target=run, daemon=True).start()
