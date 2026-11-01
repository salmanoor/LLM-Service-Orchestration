"""Python-authored room-mesh inspection and constrained planner study."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import http.client
import json
import math
import os
import platform
import re
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "aalto_hall101"
OUTPUTS = ROOT / "outputs"
FIGURES = OUTPUTS / "figures"
REPORT = OUTPUTS / "study_results.json"
MODEL = ROOT / "models" / "qwen2.5-0.5b-instruct-q4_0.gguf"
sys.path.insert(0, str(ROOT / ".vendor"))
DATASET_DOI = "10.5281/zenodo.3876363"
ZENODO_API = "https://zenodo.org/api/records/3876363"
MODEL_URL = "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_0.gguf"
MODEL_SHA256 = "7671c0c304e6ce5a7fc577bcb12aba01e2c155cc2efd29b2213c95b18edaf6ed"
TOOLS = {"measure", "segment", "compare", "rule", "provenance"}
SCANS = [
    ("Hall 101", "BLK360", "Hall_101_BLK360_mesh.ply"),
    ("Hall 101", "Matterport Pro2", "Hall_101_Matterport_mesh.ply"),
    ("Hall 101", "Ricoh Theta V", "Hall_101_ThetaV_mesh.ply"),
    ("Tetra Hall", "BLK360", "Tetra_BLK360_mesh.ply"),
    ("Tetra Hall", "Matterport Pro2", "Tetra_Matterport_mesh.ply"),
    ("Tetra Hall", "Ricoh Theta V", "Tetra_ThetaV_mesh.ply"),
]


def _range(url: str, start: int, end: int, tries: int = 6) -> bytes:
    """Read one verified HTTP byte range; retry truncated archival responses."""
    expected = end - start + 1
    for attempt in range(tries):
        request = urllib.request.Request(
            url,
            headers={"Range": f"bytes={start}-{end}", "User-Agent": "room-service-poc/0.2"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                if response.status != 206:
                    raise OSError(f"Expected HTTP 206, received {response.status}")
                data = response.read()
            if len(data) != expected:
                raise OSError(f"Short byte range ({len(data)} of {expected} bytes)")
            return data
        except (OSError, urllib.error.URLError, http.client.HTTPException):
            if attempt + 1 == tries:
                raise
            time.sleep(1 + attempt)
    raise AssertionError("unreachable")


def _range_chunks(url: str, start: int, end: int, chunk_size: int = 1024 * 1024) -> bytes:
    ranges = [
        (pos, min(pos + chunk_size - 1, end))
        for pos in range(start, end + 1, chunk_size)
    ]
    with ThreadPoolExecutor(max_workers=min(8, len(ranges))) as pool:
        chunks = pool.map(lambda pair: _range(url, *pair), ranges)
        return b"".join(chunks)


def _zip64_directory(url: str, archive_size: int) -> dict[str, tuple[int, int, int, int, int]]:
    tail_size = min(200_000, archive_size)
    tail_start = archive_size - tail_size
    tail = _range_chunks(url, tail_start, archive_size - 1)
    eocd_pos = tail.rfind(b"PK\x05\x06")
    if eocd_pos < 0:
        raise ValueError("ZIP end-of-central-directory record was not found")
    fields = struct.unpack("<4s4H2LH", tail[eocd_pos : eocd_pos + 22])
    entries, directory_size, directory_offset = fields[4], fields[5], fields[6]
    if entries == 0xFFFF or directory_size == 0xFFFFFFFF or directory_offset == 0xFFFFFFFF:
        locator_pos = tail_start + eocd_pos - 20
        locator = _range(url, locator_pos, locator_pos + 19)
        _, _, zip64_offset, _ = struct.unpack("<4sLQL", locator)
        record = _range(url, zip64_offset, zip64_offset + 55)
        _, _, _, _, _, _, _, entries, directory_size, directory_offset = struct.unpack(
            "<4sQ2H2L4Q", record
        )
    directory = _range_chunks(url, directory_offset, directory_offset + directory_size - 1)
    parsed: dict[str, tuple[int, int, int, int, int]] = {}
    offset = 0
    for _ in range(entries):
        values = struct.unpack("<4s6H3L5H2L", directory[offset : offset + 46])
        _, _, _, _, method, _, _, crc, packed_size, raw_size, name_len, extra_len, comment_len, _, _, _, local_offset = values
        name = directory[offset + 46 : offset + 46 + name_len].decode("utf-8", "replace")
        extra_start = offset + 46 + name_len
        extra = directory[extra_start : extra_start + extra_len]
        if packed_size == 0xFFFFFFFF or raw_size == 0xFFFFFFFF or local_offset == 0xFFFFFFFF:
            pos = 0
            while pos + 4 <= len(extra):
                tag, length = struct.unpack_from("<HH", extra, pos)
                payload = extra[pos + 4 : pos + 4 + length]
                if tag == 1:
                    cursor = 0
                    if raw_size == 0xFFFFFFFF:
                        raw_size = struct.unpack_from("<Q", payload, cursor)[0]
                        cursor += 8
                    if packed_size == 0xFFFFFFFF:
                        packed_size = struct.unpack_from("<Q", payload, cursor)[0]
                        cursor += 8
                    if local_offset == 0xFFFFFFFF:
                        local_offset = struct.unpack_from("<Q", payload, cursor)[0]
                    break
                pos += 4 + length
        parsed[name] = (packed_size, raw_size, method, local_offset, crc)
        offset += 46 + name_len + extra_len + comment_len
    return parsed


def _extract_zip_member(url: str, archive_size: int, member: str, destination: Path) -> None:
    directory = _zip64_directory(url, archive_size)
    packed_size, raw_size, method, local_offset, crc = directory[member]
    local = _range(url, local_offset, local_offset + 29)
    fields = struct.unpack("<IHHHHHIIIHH", local)
    if fields[0] != 0x04034B50:
        raise ValueError(f"Bad ZIP local header for {member}")
    name_len, extra_len = fields[-2], fields[-1]
    data_start = local_offset + 30 + name_len + extra_len
    packed = _range_chunks(url, data_start, data_start + packed_size - 1)
    if method == 8:
        raw = zlib.decompress(packed, -15)
    elif method == 0:
        raw = packed
    else:
        raise ValueError(f"Unsupported ZIP compression {method} for {member}")
    if len(raw) != raw_size or (zlib.crc32(raw) & 0xFFFFFFFF) != crc:
        raise ValueError(f"Size or CRC check failed for {member}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)


def fetch_meshes() -> None:
    """Range-extract six real-room meshes from two CC BY 4.0 Zenodo archives."""
    with urllib.request.urlopen(ZENODO_API, timeout=60) as response:
        record = json.load(response)
    files = {item["key"]: item for item in record["files"]}
    for archive_name, members in (
        (
            "Hall_101_room_geometry.zip",
            [f"Hall_101_room_geometry/{name}" for _, _, name in SCANS[:3]],
        ),
        (
            "Tetra_room_geometry.zip",
            [f"Tetra_room_geometry/{name}" for _, _, name in SCANS[3:]],
        ),
    ):
        archive = files[archive_name]
        for member in members:
            output = DATA / Path(member).name
            if output.exists() and output.stat().st_size > 1_000_000:
                continue
            print(f"Fetching {Path(member).name} from Zenodo by byte range...", flush=True)
            _extract_zip_member(archive["links"]["self"], archive["size"], member, output)
    attribution = (
        "Indoor low-cost sensor system data\n"
        "Creator: Matias Ingman, Aalto University.\n"
        f"DOI: {DATASET_DOI}\n"
        "License: Creative Commons Attribution 4.0 International (CC BY 4.0).\n"
        "Source record: https://zenodo.org/records/3876363\n"
        "This project retains six registered room-geometry meshes for Hall 101 and Tetra Hall.\n"
        "Mesh vertices are analyzed as point samples; they are not the original E57 scans.\n"
        "Attribute the dataset creator and cite the DOI when reusing these files.\n"
    )
    (DATA / "ATTRIBUTION.txt").write_text(attribution, encoding="utf-8")


def fetch_model() -> None:
    """Download and checksum the Apache-2.0 Qwen2.5 quantized GGUF model."""
    if MODEL.exists() and hashlib.sha256(MODEL.read_bytes()).hexdigest() == MODEL_SHA256:
        return
    MODEL.parent.mkdir(parents=True, exist_ok=True)
    size = 428_730_208
    chunk_size = 4 * 1024 * 1024
    with MODEL.open("wb") as output:
        for start in range(0, size, chunk_size):
            end = min(start + chunk_size, size) - 1
            output.write(_range(MODEL_URL, start, end))
    digest = hashlib.sha256(MODEL.read_bytes()).hexdigest()
    if digest != MODEL_SHA256:
        MODEL.unlink(missing_ok=True)
        raise ValueError(f"Model SHA-256 mismatch: {digest}")


def _parse_ply(path: Path, max_samples: int = 24_000) -> dict:
    data = path.read_bytes()
    end = data.find(b"end_header")
    if end < 0:
        raise ValueError(f"Missing PLY header in {path.name}")
    header_end = data.find(b"\n", end) + 1
    lines = data[:header_end].decode("ascii").splitlines()
    if "format binary_little_endian 1.0" not in lines:
        raise ValueError(f"Unsupported PLY encoding in {path.name}")
    vertex_count = int(next(line.split()[2] for line in lines if line.startswith("element vertex ")))
    face_count = int(next(line.split()[2] for line in lines if line.startswith("element face ")))
    expected_properties = [
        "property float x", "property float y", "property float z",
        "property uchar red", "property uchar green", "property uchar blue",
    ]
    prop_start = lines.index(f"element vertex {vertex_count}") + 1
    properties = []
    for line in lines[prop_start:]:
        if line.startswith("element "):
            break
        if line.startswith("property "):
            properties.append(line)
    if properties != expected_properties:
        raise ValueError(f"Unexpected vertex schema in {path.name}: {properties}")
    vertex_stride = 15
    vertex_start = header_end
    vertex_bytes = vertex_count * vertex_stride
    if len(data) < vertex_start + vertex_bytes:
        raise ValueError(f"Truncated vertices in {path.name}")
    vertices = data[vertex_start : vertex_start + vertex_bytes]
    mins = [float("inf")] * 3
    maxs = [float("-inf")] * 3
    samples = []
    stride = max(1, math.ceil(vertex_count / max_samples))
    valid = 0
    for index, item in enumerate(struct.iter_unpack("<fffBBB", vertices)):
        x, y, z, red, green, blue = item
        if not all(math.isfinite(value) for value in (x, y, z)):
            continue
        valid += 1
        for axis, value in enumerate((x, y, z)):
            mins[axis] = min(mins[axis], value)
            maxs[axis] = max(maxs[axis], value)
        if index % stride == 0:
            samples.append((x, y, z, red, green, blue))
    if valid != vertex_count:
        raise ValueError(f"Invalid coordinates in {path.name}: {valid}/{vertex_count} valid")
    face_start = vertex_start + vertex_bytes
    orientations = {"horizontal": 0.0, "vertical": 0.0, "oblique": 0.0}
    face_pos = face_start
    for _ in range(face_count):
        vertex_indices = data[face_pos]
        face_pos += 1
        if vertex_indices != 3:
            face_pos += 4 * vertex_indices
            continue
        i0, i1, i2 = struct.unpack_from("<iii", data, face_pos)
        face_pos += 12
        a = struct.unpack_from("<fff", vertices, i0 * vertex_stride)
        b = struct.unpack_from("<fff", vertices, i1 * vertex_stride)
        c = struct.unpack_from("<fff", vertices, i2 * vertex_stride)
        ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        twice_area = math.sqrt(nx * nx + ny * ny + nz * nz)
        if twice_area <= 1e-12:
            continue
        z_alignment = abs(nz) / twice_area
        group = "horizontal" if z_alignment >= 0.95 else "vertical" if z_alignment <= 0.10 else "oblique"
        orientations[group] += twice_area / 2.0
    extents = [maxs[axis] - mins[axis] for axis in range(3)]
    return {
        "file": str(path.relative_to(ROOT)),
        "vertices": vertex_count,
        "faces": face_count,
        "bounds_m": {"min": mins, "max": maxs, "extent": extents},
        "orientation_area_m2": orientations,
        "sha256": hashlib.sha256(data).hexdigest(),
        "samples": samples,
        "source_bytes": len(data),
    }


def scan_service() -> list[dict]:
    scans = []
    for room, sensor, filename in SCANS:
        path = DATA / filename
        if not path.exists():
            raise FileNotFoundError(f"Missing {path}; run `python poc.py --fetch`")
        scan = _parse_ply(path)
        scan.update({"room": room, "sensor": sensor})
        scans.append(scan)
    return scans


def measurement_service(scans: list[dict]) -> list[dict]:
    return [
        {
            "room": s["room"], "sensor": s["sensor"],
            "vertices": s["vertices"], "faces": s["faces"],
            "extent_m": [round(v, 4) for v in s["bounds_m"]["extent"]],
            "orientation_area_m2": {k: round(v, 3) for k, v in s["orientation_area_m2"].items()},
            "sha256": s["sha256"],
        }
        for s in scans
    ]


def compare_service(scans: list[dict]) -> list[dict]:
    results = []
    for room in sorted({scan["room"] for scan in scans}):
        group = [scan for scan in scans if scan["room"] == room]
        reference = next(scan for scan in group if scan["sensor"] == "BLK360")
        ref_extents = reference["bounds_m"]["extent"]
        for scan in group:
            extents = scan["bounds_m"]["extent"]
            results.append({
                "room": room,
                "sensor": scan["sensor"],
                "reference": "BLK360 mesh (comparison anchor only)",
                "delta_m": [round(extents[i] - ref_extents[i], 4) for i in range(3)],
                "delta_pct": [round(100 * (extents[i] - ref_extents[i]) / ref_extents[i], 2) for i in range(3)],
            })
    return results


def rule_service(rule: str, scans: list[dict]) -> dict:
    if rule == "door_clear_width":
        return {
            "rule": rule,
            "status": "NOT_EVALUABLE",
            "reason": "The room-geometry meshes contain no door-opening labels or calibrated clear-opening evidence.",
        }
    return {"rule": rule, "status": "NOT_EVALUABLE", "reason": "No supported rule evidence is available."}


def _parse_tool(raw: str) -> str | None:
    match = re.search(r"\{[^{}]*\}", raw, flags=re.DOTALL)
    if not match:
        return None
    try:
        result = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(result, dict) or set(result) != {"tool"}:
        return None
    tool = result["tool"]
    return tool if isinstance(tool, str) and tool in TOOLS else None


def keyword_router(query: str) -> str:
    q = query.lower()
    if any(word in q for word in ("license", "doi", "hash", "sha-256", "source", "origin", "evidence")):
        return "provenance"
    if any(word in q for word in ("door", "doorway", "clearance", "accessible", "threshold", "certify")):
        return "rule"
    if any(word in q for word in ("compare", "comparison", "sensor", "scanner", "between", "vary", "contrast", "capture systems")):
        return "compare"
    if any(word in q for word in ("surface", "floor", "ceiling", "wall", "triangle", "orientation", "classify")):
        return "segment"
    return "measure"


PLANNER_CASES = [
    ("Give the room's length, width, and height.", "measure"),
    ("How tall is the hall?", "measure"),
    ("What is the full bounding-box size?", "measure"),
    ("Report the room extents in meters.", "measure"),
    ("Measure the horizontal footprint dimensions.", "measure"),
    ("Find the floor, ceiling, and wall surfaces.", "segment"),
    ("Classify mesh faces by orientation.", "segment"),
    ("Which triangles are vertical?", "segment"),
    ("Estimate areas of horizontal versus vertical surfaces.", "segment"),
    ("Show surface orientation groups.", "segment"),
    ("Compare the three hall models.", "compare"),
    ("Which sensor mesh has the most vertices?", "compare"),
    ("How do hall dimensions vary by scanner?", "compare"),
    ("Contrast the Theta V and BLK360 models.", "compare"),
    ("Summarize geometry differences across capture systems.", "compare"),
    ("Is the door clear width above 0.86 m?", "rule"),
    ("Check if accessible clearance is adequate.", "rule"),
    ("Does the doorway satisfy an 860 mm threshold?", "rule"),
    ("Assess the entrance width against the requirement.", "rule"),
    ("Can you certify door clearance?", "rule"),
    ("What is the license for this scan?", "provenance"),
    ("Provide the source DOI.", "provenance"),
    ("Show the scan's SHA-256 digest.", "provenance"),
    ("Where did the model originate?", "provenance"),
    ("What evidence supports this report?", "provenance"),
]


def _load_llm():
    sys.path.insert(0, str(ROOT / ".vendor"))
    try:
        from gpt4all import GPT4All
    except ImportError as exc:
        raise RuntimeError("Install project dependencies from requirements.txt first") from exc
    if not MODEL.exists():
        raise FileNotFoundError(f"Missing open-weight planner {MODEL}; run `python poc.py --fetch-model`")
    return GPT4All(
        MODEL.name,
        model_path=str(MODEL.parent),
        allow_download=False,
        device="cpu",
        n_threads=min(os.cpu_count() or 2, 4),
        verbose=False,
    )


def llm_tool(model, query: str) -> tuple[str | None, str, float]:
    prompt = (
        "<|im_start|>system\n"
        "Choose one service tool for the user's request. Allowed tools: measure, segment, compare, rule, provenance. "
        "Use segment for surface orientation; compare for differences across scans or sensors; rule for door or clearance "
        "requirements; provenance for source, license, hash, or evidence origin; otherwise use measure for room size. "
        'Return exactly one JSON object with one key, for example {"tool":"measure"}. No other text.\n'
        "<|im_end|>\n<|im_start|>user\n"
        f"Request: {query}\n<|im_end|>\n<|im_start|>assistant\n"
    )
    start = time.perf_counter()
    raw = model.generate(prompt, max_tokens=40, temp=0, top_k=1, repeat_penalty=1.05)
    elapsed = (time.perf_counter() - start) * 1000
    return _parse_tool(raw), raw.strip(), elapsed


def planner_evaluation() -> dict:
    model = _load_llm()
    rows = []
    for query, expected in PLANNER_CASES:
        baseline = keyword_router(query)
        proposed, raw, elapsed = llm_tool(model, query)
        agreement_guard = proposed if proposed == baseline else baseline
        rows.append({
            "query": query,
            "expected": expected,
            "baseline": baseline,
            "llm_tool": proposed,
            "llm_raw": raw,
            "llm_valid": proposed in TOOLS,
            "llm_exact": proposed == expected,
            "baseline_exact": baseline == expected,
            "latency_ms": round(elapsed, 2),
            "fallback_tool": proposed or baseline,
            "fallback_used": proposed is None,
            "agreement_guard_tool": agreement_guard,
            "agreement_guard_fallback_used": proposed != baseline,
        })
    count = len(rows)
    valid_count = sum(row["llm_valid"] for row in rows)
    exact_count = sum(row["llm_exact"] for row in rows)
    guarded_fallbacks = sum(row["llm_tool"] != row["baseline"] for row in rows)
    guarded_exact = sum(row["agreement_guard_tool"] == row["expected"] for row in rows)
    latencies = sorted(row["latency_ms"] for row in rows)
    metrics = {
        "cases": count,
        "llm_schema_valid_rate": valid_count / count,
        "llm_exact_tool_accuracy": exact_count / count,
        "keyword_exact_tool_accuracy": sum(row["baseline_exact"] for row in rows) / count,
        "llm_baseline_agreement_rate": sum(row["llm_tool"] == row["baseline"] for row in rows) / count,
        "agreement_guarded_exact_accuracy": guarded_exact / count,
        "agreement_guard_fallback_rate": guarded_fallbacks / count,
        "invalid_output_fallback_rate": sum(row["fallback_used"] for row in rows) / count,
        "llm_median_latency_ms": latencies[len(latencies) // 2],
        "llm_p95_latency_ms": latencies[min(count - 1, math.ceil(0.95 * count) - 1)],
        "decode": "CPU, greedy (temperature 0, top_k 1), one generation per query",
    }
    return {"model": "Qwen2.5-0.5B-Instruct Q4_0", "model_license": "Apache-2.0", "metrics": metrics, "cases": rows}


def _render_views(scans: list[dict]) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    FIGURES.mkdir(parents=True, exist_ok=True)
    width, height = 1500, 780
    image = Image.new("RGB", (width, height), (15, 20, 28))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=22)
    footer_font = ImageFont.load_default(size=18)
    panel_w, panel_h = 480, 330
    for idx, scan in enumerate(scans):
        row, col = divmod(idx, 3)
        left, top = 20 + col * 495, 35 + row * 360
        title = f"{scan['room']} - {scan['sensor']}"
        draw.text((left, top), title, fill=(242, 245, 250), font=font)
        points = scan["samples"]
        min_z = scan["bounds_m"]["min"][2]
        max_z = scan["bounds_m"]["max"][2]
        projected = []
        for x, y, z, r, g, b in points:
            # Isometric projection; coordinates are translated per panel below.
            px = (x - y) * 0.8660254
            py = (x + y) * 0.5 - z
            level = (z - min_z) / max(max_z - min_z, 1e-6)
            red = int(min(255, max(0, 255 * (2 * level - 0.35))))
            blue = int(min(255, max(0, 255 * (1.25 - 1.7 * level))))
            green = int(min(255, max(0, 235 * (1 - abs(level - 0.5) * 2))))
            projected.append((px, py, (red, green, blue)))
        min_x = min(point[0] for point in projected); max_x = max(point[0] for point in projected)
        min_y = min(point[1] for point in projected); max_y = max(point[1] for point in projected)
        scale = min((panel_w - 25) / max(max_x - min_x, 1e-6), (panel_h - 65) / max(max_y - min_y, 1e-6))
        for px, py, color in projected:
            sx = int(left + 8 + (px - min_x) * scale)
            sy = int(top + 43 + (max_y - py) * scale)
            draw.ellipse((sx - 1, sy - 1, sx + 1, sy + 1), fill=color)
    legend_x, legend_y, legend_w = 1210, 743, 220
    draw.text((1125, 740), "low z", fill=(195, 202, 212), font=footer_font)
    for i in range(legend_w):
        level = i / max(legend_w - 1, 1)
        red = int(min(255, max(0, 255 * (2 * level - 0.35))))
        blue = int(min(255, max(0, 255 * (1.25 - 1.7 * level))))
        green = int(min(255, max(0, 235 * (1 - abs(level - 0.5) * 2))))
        draw.line((legend_x + i, legend_y, legend_x + i, legend_y + 12), fill=(red, green, blue))
    draw.text((1438, 740), "high z", fill=(195, 202, 212), font=footer_font)
    draw.text((20, 750), "Registered room-mesh vertices; colors normalized within each panel.", fill=(195, 202, 212), font=footer_font)
    path = FIGURES / "room_mesh_vertex_views.png"
    image.save(path, dpi=(180, 180))
    return path


def _render_extents(scans: list[dict]) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    FIGURES.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1500, 700), "white")
    draw = ImageDraw.Draw(image)
    title_font = ImageFont.load_default(size=24)
    label_font = ImageFont.load_default(size=20)
    value_font = ImageFont.load_default(size=20)
    colors = [(52, 110, 180), (235, 139, 61), (77, 160, 116)]
    axis_labels = ["X extent (m)", "Y extent (m)", "Z extent (m)"]
    for group_index, room in enumerate(("Hall 101", "Tetra Hall")):
        group = [scan for scan in scans if scan["room"] == room]
        left = 70 + group_index * 730
        draw.text((left + 155, 25), f"{room}: AABB extents by sensor", fill=(10, 20, 40), font=title_font)
        max_val = max(value for scan in group for value in scan["bounds_m"]["extent"])
        chart_top, chart_bottom = 80, 580
        chart_height = chart_bottom - chart_top
        for axis, label in enumerate(axis_labels):
            base_y = chart_bottom - axis * 165
            draw.text((left, base_y - 95), label, fill=(45, 55, 70), font=label_font)
            for sensor_i, scan in enumerate(group):
                val = scan["bounds_m"]["extent"][axis]
                bar_h = val / max_val * 86
                x = left + 150 + sensor_i * 170
                y = base_y - bar_h
                draw.rectangle((x, y, x + 105, base_y), fill=colors[sensor_i])
                sensor_label = {"Matterport Pro2": "Matterport", "Ricoh Theta V": "Theta V"}.get(scan["sensor"], scan["sensor"])
                draw.text((x + 15, base_y + 7), sensor_label, fill=(45, 55, 70), font=label_font)
                draw.text((x + 15, y - 30), f"{val:.2f}", fill=(30, 30, 30), font=value_font)
        draw.line((left + 120, 105, left + 120, 592), fill=(180, 185, 190), width=1)
    draw.text((70, 635), "Source coordinates interpreted as metres; AABB is not a rotated room plan or a compliance measurement.", fill=(55, 65, 75), font=label_font)
    path = FIGURES / "sensor_mesh_axis_extents.png"
    image.save(path, dpi=(180, 180))
    return path


def _write_csv(rows: list[dict], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["room", "sensor", "vertices", "faces", "x_extent_m", "y_extent_m", "z_extent_m", "horizontal_area_m2", "vertical_area_m2", "oblique_area_m2", "sha256"])
        for row in rows:
            writer.writerow([
                row["room"], row["sensor"], row["vertices"], row["faces"],
                *[f"{v:.4f}" for v in row["bounds_m"]["extent"]],
                *[f"{row['orientation_area_m2'][key]:.3f}" for key in ("horizontal", "vertical", "oblique")],
                row["sha256"],
            ])


def run_study() -> dict:
    scans = scan_service()
    comparisons = compare_service(scans)
    planner = planner_evaluation()
    labels = evaluate_3dses()
    compact = measurement_service(scans)
    figures = [_render_views(scans), _render_extents(scans)]
    _write_csv(scans, OUTPUTS / "scan_measurements.csv")
    report = {
        "study": "Python point-cloud service orchestration pilot",
        "dataset": {
            "title": "Indoor low-cost sensor system data",
            "doi": DATASET_DOI,
            "license": "CC-BY-4.0",
            "rooms": ["Aalto University Lecture Hall 101", "Tetra Conference Hall"],
            "scope_note": "Six registered PLY room-geometry meshes; mesh vertices used as point samples, not original E57 scans.",
        },
        "method": {
            "source_immutable": True,
            "geometry": "binary PLY vertex bounds and triangle orientation-area bins",
            "orientation_thresholds": {"horizontal_abs_normal_z_min": 0.95, "vertical_abs_normal_z_max": 0.10},
            "comparison_anchor": "BLK360 mesh per room; comparison anchor only, not ground truth",
            "planner": planner["model"],
            "planner_license": planner["model_license"],
            "planner_sha256": hashlib.sha256(MODEL.read_bytes()).hexdigest(),
        },
        "execution": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "logical_cpu_count": os.cpu_count(),
            "created_utc": datetime.now(timezone.utc).isoformat(),
        },
        "scans": compact,
        "cross_sensor_extent_comparison": comparisons,
        "planner_evaluation": planner,
        "3dses_manual_label_evaluation": labels,
        "door_clearance_rule": rule_service("door_clear_width", scans),
        "service_trace": [
            {"step": 1, "service": "cloud_access", "result": f"{len(scans)} immutable source meshes read"},
            {"step": 2, "service": "surface_segmentation", "result": "triangle orientation groups; not semantic wall/floor labels"},
            {"step": 3, "service": "measurement", "result": "axis-aligned extents and orientation area"},
            {"step": 4, "service": "comparison", "result": "cross-sensor extents relative to BLK360 anchor"},
            {"step": 5, "service": "rule", "result": "door-clearance check abstained"},
            {"step": 6, "service": "provenance", "result": "source SHA-256 recorded for each scan"},
        ],
        "figures": [str(path.relative_to(ROOT)) for path in figures],
        "limits": [
            "only two rooms and three mesh products per room are evaluated",
            "PLY inputs are registered mesh reconstructions; results do not replace raw-scan evaluation",
            "AABB extents are orientation-dependent and not engineering-grade room dimensions",
            "orientation-based face groups are not semantic wall/floor/ceiling segmentation",
            "door opening and accessibility evidence are absent; the rule correctly abstains",
            "the small open-weight LLM is evaluated only for tool selection on 25 authored prompts",
            "the service boundaries run in one Python process; network discovery and distributed reliability remain untested",
        ],
    }
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def evaluate_3dses() -> dict:
    """Evaluate dataset-provided CAD pseudo-labels against manual Gold labels."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    data_dir = ROOT / "data" / "3dses_gold"
    names = ["Column", "Components", "Covering", "Damper", "Door", "Exit sign", "Fire terminal", "Furniture", "Heater", "Lamp", "Outlet", "Railing", "Slab", "Stair", "Switch", "Wall", "Window", "Clutter"]
    total = np.zeros((18, 18), dtype=np.int64)
    station_results = []
    for path in sorted(data_dir.glob("S*.npy")):
        array = np.load(path, mmap_mode="r", allow_pickle=False)
        if array.ndim != 2 or array.shape[1] != 9:
            raise ValueError(f"Unexpected 3DSES schema: {path.name} {array.shape}")
        matrix = np.zeros_like(total)
        for start in range(0, len(array), 500_000):
            block = array[start:start + 500_000]
            truth = block[:, 7].astype(np.int64)
            predicted = block[:, 8].astype(np.int64)
            valid = (truth >= 0) & (truth < 18) & (predicted >= 0) & (predicted < 18)
            matrix += np.bincount(18 * truth[valid] + predicted[valid], minlength=324).reshape(18, 18)
        total += matrix
        station_results.append({"station": path.stem, "points": int(matrix.sum()), "accuracy": float(np.trace(matrix) / max(1, matrix.sum()))})
    if not station_results:
        raise FileNotFoundError(f"No manually labeled 3DSES Gold arrays in {data_dir}")
    tp = np.diag(total)
    support = total.sum(axis=1)
    predicted_support = total.sum(axis=0)
    iou = tp / np.maximum(1, support + predicted_support - tp)
    recall = tp / np.maximum(1, support)
    class_rows = [{"class": names[i], "manual_points": int(support[i]), "pseudo_points": int(predicted_support[i]), "iou": float(iou[i]), "recall": float(recall[i])} for i in range(18)]
    accuracy = float(tp.sum() / max(1, total.sum()))
    macro_iou = float(np.mean(iou))
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    with (OUTPUTS / "3dses_class_metrics.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=class_rows[0].keys()); writer.writeheader(); writer.writerows(class_rows)
    with (OUTPUTS / "3dses_station_metrics.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=station_results[0].keys()); writer.writeheader(); writer.writerows(station_results)
    FIGURES.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1300, 720), "white"); draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=18); small = ImageFont.load_default(size=14)
    draw.text((35, 20), "3DSES Gold: CAD pseudo-label IoU vs manual labels", fill="black", font=font)
    start_x, start_y, plot_w, plot_h = 330, 75, 900, 560
    draw.line((start_x, start_y, start_x, start_y + plot_h), fill="#555")
    for tick in range(0, 11, 2):
        y = start_y + plot_h - tick * plot_h / 10
        draw.line((start_x, int(y), start_x + plot_w, int(y)), fill="#dddddd")
        draw.text((290, int(y)-7), f"{tick/10:.1f}", fill="#333", font=small)
    for i, row in enumerate(class_rows):
        y = start_y + i * 30
        draw.text((35, y + 2), f"{row['class']} (n={row['manual_points']:,})", fill="#222", font=small)
        draw.rectangle((start_x, y + 2, start_x + int(plot_w * row["iou"]), y + 21), fill="#3977a8")
        draw.text((start_x + plot_w + 7, y + 2), f"{row['iou']:.3f}", fill="#222", font=small)
    draw.text((35, 665), f"Micro accuracy={accuracy:.4f}; macro IoU={macro_iou:.4f}; {int(total.sum()):,} points / {len(station_results)} stations", fill="#222", font=small)
    figure = FIGURES / "3dses_class_iou.png"; image.save(figure, dpi=(180, 180))
    result = {"dataset": "3DSES Gold", "doi": "10.5281/zenodo.13323342", "license": "CC-BY-SA-4.0", "truth": "manual label column 7", "prediction": "dataset-provided CAD pseudo-label column 8", "points": int(total.sum()), "stations": station_results, "micro_accuracy": accuracy, "macro_iou": macro_iou, "macro_recall": float(np.mean(recall)), "classes": class_rows, "figure": str(figure.relative_to(ROOT)), "interpretation": "Agreement of dataset-provided CAD pseudo-labels with manual annotations; not a model trained or evaluated by this project."}
    (OUTPUTS / "3dses_label_evaluation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def _format_chat_result(tool: str, response: dict) -> str:
    """Render service evidence as concise text; the model does not invent results."""
    rows = response["result"]
    if tool == "measure":
        lines = [
            f"{row['room']} / {row['sensor']}: "
            f"{' x '.join(f'{value:.2f}' for value in row['extent_m'])} m "
            f"(AABB extents; {row['vertices']:,} vertices)"
            for row in rows
        ]
        lines.append("These are axis-aligned mesh bounds, not validated room dimensions.")
    elif tool == "segment":
        lines = [
            f"{row['room']} / {row['sensor']}: "
            + ", ".join(f"{name} {area:.2f} m^2" for name, area in row["orientation_area_m2"].items())
            for row in rows
        ]
        lines.append("Orientation groups are geometric categories, not semantic floor/wall labels.")
    elif tool == "compare":
        lines = [
            f"{row['room']} / {row['sensor']} vs BLK360 mesh: "
            f"delta XYZ {', '.join(f'{value:+.3f}' for value in row['delta_m'])} m"
            for row in rows if row["sensor"] != "BLK360"
        ]
        lines.append("BLK360 is a comparison anchor, not independent ground truth.")
    elif tool == "rule":
        lines = [f"{rows['status']}: {rows['reason']}"]
    else:
        lines = [f"Aalto indoor scan dataset, DOI {DATASET_DOI}, CC BY 4.0."]
        lines.extend(f"{row['file']} - SHA-256 {row['sha256']}" for row in rows)
    return "\n".join(lines)


def interactive_chat() -> None:
    """Run the local planner against the five independent loopback services."""
    import ecosystem_study as ecosystem

    model = _load_llm()
    processes = []
    registry = {}
    try:
        for name in sorted(TOOLS):
            port = ecosystem.free_port()
            process = ecosystem.spawn(name, port)
            processes.append(process)
            registry[name] = {"port": port, "process": process,
                              "url": f"http://127.0.0.1:{port}/v1/{name}"}
        for name, service in registry.items():
            ecosystem.wait_ready(name, service["port"], service["process"])

        print("Inspection chat ready. Ask a question; type exit or quit to stop.")
        while True:
            try:
                query = input("\nyou> ").strip()
            except EOFError:
                break
            if query.lower() in {"exit", "quit"}:
                break
            if not query:
                continue

            proposed, raw, elapsed_ms = llm_tool(model, query)
            baseline = keyword_router(query)
            selected = proposed if proposed == baseline else baseline
            print(f"LLM proposal: {proposed or 'invalid'} ({elapsed_ms:.0f} ms)")
            if proposed != selected:
                print(f"Guarded route: {selected} (keyword-router fallback)")
            status, _, response = ecosystem.checked_call(
                registry[selected]["url"], {"dataset": "aalto_hall101"}, timeout=5.0
            )
            if status != "ok" or response is None:
                print(f"Service unavailable; no answer returned ({status}).")
                continue
            print(f"\n{selected.title()} result:\n{_format_chat_result(selected, response)}")
    finally:
        for process in reversed(processes):
            ecosystem.stop(process)
        print("\nInspection chat stopped; service processes closed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Python indoor-room service orchestration PoC")
    parser.add_argument("--fetch", action="store_true", help="range-fetch the six openly licensed room meshes")
    parser.add_argument("--fetch-model", action="store_true", help="download and checksum the open-weight local planner")
    parser.add_argument("--run-study", action="store_true", help="run geometry analysis, planner evaluation, figures, and tables")
    parser.add_argument("--evaluate-3dses", action="store_true", help="evaluate the dataset's CAD pseudo-labels against manual Gold labels")
    parser.add_argument("--chat", action="store_true", help="start an interactive local inspection chat")
    args = parser.parse_args()
    if args.fetch:
        fetch_meshes()
    if args.fetch_model:
        fetch_model()
    if args.run_study:
        report = run_study()
        print(json.dumps({
            "report": str(REPORT),
            "scans": len(report["scans"]),
            "planner_metrics": report["planner_evaluation"]["metrics"],
            "figures": report["figures"],
        }, indent=2))
    if args.evaluate_3dses:
        result = evaluate_3dses()
        print(json.dumps({k: result[k] for k in ("points", "stations", "micro_accuracy", "macro_iou", "macro_recall", "figure")}, indent=2))
    if args.chat:
        interactive_chat()
    if not any((args.fetch, args.fetch_model, args.run_study, args.evaluate_3dses, args.chat)):
        parser.print_help()


if __name__ == "__main__":
    main()
