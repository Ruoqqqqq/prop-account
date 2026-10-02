"""One-time setup: store the Jasper exe's password in Windows Credential Manager.

Run this once (and again any time the password changes):

    python setup_jasper_password.py

The password is typed with hidden input (via getpass) and handed straight
to Windows Credential Manager through the keyring library -- it is never
written to disk in plain text, logged, or kept in this project's files.
"""

from __future__ import annotations

import getpass

from utils.jasper_downloader import CREDENTIAL_SERVICE, load_config
import keyring


def main() -> None:
    config = load_config()
    username = config["credential_username"]
    password = getpass.getpass(f"Enter the Jasper password for '{username}': ")
    if not password:
        print("No password entered, nothing stored.")
        return
    keyring.set_password(CREDENTIAL_SERVICE, username, password)
    print(f"Stored password for '{username}' in Windows Credential Manager (service '{CREDENTIAL_SERVICE}').")


if __name__ == "__main__":
    main()
