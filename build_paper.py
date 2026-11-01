"""Build the IEEE Computer Society double-column Word case-study draft."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / ".vendor"))

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

REPORT = ROOT / "outputs" / "study_results.json"
OUT = ROOT / "Point_Cloud_Service_Orchestration_PoC_Paper.docx"
TEMPLATE = ROOT / "Transactions-template-and-instructions-on-how-to-create-your-article-formatted (4).docx"

TITLE = "Evidence-Grounded LLM Service Orchestration for Auditable Engineering Inspection: Architecture and Point-Cloud Case Study"
ABSTRACT = (
    "Large language models (LLMs) can coordinate engineering services, but inspection decisions require evidence that can be traced and independently checked. We present an evidence-grounded service-oriented architecture for this problem and evaluate an initial Python realization in indoor 3D inspection. The target architecture separates intent planning, capability discovery, contract and policy validation, deterministic geometry services, an evidence ledger, rule evaluation, and constrained explanation. The prototype implements only a bounded slice: five independently launched loopback services, static capability mapping, file-level hashes, deterministic geometry, and explicit abstention; it does not implement dynamic discovery, multi-step workflow planning, spatial-region lineage, or uncertainty propagation. In 25 prompts, a 0.5B Qwen2.5 planner selects the expected tool on 48%, versus 96% for a keyword baseline. All 25 guarded calls were dispatched, and four input-rejection and four fault/recovery checks passed. Results establish feasibility and expose architectural gaps, not validated engineering accuracy or production readiness. The point-cloud case study serves as a testbed for service composition, evidence traceability, and reliability."
)


def set_columns(section, count=2):
    cols = section._sectPr.xpath("./w:cols")
    element = cols[0] if cols else OxmlElement("w:cols")
    if not cols:
        section._sectPr.append(element)
    element.set(qn("w:num"), str(count))
    element.set(qn("w:space"), "360")
    element.set(qn("w:sep"), "0")


def set_run(run, size=9.5, bold=False, italic=False, color="000000"):
    run.font.name = "Times New Roman"
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Times New Roman")
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = RGBColor.from_string(color)


def add_text(doc, text, size=10, bold_lead=None, align=None, keep=False):
    text = text.replace("`", "")
    p = doc.add_paragraph()
    if align is not None:
        p.alignment = align
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.line_spacing = Pt(12)
    if keep:
        p.paragraph_format.keep_with_next = True
    if bold_lead and text.startswith(bold_lead):
        set_run(p.add_run(bold_lead), size=size, bold=True)
        set_run(p.add_run(text[len(bold_lead):]), size=size)
    else:
        set_run(p.add_run(text), size=size)
    return p


def add_heading(doc, text, level=1):
    p = doc.add_paragraph(style="Heading 1" if level == 1 else "Heading 2")
    p.paragraph_format.keep_with_next = True
    p.add_run(text)
    return p


def add_caption(doc, text):
    if text.startswith("TABLE ") and ". " in text:
        label, title = text.split(". ", 1)
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(4)
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.keep_with_next = True
        set_run(p.add_run(label), size=8.5)
        title_p = doc.add_paragraph()
        title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        title_p.paragraph_format.space_after = Pt(2)
        title_p.paragraph_format.keep_with_next = True
        set_run(title_p.add_run(title), size=8.5)
        return title_p
    text = text.replace("FIG. ", "Fig. ", 1) if text.startswith("FIG. ") else text
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.keep_with_next = True
    p.paragraph_format.line_spacing = Pt(9.5)
    set_run(p.add_run(text), size=8)
    return p


def add_table(doc, headers, rows, widths, font_size=7):
    table = doc.add_table(rows=1, cols=len(headers))
    table.autofit = False
    table.style = "Normal Table"
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "bottom", "insideH"):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), "4")
        element.set(qn("w:color"), "000000")
        borders.append(element)
    for edge in ("left", "right", "insideV"):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "nil")
        borders.append(element)
    table._tbl.tblPr.append(borders)
    for col, width in zip(table.columns, widths):
        col.width = Inches(width)
    for row_index, values in enumerate([headers, *rows]):
        cells = table.rows[0].cells if row_index == 0 else table.add_row().cells
        for i, value in enumerate(values):
            cell = cells[i]
            cell.text = str(value)
            cell.width = Inches(widths[i])
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            for p in cell.paragraphs:
                p.paragraph_format.space_before = Pt(0)
                p.paragraph_format.space_after = Pt(0)
                p.paragraph_format.line_spacing = Pt(8)
                for run in p.runs:
                    set_run(run, size=font_size, bold=(row_index == 0))
    return table


def add_figure(doc, path, caption, width=3.35):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.line_spacing = 1.0
    p.paragraph_format.keep_with_next = True
    p.add_run().add_picture(str(path), width=Inches(width))
    add_caption(doc, caption)


def initialize():
    if not TEMPLATE.exists():
        raise FileNotFoundError(f"IEEE Transactions template not found: {TEMPLATE}")
    doc = Document(TEMPLATE)
    body = doc._element.body
    children = list(body)
    section_break = next(i for i, child in enumerate(children)
                         if child.tag.endswith("}p") and child.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pPr") is not None
                         and child.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pPr").find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}sectPr") is not None)
    for child in children[section_break + 1:]:
        if child.tag.endswith("}sectPr"):
            continue
        body.remove(child)
    for section in doc.sections:
        for p in section.header.paragraphs:
            if "REPLACE THIS LINE" in p.text:
                p.clear()
    return doc
    return doc


def fmt_extent(values):
    return " × ".join(f"{value:.2f}" for value in values)


def build():
    if not REPORT.exists():
        raise FileNotFoundError("Run `python poc.py --run-study` before building the paper")
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    raw_scan = json.loads((ROOT / "outputs" / "raw_scan_validation.json").read_text(encoding="utf-8"))
    ecosystem = json.loads((ROOT / "outputs" / "ecosystem_study.json").read_text(encoding="utf-8"))
    metrics = report["planner_evaluation"]["metrics"]
    scans = report["scans"]
    comparisons = report["cross_sensor_extent_comparison"]
    doc = initialize()

    title_p = doc.paragraphs[0]
    title_p.clear(); title_p.style = "Title"
    title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_run(title_p.add_run(TITLE), size=24)
    author_p = doc.paragraphs[2]
    author_p.clear(); author_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_run(author_p.add_run("Author names and affiliations to be added"), size=11)
    abstract_p = doc.add_paragraph(style="Abstract")
    abstract_p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    set_run(abstract_p.add_run("Abstract—"), size=9, bold=True, italic=True)
    set_run(abstract_p.add_run(ABSTRACT), size=9, bold=True)
    index_p = doc.add_paragraph()
    index_p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    set_run(index_p.add_run("Index Terms—"), size=9, bold=True, italic=True)
    set_run(index_p.add_run("engineering inspection, evidence provenance, large language models, point clouds, service composition, service orchestration, service-oriented architecture"), size=9, bold=True)
    break_p = doc.add_paragraph()
    break_p.add_run().add_break(WD_BREAK.COLUMN)

    add_heading(doc, "I. INTRODUCTION")
    add_text(doc, "LLM-enabled service ecosystems promise flexible composition from natural-language goals, but engineering inspection has a stricter requirement: every conclusion must be tied to the data, operations, rules, and uncertainty that support it. A model can propose a workflow, but it cannot serve as the measurement instrument or silently convert missing evidence into compliance. This motivates an architecture in which language-based planning is separated from capability contracts, deterministic computation, rule assessment, and an auditable evidence record [3]–[5].")
    add_text(doc, "We frame indoor 3D inspection as a demanding case study for this service-oriented problem. A query such as ‘check the opening’ may require intent interpretation, geometry access, segmentation or measurement, rule selection, and an explanation of what is known or unavailable. The target architecture therefore treats service composition and evidence lineage as the research focus; room geometry is the application data. The LLM operates on service descriptions and typed results, not raw coordinates, and numerical values remain outputs of specialized computation.")
    add_text(doc, "This paper presents (1) a layered architecture that separates planning, discovery, contract/policy checks, computation, evidence recording, rule assessment, and explanation; (2) an honest account of a partial Python implementation, including its static registry and file-level provenance limits; and (3) an empirical case study of planner selection, service interoperability, fault closure, and real point-cloud-derived geometry. The study combines six registered meshes, two paired raw E57/mesh segments, five independent local processes, and a secondary 3DSES annotation-agreement benchmark. It does not implement the full target architecture, train a point-cloud model, or establish engineering measurement accuracy.")
    add_text(doc, "Research questions: RQ1, what architecture can constrain LLM-proposed engineering workflows and preserve evidence lineage across services? RQ2, does the tested compact LLM improve service selection over a deterministic router? RQ3, do independent service contracts reject tested invalid inputs and fail closed under controlled faults? RQ4, which target-architecture requirements remain unimplemented or unevaluated?")

    add_heading(doc, "II. SERVICE-ORIENTED RESEARCH FRAMING")
    add_text(doc, "The special issue centers LLMs in service-oriented ecosystem design: service selection, composition, orchestration, interoperability, and applied evaluation [1]. The contribution is framed accordingly. The point-cloud task is not proposed as the primary novelty; it is a case where an unsupported answer has operational consequences and where a service workflow can be tested against source evidence. Tool-using LLM research motivates treating model output as a proposal subject to validation [4], [5], while service-oriented computing motivates explicit capability boundaries and interoperable contracts [3].")
    add_text(doc, "The data source has also been used to compare Matterport Pro2, Ricoh Theta V, and Leica BLK360 products against Leica RTC360 reference scans. The prior study reports differences in reconstruction quality across sensors and distinguishes registered point clouds from derived meshes [6]. We use six full-room mesh products from that open dataset to test service contracts and cross-sensor bounds; we do not reproduce its point-to-cloud accuracy analysis.")
    add_text(doc, "The fit is strongest at the architectural problem level and preliminary at the evaluation level. This realization measures process separation, contract behavior, and controlled failure response, but uses a static registry on one host and a single-tool proposal rather than dynamic multi-step composition. The language-model result is below the keyword baseline. The separate 3DSES benchmark measures dataset pseudo-label agreement, not service accuracy. Thus the case supports architectural feasibility and a gap analysis; it does not yet validate the full proposed architecture. The call sets a 14-page ceiling and refers authors to TSC formatting guidance [1], [2]. This draft follows the supplied IEEE Transactions Word authoring template and remains below the page ceiling.")

    add_heading(doc, "III. EVIDENCE-GROUNDED SERVICE ARCHITECTURE")
    add_text(doc, "The target architecture has seven stages. First, the LLM translates an engineering request into a typed workflow graph whose nodes name capabilities and whose edges declare data dependencies. Second, a service registry resolves capability descriptions and versions. Third, contract and policy gates validate the plan, input types, authorization, and permitted data access before execution. Fourth, independent geometry services return typed results with units and quality metadata. Fifth, an evidence ledger binds each result to source-file hashes, spatial entity/region identifiers, service and algorithm versions, parameters, and uncertainty. Sixth, a rule service evaluates explicit criteria and returns `PASS`, `FAIL`, or `UNKNOWN` with required-evidence status. Finally, the LLM produces an explanation constrained to the recorded evidence. Claims are categorized as measured, inferred, rule-evaluated, or unavailable; unsupported transitions must remain unknown.")
    add_text(doc, "The current Python realization implements a deliberately smaller slice. It registers five capabilities (`measure`, `segment`, `compare`, `rule`, `provenance`), asks Qwen2.5-0.5B to return one allow-listed tool name, and validates that single choice. It has no workflow graph, dynamic service discovery, capability/version negotiation, authorization system, region-level evidence link, quantified measurement uncertainty, or LLM evidence-constrained explanation. The provenance service records dataset citation, source-file SHA-256, service sequence, and outputs; this is file-level lineage rather than a complete claim-to-region evidence ledger.")
    add_text(doc, "The five capabilities run as separately launched Python HTTP processes bound to loopback. Each independently loads six PLY meshes and computes its capability output. A static registry maps names to process URLs. Requests require JSON containing exactly `dataset: aalto_hall101`, are limited to 4,096 bytes, and accept no paths or commands. Unknown routes return 404; path-like or extra-field payloads are rejected. The rule service abstains when clear-opening evidence is absent. This demonstrates a local process boundary, not multi-host discovery, authentication, or production deployment.")
    add_caption(doc, "FIG. 1. Target evidence-grounded service workflow. The proof of concept implements single-tool selection, static routing, independent Python processes, file hashes, and limited abstention; the other stages are architectural requirements for future evaluation.")
    add_text(doc, "TARGET: request → workflow plan → capability discovery → contract/policy gate → independent services → evidence ledger → rule evaluation → grounded explanation.  CURRENT PoC: query → one tool proposal → static registry → independent service → deterministic output and file-level trace.", size=7.7, bold_lead="TARGET:")

    add_heading(doc, "IV. DATA AND EXPERIMENTAL METHOD")
    add_text(doc, "We use the CC BY 4.0 *Indoor low-cost sensor system data* dataset [7]. It contains registered point clouds and meshes for Lecture Hall 101 at Aalto University and Tetra Conference Hall at Hanaholmen, with Matterport Pro2, Ricoh Theta V, Leica BLK360, and Leica RTC360 reference products. The study selects the three Matterport-processed meshes per room: one for each of the first three capture systems. The original source archives are multi-gigabyte; `poc.py --fetch` uses verified byte ranges to extract only six PLY mesh files into this project. Each file's SHA-256 appears in the machine-readable output. The input files are real room reconstructions, but the PLY vertices are mesh-derived point samples rather than the original E57 scans.")
    add_text(doc, "The dataset's associated sensor-comparison publication describes Hall 101 as a 69 m² rectangular lecture hall with a 2.50 m roof and Tetra as a 173.5 m² irregular conference hall with a 3.20 m roof [6]. These published descriptors are context, not ground-truth labels for our AABB results. The coordinate values are treated as metres. The six products are registered within each room, but an axis-aligned bounding box depends on orientation and can include geometry beyond the walkable footprint.")
    add_heading(doc, "A. Geometry services", level=2)
    add_text(doc, "The parser accepts binary little-endian PLY with float x/y/z and RGB byte fields and triangular faces. It streams all vertices to calculate minima and maxima, then reports X, Y, and Z extents. For each triangle, the surface normal is computed from the cross product of two edges and the triangle area from its normal magnitude. Faces with |n_z|/||n|| ≥ 0.95 are horizontal, those with |n_z|/||n|| ≤ 0.10 are vertical, and remaining faces are oblique. Areas sum by group. This is a deterministic orientation analysis; no RANSAC plane fit, semantic classifier, opening detector, or human correction is used.")
    add_heading(doc, "B. Raw scan consistency", level=2)
    add_text(doc, "Two CC BY 4.0 registered Theta V segment E57 clouds (Hall 101 and Tetra) are paired with their dataset mesh segments. E57 poses are applied by the reader; each mesh is deterministically sampled at up to 24,000 vertices. We compute nearest-neighbor point distances in both directions using a KD-tree and summarize the concatenated distances by mean, p95, RMSE, and fractions within 5 cm and 10 cm. This is a within-source consistency and coverage check: paired products share acquisition and registration provenance, vertices are not a continuous surface, and neither product is independent geometric ground truth.")
    add_heading(doc, "C. Planner comparison", level=2)
    add_text(doc, "The local planner is Qwen2.5-0.5B-Instruct, Q4_0 GGUF, under Apache 2.0 [8], [9]. Orchestration and geometry are Python; GPT4All 2.8.2 invokes the model through its bundled native CPU inference backend, with greedy decoding, temperature 0, top-k 1, and a four-thread cap. We authored 25 prompts, with five prompts for each expected tool. Each prompt is run once. A deterministic keyword router is evaluated on the same prompts. A valid model proposal is the first parseable JSON object containing only the `tool` key and an allowed value. Generation time excludes model loading. The agreement guard accepts a model proposal only if it matches the deterministic router; on disagreement, it selects the baseline output. This deliberately conservative comparison measures planner selection, not the quality of natural-language answer generation.")
    add_heading(doc, "D. Manual-label benchmark", level=2)
    add_text(doc, "We use the 3DSES CC BY-SA 4.0 indoor TLS dataset and its seven downloadable Gold NPY stations [10], [11]. Each array has nine columns: local XYZ, RGB, intensity, manual label (column 7), and CAD-derived pseudo-label (column 8). A chunked NumPy evaluator accumulates an 18 × 18 confusion matrix; manual labels are reference and pseudo-labels are predictions. We report micro accuracy, unweighted class macro IoU, macro recall, per-class support, and station-level accuracy. This is a secondary data-quality benchmark: no model is trained, and the CAD alignment/pseudo-labeling process is supplied by the dataset authors. Point-level metrics are descriptive; points are spatially correlated, so we do not treat millions of points as independent replicates or calculate point-wise confidence intervals. One building limits external validity.")
    add_heading(doc, "E. Service and reliability protocol", level=2)
    add_text(doc, "Five independent processes are started on ephemeral loopback ports. The 25 recorded guarded planner decisions are dispatched over HTTP and timed per capability. Security-oriented contract checks submit an unknown route, a path-like dataset value, an extra command field, and an oversized body. Controlled fault modes simulate HTTP 503, malformed JSON, a 1 s delay against a 100 ms client timeout, and recovery by replacing the failed service process. These are bounded functional checks, not penetration testing, load testing, or a security certification.")
    add_heading(doc, "F. Reproducibility", level=2)
    add_text(doc, "`poc.py --run-study`, `raw_scan_validation.py`, and `ecosystem_study.py` write machine-readable JSON/CSV and figures. The full service run records prompt dispatch, latency, input rejection, and fault/recovery outcomes. Mesh assets, two E57 segments, model weights, dependencies, code, outputs, and this Word paper are kept under the project folder. The code does not modify source scans or meshes.")

    add_heading(doc, "V. RESULTS")
    add_heading(doc, "A. Real-room models and geometry outputs", level=2)
    add_text(doc, "The parser completed on all six registered meshes, ranging from 315,792 to 1,727,201 vertices and from 79,670 to 1,068,123 faces. Figure 2 displays deterministic, height-colored vertex samples from all six source meshes. The image is generated from the actual mesh vertices; it is not a synthetic room illustration.")
    add_figure(doc, ROOT / "outputs" / "figures" / "room_mesh_vertex_views.png", "FIG. 2. Registered mesh vertices for the two real rooms and three capture systems. Colors show height normalized within each panel; each panel uses a deterministic subset of up to 24,000 vertices, while the complete files are analyzed for bounds and face areas.", width=3.33)
    add_caption(doc, "TABLE I. ROOM-MESH COUNTS AND AABB EXTENTS FROM THE EXECUTED PYTHON RUN")
    scan_rows = []
    for scan in scans:
        scan_rows.append([
            f"{scan['room']} / {scan['sensor']}",
            f"{scan['vertices']:,}",
            f"{scan['faces']:,}",
            fmt_extent(scan["extent_m"]),
        ])
    add_table(doc, ["Room / sensor", "Vertices", "Faces", "X × Y × Z (m)"], scan_rows, [1.22, 0.60, 0.57, 0.96], font_size=7)
    add_text(doc, "Across Hall 101, X and Y extents differ by at most 0.060 m, but the Z extent ranges from 2.550 to 3.049 m. For Tetra, the across-product Z range is 0.084 m. Figure 3 shows the measured extents. The especially large Hall 101 height spread comes from the Matterport Pro2 mesh; without point-to-mesh validation against the RTC360 reference, we cannot identify which bound is correct. The result shows why the measurement service must expose the exact primitive and evidence rather than call an AABB value a room dimension.")
    add_figure(doc, ROOT / "outputs" / "figures" / "sensor_mesh_axis_extents.png", "FIG. 3. Script-generated axis-aligned extents by room and sensor-derived mesh. These values are not rotated plan dimensions or code-compliance measurements.", width=3.33)

    add_heading(doc, "B. Cross-sensor comparison", level=2)
    add_text(doc, "Table II reports extent deltas relative to the BLK360-derived mesh for each room. This anchor is only a comparison reference, not independent truth. X/Y variations remain below 0.073 m for all compared meshes, while Hall 101 Z varies by +0.370 m for Matterport Pro2 and −0.129 m for Theta V relative to the BLK360-derived model. For Tetra, all three Z extents are within 0.084 m. These simple bounds indicate where a service composition should request higher-quality evidence or abstain; they do not substitute for the dataset's reference-scan distance analysis [6].")
    add_caption(doc, "TABLE II. DELTAS FROM THE BLK360-DERIVED ROOM MESH (COMPARISON ANCHOR, NOT GROUND TRUTH)")
    comparison_rows = []
    for row in comparisons:
        if row["sensor"] == "BLK360":
            continue
        comparison_rows.append([
            f"{row['room']} / {row['sensor']}",
            ", ".join(f"{v:+.3f}" for v in row["delta_m"]),
            ", ".join(f"{v:+.2f}" for v in row["delta_pct"]),
        ])
    add_table(doc, ["Room / sensor", "ΔX, ΔY, ΔZ (m)", "ΔX, ΔY, ΔZ (%)"], comparison_rows, [1.35, 1.10, 0.90], font_size=7)
    add_text(doc, "The face-orientation service also completed for all six meshes. Horizontal/vertical/oblique areas are present in the JSON and CSV output. Their values vary with mesh reconstruction and triangle tessellation; because the dataset supplies no semantic face labels, these areas are service outputs rather than accuracy scores. The rule service returned `NOT_EVALUABLE` for door clearance on every request because the selected room-geometry meshes contain no annotated clear opening.")

    add_heading(doc, "C. Raw E57-to-mesh consistency", level=2)
    add_text(doc, "Two registered Theta V segment E57 clouds (Hall 101 and Tetra) are paired with their dataset mesh segments. E57 poses are applied; each mesh is deterministically sampled at up to 24,000 vertices. We compute nearest-neighbor distances in both directions and summarize the concatenated distances. This is a within-source consistency and coverage check: paired products share acquisition and registration provenance, mesh vertices are not a continuous surface, and neither product is independent geometric ground truth.")
    add_text(doc, "Hall 101 symmetric mean/p95 are 0.052/0.138 m, with 80.37% within 5 cm; Tetra mean/p95 are 0.047/0.080 m, with 72.35% within 5 cm. The mesh-to-raw p95 reaches 0.655 m for Hall 101 and 0.930 m for Tetra, so the small means must not be interpreted alone. The directional tails indicate coverage and sampling differences, not a validated accuracy bound.")
    add_figure(doc, ROOT / "outputs" / "figures" / "raw_scan_mesh_nn.png", "FIG. 4. Bidirectional nearest-neighbor distances for registered E57/mesh pairs. Large directional tails reflect coverage/sampling mismatch; this is not independent accuracy ground truth.", width=3.25)
    add_caption(doc, "TABLE III. RAW E57 / MESH CONSISTENCY RESULTS")
    raw_rows = [[pair["room"], f"{pair['raw_points']:,}", f"{pair['symmetric_mean_m']:.3f}/{pair['symmetric_p95_m']:.3f}", f"{pair['mesh_to_raw_p95_m']:.3f}", f"{pair['within_0_05m']:.1%}"] for pair in raw_scan["pairs"]]
    add_table(doc, ["Segment", "Raw N", "Mean/p95 m", "M→R p95 m", "≤5 cm"], raw_rows, [0.68, 0.48, 0.72, 0.72, 0.45], font_size=7)

    add_heading(doc, "D. LLM tool-selection results", level=2)
    add_text(doc, f"Table IV shows a weak raw LLM result against a strong simple baseline. The model returned a valid allowed tool object on 20 of 25 prompts and exactly matched expected tool labels on 12. The keyword router matched 24 labels. When the guard falls back on every disagreement, it also matches 24, but it rejects or overrides 13 of 25 model outputs. The planner has therefore not shown a benefit over deterministic routing in this task. This is a single-tool selection test, not a multi-step composition experiment. Median generation latency was {metrics['llm_median_latency_ms'] / 1000:.2f} s and p95 was {metrics['llm_p95_latency_ms'] / 1000:.2f} s, excluding the one-time model load.")
    add_caption(doc, "TABLE IV. PLANNER RESULTS ON 25 FIXED, AUTHORED PROMPTS")
    planner_rows = [
        ["Qwen2.5-0.5B, raw", f"{metrics['llm_exact_tool_accuracy']:.0%}", f"{metrics['llm_schema_valid_rate']:.0%}", f"{metrics['llm_median_latency_ms']:.0f} / {metrics['llm_p95_latency_ms']:.0f}", "n/a"],
        ["Keyword baseline", f"{metrics['keyword_exact_tool_accuracy']:.0%}", "n/a", "<1 ms", "n/a"],
        ["Agreement guard", f"{metrics['agreement_guarded_exact_accuracy']:.0%}", "n/a", "LLM + guard", f"{metrics['agreement_guard_fallback_rate']:.0%}"],
    ]
    add_table(doc, ["Planner", "Exact tool", "Valid JSON", "Median / p95 ms", "Fallback"], planner_rows, [1.08, 0.55, 0.58, 0.77, 0.45], font_size=7)
    add_text(doc, f"The 25-prompt test is intentionally transparent but too small and too authored to support generalization. Greedy decoding gives one result per prompt, not a robustness estimate. In this CPU run, median generation latency was {metrics['llm_median_latency_ms'] / 1000:.2f} s and p95 was {metrics['llm_p95_latency_ms'] / 1000:.2f} s, excluding model load. The 0.5B quantized planner is used as an orchestration probe, not as a state-of-the-art model. Its output is not allowed to compute dimensions or turn missing geometry into a compliance verdict.")

    labels = report["3dses_manual_label_evaluation"]
    add_heading(doc, "E. Manual labels and CAD pseudo-label agreement", level=2)
    add_text(doc, f"Across {labels['points']:,} points from {len(labels['stations'])} stations, the CAD pseudo-labels agree with manual labels at {labels['micro_accuracy']:.2%} micro accuracy, but unweighted macro IoU is {labels['macro_iou']:.2%} and macro recall is {labels['macro_recall']:.2%}. The gap between micro accuracy and macro IoU shows why the overall accuracy alone obscures minority-class errors. Agreement is lowest for clutter (IoU {labels['classes'][17]['iou']:.2%}) and several rare classes have limited support; class IoU is zero for Column because no manual points of that class appear in these seven files. Station accuracy ranges from {min(x['accuracy'] for x in labels['stations']):.2%} to {max(x['accuracy'] for x in labels['stations']):.2%}. These are dataset annotation-agreement results, not performance attributable to this project.")
    add_figure(doc, ROOT / "outputs" / "figures" / "3dses_class_iou.png", "FIG. 5. Class IoU for dataset-provided CAD pseudo-labels against manual labels. Parenthetical support is the manual point count; zero-support classes remain visible and are assigned IoU 0 for this report.", width=3.33)
    add_caption(doc, "TABLE V. DATASET PSEUDO-LABEL AGREEMENT SUMMARY")
    label_rows = [["All points", f"{labels['points']:,}", f"{labels['micro_accuracy']:.2%}", f"{labels['macro_iou']:.2%}", f"{labels['macro_recall']:.2%}"]]
    label_rows += [[r["station"], f"{r['points']:,}", f"{r['accuracy']:.2%}", "—", "—"] for r in labels["stations"]]
    add_table(doc, ["Unit", "Points", "Accuracy", "Macro IoU", "Macro recall"], label_rows, [0.74, 0.68, 0.55, 0.60, 0.62], font_size=7)

    add_heading(doc, "F. Independent service and reliability results", level=2)
    add_text(doc, f"Five independently launched processes served their capabilities. All 25 guarded planner decisions were dispatched successfully ({ecosystem['planner_dispatch_success_rate']:.0%}). Table VI reports single-host loopback response latency; cold startup is excluded and samples do not support throughput claims.")
    add_caption(doc, "TABLE VI. LOOPBACK SERVICE RESPONSE LATENCY (MS)")
    service_rows = [[name, f"{vals['median']:.2f}", f"{vals['p95']:.2f}"] for name, vals in ecosystem["service_latency_ms"].items()]
    add_table(doc, ["Capability", "Median", "p95"], service_rows, [1.20, 0.80, 0.80], font_size=7)
    add_text(doc, f"All {len(ecosystem['security_rejections'])} invalid-input checks passed (unknown route 404; path-like value and extra field 400; oversized body 413). All {len(ecosystem['fault_injection'])} fault/recovery checks behaved as expected: unavailable service returned 503, malformed JSON was rejected, timeout failed closed, and replacement process recovered to `ok`. These are functional checks, not penetration testing, load testing, or a security certification.")

    add_heading(doc, "VI. DISCUSSION")
    add_text(doc, "RQ1 is answered as an architectural design, not as a fully realized system: the paper specifies workflow planning, capability discovery, contract/policy gates, specialized computation, claim-level evidence lineage, rule assessment, and constrained explanation. The prototype realizes only a slice and its file-level hashes cannot support claims about source-region traceability or propagated uncertainty. For RQ2, raw tool accuracy is 48%, versus 96% for the keyword baseline; the agreement guard recovers baseline accuracy by reverting on disagreements. For RQ3, all 25 guarded requests were dispatched and the tested input/fault cases behaved as expected. RQ4 identifies the central gap: dynamic multi-service composition and claim-to-evidence auditing remain design goals, not demonstrated capabilities.")
    add_text(doc, "The intended TSC contribution is the relationship between LLM composition and evidence governance, rather than a new point-cloud algorithm. The case study shows that simple independent processes and typed request rejection are feasible, but the static registry, single-tool planner, file-level trace, and one-host deployment do not constitute a service ecosystem. A complete evaluation must compare monolithic computation, deterministic composition, and LLM-planned workflows while measuring contract compatibility, evidence completeness, task correctness, uncertainty handling, latency, and recovery under service substitution and version changes.")

    add_heading(doc, "A. Relevance and gap assessment", level=2)
    add_text(doc, "Journal relevance is strong in framing but preliminary in evidence. The paper now centers service composition, interoperability, provenance, and failure handling; five independent local processes and contract/fault checks support feasibility. However, core advertised architecture elements remain unimplemented: dynamic discovery, dependency-aware workflow composition, region-level provenance, uncertainty propagation, and evidence-constrained explanations. Evaluation also lacks multi-host operation, realistic load, production security, independent geometric ground truth, and broad building coverage. Only one small model and one authored prompt set are tested. The study is therefore an architecture proposal with a reproducible partial case-study implementation, not yet a validated TSC ecosystem contribution.")
    add_heading(doc, "B. Priority experiments for a full submission", level=2)
    for step in [
        "Implement dynamic service discovery and typed multi-step workflow graphs; evaluate capability matching, dependency execution, substitutions, and schema/version incompatibility.",
        "Build the evidence ledger around stable source-region/entity identifiers and retain units, uncertainty, algorithm/service versions, parameters, and rule versions for every claim; test trace completeness end to end.",
        "Add diverse open-license buildings, independent semantic annotations, and surveyed reference geometry; evaluate measurement error and building-held-out generalization.",
        "Compare multiple open-weight LLMs, deterministic orchestration, and monolithic processing on preregistered workflows; report repeated runs, task correctness, calibration, invalid calls, abstention, and cost/latency.",
        "Extend beyond loopback to multi-host deployment, concurrent load, network partitions, authentication, prompt injection, payload fuzzing, and provenance tampering under a formal threat model.",
    ]:
        add_text(doc, f"{step}", size=9.1)

    add_heading(doc, "VII. VALIDITY AND LIMITATIONS")
    add_text(doc, "Construct validity: axis-aligned bounds are not a room plan, orientation bins are not semantic labels, and pseudo-label agreement is not service accuracy. E57/mesh comparisons share provenance and use sampled mesh vertices; they are not independent truth. The implemented provenance is file-level, not a spatial claim-to-region ledger, and no measurement uncertainty is propagated. 3DSES station metrics are not independent building replicates; absent Column is assigned IoU zero. Internal validity: one greedy generation per prompt, an authored suite, few latency samples, and no repeated seeds limit attribution; the keyword baseline may fit this suite. External validity: two Aalto rooms plus one 3DSES lab provide only three contexts, and all services run on one host. Reproducibility: source assets, model, code, licenses, outputs, and figures are retained in this project. Independent annotation, building diversity, hardware replication, load testing, authentication, and adversarial security testing remain absent. No accessibility conclusion is made; the clearance rule abstains.")

    add_heading(doc, "VIII. CONCLUSION")
    add_text(doc, "This paper reframes point-cloud inspection as a testbed for evidence-grounded LLM service composition. The proposed architecture binds plans to typed services, gates execution through contracts and policies, records evidence lineage, and constrains rules and explanations to supported claims. The current realization demonstrates only single-tool selection, static routing, five local service processes, file-level hashes, explicit abstention, and controlled input/fault handling. Tool selection by the small LLM trails the keyword baseline, while the guard restores the baseline by fallback. Raw scan checks are not independent accuracy validation. The case study therefore establishes a reproducible starting point and a concrete implementation/evaluation agenda; it does not claim the full architecture or validated engineering decisions have been achieved.")

    add_heading(doc, "REFERENCES")
    refs = [
        "IEEE Transactions on Services Computing, ‘Special Issue on Large Language Models in Service-Oriented Ecosystems Design: Advances and Applications,’ Call for Papers, 2026. [Online]. Available: https://www.computer.org/digital-library/journals/tsc/cfp-language-models-ecosystems-design.",
        "IEEE Computer Society, ‘How to Get Published: Author Guidelines for IEEE Computer Society Publications.’ [Online]. Available: https://www.computer.org/publications/author-resources.",
        "M. P. Papazoglou and D. Georgakopoulos, ‘Service-oriented computing,’ Communications of the ACM, vol. 46, no. 10, pp. 25–28, 2003.",
        "S. Yao et al., ‘ReAct: Synergizing reasoning and acting in language models,’ in Proc. International Conference on Learning Representations, 2023.",
        "T. Schick et al., ‘Toolformer: Language models can teach themselves to use tools,’ Advances in Neural Information Processing Systems, vol. 36, 2023.",
        "M. Ingman, J.-P. Virtanen, M. T. Vaaja, and H. Hyyppä, ‘A comparison of low-cost sensor systems in automatic cloud-based indoor 3D modeling,’ Remote Sensing, vol. 12, no. 16, Art. no. 2624, 2020, doi: 10.3390/rs12162624.",
        "M. Ingman, Indoor low-cost sensor system data, Zenodo, 2020, doi: 10.5281/zenodo.3876363. Licensed CC BY 4.0.",
        "Qwen Team, ‘Qwen2 technical report,’ arXiv:2407.10671, 2024.",
        "Qwen Team, Qwen2.5-0.5B-Instruct-GGUF model card and Q4_0 weights, Apache-2.0. [Online]. Available: https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF.",
        "A. Mérizette et al., ‘3DSES: A dataset for semantic segmentation of indoor point clouds,’ in Proc. VISAPP, 2025. [Online]. Available: https://www.scitepress.org/Papers/2025/132373/132373.pdf.",
        "A. Mérizette et al., 3DSES: An Indoor TLS Point Cloud Dataset with Semantic Annotations, Zenodo, 2024, doi: 10.5281/zenodo.13323342. Licensed CC BY-SA 4.0.",
    ]
    for i, reference in enumerate(refs, 1):
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = Inches(0.18)
        p.paragraph_format.first_line_indent = Inches(-0.18)
        p.paragraph_format.space_after = Pt(2)
        p.paragraph_format.line_spacing = Pt(9)
        set_run(p.add_run(f"[{i}] {reference}"), size=8)

    doc.core_properties.title = TITLE
    doc.core_properties.subject = "IEEE TSC special-issue case-study working manuscript"
    doc.core_properties.author = "Author details to be completed"
    doc.core_properties.keywords = "LLM orchestration, service composition, indoor 3D, point clouds, provenance"
    doc.save(OUT)
    print(OUT)


if __name__ == "__main__":
    build()
