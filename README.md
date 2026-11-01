# Evidence grounded LLM service orchestration study

This project studies evidence-grounded LLM service composition, using indoor 3D inspection as the application testbed. The manuscript proposes a layered architecture for planning, capability discovery, contract and policy checks, deterministic services, claim-level evidence lineage, rule assessment, and constrained explanation. The executable Python proof of concept realizes only a subset: five local HTTP services, a static capability registry, single-tool LLM selection, file-level hashes, deterministic geometry, limited abstention, and controlled fault/input checks. It does not implement dynamic discovery, multi-step workflow planning, region-level provenance, uncertainty propagation, or evidence-constrained explanations. The LLM is not trained on point clouds.

## Reproduce

Use Python 3.12. Project-local dependencies are in `.vendor`; model weights and selected data are retained in this project.

```powershell
python .\poc.py --run-study
python .\raw_scan_validation.py
python .\ecosystem_study.py
python .\build_paper.py
```

`--run-study` recomputes six-mesh geometry, the 25-query planner comparison, and the 3DSES label benchmark. `raw_scan_validation.py --fetch` downloads the four small Hall/Tetra E57 and mesh segment assets. Data acquisition options are `--fetch` for six Aalto mesh assets and `--fetch-model` for the pinned GGUF model. `--evaluate-3dses` runs only the label benchmark. Results and figures are written to `outputs/`.

## Findings

- Geometry: 315,792–1,727,201 mesh vertices per model. Hall 101 AABB height varies 2.55–3.05 m across reconstructions; these are not validated room dimensions.
- Planner: 20/25 allowed JSON plans (80%), 12/25 exact tool selections (48%), versus 24/25 (96%) for the deterministic keyword baseline. Agreement guard accuracy is 96%, with 52% fallback.
- Label agreement: 44,527,388 points across seven 3DSES Gold stations. Dataset-provided CAD pseudo-labels achieve 94.21% micro accuracy, 68.16% macro IoU, and 76.90% macro recall against manual labels. This measures the supplied pseudo-labels, not a model or service built here. Points are spatially correlated and station scans come from one building; pointwise confidence intervals are not reported.
- Raw-scan consistency: symmetric nearest-neighbor distances compare each registered Theta V E57 segment with up to 24,000 corresponding mesh vertices. Mean / p95 are 0.052 / 0.138 m for Hall 101 and 0.047 / 0.080 m for Tetra. This checks within-source scan/mesh consistency, not independent geometric accuracy.
- Service ecosystem: five separate Python processes; all 25 planner decisions dispatched successfully. Median HTTP latency was 1.7–15.9 ms per service in this local run. Injected 503, malformed-response, and timeout faults failed closed; replacement service recovered successfully. Four invalid-input checks returned expected 400/404/413 responses.
- The doorway clearance service abstains because no clear-opening truth is available in the selected Aalto meshes.

## Journal fit and remaining gaps

The TSC fit is strongest in the service-composition and evidence-governance problem framing, but the implementation remains partial. Service processes are isolated on one host; dynamic discovery, multi-step composition, region-level lineage, uncertainty propagation, load, and production security are untested. The geometry case covers two Aalto rooms; 3DSES contributes one additional lab/building. The pseudo-labels are not independently validated, and raw/mesh distances are within-source checks. The revised paper distinguishes the target architecture from demonstrated capabilities and identifies the additional experiments required for a full ecosystem evaluation.

See `MANUAL_TESTING.md` for the step-by-step PowerShell tutorial, endpoint examples, expected rejection codes, and full reliability/security test procedure.

## Data and model attribution

- Aalto University, *Indoor low-cost sensor system data*, DOI 10.5281/zenodo.3876363, CC BY 4.0. Attribution is in `data/aalto_hall101/ATTRIBUTION.txt`.
- Aalto Hall 101 and Tetra segment E57/PLY assets, same DOI and license. Attribution is in `data/aalto_segments/ATTRIBUTION.txt`.
- A. Mérizette et al., *3DSES*, DOI 10.5281/zenodo.13323342, CC BY-SA 4.0. Attribution is in `data/3dses_gold/ATTRIBUTION.txt`. Only manually annotated Gold arrays are retained.
- Qwen Team, *Qwen2.5-0.5B-Instruct-GGUF*, Apache-2.0. Attribution is in `models/ATTRIBUTION.txt`.
