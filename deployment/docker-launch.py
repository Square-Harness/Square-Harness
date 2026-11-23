# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

import os
import stat
import sys

APP_UID = 10001
APP_GID = 10001
SECRET_TARGET = "/run/square-harness/identity.secret"


def stage_proxy_secret():
    source_path = os.environ.get("SQUARE_TRUSTED_PROXY_SECRET_SOURCE")
    if not source_path:
        raise ValueError("Server mode requires SQUARE_TRUSTED_PROXY_SECRET_SOURCE")

    source_fd = os.open(source_path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(source_fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) not in {
            0o400, 0o600, 0o640,
        }:
            raise ValueError("Trusted-proxy source must be a private regular file")
        secret = source.read(4097)
    if not secret or len(secret) > 4096:
        raise ValueError("Trusted-proxy source must contain 1 to 4096 bytes")

    directory = os.path.dirname(SECRET_TARGET)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    os.chown(directory, APP_UID, APP_GID)
    os.chmod(directory, 0o700)
    target_fd = os.open(
        SECRET_TARGET,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(target_fd, "wb") as target:
        os.fchown(target.fileno(), APP_UID, APP_GID)
        os.fchmod(target.fileno(), 0o600)
        target.write(secret)
    os.environ["SQUARE_TRUSTED_PROXY_SECRET_FILE"] = SECRET_TARGET


def main():
    if len(sys.argv) < 2:
        raise ValueError("Expected the application command")
    if os.geteuid() == 0:
        if os.environ.get("SQUARE_HARNESS_MODE", "local") == "server":
            stage_proxy_secret()
        os.setgroups([])
        os.setgid(APP_GID)
        os.setuid(APP_UID)
    os.execvpe(sys.argv[1], sys.argv[1:], os.environ)


if __name__ == "__main__":
    main()
