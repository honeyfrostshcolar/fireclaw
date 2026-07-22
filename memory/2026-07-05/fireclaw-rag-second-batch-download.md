# FireClaw RAG Second Batch Download

**Date:** 2026-07-05
**Timestamp:** 2026-07-05T12:41:00+08:00
**Status:** Second fire-rescue RAG corpus batch downloaded and verified.

## Task Goal

Continue collecting professional source files for FireClaw's future firefighting robot RAG knowledge base. This batch focused on sources closer to fireground operation and sensing:

- NIOSH incident reports;
- FEMA / USFA firefighter safety;
- NIST FDS / Smokeview;
- thermal imaging;
- gas detection / olfaction;
- structural collapse / confined-space USAR.

## Files Added

- `data/rag/fire_rescue/raw/download_results_batch2_2026-07-05.json`
- `data/rag/fire_rescue/manifests/seed_sources_batch2_2026-07-05.jsonl`
- 17 additional downloaded PDF files under `data/rag/fire_rescue/raw/`.

## Downloaded Official / Institutional PDFs

NIST / Fire Research Division FDS-SMV 6.11.0 manuals:

- `nist_fds_6_11_0_user_guide.pdf`
- `nist_fds_6_11_0_technical_reference_guide.pdf`
- `nist_fds_6_11_0_verification_guide.pdf`
- `nist_fds_6_11_0_validation_guide.pdf`
- `nist_smv_6_11_0_user_guide.pdf`

USFA / FEMA PDFs:

- `usfa_emergency_incident_rehabilitation_fa_314.pdf`
- `usfa_firefighter_fatalities_2023.pdf`
- `usfa_firefighter_fatalities_2022.pdf`
- `usfa_firefighter_autopsy_protocol.pdf`

## Downloaded Research / Preprint PDFs

- `arxiv_2009_10679_firefighting_ar_thermal_rgb_depth.pdf`
- `arxiv_2307_04223_fire_human_detection_ir_thermal_fusion.pdf`
- `arxiv_2404_06653_flamefinder_smoke_thermal_fire_detection.pdf`
- `arxiv_2506_02167_fire360_firefighting_perception_memory.pdf`
- `arxiv_1801_06819_next_best_smell_mobile_robot_gas_detection.pdf`
- `arxiv_2602_19108_thermal_radiation_fields_mobile_robots_fire.pdf`
- `arxiv_1910_03617_thermal_image_target_detection_firefighting.pdf`
- `arxiv_2411_06615_vine_robots_usar_field_insights.pdf`

## Failed / Candidate Sources

- PHMSA ERG2024 PDF:
  - Official page: `https://www.phmsa.dot.gov/training/hazmat/erg/erg2024-pdf-accessible-english`
  - Direct PDF download returned HTTP 403 from this environment.
  - Kept as a manifest candidate with `allowed_use="candidate_pdf_403"`.
- `arxiv_2409_10000_vine_robot_confined_rubble_usar.pdf`
  - PDF URL returned HTTP 404.
  - Kept as a manifest candidate with `allowed_use="candidate_pdf_unavailable"`.

## Verification

- Read `data/rag/fire_rescue/raw/download_results_batch2_2026-07-05.json`.
- Checked all `.pdf` file sizes with `Get-ChildItem`.
- Read first bytes of each `.pdf`.
- All currently downloaded PDFs under `data/rag/fire_rescue/raw/` start with `%PDF`.

## NIOSH Note

NIOSH Fire Fighter Fatality Investigation pages were searched. The current CDC site says:

- reports from 2017 onward are listed on the new NIOSH FFFIPP page;
- reports from 1998-2016 are in CDC Stacks;
- older `cdc.gov/niosh/fire/reports/face*.html` links appear migrated or removed.

The page appears to dynamically load report listings, so this batch did not successfully download individual NIOSH PDF reports. Do not treat NIOSH as finished. A later focused task should inspect CDC Stacks or the current NIOSH report API/page scripts and download a curated set of incident reports about:

- thermal imaging not used;
- flashover / rapid fire growth;
- structural collapse;
- mayday and lost/disoriented firefighters;
- basement or below-grade fires;
- hazardous material or gas exposure.

## Current Corpus Size

After batch 2:

- 28 valid PDFs under `data/rag/fire_rescue/raw/`.
- 2 download-result JSON files.
- 2 source-manifest JSONL files.

## Current Conclusion

The project now has enough raw source material to support an initial RAG prototype across:

- response robot testing and capabilities;
- USAR operations;
- fireground building/safety knowledge;
- firefighter fatality statistics and rehab;
- fire dynamics simulation and smoke visualization;
- thermal perception and fire/human detection;
- gas detection / robot olfaction;
- confined-space USAR robotics.

## Next Recommended Step

Either:

1. Continue a third source-discovery pass focused only on NIOSH / CDC Stacks incident reports and official HazMat ERG download; or
2. Start building the first ingestion prototype that extracts text from these PDFs with page-level metadata and stores chunks in a dedicated FireClaw RAG index.
