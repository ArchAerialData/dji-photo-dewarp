"""DJI JPEG dewarping, metadata preservation, and bounded batch execution.

No GUI dependencies. Each batch owns its cache, executor and ExifTool process.
"""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import xml.etree.ElementTree as ET

import cv2
import numpy as np
from PIL import Image
import piexif


NUM_RE = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
JPEG_SUFFIXES = {".jpg", ".jpeg"}
OUTPUT_MARKER = ".dewarp-output"
_BATCH_LOCK = threading.Lock()  # OpenCV's thread setting is process-wide.
_SUPPRESSED_MAKERNOTE_WARNING = re.compile(
    r"(?:Warning: )?\[minor\] Possibly incorrect maker notes offsets "
    r"\(fix by [+-]?\d+\?\)(?: - .+)?")


def is_suppressed_warning(warning: str) -> bool:
    """Keep this known minor diagnostic in reports, without flagging completion.

    Match the specific ExifTool warning, never arbitrary MakerNotes errors.
    """
    return _SUPPRESSED_MAKERNOTE_WARNING.fullmatch(warning.strip()) is not None


@dataclass(frozen=True)
class BatchOptions:
    output_root: Path | None = None
    copy_xmp: bool = True
    alpha: float = 0.0
    workers: int = 2
    require_dewarp: bool = True  # Legacy option: missing calibration always skips.


def canonical(value: str) -> str:
    return re.sub(r"[\s:\-_]", "", value).lower()


def parse_dewarp_numbers(value):
    if not value:
        return None
    value = str(value)
    if re.search(r"\b(?:nan|inf(?:inity)?)\b", value, re.I):
        return None
    # DJI prefixes calibration with a date and semicolon. Do not accidentally
    # use date digits to fill in a missing/invalid calibration coefficient.
    payload = value.rsplit(";", 1)[-1].strip()
    numbers = payload.split(",") if "," in payload else payload.split()
    # Extracting numeric substrings silently accepts corrupt coefficients such
    # as "broken120", and taking the last nine shifts extra coefficients.
    if len(numbers) != 9 or any(re.fullmatch(NUM_RE, n.strip()) is None for n in numbers):
        return None
    values = [float(n) for n in numbers]
    if not all(math.isfinite(n) for n in values) or min(values[:2]) <= 0:
        return None
    fx, fy, cx, cy, *coefficients = values
    with np.errstate(over="ignore"):
        dist = np.array(coefficients, dtype=np.float32)
    if not np.isfinite(dist).all():
        return None
    return fx, fy, cx, cy, dist


def already_dewarped(value) -> bool:
    return str(value).strip().lower() in ("1", "1.0", "true")


def xmp_extract_values(xmp_text) -> dict:
    """Read attributes and elements without assuming a namespace prefix."""
    if not xmp_text:
        return {}
    if isinstance(xmp_text, bytes):
        xmp_text = xmp_text.decode("utf-8", errors="replace")
    fields = {}
    names = {"dewarpdata": "dewarp_data", "dewarpflag": "dewarp_flag",
             "calibratedopticalcenterx": "cocx", "calibratedopticalcentery": "cocy"}
    try:
        root = ET.fromstring(xmp_text)
        for element in root.iter():
            for key, value in [*element.attrib.items(), (element.tag, element.text)]:
                local = canonical(key.rsplit("}", 1)[-1].split(":")[-1])
                if local in names and value is not None:
                    fields[names[local]] = value.strip()
    except ET.ParseError:
        # Tolerate older DJI packets with undeclared XML namespace prefixes.
        for key, name in names.items():
            match = re.search(rf"(?:[\w-]+:)?{key}\s*=\s*(['\"])(.*?)\1",
                              xmp_text, re.I | re.S)
            if not match:
                match = re.search(rf"<(?:[\w-]+:)?{key}\b[^>]*>([^<]*)</", xmp_text, re.I)
                if match:
                    fields[name] = match.group(1).strip()
            else:
                fields[name] = match.group(2).strip()
    return fields


def build_K_dist(w: int, h: int, meta: dict):
    values = parse_dewarp_numbers(meta.get("dewarp_data"))
    if values is None:
        return None
    fx, fy, cx_off, cy_off, dist = values
    try:
        cx, cy = meta.get("cocx"), meta.get("cocy")
        if cx is None or cy is None:
            cx, cy = w / 2.0, h / 2.0
        cx, cy = float(cx) + cx_off, float(cy) + cy_off
        with np.errstate(over="ignore"):
            matrix = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], dtype=np.float32)
        if not np.isfinite(matrix).all() or min(matrix[0, 0], matrix[1, 1]) <= 0:
            return None
    except (ValueError, TypeError, OverflowError):
        return None
    return matrix, dist


def exiftool_path() -> str | None:
    return shutil.which("exiftool")


class ExifToolSession:
    """One serialized stay-open process, with bounded waits and checked status.

    Both pipes are drained independently to prevent stderr pipe deadlocks. A
    command ends only after stdout's ready marker and stderr's status marker.
    """
    def __init__(self, executable: str, timeout: float = 120):
        self.executable = executable
        self.timeout = timeout
        self._lock = threading.Lock()
        self._process = None
        self._counter = 0
        self._readers = []

    def _start(self):
        self._lines = queue.Queue()
        env = os.environ.copy()
        if os.name == "nt":
            env.update(LC_ALL="C", LANG="C")
        self._process = subprocess.Popen(
            [self.executable, "-stay_open", "True", "-@", "-"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace", bufsize=1, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._readers = []
        for name in ("stdout", "stderr"):
            thread = threading.Thread(target=self._drain,
                                      args=(name, getattr(self._process, name), self._lines),
                                      daemon=True)
            thread.start()
            self._readers.append(thread)

    @staticmethod
    def _drain(name, stream, lines):
        try:
            for line in stream:
                lines.put((name, line.rstrip("\r\n")))
        finally:
            lines.put((name, None))

    def _stop(self):
        process = self._process
        if process is None:
            return
        # Detach even if teardown raises; a later command must start fresh.
        self._process = None
        try:
            if process.poll() is None:
                process.stdin.write("-stay_open\nFalse\n")
                process.stdin.flush()
                process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
            process.wait()
        finally:
            for thread in self._readers:
                thread.join(timeout=2)
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    stream.close()
                except OSError:
                    pass  # A terminated child can leave a broken stdin pipe.

    def close(self):
        with self._lock:
            self._stop()

    def execute(self, arguments: list[str]) -> tuple[str, list[str]]:
        with self._lock:
            if any("\n" in arg or "\r" in arg for arg in arguments):
                raise ValueError("ExifTool cannot accept a path containing a newline")
            if self._process is None or self._process.poll() is not None:
                self._stop()
                self._start()
            self._counter += 1
            token = str(self._counter)
            done = f"{{status{token}}}"
            args = ["-charset", "filename=UTF8", *arguments,
                    "-echo4", done + "${status}", "-execute" + token]
            stdout, stderr = [], []
            ready, status = False, None
            deadline = time.monotonic() + self.timeout
            try:
                self._process.stdin.write("\n".join(args) + "\n")
                self._process.stdin.flush()
                while not ready or status is None:
                    stream, line = self._lines.get(timeout=max(0.001, deadline - time.monotonic()))
                    if line is None:
                        raise RuntimeError("ExifTool exited unexpectedly")
                    if stream == "stdout" and line == f"{{ready{token}}}":
                        ready = True
                    elif stream == "stderr" and line.startswith(done):
                        status = int(line[len(done):])
                    elif stream == "stdout":
                        stdout.append(line)
                    else:
                        stderr.append(line)
                    if time.monotonic() >= deadline and (not ready or status is None):
                        raise queue.Empty
            except queue.Empty as exc:
                self._stop()
                raise RuntimeError("ExifTool timed out; the unfinished output was discarded") from exc
            except (OSError, ValueError, RuntimeError):
                self._stop()
                raise
            if status:
                raise RuntimeError("; ".join(stderr) or f"ExifTool command failed (status {status})")
            return "\n".join(stdout), stderr

    def read(self, source: Path) -> tuple[dict, list[str]]:
        output, warnings = self.execute(["-j", "-n", "-G1", "-XMP:all", str(source)])
        data = json.loads(output)[0]
        meta = {}
        for key, value in data.items():
            key = canonical(key)
            for suffix, target in (("dewarpdata", "dewarp_data"), ("dewarpflag", "dewarp_flag"),
                                   ("calibratedopticalcenterx", "cocx"),
                                   ("calibratedopticalcentery", "cocy")):
                if key.endswith(suffix):
                    meta[target] = value
        return meta, warnings

    def copy_xmp(self, source: Path, target: Path, width: int, height: int) -> list[str]:
        _, warnings = self.execute([
            "-overwrite_original", "-TagsFromFile", str(source), "-XMP:all",
            *self._exif_updates(width, height),
            "-XMP-drone-dji:DewarpFlag=1", "-XMP-tiff:Orientation#=1",
            f"-XMP-tiff:ImageWidth={width}", f"-XMP-tiff:ImageHeight={height}",
            f"-XMP-exif:ExifImageWidth={width}", f"-XMP-exif:ExifImageHeight={height}",
            "-XMP-xmp:Thumbnails=", str(target)])
        return warnings

    @staticmethod
    def _exif_updates(width: int, height: int) -> list[str]:
        return ["-EXIF:Orientation#=1", f"-EXIF:ExifImageWidth={width}",
                f"-EXIF:ExifImageHeight={height}", "-IFD0:ImageWidth=", "-IFD0:ImageHeight=",
                "-IFD1:all="]

    def normalize_exif(self, target: Path, width: int, height: int) -> list[str]:
        _, warnings = self.execute(["-overwrite_original", *self._exif_updates(width, height), str(target)])
        return warnings


class MapCache:
    """Thread-safe LRU, keyed by exact calibration, capped at 256 MiB."""
    def __init__(self, max_bytes: int = 256 * 1024 * 1024):
        self.max_bytes = max_bytes
        self._bytes = 0
        self._maps = OrderedDict()
        self._lock = threading.Lock()

    def get(self, w, h, matrix, dist, alpha):
        key = (w, h, matrix.dtype.str, matrix.tobytes(), dist.dtype.str, dist.tobytes(), float(alpha))
        with self._lock:
            if key in self._maps:
                self._maps.move_to_end(key)
                return self._maps[key]
            new_matrix, roi = cv2.getOptimalNewCameraMatrix(matrix, dist, (w, h), float(alpha))
            map1, map2 = cv2.initUndistortRectifyMap(matrix, dist, None, new_matrix,
                                                  (w, h), cv2.CV_16SC2)
            value = map1, map2, roi
            size = map1.nbytes + map2.nbytes
            while self._maps and self._bytes + size > self.max_bytes:
                _, old = self._maps.popitem(last=False)
                self._bytes -= old[0].nbytes + old[1].nbytes
            if size <= self.max_bytes:
                self._maps[key] = value
                self._bytes += size
            return value


def undistort(img_bgr, K, dist, alpha, cache: MapCache | None = None):
    h, w = img_bgr.shape[:2]
    map1, map2, roi = (cache or MapCache()).get(w, h, K, dist, alpha)
    out = cv2.remap(img_bgr, map1, map2, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    x, y, width, height = roi
    # Preserve both legacy alpha modes, including their valid-region crop.
    if width > 0 and height > 0:
        out = out[y:y + height, x:x + width]
    return out


def normalize_orientation(image, orientation):
    transforms = {
        2: lambda a: cv2.flip(a, 1), 3: lambda a: cv2.rotate(a, cv2.ROTATE_180),
        4: lambda a: cv2.flip(a, 0), 5: cv2.transpose,
        6: lambda a: cv2.rotate(a, cv2.ROTATE_90_CLOCKWISE),
        7: lambda a: cv2.flip(cv2.transpose(a), -1),
        8: lambda a: cv2.rotate(a, cv2.ROTATE_90_COUNTERCLOCKWISE),
    }
    return transforms.get(orientation, lambda a: a)(image)


def _normalized_simple_exif(exif_dict, original_exif, width, height):
    """Use Pillow's aligned TIFF serializer when no vendor MakerNotes exist."""
    if piexif.ExifIFD.MakerNote in (exif_dict or {}).get("Exif", {}):
        raise ValueError("ExifTool is required to preserve camera MakerNotes, even with Copy XMP off")
    exif = Image.Exif()
    if original_exif:
        exif.load(original_exif)
    elif exif_dict:
        exif.load(piexif.dump(exif_dict))
    exif[piexif.ImageIFD.Orientation] = 1
    # JPEG stores dimensions in SOF and ExifIFD; these TIFF IFD0 tags are invalid.
    exif.pop(piexif.ImageIFD.ImageWidth, None)
    exif.pop(piexif.ImageIFD.ImageLength, None)
    values = exif.get_ifd(piexif.ImageIFD.ExifTag)
    values.update({piexif.ExifIFD.PixelXDimension: width, piexif.ExifIFD.PixelYDimension: height})
    if piexif.ImageIFD.ExifTag not in exif:
        exif[piexif.ImageIFD.ExifTag] = values
    # Do not load IFD1: the old thumbnail must not describe the corrected photo.
    return exif.tobytes()


def save_with_exif(dst: Path, rgb, exif_dict, icc_profile=None, *, original_exif=None,
                   preserve_exif_layout=False):
    h, w = rgb.shape[:2]
    # MakerNotes can point outside their tag's bytes. Preserve the whole original
    # block so ExifTool can relocate those references safely while editing it.
    exif_bytes = (original_exif or b"") if preserve_exif_layout else _normalized_simple_exif(
        exif_dict, original_exif, w, h)
    xmp = ('<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>'
           f'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
           f'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description '
           f'rdf:about="" xmlns:drone-dji="http://www.dji.com/drone-dji/1.0/" '
           f'drone-dji:DewarpFlag="1"/></rdf:RDF></x:xmpmeta><?xpacket end="w"?>').encode("utf-8")
    Image.fromarray(rgb).save(dst, "JPEG", quality=95, subsampling=0,
                              exif=exif_bytes, icc_profile=icc_profile, xmp=xmp)


def _process_image(source, output, options, cache, exiftool):
    result = {"name": source.name, "status": "failed", "message": "", "warnings": []}
    temporary = None
    try:
        data = source.read_bytes()
        with Image.open(BytesIO(data)) as image:
            xmp = image.info.get("xmp") or image.info.get("XML:com.adobe.xmp")
            icc = image.info.get("icc_profile")
            original_exif = image.info.get("exif")
        exif = piexif.load(data)
        if not exiftool and piexif.ExifIFD.MakerNote in exif.get("Exif", {}):
            raise ValueError("ExifTool is required to preserve camera MakerNotes, even with Copy XMP off")
        orientation = exif.get("0th", {}).get(piexif.ImageIFD.Orientation, 1)
        # Intrinsics refer to sensor pixels, before EXIF rotation or mirroring.
        decoded = cv2.imdecode(np.frombuffer(data, dtype=np.uint8),
                               cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
        if decoded is None:
            raise ValueError("Cannot decode JPEG")
        if not xmp:
            match = re.search(br"<x:xmpmeta\b.*?</x:xmpmeta>", data, re.I | re.S)
            xmp = match.group(0) if match else None
        del data
        if exiftool:
            meta, warnings = exiftool.read(source)
            result["warnings"].extend(warnings)
            # A successful ExifTool read can still lack fields present in a raw packet.
            meta = {**xmp_extract_values(xmp), **meta}
        else:
            meta = xmp_extract_values(xmp)
        h, w = decoded.shape[:2]
        if already_dewarped(meta.get("dewarp_flag")):
            corrected, status = decoded, "already"
        else:
            intrinsics = build_K_dist(w, h, meta)
            if intrinsics is None:
                result.update(status="skipped", message="Missing or invalid DJI DewarpData/calibration")
                return result
            corrected = undistort(decoded, *intrinsics, options.alpha, cache=cache)
            status = "applied"
        corrected = normalize_orientation(corrected, orientation)
        rgb = cv2.cvtColor(corrected, cv2.COLOR_BGR2RGB)
        del decoded, corrected
        output.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(prefix=".dewarp-", suffix=".jpg", dir=output)
        os.close(handle)
        temporary = Path(name)
        save_with_exif(temporary, rgb, exif, icc, original_exif=original_exif,
                       preserve_exif_layout=exiftool is not None)
        height, width = rgb.shape[:2]
        del rgb
        if options.copy_xmp:
            result["warnings"].extend(exiftool.copy_xmp(source, temporary, width, height))
        elif exiftool:
            result["warnings"].extend(exiftool.normalize_exif(temporary, width, height))
        destination = output / source.name
        # The entire output directory was exclusively reserved for this run.
        if destination.exists():
            raise FileExistsError(f"Refusing to replace an existing photo: {destination}")
        temporary.rename(destination)
        temporary = None
        result.update(status=status, message="Dewarped" if status == "applied" else "Already dewarped; normalized")
    except Exception as exc:
        result.update(status="failed", message=f"{type(exc).__name__}: {exc}")
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as exc:
                result["warnings"].append(f"Could not remove unfinished temporary file {temporary}: {exc}")
    return result


def _is_previous_output(folder: Path) -> bool:
    if (folder / OUTPUT_MARKER).is_file():
        return True
    # Recognize outputs from versions predating the reservation marker too.
    report = folder / "processing-report.json"
    if report.is_file():
        try:
            data = json.loads(report.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError):
            return False
        return (isinstance(data, dict) and data.get("version") == 2
                and isinstance(data.get("summary"), dict)
                and bool(data["summary"].get("output"))
                and bool(data["summary"].get("folder")))
    return False


def scan_photos(folder: Path, output_root: Path | None = None, cancel_event=None) -> list[Path]:
    """Inventory JPEGs recursively without following directory links or outputs.

    Raise on unreadable subtrees instead of silently reporting a partial scan
    as successful. An explicitly selected output folder can still be an input.
    """
    def scan_error(error):
        raise error

    def include_directory(path):
        # lstat also recognizes Windows junctions on supported Python 3.10/3.11,
        # where os.path.isjunction is not available yet.
        info = path.lstat()
        is_link = (stat.S_ISLNK(info.st_mode)
                   or getattr(info, "st_reparse_tag", None) == stat.IO_REPARSE_TAG_MOUNT_POINT)
        return not is_link and path != output_root and not _is_previous_output(path)

    photos = []
    for directory, children, files in os.walk(folder, onerror=scan_error, followlinks=False):
        if cancel_event is not None and cancel_event.is_set():
            return []  # Never expose a partial inventory as a complete count.
        parent = Path(directory)
        children[:] = sorted(
            (name for name in children if include_directory(parent / name)),
            key=str.casefold)
        photos.extend(parent / name for name in files
                      if Path(name).suffix.lower() in JPEG_SUFFIXES and (parent / name).is_file())
    return sorted(photos, key=lambda path: path.relative_to(folder).as_posix().casefold())


def reserve_output(folder: Path, root: Path | None) -> Path:
    parent = root if root is not None else folder
    parent.mkdir(parents=True, exist_ok=True)
    stem = folder.name or folder.drive.replace(":", "") or "Photos"
    if root is None:
        stem += " - Dewarped Photos"
    number = 1
    while True:
        output = parent / (stem if number == 1 else f"{stem} ({number})")
        try:
            output.mkdir()
        except FileExistsError:
            number += 1
            continue
        # Reserve before processing, so cancelled/failed runs are excluded too.
        (output / OUTPUT_MARKER).write_text("DJI Photo Dewarp output\n", encoding="utf-8")
        return output


def _write_report(result, files, options):
    destination = Path(result["output"]) / "processing-report.json"
    document = {"version": 2, "finished_utc": datetime.now(timezone.utc).isoformat(),
                "options": {"alpha": options.alpha, "workers": options.workers,
                            "copy_xmp": options.copy_xmp, "jpeg_quality": 95, "jpeg_subsampling": 0},
                "summary": result, "files": sorted(files, key=lambda item: item["name"].casefold())}
    temporary = destination.with_suffix(".json.tmp")
    try:
        temporary.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def run_batch(folders, options: BatchOptions, on_event=None, cancel_event=None) -> list[dict]:
    """Process folders sequentially, images concurrently; callbacks run off the UI thread.

    Only ``workers`` images are submitted at a time. Cancellation drains these
    images and leaves the rest untouched. Reports are written after each folder.
    Progress counts completed attempts, with successes/skips/failures separate.
    """
    if options.workers not in range(1, 5):
        raise ValueError("Workers must be between 1 and 4")
    if options.alpha not in (0.0, 1.0):
        raise ValueError("Alpha must be 0 or 1")
    exe = exiftool_path()
    if options.copy_xmp and not exe:
        raise ValueError("ExifTool is required to preserve full XMP. Install ExifTool or turn off Copy XMP.")
    # Pillow versions before 11.0 silently ignore the JPEG xmp save parameter.
    import PIL
    if tuple(int(n) for n in PIL.__version__.split(".")[:2]) < (11, 0):
        raise ValueError("Pillow 11.0 or newer is required. Run: python -m pip install -r requirements.txt")
    unique = list(dict.fromkeys(Path(p).expanduser().resolve() for p in folders))
    if not unique:
        raise ValueError("Add at least one input folder")
    for folder in unique:
        if not folder.is_dir():
            raise ValueError(f"Not a directory: {folder}")
    output_root = Path(options.output_root).expanduser().resolve() if options.output_root else None
    cancel = cancel_event if cancel_event is not None else threading.Event()
    emit = on_event if on_event is not None else lambda event: None
    if not _BATCH_LOCK.acquire(blocking=False):
        raise RuntimeError("Another batch is already running in this process")
    old_threads = cv2.getNumThreads()
    session = ExifToolSession(exe) if exe else None
    started = time.monotonic()
    results, inventories = [], []
    counts = dict(completed=0, applied=0, already=0, skipped=0, failed=0, warnings=0, suppressed_warnings=0)
    total = 0
    try:
        # JPEG encoding dominates; a small OpenCV thread team avoids oversubscription.
        cv2.setNumThreads(min(4, max(1, (os.cpu_count() or 2) // options.workers)))
        for index, folder in enumerate(unique):
            error = None
            try:
                photos = scan_photos(folder, output_root)
            except OSError as exc:
                photos, error = [], str(exc)
            inventories.append((photos, error))
            results.append(dict(index=index, folder=str(folder), output="", total=len(photos),
                                completed=0, applied=0, already=0, skipped=0, failed=0,
                                warnings=0, suppressed_warnings=0, status="Queued", error=error, elapsed_seconds=0.0))
            total += len(photos)
        emit(dict(event="batch_start", total=total, folders_total=len(unique), workers=options.workers))
        for result in results:
            emit(dict(event="folder_queued", **result))
        cache = MapCache()
        with ThreadPoolExecutor(max_workers=options.workers, thread_name_prefix="dewarp") as pool:
            for result, (photos, scan_error) in zip(results, inventories):
                if cancel.is_set():
                    result["status"] = "Cancelled"
                    emit(dict(event="folder_done", **result))
                    continue
                records = []
                folder_started = time.monotonic()
                try:
                    if scan_error:
                        raise OSError(scan_error)
                    if not photos:
                        result["status"] = "Empty"
                        result["elapsed_seconds"] = time.monotonic() - folder_started
                        emit(dict(event="folder_done", **result))
                        continue
                    folder = Path(result["folder"])
                    output = reserve_output(folder, output_root)
                    result.update(output=str(output), status="Processing")
                    emit(dict(event="folder_start", **result))
                    iterator = iter(photos)
                    pending = {}

                    def submit_next():
                        if cancel.is_set():
                            return
                        src = next(iterator, None)
                        if src is not None:
                            destination = output / src.relative_to(folder).parent
                            future = pool.submit(_process_image, src, destination, options, cache, session)
                            pending[future] = src

                    for _ in range(options.workers):
                        submit_next()
                    while pending:
                        finished, _ = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
                        for future in finished:
                            source = pending.pop(future)
                            try:
                                record = future.result()
                            except Exception as exc:
                                # A worker bug must not abandon other in-flight outputs.
                                record = dict(name=source.name, status="failed",
                                              message=f"Unexpected worker error: {exc}", warnings=[])
                            # Relative paths disambiguate identical names in different flights.
                            record["name"] = source.relative_to(folder).as_posix()
                            records.append(record)
                            status = record["status"]
                            result[status] += 1
                            result["completed"] += 1
                            result["warnings"] += len(record["warnings"])
                            suppressed = sum(is_suppressed_warning(warning) for warning in record["warnings"])
                            result["suppressed_warnings"] += suppressed
                            counts[status] += 1
                            counts["completed"] += 1
                            counts["warnings"] += len(record["warnings"])
                            counts["suppressed_warnings"] += suppressed
                            emit(dict(event="photo_done", index=result["index"], **record,
                                      completed=result["completed"], remaining=result["total"] - result["completed"],
                                      total=result["total"], applied=result["applied"], already=result["already"],
                                      skipped=result["skipped"], failed=result["failed"],
                                      batch_completed=counts["completed"], batch_total=total,
                                      elapsed=time.monotonic() - started))
                            submit_next()
                    result["status"] = ("Cancelled" if result["completed"] < result["total"] else
                                        "Completed with issues" if result["failed"] or result["skipped"] or result["warnings"] > result["suppressed_warnings"]
                                        else "Completed")
                except Exception as exc:
                    result.update(status="Failed", error=f"{type(exc).__name__}: {exc}")
                    emit(dict(event="log", message=f"{result['folder']}: {result['error']}"))
                # Keep a per-folder measurement in both the report and events.
                # This includes reservation and all photo work, excluding report I/O.
                result["elapsed_seconds"] = time.monotonic() - folder_started
                if result["output"]:
                    try:
                        _write_report(result, records, options)
                    except OSError as exc:
                        result.update(status="Failed", error=f"Could not save processing report: {exc}")
                        emit(dict(event="log", message=result["error"]))
                emit(dict(event="folder_done", **result))
        emit(dict(event="batch_done", cancelled=cancel.is_set(), total=total, **counts,
                  elapsed=time.monotonic() - started, folders_total=len(results),
                  folders_completed=sum(r["status"] in ("Completed", "Completed with issues", "Empty") for r in results),
                  folders_failed=sum(r["status"] == "Failed" for r in results)))
        return results
    finally:
        try:
            if session:
                session.close()
        finally:
            try:
                cv2.setNumThreads(old_threads)
            finally:
                _BATCH_LOCK.release()


def process_folder(folder: Path, require_dewarp=True, report=False, copy_xmp=False, alpha=0.0):
    """Compatibility wrapper for callers of the original script's function."""
    results = run_batch([folder], BatchOptions(copy_xmp=copy_xmp, alpha=alpha,
                                             require_dewarp=require_dewarp))
    if report:
        print(json.dumps(results, indent=2))
    return results[0]
