"""Compare Aalto segment mesh vertices with corresponding registered raw E57 scans."""
from __future__ import annotations

import csv
import hashlib
import json
import argparse
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / ".vendor"))
sys.path.insert(0, str(ROOT))
import numpy as np
import pye57
from scipy.spatial import cKDTree
import poc

PAIRS = [
    ("Hall 101", ROOT / "data/aalto_segments/Hall_101_segment_ThetaV_cloud.e57", ROOT / "data/aalto_segments/Hall_101_segment_ThetaV_mesh.ply"),
    ("Tetra Hall", ROOT / "data/aalto_segments/Tetra_segment_ThetaV_cloud.e57", ROOT / "data/aalto_segments/Tetra_segment_ThetaV_mesh.ply"),
]


def fetch_segments() -> None:
    with urllib.request.urlopen("https://zenodo.org/api/records/3876363", timeout=30) as response:
        record = json.load(response)
    files = {item["key"]: item for item in record["files"]}
    members = [
        ("Hall_101_segment.zip", "Hall_101_segment/Hall_101_segment_ThetaV_cloud.e57"),
        ("Hall_101_segment.zip", "Hall_101_segment/Hall_101_segment_ThetaV_mesh.ply"),
        ("Tetra_segment.zip", "Tetra_segment/Tetra_segment_ThetaV_cloud.e57"),
        ("Tetra_segment.zip", "Tetra_segment/Tetra_segment_ThetaV_mesh.ply"),
    ]
    for archive, member in members:
        asset = files[archive]
        target = ROOT / "data/aalto_segments" / Path(member).name
        poc._extract_zip_member(asset["links"]["self"], asset["size"], member, target)
    (ROOT / "data/aalto_segments/ATTRIBUTION.txt").write_text(
        "Indoor low-cost sensor system data, DOI 10.5281/zenodo.3876363, CC BY 4.0. "
        "Creator: Matias Ingman, Aalto University. These assets are Hall 101 and Tetra segment Theta V E57 clouds and meshes.\n",
        encoding="utf-8",
    )


def run() -> dict:
    rows = []
    for room, e57_path, mesh_path in PAIRS:
        if not e57_path.exists() or not mesh_path.exists():
            raise FileNotFoundError(f"Missing segment assets for {room}; rerun the fetch step documented in README.md")
        scan = pye57.E57(str(e57_path))
        if scan.scan_count != 1:
            raise ValueError(f"Expected one E57 scan in {e57_path.name}")
        cloud = scan.read_scan(0, transform=True, ignore_missing_fields=True)
        raw = np.column_stack([cloud["cartesianX"], cloud["cartesianY"], cloud["cartesianZ"]])
        raw = raw[np.isfinite(raw).all(axis=1)]
        mesh = poc._parse_ply(mesh_path, max_samples=24_000)
        vertices = np.asarray([point[:3] for point in mesh["samples"]], dtype=np.float64)
        if len(raw) == 0 or len(vertices) == 0:
            raise ValueError(f"Empty points in {room}")
        # Symmetric point-to-point nearest-neighbor distances; mesh is deterministically sampled.
        d_raw_to_mesh = cKDTree(vertices).query(raw, k=1, workers=-1)[0]
        d_mesh_to_raw = cKDTree(raw).query(vertices, k=1, workers=-1)[0]
        both = np.concatenate([d_raw_to_mesh, d_mesh_to_raw])
        rows.append({
            "room": room, "raw_points": int(len(raw)), "mesh_vertices_sampled": int(len(vertices)),
            "raw_to_mesh_median_m": float(np.median(d_raw_to_mesh)),
            "raw_to_mesh_p95_m": float(np.percentile(d_raw_to_mesh, 95)),
            "mesh_to_raw_median_m": float(np.median(d_mesh_to_raw)),
            "mesh_to_raw_p95_m": float(np.percentile(d_mesh_to_raw, 95)),
            "symmetric_mean_m": float(np.mean(both)), "symmetric_p95_m": float(np.percentile(both, 95)),
            "symmetric_rmse_m": float(np.sqrt(np.mean(both**2))),
            "within_0_05m": float(np.mean(both <= 0.05)), "within_0_10m": float(np.mean(both <= 0.10)),
            "raw_sha256": hashlib.sha256(e57_path.read_bytes()).hexdigest(),
            "mesh_sha256": mesh["sha256"],
        })
    out = ROOT / "outputs"
    (out / "raw_scan_validation.json").write_text(json.dumps({"method": "symmetric nearest-neighbor point distances; registered raw E57 vs up to 24,000 mesh vertices; within-scan consistency, not independent geometric accuracy", "dataset_doi": "10.5281/zenodo.3876363", "license": "CC-BY-4.0", "pairs": rows}, indent=2), encoding="utf-8")
    with (out / "raw_scan_validation.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys()); writer.writeheader(); writer.writerows(rows)
    from PIL import Image, ImageDraw, ImageFont
    image = Image.new("RGB", (1000, 430), "white"); draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=20); small = ImageFont.load_default(size=17)
    draw.text((30, 18), "Registered E57 cloud vs sampled mesh vertices", fill="black", font=font)
    scale = 720 / max(row["symmetric_p95_m"] for row in rows)
    for index, row in enumerate(rows):
        top = 110 + index * 140
        draw.text((30, top + 18), row["room"], fill="#222", font=font)
        mean_width = int(row["symmetric_mean_m"] * scale); p95_width = int(row["symmetric_p95_m"] * scale)
        draw.rectangle((240, top, 240 + mean_width, top + 36), fill="#3977a8")
        draw.rectangle((240, top + 48, 240 + p95_width, top + 84), fill="#e09a43")
        draw.text((250 + mean_width, top + 5), f"mean {row['symmetric_mean_m']:.3f} m", fill="#222", font=small)
        draw.text((250 + p95_width, top + 52), f"p95 {row['symmetric_p95_m']:.3f} m", fill="#222", font=small)
    draw.text((30, 390), "Directional point-to-point NN; not point-to-surface accuracy or independent ground truth.", fill="#444", font=small)
    image.save(out / "figures/raw_scan_mesh_nn.png", dpi=(180, 180))
    print(json.dumps(rows, indent=2))
    return {"pairs": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch", action="store_true", help="fetch the four small, openly licensed E57/PLY segment assets")
    args = parser.parse_args()
    if args.fetch:
        fetch_segments()
    run()
