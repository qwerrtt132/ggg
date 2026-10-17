import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path

from agent_team.review import WorkspaceReview
from agent_team.tools import ToolError


class WorkspaceReviewTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = Path(directory.name) / "workspace"
        self.workspace.mkdir()
        self.review = WorkspaceReview(self.workspace, Path(directory.name) / "accepted")
        self.review.ensure_baseline()

    def changes(self):
        return {change["path"]: change for change in self.review.changes()}

    def test_new_changed_and_removed_files_are_pending_until_reviewed(self):
        (self.workspace / "a.txt").write_text("hello\n")
        self.assertEqual(self.changes()["a.txt"]["status"], "added")

        self.review.accept("a.txt")
        self.assertEqual(self.changes(), {})

        (self.workspace / "a.txt").write_text("hello there\n")
        change = self.changes()["a.txt"]
        self.assertEqual(change["status"], "changed")
        self.assertIn("-hello", change["diff"])
        self.assertIn("+hello there", change["diff"])

        (self.workspace / "a.txt").unlink()
        self.assertEqual(self.changes()["a.txt"]["status"], "removed")

    def test_reject_reverts_to_the_accepted_version(self):
        (self.workspace / "a.txt").write_text("accepted\n")
        self.review.accept("a.txt")
        (self.workspace / "a.txt").write_text("unreviewed\n")

        self.review.reject("a.txt")

        self.assertEqual((self.workspace / "a.txt").read_text(), "accepted\n")
        self.assertEqual(self.changes(), {})

    def test_rejecting_a_new_file_removes_it(self):
        (self.workspace / "new.txt").write_text("unwanted")
        self.review.reject("new.txt")
        self.assertFalse((self.workspace / "new.txt").exists())

    def test_export_contains_only_accepted_files(self):
        (self.workspace / "a.txt").write_text("accepted")
        self.review.accept("a.txt")
        (self.workspace / "b.txt").write_text("still pending")

        with zipfile.ZipFile(BytesIO(self.review.accepted_archive())) as archive:
            self.assertEqual(archive.namelist(), ["a.txt"])
            self.assertEqual(archive.read("a.txt"), b"accepted")

    def test_binary_changes_are_listed_without_a_diff(self):
        (self.workspace / "image.bin").write_bytes(b"\x00\xff\x10")
        change = self.changes()["image.bin"]
        self.assertEqual(change["status"], "added")
        self.assertIsNone(change["diff"])

    def test_paths_outside_the_workspace_are_refused(self):
        with self.assertRaises(ToolError):
            self.review.accept("../escape.txt")
        with self.assertRaises(ToolError):
            self.review.reject("/etc/passwd")

    def test_a_missing_accepted_folder_starts_from_the_current_workspace(self):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(directory, ignore_errors=True))
        workspace = directory / "ws"
        workspace.mkdir()
        (workspace / "old.txt").write_text("already here")

        review = WorkspaceReview(workspace, directory / "accepted")
        review.ensure_baseline()

        self.assertEqual(review.changes(), [])


if __name__ == "__main__":
    unittest.main()
