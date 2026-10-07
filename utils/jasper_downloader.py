"""Automate the Jasper report-download exe via its interactive console prompts.

The exe takes a single keyword (configured once inside the exe itself to
download several reports, each to its own configured output path) and then
a password -- no username.

A plain piped subprocess doesn't work here: the password prompt reads via
a masked, console-only call (e.g. msvcrt.getch(), which reads the real
console input buffer directly and ignores a redirected stdin pipe) --
confirmed by three attempts that hung for 29min/45min/3hrs with ~0 CPU
until force-killed. Instead, this drives the exe through a real Windows
pseudo-console (ConPTY, via the pywinpty package), which a masked read can
actually see, and watches the output to type the keyword/password at the
right prompts -- confirmed working (2026-08-25): the exe echoed the masked
password and made a real authentication attempt against the Jasper server.

Confirmed (2026-09-18): authentication and both downloads were succeeding
the entire time -- the exe finishes in ~2-3 minutes and then prompts
"Would you like to run another task? (ENTER/y to run again)", which nothing
was answering, so every run sat there until the 400s timeout force-killed
it and got logged as a failure. Fixed by answering "n" to that prompt so
the exe exits on its own once it's done.

The password itself is never stored here or in jasper_config.json -- it
lives in Windows Credential Manager (see setup_jasper_password.py).
`credential_username` in the config is only a label for where we store that
password locally; it isn't sent to the exe. No Streamlit imports here on
purpose, so this is usable standalone from archive_snapshot.py.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from pathlib import Path

import keyring

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "jasper_config.json"
CREDENTIAL_SERVICE = "nbs-dashboard-jasper"

log = logging.getLogger(__name__)


def load_config(config_path: Path = CONFIG_PATH) -> dict:
    if not config_path.exists():
        raise FileNotFoundError(
            f"{config_path} not found. Copy jasper_config.example.json to jasper_config.json and fill it in."
        )
    text = config_path.read_text()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{config_path} is not valid JSON ({exc}). If you typed a Windows path into exe_path, every "
            r'backslash needs to be doubled -- e.g. "C:\\path\\to\\app.exe" or "\\\\server\\share\\app.exe" '
            "for a network path -- or use forward slashes instead (C:/path/to/app.exe)."
        ) from exc


def get_password(credential_username: str) -> str:
    password = keyring.get_password(CREDENTIAL_SERVICE, credential_username)
    if not password:
        raise RuntimeError(
            f"No password stored for '{credential_username}' under service '{CREDENTIAL_SERVICE}'. "
            "Run setup_jasper_password.py first."
        )
    return password


def keyword_for(config: dict, keyword_set: str) -> str:
    """The Jasper keyword for a download set.

    "live" = MarginNowV2 + prop equity monitor (no trade date needed, safe to run every few minutes).
    "eod"  = FinancialSummary + monthly adjustment (need a trade date that only rolls over after the
             morning EOD process, so they run once a day -- see eod_refresh.py).
    Configs that only have the older single "keyword" use it for "live".
    """
    keywords = config.get("keywords") or {}
    if keyword_set in keywords:
        return keywords[keyword_set]
    if keyword_set == "live" and config.get("keyword"):
        return config["keyword"]
    raise ValueError(
        f"jasper_config.json has no keyword for the '{keyword_set}' download set "
        f'(add it under "keywords": {{"{keyword_set}": "..."}}).'
    )


DEFAULT_TIMEOUT = 400  # a real run takes ~5 minutes manually; leave headroom over that


def _force_kill_tree(pid: int) -> None:
    """taskkill /T /F: a plain Popen.kill() left the child alive in testing here."""
    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(pid)],
        capture_output=True, text=True, timeout=15,
    )


def download_reports(config_path: Path = CONFIG_PATH, timeout: int | None = None,
                      keyword_set: str = "live") -> tuple[bool, str]:
    """Run the exe once with the configured keyword + password.

    Drives the exe through a real Windows pseudo-console (ConPTY, via
    pywinpty) instead of a plain pipe: the password prompt reads via a
    masked console-only call (confirmed by testing -- see module
    docstring), which only a genuine console -- not a redirected pipe --
    can satisfy. We watch the output for the "task name" and "password"
    prompts and type the keyword/password in response, same as a human
    would. The exe downloads whatever reports that keyword is configured
    for (each to its own output path, set up inside the exe itself) in one
    go. Returns (success, combined output).
    """
    from winpty import PtyProcess

    config = load_config(config_path)
    exe_path = config["exe_path"]
    keyword = keyword_for(config, keyword_set)
    password = get_password(config["credential_username"])
    timeout = timeout if timeout is not None else config.get("timeout_seconds", DEFAULT_TIMEOUT)

    try:
        proc = PtyProcess.spawn([exe_path], cwd=str(Path(exe_path).parent))
    except OSError as exc:
        return False, f"Could not launch '{exe_path}': {exc}"

    # proc.read() blocks with no timeout of its own, so it runs on a background
    # thread -- the main thread polls the buffer it fills, on its own clock,
    # so a real wall-clock timeout works even if read() never returns.
    chunks: list[str] = []
    lock = threading.Lock()
    reader_done = threading.Event()

    def _reader() -> None:
        try:
            while True:
                chunk = proc.read(4096)
                if not chunk:
                    break
                with lock:
                    chunks.append(chunk)
        except EOFError:
            pass
        except Exception as exc:  # noqa: BLE001 -- surface any reader-thread failure in the output
            with lock:
                chunks.append(f"\n[reader error: {exc}]")
        finally:
            reader_done.set()

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    sent_keyword = False
    sent_password = False
    declined_rerun = False
    sent_final_enter = False
    start = time.monotonic()
    timed_out = False

    while True:
        if reader_done.is_set() and not proc.isalive():
            break
        if time.monotonic() - start > timeout:
            timed_out = True
            break
        with lock:
            tail = "".join(chunks)[-1000:].lower()
        if not sent_keyword and "task name" in tail:
            # \r alone, not \r\n: the exe's prompts only treat \r as Enter, so a
            # trailing \n is left pending in the input buffer and gets consumed
            # as a stray leading character by the *next* prompt -- confirmed by
            # reproducing it in isolation: corrupted a 9-char password into a
            # 10-char one with a leading "\n", causing every auth attempt to fail.
            proc.write(keyword + "\r")
            sent_keyword = True
            log.info("Sent keyword")
        elif sent_keyword and not sent_password and "password" in tail:
            proc.write(password + "\r")
            sent_password = True
            log.info("Sent password")
        elif sent_password and not declined_rerun and "run another task" in tail:
            # Anything other than blank/"y" declines (the prompt is "ENTER/y to run
            # again"); without this the exe just waits here until force-killed.
            proc.write("n\r")
            declined_rerun = True
            log.info("Declined 'run another task?' prompt")
        elif declined_rerun and not sent_final_enter and "press enter to exit" in tail:
            proc.write("\r")
            sent_final_enter = True
            log.info("Sent final Enter to exit")
        time.sleep(0.2)

    if proc.isalive():
        proc.terminate(force=True)
        time.sleep(0.5)
        if proc.isalive():
            _force_kill_tree(proc.pid)

    reader_thread.join(timeout=5)
    with lock:
        output = "".join(chunks)

    if timed_out:
        return False, (
            f"Timed out after {timeout}s and terminated (sent_keyword={sent_keyword}, "
            f"sent_password={sent_password}).\n{output}"
        )

    success = proc.exitstatus == 0
    if success:
        log.info("Jasper download succeeded for keyword '%s' (reports: %s)", keyword, config.get("reports"))
    else:
        log.error("Jasper download failed for keyword '%s': %s", keyword, output.strip())
    return success, output
