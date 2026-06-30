from __future__ import annotations

import os
from pathlib import Path


class RunLock:
    """Prevent concurrent collect runs that double API spend."""

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path

    def __enter__(self) -> RunLock:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        if self.lock_path.exists():
            try:
                pid = int(self.lock_path.read_text(encoding="utf-8").strip())
            except ValueError:
                pid = None
            hint = f" (pid {pid})" if pid else ""
            raise RuntimeError(
                f"Another collect run is already active for this work dir{hint}. "
                "Wait for it to finish before starting another."
            )
        self.lock_path.write_text(str(os.getpid()), encoding="utf-8")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.lock_path.unlink(missing_ok=True)
        except OSError:
            pass