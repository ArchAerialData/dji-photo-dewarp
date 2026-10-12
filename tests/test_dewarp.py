"""Regression tests using generated photos only; run with unittest discover -s tests."""

from __future__ import annotations

import hashlib
from fractions import Fraction
import io
import json
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import threading
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

import cv2
import numpy as np
from PIL import Image, ImageCms
import piexif

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app" / "src"))
from dewarp.core import engine


CALIBRATION = "2026-10-10;120,118,0.15,-0.25,-0.13,0.035,0.001,-0.0007,0.0001"
GPS = {
    piexif.GPSIFD.GPSLatitudeRef: b"N",
    piexif.GPSIFD.GPSLatitude: ((30, 1), (16, 1), (1234, 100)),
    piexif.GPSIFD.GPSLongitudeRef: b"W",
    piexif.GPSIFD.GPSLongitude: ((97, 1), (44, 1), (5678, 100)),
    piexif.GPSIFD.GPSAltitudeRef: 0,
    piexif.GPSIFD.GPSAltitude: (12345, 100),
}
CAPTURE_TIME = b"2026:10:10 09:08:07"
ICC = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def synthetic_photo(path: Path, *, orientation=1, flag="0", calibration=CALIBRATION):
    """Generate asymmetric content so wrong rotation/calibration is observable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    y, x = np.indices((100, 160))
    rgb = np.stack(((x * 3 + y) % 256, (y * 5) % 256, (x + y * 2) % 256), axis=2)
    rgb = rgb.astype(np.uint8)
    rgb[8:25, 15:45] = (245, 20, 15)
    attributes = f'drone-dji:DewarpFlag="{flag}" drone-dji:FlightYawDegree="42.5"'
    if calibration is not None:
        attributes += f' drone-dji:DewarpData="{calibration}"'
    xmp = (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" '
        'xmlns:drone-dji="http://www.dji.com/drone-dji/1.0/" '
        f'{attributes}/></rdf:RDF></x:xmpmeta>'
    ).encode("utf-8")
    exif = {
        "0th": {piexif.ImageIFD.Orientation: orientation, piexif.ImageIFD.Make: b"DJI"},
        "Exif": {
            piexif.ExifIFD.DateTimeOriginal: CAPTURE_TIME,
            piexif.ExifIFD.PixelXDimension: 160,
            piexif.ExifIFD.PixelYDimension: 100,
        },
        "GPS": dict(GPS),
        "1st": {},
        "Interop": {},
    }
    Image.fromarray(rgb).save(
        path, "JPEG", quality=98, subsampling=0,
        exif=piexif.dump(exif), xmp=xmp, icc_profile=ICC,
    )
    return path


def read_xmp(path: Path):
    with Image.open(path) as image:
        packet = image.info.get("xmp") or image.info.get("XML:com.adobe.xmp")
    if not packet:
        return {}
    root = ET.fromstring(packet)
    values = {}
    for element in root.iter():
        for key, value in element.attrib.items():
            values[key.rsplit("}", 1)[-1]] = value
        if element.text and element.text.strip():
            values[element.tag.rsplit("}", 1)[-1]] = element.text.strip()
    return values


def add_external_makernote(path):
    """A DJI MakerNote directory points to data beyond its own EXIF tag length."""
    payload = b"external-vendor-payload-must-survive-" * 4
    metadata = piexif.load(str(path))
    marker = struct.pack(">H", 1) + struct.pack(">HHII", 0x1001, 7, len(payload), 0) + b"\0" * 4
    metadata["Exif"][piexif.ExifIFD.MakerNote] = marker
    raw = bytearray(piexif.dump(metadata))
    start = raw.index(marker)
    struct.pack_into(">I", raw, start + 10, len(raw) - 6)  # Offset from TIFF header, beyond tag.
    raw.extend(payload)
    piexif.insert(bytes(raw), str(path))
    return payload


def metadata_warnings(path):
    import os
    response = subprocess.run(
        [engine.exiftool_path(), "-j", "-a", "-G4", "-validate", "-warning", "-error", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True, timeout=30,
        env={**os.environ, "LC_ALL": "C", "LANG": "C"},
    )
    values = json.loads(response.stdout)[0]
    return {str(value) for key, value in values.items() if key.split(":")[-1] in ("Warning", "Error")}


def raw_bgr(path: Path):
    return cv2.imdecode(
        np.fromfile(path, np.uint8), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION
    )


def legacy_undistort(image, camera, distortion, alpha):
    height, width = image.shape[:2]
    new_camera, roi = cv2.getOptimalNewCameraMatrix(
        camera, distortion, (width, height), alpha=float(alpha)
    )
    result = cv2.undistort(image, camera, distortion, None, new_camera)
    x, y, crop_width, crop_height = roi
    if crop_width > 0 and crop_height > 0:
        result = result[y:y + crop_height, x:x + crop_width]
    return result


class DewarpRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="dewarp-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def options(self, **overrides):
        values = dict(copy_xmp=False, workers=1)
        values.update(overrides)
        return engine.BatchOptions(**values)

    def run_folder(self, folder, **options):
        results = engine.run_batch([folder], self.options(**options))
        self.assertEqual(len(results), 1)
        return results[0]

    def test_calibration_parser_and_optical_center(self):
        parsed = engine.parse_dewarp_numbers(CALIBRATION)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed[:4], (120.0, 118.0, 0.15, -0.25))
        self.assertIsNone(engine.parse_dewarp_numbers("missing or truncated 1,2,3"))
        self.assertIsNone(engine.parse_dewarp_numbers("0,118,0,0,-0.13,0.035,0,0,0"))
        self.assertIsNone(engine.parse_dewarp_numbers("1e999,118,0,0,-0.13,0.035,0,0,0"))
        camera, distortion = engine.build_K_dist(
            160, 100,
            {"dewarp_data": CALIBRATION, "cocx": "81", "cocy": "49"},
        )
        self.assertAlmostEqual(float(camera[0, 2]), 81.15, places=4)
        self.assertAlmostEqual(float(camera[1, 2]), 48.75, places=4)
        np.testing.assert_array_equal(distortion, parsed[4])
        for flag in (1, "1", "true", " True "):
            self.assertTrue(engine.already_dewarped(flag))
        for flag in (0, "0", None, "false"):
            self.assertFalse(engine.already_dewarped(flag))

    def test_remap_is_pixel_identical_to_original_undistort(self):
        rng = np.random.default_rng(20261010)
        cache = engine.MapCache()
        for width, height in ((160, 100), (321, 241), (1280, 960)):
            image = rng.integers(0, 256, (height, width, 3), dtype=np.uint8)
            camera = np.array([
                [width * 0.83, 0, width / 2 + 0.15],
                [0, width * 0.82, height / 2 - 0.25],
                [0, 0, 1],
            ], dtype=np.float32)
            distortion = np.array([-0.13, 0.035, 0.001, -0.0007, 0.0001], np.float32)
            for alpha in (0.0, 1.0):
                with self.subTest(size=(width, height), alpha=alpha):
                    expected = legacy_undistort(image, camera, distortion, alpha)
                    actual = engine.undistort(image, camera, distortion, alpha, cache=cache)
                    np.testing.assert_array_equal(actual, expected)
                    # Reusing the same calibration must not change cached-map output.
                    np.testing.assert_array_equal(
                        engine.undistort(image, camera, distortion, alpha, cache=cache), expected
                    )
            # DJI calibration varies between photos of otherwise identical size.
            different_camera = camera.copy()
            different_camera[0, 2] += 2.5
            different_distortion = distortion.copy()
            different_distortion[0] = -0.17
            np.testing.assert_array_equal(
                engine.undistort(image, different_camera, different_distortion, 0.0, cache=cache),
                legacy_undistort(image, different_camera, different_distortion, 0.0),
            )

    def test_parser_rejects_nonfinite_or_truncated_dated_calibration(self):
        coefficients = CALIBRATION.split(";", 1)[1]
        expected = engine.parse_dewarp_numbers(CALIBRATION)
        actual = engine.parse_dewarp_numbers("2026/10/10;" + coefficients)
        self.assertEqual(actual[:4], expected[:4])
        np.testing.assert_array_equal(actual[4], expected[4])
        invalid_payloads = [
            coefficients.rsplit(",", 1)[0],  # Date digits must not replace a missing ninth coefficient.
            coefficients + ",0",  # An extra coefficient is ambiguous.
            coefficients.replace("0.0001", "NaN"),
            coefficients.replace("0.0001", "+Inf"),
            coefficients.replace("0.0001", "-Infinity"),
            coefficients.replace("120", "broken120"),
            coefficients.replace("0.035", "0.035garbage"),
            coefficients.replace("120,118", "120,,118"),
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                self.assertIsNone(engine.parse_dewarp_numbers("2026/10/10;" + payload))

    def test_undated_calibration_requires_exact_coefficients_and_representable_focal_lengths(self):
        valid = "120,118,16,5,-0.13,0.035,0,0,0"
        self.assertIsNotNone(engine.parse_dewarp_numbers(valid))
        self.assertIsNotNone(engine.parse_dewarp_numbers(valid.replace("120", "120.")))
        self.assertIsNotNone(engine.parse_dewarp_numbers(valid.replace(",", " ")))
        for payload in (valid + ",99", valid.replace("0.035", "broken0.035")):
            with self.subTest(payload=payload):
                self.assertIsNone(engine.parse_dewarp_numbers(payload))
        self.assertIsNone(engine.build_K_dist(160, 100, {"dewarp_data": valid.replace("120", "1e-99")}))

    def test_output_names_are_unique_and_previous_results_are_preserved(self):
        first = self.root / "one" / "Gorgon LGL"
        second = self.root / "two" / "Gorgon LGL"
        for folder in (first, second):
            synthetic_photo(folder / "photo.JPG")
        output_root = self.root / "exports"
        options = self.options(output_root=output_root, workers=2)
        results = engine.run_batch([first, second], options)
        self.assertEqual(len(results), 2)
        outputs = [Path(result["output"]) for result in results]
        self.assertEqual(len(set(outputs)), 2)
        self.assertEqual(outputs[0].name, "Gorgon LGL")
        self.assertEqual(outputs[1].name, "Gorgon LGL (2)")
        for result, output in zip(results, outputs):
            self.assertEqual(output.parent, output_root)
            self.assertEqual(result["applied"], 1)
            self.assertEqual(result["failed"], 0)
            self.assertTrue((output / "photo.JPG").is_file())
        saved_bytes = (outputs[0] / "photo.JPG").read_bytes()
        repeated = engine.run_batch([first], options)[0]
        self.assertNotIn(Path(repeated["output"]), outputs)
        self.assertEqual((outputs[0] / "photo.JPG").read_bytes(), saved_bytes)

    def test_default_output_is_inside_input_folder_and_sources_are_untouched(self):
        folder = self.root / "Flight"
        source = synthetic_photo(folder / "source.jpeg")
        original_hash = hashlib.sha256(source.read_bytes()).digest()
        result = self.run_folder(folder)
        self.assertEqual(Path(result["output"]), folder / "Flight - Dewarped Photos")
        self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), original_hash)

    def test_recursive_batch_mirrors_paths_and_reports_duplicate_names(self):
        folder = self.root / "Raw" / "Abu CS"
        names = ["root.JpG", "2D/Flight A/photo.JPG", "2D/Flight B/photo.JPG",
                 "2D/Flight B/deeper/photo.JPEG"]
        sources = [synthetic_photo(folder / name) for name in names]
        before = {source: source.read_bytes() for source in sources}
        (folder / "2D" / "Flight A" / "sidecar.txt").write_text("ignore")
        events = []
        result = engine.run_batch([folder], self.options(output_root=self.root / "Output", workers=2),
                                  on_event=events.append)[0]
        output = Path(result["output"])
        self.assertEqual(output, self.root / "Output" / "Abu CS")
        self.assertEqual(result["total"], 4)
        self.assertEqual(result["applied"], 4)
        self.assertEqual(result["failed"], 0)
        actual = {p.relative_to(output).as_posix() for p in output.rglob("*")
                  if p.suffix.lower() in engine.JPEG_SUFFIXES}
        self.assertEqual(actual, set(names))
        self.assertFalse((output / "2D/Flight A/sidecar.txt").exists())
        report = json.loads((output / "processing-report.json").read_text(encoding="utf-8"))
        self.assertEqual({item["name"] for item in report["files"]}, set(names))
        self.assertEqual({item["name"] for item in events if item["event"] == "photo_done"}, set(names))
        self.assertEqual(events[-1]["completed"], 4)
        self.assertEqual(before, {source: source.read_bytes() for source in sources})

    def test_recursive_rerun_excludes_previous_and_interrupted_outputs(self):
        folder = self.root / "Flight"
        synthetic_photo(folder / "2D" / "source.jpeg")
        first = self.run_folder(folder)
        output = Path(first["output"])
        # A pre-marker output remains recognizable by its existing report.
        (output / engine.OUTPUT_MARKER).unlink()
        interrupted = engine.reserve_output(folder, None)
        synthetic_photo(interrupted / "unfinished.JPG")
        # A legitimate input folder with the old output naming style is included.
        synthetic_photo(folder / "Survey - Dewarped Photos" / "source.JPG")
        second = self.run_folder(folder)
        self.assertEqual(second["total"], 2)
        self.assertEqual(second["applied"], 2)
        self.assertEqual(second["already"], 0)
        self.assertEqual(Path(second["output"]).name, "Flight - Dewarped Photos (3)")

    def test_nested_output_location_is_excluded_from_recursive_input(self):
        folder = self.root / "Flight"
        synthetic_photo(folder / "2D" / "source.jpeg")
        exports = folder / "Exports"
        synthetic_photo(exports / "unrelated.jpg")
        for _ in range(2):
            result = self.run_folder(folder, output_root=exports)
            self.assertEqual(result["total"], 1)
            self.assertEqual(result["applied"], 1)
            self.assertTrue((Path(result["output"]) / "2D/source.jpeg").is_file())

    def test_recursive_scan_errors_are_reported_instead_of_partial_success(self):
        folder = self.root / "Flight"
        folder.mkdir()

        def unreadable_walk(top, *, onerror, followlinks):
            yield str(top), [], ["photo.JPG"]
            onerror(PermissionError("Cannot read nested flight"))

        with mock.patch.object(engine.os, "walk", side_effect=unreadable_walk):
            result = self.run_folder(folder)
        self.assertEqual(result["status"], "Failed")
        self.assertIn("Cannot read nested flight", result["error"])
        self.assertFalse(result["output"])

    def test_duplicate_inputs_are_processed_once_and_empty_folders_are_reported(self):
        folder = self.root / "One flight"
        synthetic_photo(folder / "photo.jpg")
        empty = self.root / "Empty flight"
        empty.mkdir()
        events = []
        results = engine.run_batch(
            [folder, folder / ".", empty], self.options(), on_event=events.append
        )
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["applied"], 1)
        self.assertEqual(results[1]["status"], "Empty")
        self.assertEqual(results[1]["total"], 0)
        self.assertEqual(results[1]["completed"], 0)
        self.assertEqual(results[1]["output"], "")
        self.assertEqual(list(empty.iterdir()), [])
        self.assertEqual(sum(event["event"] == "photo_done" for event in events), 1)
        self.assertEqual(events[-1]["total"], 1)
        self.assertEqual(events[-1]["completed"], 1)

    def test_corrupt_and_uncalibrated_photos_do_not_abort_the_folder(self):
        folder = self.root / "Mixed"
        synthetic_photo(folder / "good.JPG")
        synthetic_photo(folder / "uncalibrated.jpeg", calibration=None)
        synthetic_photo(folder / "bad_calibration.jpeg", calibration="0,118,0,0,-0.13,0.035,0,0,0")
        (folder / "broken.jpg").write_bytes(b"this is not a JPEG")
        (folder / "directory.jpg").mkdir()
        (folder / "readme.txt").write_text("ignore me", encoding="utf-8")
        result = self.run_folder(folder, workers=2)
        self.assertEqual(result["total"], 4)
        self.assertEqual(result["completed"], 4)
        self.assertEqual(result["applied"], 1)
        self.assertEqual(result["skipped"], 2)
        self.assertEqual(result["failed"], 1)
        output = Path(result["output"])
        self.assertTrue((output / "good.JPG").is_file())
        self.assertFalse((output / "uncalibrated.jpeg").exists())
        self.assertFalse((output / "bad_calibration.jpeg").exists())
        self.assertFalse((output / "broken.jpg").exists())

    def test_orientation_exif_gps_icc_and_output_dimensions(self):
        folder = self.root / "Portrait"
        source = synthetic_photo(folder / "portrait.jpg", orientation=6)
        result = self.run_folder(folder)
        self.assertEqual(result["applied"], 1)
        output = Path(result["output"]) / source.name
        camera, distortion = engine.build_K_dist(160, 100, {"dewarp_data": CALIBRATION})
        expected_bgr = legacy_undistort(raw_bgr(source), camera, distortion, 0.0)
        expected_bgr = cv2.rotate(expected_bgr, cv2.ROTATE_90_CLOCKWISE)
        expected_jpeg = io.BytesIO()
        Image.fromarray(cv2.cvtColor(expected_bgr, cv2.COLOR_BGR2RGB)).save(
            expected_jpeg, "JPEG", quality=95, subsampling=0
        )
        expected_decoded = cv2.imdecode(np.frombuffer(expected_jpeg.getvalue(), np.uint8), cv2.IMREAD_COLOR)
        np.testing.assert_array_equal(raw_bgr(output), expected_decoded)
        exif = piexif.load(str(output))
        height, width = expected_bgr.shape[:2]
        self.assertEqual(exif["0th"][piexif.ImageIFD.Orientation], 1)
        self.assertEqual(exif["Exif"][piexif.ExifIFD.PixelXDimension], width)
        self.assertEqual(exif["Exif"][piexif.ExifIFD.PixelYDimension], height)
        self.assertEqual(exif["Exif"][piexif.ExifIFD.DateTimeOriginal], CAPTURE_TIME)
        self.assertEqual(exif["GPS"], GPS)
        with Image.open(output) as image:
            self.assertEqual(image.info.get("icc_profile"), ICC)
            self.assertEqual(image.size, (width, height))

    def test_output_marker_prevents_accidental_second_dewarp_without_exiftool(self):
        folder = self.root / "Twice"
        source = synthetic_photo(folder / "photo.jpg")
        with mock.patch.object(engine, "exiftool_path", return_value=None):
            first = self.run_folder(folder, copy_xmp=False)
        output = Path(first["output"]) / source.name
        self.assertEqual(read_xmp(output).get("DewarpFlag"), "1")
        with mock.patch.object(engine, "exiftool_path", return_value=None):
            second = self.run_folder(Path(first["output"]), copy_xmp=False)
        self.assertEqual(second["applied"], 0)
        self.assertEqual(second["already"], 1)
        self.assertEqual(second["failed"], 0)
        with Image.open(output) as before, Image.open(Path(second["output"]) / source.name) as after:
            self.assertEqual(before.size, after.size)

    @unittest.skipUnless(engine.exiftool_path(), "ExifTool not installed")
    def test_external_makernote_data_survives_with_and_without_xmp_copy(self):
        folder = self.root / "Vendor data"
        source = synthetic_photo(folder / "photo.jpg", orientation=6)
        payload = add_external_makernote(source)
        original = source.read_bytes()
        # The payload deliberately is NOT in the value piexif.load returns.
        self.assertNotIn(payload, piexif.load(str(source))["Exif"][piexif.ExifIFD.MakerNote])
        source_warnings = metadata_warnings(source)
        for copy_xmp in (False, True):
            with self.subTest(copy_xmp=copy_xmp):
                result = self.run_folder(folder, copy_xmp=copy_xmp)
                self.assertEqual(result["applied"], 1, result)
                output = Path(result["output"]) / source.name
                with Image.open(output) as image:
                    self.assertIn(payload, image.info["exif"])
                self.assertFalse(metadata_warnings(output) - source_warnings)
        self.assertEqual(source.read_bytes(), original)

    def test_makernotes_require_exiftool_before_any_output_is_published(self):
        folder = self.root / "No vendor writer"
        source = synthetic_photo(folder / "photo.jpg")
        add_external_makernote(source)
        with mock.patch.object(engine, "exiftool_path", return_value=None):
            result = self.run_folder(folder, copy_xmp=False)
        self.assertEqual(result["failed"], 1)
        output = Path(result["output"])
        self.assertFalse((output / source.name).exists())
        self.assertEqual(list(output.glob(".dewarp-*.jpg")), [])
        report = json.loads((output / "processing-report.json").read_text(encoding="utf-8"))
        self.assertIn("ExifTool is required to preserve camera MakerNotes", report["files"][0]["message"])

    @unittest.skipUnless(engine.exiftool_path(), "ExifTool not installed")
    def test_simple_exif_fallback_preserves_metadata_without_new_structure_warnings(self):
        folder = self.root / "Simple EXIF fallback"
        source = synthetic_photo(folder / "photo.jpg", orientation=6)
        before_warnings = metadata_warnings(source)
        with mock.patch.object(engine, "exiftool_path", return_value=None):
            result = self.run_folder(folder, copy_xmp=False)
        self.assertEqual(result["applied"], 1, result)
        output = Path(result["output"]) / source.name
        self.assertFalse(metadata_warnings(output) - before_warnings)
        metadata = piexif.load(str(output))
        # Pillow reduces fractions (e.g. 1234/100 -> 617/50) without losing value.
        for tag, expected in GPS.items():
            actual = metadata["GPS"][tag]
            if tag in (piexif.GPSIFD.GPSLatitude, piexif.GPSIFD.GPSLongitude):
                self.assertEqual([Fraction(*value) for value in actual],
                                 [Fraction(*value) for value in expected])
            elif tag == piexif.GPSIFD.GPSAltitude:
                self.assertEqual(Fraction(*actual), Fraction(*expected))
            else:
                self.assertEqual(actual, expected)
        self.assertEqual(metadata["Exif"][piexif.ExifIFD.DateTimeOriginal], CAPTURE_TIME)
        self.assertEqual(metadata["0th"][piexif.ImageIFD.Orientation], 1)
        self.assertNotIn(piexif.ImageIFD.ImageWidth, metadata["0th"])
        self.assertNotIn(piexif.ImageIFD.ImageLength, metadata["0th"])
        with Image.open(output) as image:
            self.assertEqual(image.size, (metadata["Exif"][piexif.ExifIFD.PixelXDimension],
                                          metadata["Exif"][piexif.ExifIFD.PixelYDimension]))

    @unittest.skipUnless(engine.exiftool_path(), "ExifTool not installed")
    def test_copy_xmp_preserves_dji_tags_and_updates_dewarp_flag(self):
        folder = self.root / "Metadata"
        source = synthetic_photo(folder / "photo.jpg")
        result = self.run_folder(folder, copy_xmp=True)
        self.assertEqual(result["failed"], 0)
        output = Path(result["output"]) / source.name
        response = subprocess.run(
            [engine.exiftool_path(), "-j", "-n", "-DewarpFlag", "-DewarpData", "-FlightYawDegree", str(output)],
            capture_output=True, text=True, check=True, timeout=30,
        )
        metadata = json.loads(response.stdout)[0]
        self.assertEqual(int(metadata["DewarpFlag"]), 1)
        self.assertEqual(metadata["DewarpData"], CALIBRATION)
        self.assertEqual(float(metadata["FlightYawDegree"]), 42.5)

    def test_progress_counts_cover_multiple_folders(self):
        folders = [self.root / "A", self.root / "B"]
        for folder in folders:
            for index in range(3):
                synthetic_photo(folder / f"{index}.jpg")
        events = []
        results = engine.run_batch(folders, self.options(workers=2), on_event=events.append)
        self.assertEqual(sum(result["completed"] for result in results), 6)
        photo_events = [event for event in events if event["event"] == "photo_done"]
        self.assertEqual(len(photo_events), 6)
        self.assertEqual([event["batch_completed"] for event in photo_events], list(range(1, 7)))
        self.assertTrue(all(event["batch_total"] == 6 for event in photo_events))
        for index, folder in enumerate(folders):
            progress = [event for event in photo_events if event["index"] == index]
            self.assertEqual([event["completed"] for event in progress], [1, 2, 3])
            self.assertTrue(all(event["total"] == 3 for event in progress))
        types = [event["event"] for event in events]
        self.assertEqual(types.count("folder_queued"), 2)
        self.assertEqual(types.count("folder_start"), 2)
        self.assertEqual(types.count("folder_done"), 2)
        self.assertIn("batch_start", types)
        self.assertEqual(types[-1], "batch_done")
        for result in results:
            self.assertGreater(result["elapsed_seconds"], 0)
            report = json.loads((Path(result["output"]) / "processing-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["summary"]["elapsed_seconds"], result["elapsed_seconds"])
            done = next(event for event in events if event["event"] == "folder_done" and event["index"] == result["index"])
            self.assertEqual(done["elapsed_seconds"], result["elapsed_seconds"])
        self.assertGreaterEqual(events[-1]["elapsed"], sum(result["elapsed_seconds"] for result in results))

    def test_cancellation_leaves_pending_folder_unprocessed(self):
        folders = [self.root / "Active", self.root / "Pending"]
        for folder in folders:
            for index in range(5):
                synthetic_photo(folder / f"{index}.jpg")
        cancelled = threading.Event()
        events = []

        def cancel_after_first_photo(event):
            events.append(event)
            if event["event"] == "photo_done":
                cancelled.set()

        results = engine.run_batch(
            folders, self.options(workers=1), on_event=cancel_after_first_photo,
            cancel_event=cancelled,
        )
        self.assertEqual(len(results), 2)
        self.assertGreaterEqual(results[0]["completed"], 1)
        self.assertLess(results[0]["completed"], 5)
        self.assertEqual(results[1]["completed"], 0)
        self.assertEqual(results[1]["elapsed_seconds"], 0)
        self.assertIn("cancel", results[1]["status"].lower())
        self.assertFalse(any(
            event["event"] == "photo_done" and event["index"] == 1
            for event in events
        ))

    def test_metadata_write_failure_discards_unfinished_output_and_reports_error(self):
        folder = self.root / "Metadata failure"
        source = synthetic_photo(folder / "photo.jpg")
        with mock.patch.object(engine, "exiftool_path", return_value="mock-exiftool"), \
             mock.patch.object(engine.ExifToolSession, "read", return_value=({}, [])), \
             mock.patch.object(engine.ExifToolSession, "copy_xmp", side_effect=RuntimeError("Metadata write failed")):
            result = self.run_folder(folder, copy_xmp=True)
        self.assertEqual(result["completed"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["applied"], 0)
        output = Path(result["output"])
        self.assertFalse((output / source.name).exists())
        self.assertEqual(list(output.glob(".dewarp-*.jpg")), [])
        report = json.loads((output / "processing-report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["summary"]["failed"], 1)
        self.assertIn("Metadata write failed", report["files"][0]["message"])

    def test_full_xmp_request_reports_missing_exiftool_before_processing(self):
        folder = self.root / "No ExifTool"
        synthetic_photo(folder / "photo.jpg")
        with mock.patch.object(engine, "exiftool_path", return_value=None):
            with self.assertRaisesRegex(ValueError, "ExifTool"):
                self.run_folder(folder, copy_xmp=True)
        self.assertEqual([path.name for path in folder.iterdir()], ["photo.jpg"])

    def test_exiftool_cleanup_failure_restores_opencv_and_releases_batch_lock(self):
        folder = self.root / "Cleanup failure"
        synthetic_photo(folder / "photo.jpg")
        original_threads = cv2.getNumThreads()
        self.addCleanup(cv2.setNumThreads, original_threads)
        cv2.setNumThreads(7)  # Deliberately different from the engine's at-most-four setting.
        with mock.patch.object(engine, "exiftool_path", return_value="mock-exiftool"), \
             mock.patch.object(engine.ExifToolSession, "read", return_value=({}, [])), \
             mock.patch.object(engine.ExifToolSession, "close", side_effect=BrokenPipeError("Pipe closed")):
            with self.assertRaisesRegex(BrokenPipeError, "Pipe closed"):
                self.run_folder(folder, copy_xmp=False)
        self.assertEqual(cv2.getNumThreads(), 7)
        self.assertFalse(engine._BATCH_LOCK.locked())
        with mock.patch.object(engine, "exiftool_path", return_value=None):
            subsequent = self.run_folder(folder, copy_xmp=False)
        self.assertEqual(subsequent["applied"], 1)
        self.assertEqual(subsequent["failed"], 0)
        self.assertEqual(cv2.getNumThreads(), 7)

    def test_stable_cli_entrypoint_accepts_multiple_folders_and_legacy_options(self):
        script = ROOT / "scripts/dewarp.py"
        help_result = subprocess.run(
            [sys.executable, str(script), "--help"],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("--output", help_result.stdout)
        self.assertIn("--workers", help_result.stdout)
        folders = [self.root / "CLI A", self.root / "CLI B"]
        for folder in folders:
            synthetic_photo(folder / "nested" / "photo.jpg")
        output_root = self.root / "CLI output"
        response = subprocess.run(
            [sys.executable, str(script), *(str(folder) for folder in folders),
             "--output-root", str(output_root), "--workers", "2", "--report", "--require-dewarp", "--alpha", "0"],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(response.returncode, 0, response.stdout + response.stderr)
        self.assertIn("2/2 photos", response.stdout)
        for folder in folders:
            output = output_root / folder.name
            self.assertTrue((output / "nested" / "photo.jpg").is_file())
            self.assertTrue((output / "processing-report.json").is_file())


if __name__ == "__main__":
    unittest.main()
