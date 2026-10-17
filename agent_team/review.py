"""Review of what agents changed in a run's workspace.

The accepted state is kept in a second folder, the baseline. A file that differs from the baseline is pending.
Accepting a change copies the current file into the baseline. Rejecting copies the baseline back over the
workspace. Export is the baseline, so it only ever contains reviewed content.
"""

import difflib
import io
import shutil
import zipfile
from pathlib import Path

from .tools import ToolError, Workspace

MAX_DIFF_LINES = 400
MAX_TEXT_BYTES = 200_000


class WorkspaceReview:
    def __init__(self, workspace_root, baseline_root):
        self.workspace = Workspace(workspace_root)
        self.baseline = Path(baseline_root)

    def ensure_baseline(self):
        """Create the accepted state if it does not exist yet. For a run that predates reviews, the current
        workspace becomes the accepted state."""
        if self.baseline.exists():
            return
        self.baseline.mkdir(parents=True)
        for path in self._files(self.workspace.root):
            target = self.baseline / path.relative_to(self.workspace.root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)

    def changes(self):
        self.ensure_baseline()
        current = self._map(self.workspace.root)
        accepted = self._map(self.baseline)
        changes = []
        for path in sorted(set(current) | set(accepted)):
            if path in current and path not in accepted:
                status = "added"
            elif path not in current:
                status = "removed"
            elif current[path].read_bytes() != accepted[path].read_bytes():
                status = "changed"
            else:
                continue
            changes.append({"path": path, "status": status, "diff": self._diff(accepted.get(path), current.get(path), path)})
        return changes

    def accept(self, relative):
        self.ensure_baseline()
        current = self.workspace.resolve(relative)
        accepted = self._baseline_path(relative)
        if current.is_file():
            accepted.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(current, accepted)
        elif accepted.is_file():
            accepted.unlink()

    def reject(self, relative):
        self.ensure_baseline()
        current = self.workspace.resolve(relative)
        accepted = self._baseline_path(relative)
        if accepted.is_file():
            current.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(accepted, current)
        elif current.is_file():
            current.unlink()

    def accepted_archive(self):
        """A zip of the accepted state only."""
        self.ensure_baseline()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in self._files(self.baseline):
                archive.write(path, path.relative_to(self.baseline).as_posix())
        return buffer.getvalue()

    def _baseline_path(self, relative):
        """Same rules as the workspace: no escapes, no links. Checked against the baseline's own root."""
        Workspace(self.baseline).resolve(relative)
        return self.baseline / relative

    def _files(self, root):
        return [path for path in Path(root).rglob("*") if path.is_file() and not path.is_symlink()]

    def _map(self, root):
        root = Path(root)
        return {path.relative_to(root).as_posix(): path for path in self._files(root)}

    def _diff(self, before, after, path):
        old = self._text(before)
        new = self._text(after)
        if old is False or new is False:
            return None  # binary or too large: listed, not shown
        lines = list(
            difflib.unified_diff(
                (old or "").splitlines(keepends=True),
                (new or "").splitlines(keepends=True),
                fromfile=f"accepted/{path}",
                tofile=f"current/{path}",
            )
        )
        if len(lines) > MAX_DIFF_LINES:
            lines = lines[:MAX_DIFF_LINES] + ["... the diff continues\n"]
        return "".join(lines)

    def _text(self, path):
        if path is None:
            return None
        data = path.read_bytes()
        if len(data) > MAX_TEXT_BYTES or b"\x00" in data:
            return False
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return False
