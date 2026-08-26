"""A filesystem backend that forgives host-absolute paths.

The agent lives in two path worlds at once. The file tools run against a
sandboxed backend where the workspace is `/`, while `bash` runs against the real
filesystem and prints real paths. So the model reads
`/Users/you/project/app` out of `ls` output, hands it to `read_file`, and gets
`path_not_found` for a file that is plainly there.

Telling the model about the distinction helps but never fully holds -- every
line of shell output re-tempts it. So the backend accepts either spelling and
translates, keeping the sandbox intact: a path outside the workspace is still
refused, exactly as before.
"""

from __future__ import annotations

from pathlib import Path

from deepagents.backends import FilesystemBackend


class ForgivingFilesystemBackend(FilesystemBackend):
    """`FilesystemBackend`, but a real path inside the workspace also works."""

    def _resolve_path(self, key: str) -> Path:
        return super()._resolve_path(self._as_virtual(key))

    def _as_virtual(self, key: str) -> str:
        """Rewrite a host path under the workspace as the virtual path for it."""
        if not self.virtual_mode or not key:
            return key

        expanded = key
        if key.startswith("~"):
            expanded = str(Path(key).expanduser())
        # A host path is absolute; on Windows that means a drive letter rather
        # than a leading slash. Keep the POSIX test first so a virtual path --
        # which has no drive and so isn't "absolute" to a WindowsPath -- still
        # reaches the translation below.
        if not (expanded.startswith("/") or Path(expanded).is_absolute()):
            return key

        # The virtual reading wins whenever it names something real -- only fall
        # back to translating when it doesn't, so a workspace that happens to
        # contain a directory shadowing the host prefix still behaves.
        if self._exists_virtually(key):
            return key

        try:
            relative = Path(expanded).relative_to(self.cwd)
        except ValueError:
            return key  # genuinely outside the workspace; let the sandbox refuse it

        return "/" + relative.as_posix() if relative.parts else "/"

    def _exists_virtually(self, key: str) -> bool:
        try:
            return super()._resolve_path(key).exists()
        except (ValueError, OSError):
            return False
