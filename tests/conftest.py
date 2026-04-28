from __future__ import annotations

import os


if os.name == "nt":
    _ORIGINAL_MKDIR = os.mkdir

    def _mkdir_windows_accessible(path, mode=0o777, *, dir_fd=None):
        # In this Windows/Codex environment, directories created with 0o700 can
        # become unreadable to the same process. Pytest creates tmp_path roots
        # with 0o700, so coerce that test-only mode to a readable user dir.
        effective_mode = 0o755 if mode == 0o700 else mode
        if dir_fd is None:
            return _ORIGINAL_MKDIR(path, effective_mode)
        return _ORIGINAL_MKDIR(path, effective_mode, dir_fd=dir_fd)

    os.mkdir = _mkdir_windows_accessible
