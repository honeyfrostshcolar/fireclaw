# FireClaw Chinese RAG Source Probe

**Date:** 2026-07-05
**Status:** Chinese source probing attempted; no Chinese PDFs were successfully downloaded in this pass.

## Task Goal

The user noticed the existing RAG seed corpus is almost entirely English and asked for Chinese firefighting / rescue materials because FireClaw is intended mainly for Chinese firefighting contexts. The user later said they can download manually if needed, but wanted Codex to first try downloading what it can.

## Commands / Actions

- Tried web search for Chinese public fire-rescue manuals and standards:
  - `消防救援队伍作战训练安全行动手册 PDF`
  - `消防安全知识手册 PDF`
  - `建筑防火通用规范 PDF`
  - `消防设施通用规范 PDF`
  - `危险化学品事故应急救援指南 PDF`
  - `消防机器人 通用技术条件 PDF`
- Tried Bing and Baidu result pages through PowerShell. Both returned pages that were not easily parseable for direct links.
- Tried official/standards entry pages:
  - `https://openstd.samr.gov.cn/`
  - `https://std.samr.gov.cn/`
  - `https://www.mem.gov.cn/`
  - `https://www.119.gov.cn/`
  - selected MOHURD announcement URLs for GB 55036 / GB 55037.

## Files Created

Probe files were saved under:

- `data/rag/fire_rescue/raw_zh/`

Files:

- `download_results_zh_probe_2026-07-05.json`
- `china_gb_55037_2022_building_fire_general_code_candidate.html`
- `china_gb_55036_2022_fire_facilities_general_code_candidate.html`
- `china_gb_50016_building_design_fire_code_candidate.html`
- `china_mem_home_search_fire_safety.html`

These are not final RAG corpus PDFs. They are only probe artifacts / official entry-page snapshots.

## Findings

- The national standards site page contains buttons for `在线预览` and `下载标准`.
- Its download URL shape is:
  - `/bzgk/std/showGb?type=download&hcno=<real_hcno>&request_locale=zh_CN`
- The important blocker is that `hcno` is not the visible standard number. It is a hidden unique ID returned by the standards search result. Manually searching the standard in a browser and clicking `下载标准` is more reliable.
- Directly guessing `hcno=GB 55037-2022` or similar only returns a generic/no-standard template page.
- `www.119.gov.cn` returned HTTP 405 to this automated request.
- MOHURD announcement URLs attempted from memory failed with connection-send errors; user browser may work better.

## Recommended Manual Download Targets

Use these official portals:

1. 国家标准全文公开系统:
   - `https://openstd.samr.gov.cn/`
   - Search and download:
     - `GB 55037-2022 建筑防火通用规范`
     - `GB 55036-2022 消防设施通用规范`
     - `GB 50016 建筑设计防火规范`
     - `GB 50974 消防给水及消火栓系统技术规范`
     - `GB 51251 建筑防烟排烟系统技术标准`
     - `GB 51309 消防应急照明和疏散指示系统技术标准`
     - `消防机器人`
     - `消防装备`
     - `危险化学品`
2. 全国标准信息公共服务平台:
   - `https://std.samr.gov.cn/`
   - Search:
     - `XF 消防机器人`
     - `XF 灭火救援`
     - `XF 消防员防护装备`
     - `YJ 危险化学品事故`
     - `YJ 应急救援`
3. 国家消防救援局:
   - `https://www.119.gov.cn/`
   - Search:
     - `作战训练安全`
     - `灭火救援`
     - `典型战例`
     - `消防机器人`
     - `高层建筑火灾`
     - `地下建筑火灾`
     - `危化品事故处置`
     - `有限空间救援`
4. 应急管理部:
   - `https://www.mem.gov.cn/`
   - Search:
     - `危险化学品事故应急救援`
     - `生产安全事故应急预案`
     - `有限空间作业`
     - `地震灾害救援`
5. 住房和城乡建设部:
   - `https://www.mohurd.gov.cn/`
   - Search:
     - `建筑防火通用规范`
     - `消防设施通用规范`
     - `建筑设计防火规范`
     - `防烟排烟`
     - `消防给水`

## Suggested Local Placement

After manual download, put Chinese PDFs under:

- `data/rag/fire_rescue/raw_zh/`

Suggested filename examples:

- `china_gb_55037_2022_building_fire_general_code.pdf`
- `china_gb_55036_2022_fire_facilities_general_code.pdf`
- `china_gb_50016_building_design_fire_code.pdf`
- `china_fire_rescue_training_safety_manual.pdf`
- `china_hazmat_emergency_response_guide.pdf`
- `china_fire_robot_standard_xxx.pdf`

## Current Conclusion

Chinese sources are essential for this project, but automatic download is much less reliable than for English/NIST/FEMA/arXiv sources. The next productive step is for the user to manually download standards/manual PDFs from official Chinese portals, then Codex can handle manifest creation, validation, text extraction, chunking, and RAG indexing.
