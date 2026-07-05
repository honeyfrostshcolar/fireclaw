# FireClaw Chinese Firefighter Manual Source Follow-up

**Date:** 2026-07-05
**Status:** Confirmed that Chinese firefighter learning manuals / textbooks exist, but most appear as copyrighted paper books or bookstore listings rather than freely downloadable official PDFs.

## Task Goal

The user asked whether China has already-written firefighter learning manuals or textbooks suitable for firefighter study, because open web searches for "消防员学习手册" were frustrating and sparse.

## Current Progress

- Previous Chinese RAG source probing did not produce legitimate downloadable Chinese PDF manuals.
- This follow-up checked Chinese bookstore-style sources and found several close matches to the user's expected "firefighter learning manual" category.

## Findings

The issue appears to be naming and distribution:

- Public web search for `消防员学习手册` is weak.
- Better search terms are:
  - `消防救援人员业务训练系列教材`
  - `消防员入职技能训练`
  - `消防员安全基础训练手册`
  - `消防救援人员体能训练`
  - `消防士兵职业技能鉴定培训教材 灭火救援专业`
  - `消防员 职业技能鉴定 培训教材`
  - `作战训练安全 行动要则 消防救援`
- Many useful sources are paper books / commercial textbook listings, not legally open PDFs.
- For FireClaw RAG, these should be treated as acquisition candidates rather than scraped web PDFs unless the user obtains the book / ebook legally and confirms permitted local research use.

## Candidate Chinese Textbooks Found

1. `消防员安全基础训练手册`
   - Author/editor shown by Dangdang: `消防救援人员业务训练系列教材编委会`
   - Publisher: `上海科学技术出版社`
   - Publication date shown: `2019年07月`
   - ISBN shown: `9787547844946`
   - Link checked: `https://product.dangdang.com/11971428983.html`
   - Notes: very close to the user's requested "消防员学习手册" concept. Good RAG candidate if legally acquired.

2. `消防员入职技能训练`
   - Author/editor shown by Dangdang: `消防救援人员业务训练系列教材编委会`
   - Publisher: `上海科学技术出版社`
   - Publication date shown: `2019年07月`
   - Link checked: `https://product.dangdang.com/12357095069.html`
   - Notes: likely useful for baseline firefighter operational skill knowledge. The listing also showed ISBN `9787547844946`; verify against the physical book or publisher record before manifesting.

3. `消防救援人员体能训练`
   - Author/editor shown by Dangdang: `消防救援人员业务训练系列教材编委会`
   - Publisher: `上海科学技术出版社`
   - Publication date shown: `2018年01月`
   - ISBN shown: `9787547844694`
   - Link checked: `https://product.dangdang.com/12536268121.html`
   - Notes: less central to robot RAG, but useful for understanding firefighter training context and human-robot collaboration assumptions.

4. `消防士兵职业技能鉴定培训教材 灭火救援专业 消防员基础...`
   - Listing title was truncated, but it clearly points to a pre-reform / firefighter skill-identification training textbook.
   - Publication date shown: `2016年01月`
   - ISBN-like value shown: `9787305158063_5897`
   - Link checked: `https://product.dangdang.com/11851192422.html`
   - Notes: potentially useful but should be verified carefully because bookstore metadata says author/publisher `其他`; could be an older or reseller listing.

## Recommended Acquisition Strategy

Use a two-track Chinese corpus:

1. Legally acquired paper / ebook textbooks:
   - Buy or borrow the `消防救援人员业务训练系列教材` books.
   - OCR/scan only if legally allowed for local research use.
   - Put resulting PDFs under `data/rag/fire_rescue/raw_zh/` with clear provenance metadata.

2. Public official materials:
   - Standards from `https://openstd.samr.gov.cn/`
   - National Fire and Rescue Administration / MEM / MOHURD notices and training-safety documents from official sites.
   - These are better for auditable RAG citations and safety gating.

## Current Conclusion

The user's instinct is correct: Chinese firefighter learning materials do exist. They are just usually not named "消防员学习手册" on the public web and are rarely available as free, legitimate PDFs. For FireClaw, the most promising seed is the `消防救援人员业务训练系列教材` family plus official GB/XF/YJ standards and fire-rescue safety documents.

## Next Recommended Step

Ask the user whether they want to:

1. buy / borrow the candidate books and place scans or ebooks under `data/rag/fire_rescue/raw_zh/`; or
2. continue searching official free documents first; or
3. start a Chinese RAG source manifest with these as `candidate_acquisition` entries.
