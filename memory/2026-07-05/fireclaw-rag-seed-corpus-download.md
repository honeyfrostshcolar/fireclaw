# FireClaw RAG Seed Corpus Download

**Date:** 2026-07-05
**Timestamp:** 2026-07-05T04:38:00+08:00
**Status:** First public fire-rescue / response-robot RAG seed corpus downloaded.

## Task Goal

Collect an initial set of professional, open, source-traceable documents for a future FireClaw RAG knowledge base. The user specifically wanted the project to first download professional firefighting robot / rescue knowledge files before designing cleaning, chunking, or indexing.

## Files Added

- `data/rag/fire_rescue/raw/`
  - downloaded source PDFs and `download_results.json`
- `data/rag/fire_rescue/manifests/seed_sources_2026-07-05.jsonl`
  - JSONL source manifest with title, publisher, URL, domain, authority level, retrieval time, and local RAG usage note.

## Downloaded PDFs

Official / institutional sources:

- `nist_response_robot_test_methods_guide.pdf`
  - NIST response robot standard test methods guide.
- `nist_usar_robot_performance_requirements.pdf`
  - NIST preliminary performance requirements for urban search and rescue robots.
- `osha_fire_service_features_buildings_fire_protection_systems_2015.pdf`
  - OSHA fire service features of buildings and fire protection systems.
- `insarag_guidelines_2020_volume_i_policy.pdf`
- `insarag_guidelines_2020_volume_ii_manual_a_capacity_building.pdf`
- `insarag_guidelines_2020_volume_ii_manual_b_operations.pdf`
- `insarag_guidelines_2020_volume_ii_manual_c_classification.pdf`
- `insarag_guidelines_2020_volume_iii_operational_field_guide.pdf`

Research/preprint sources:

- `arxiv_2603_19063_fire_as_a_service_robot_simulators_fire_dynamics.pdf`
- `arxiv_2606_23246_robotic_intervention_industrial_emergency.pdf`
- `arxiv_2311_08732_emergency_decision_kg_llm.pdf`

## Failed / Candidate Source

- `https://arxiv.org/abs/2509.00054`
  - Candidate title: `Robotic Fire Risk Detection based on Dynamic Knowledge Graph Reasoning: An LLM-Driven Approach with Graph Chain-of-Thought`
  - The abstract page was kept in the manifest as a candidate, but the PDF URL returned HTTP 404 during download. No bogus PDF was saved.

## Verification

- Checked `data/rag/fire_rescue/raw/download_results.json`.
- Checked file sizes with `Get-ChildItem`.
- Checked PDF headers by reading the first bytes of each `.pdf`.
- 11 downloaded PDF files start with `%PDF`.

## Current Conclusion

This is a good first seed corpus for FireClaw RAG, especially for:

- response robot capabilities and test methods;
- urban search and rescue operations;
- building/fire-protection knowledge relevant to firefighter and robot safety;
- fire robotics simulation;
- emergency decision-making knowledge graph / LLM references;
- field robotic intervention case study in an industrial fire emergency.

## Next Recommended Step

Before cleaning/chunking, continue source discovery for a second batch:

- NIOSH firefighter fatality investigation reports;
- FEMA / USFA firefighter safety and US&R operational PDFs;
- NIST FDS / Smokeview technical manuals;
- open-access fire dynamics, thermal imaging, gas detection, and structural collapse documents;
- public robot/firefighting robot datasets or benchmark documentation.

Then build a simple ingestion script that:

- validates manifest entries;
- extracts PDF text;
- preserves source URL, page number, title, publisher, and authority level on every chunk;
- indexes into the existing FireClaw memory / retrieval modules or a dedicated RAG index.
