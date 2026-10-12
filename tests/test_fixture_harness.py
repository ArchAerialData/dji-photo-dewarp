"""Fixture harness safety/selection checks; never read or process the real fixture."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from tools.test_real_fixture import choose_photos, metadata_diagnostics, normalized_warning, snapshot, stage


class FixtureHarnessTests(unittest.TestCase):
    def test_warning_comparison_retains_severity_and_diagnostic_content(self):
        self.assertEqual(normalized_warning("Warning: [minor] Maker offsets (fix by -452?) - " + str(Path.cwd() / "photo.JPG")),
                         "[minor] Maker offsets (fix by -452?)")
        self.assertNotEqual(normalized_warning("Warning: Bad offset (fix by -366?)"),
                            normalized_warning("Warning: Bad offset (fix by -452?)"))
        self.assertEqual(normalized_warning("Invalid marker - ignored"), "Invalid marker - ignored")
        self.assertEqual(normalized_warning("Warning: [minor] Source warning - K:/Fixture/Conan CS - Dewarped Photos/photo.JPG"),
                         "[minor] Source warning")

    def test_metadata_diagnostics_keep_repeated_warnings_and_reject_errors(self):
        session = Mock()
        session.execute.return_value = (json.dumps([{"Warning": "first", "Copy1:Warning": "second"}]), [])
        self.assertEqual(metadata_diagnostics(session, Path("photo.JPG"))["warnings"], ["first", "second"])
        session.execute.return_value = (json.dumps([{"Error": "broken metadata"}]), [])
        with self.assertRaisesRegex(AssertionError, "broken metadata"):
            metadata_diagnostics(session, Path("photo.JPG"))

    def test_even_samples_include_boundaries_and_do_not_repeat(self):
        manifest = {"files": [{"path": f"Flight/{index:04d}.JPG"} for index in range(100)]
                    + [{"path": "Flight/GPS.MRK"}, {"path": "Small/1.jpeg"}]}
        selected = choose_photos(manifest, 12)
        names = [item["path"] for item in selected["Flight"]]
        self.assertEqual(len(set(names)), 12)
        self.assertEqual(names[0], "Flight/0000.JPG")
        self.assertEqual(names[-1], "Flight/0099.JPG")
        self.assertEqual(len(selected["Small"]), 1)
        self.assertEqual(len(choose_photos(manifest, None)["Flight"]), 100)

    def test_manifest_detects_content_mtime_and_added_files(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory)
            photo = root / "sample.JPG"
            photo.write_bytes(b"original")
            before = snapshot(root)
            self.assertEqual(before, snapshot(root))
            photo.write_bytes(b"modified")
            self.assertNotEqual(before, snapshot(root))
            photo.write_bytes(b"original")
            (root / "unwanted-output").mkdir()
            self.assertNotEqual(before, snapshot(root))

    def test_staging_uses_verified_copies_without_touching_originals(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory)
            source, scratch = root / "Original", root / "Scratch"
            (source / "Flight").mkdir(parents=True)
            scratch.mkdir()
            (source / "Flight" / "Photo.JPG").write_bytes(b"fixture bytes")
            before = snapshot(source)
            selected = choose_photos(before, 12)
            stage({"source": str(source), "scratch": str(scratch), "selected": selected})
            copy = scratch / "inputs" / "Flight" / "Photo.JPG"
            self.assertEqual(copy.read_bytes(), b"fixture bytes")
            copy.write_bytes(b"independently mutable copy")
            self.assertEqual(snapshot(source), before)

    def test_staging_rejects_path_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scratch = root / "Scratch"
            scratch.mkdir()
            with self.assertRaisesRegex(ValueError, "escapes"):
                stage({"source": str(root), "scratch": str(scratch),
                       "selected": {"bad": [{"path": "../../escape.JPG", "sha256": ""}]}})


if __name__ == "__main__":
    unittest.main()
