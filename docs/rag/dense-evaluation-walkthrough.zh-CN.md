# FireClaw Dense Retrieval 评估流程说明

日期：2026-07-07

这份文档解释当前第一版 Dense Retrieval 评估到底做了什么。它不是算法论文式描述，而是按一次实际评估的顺序，把“从 chunk 选证据、写问题、跑检索、算指标”的流程展开。

## 相关文件

输入 gold case 文件：

```text
data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl
```

真实 BGE-M3 评估输出：

```text
data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json
```

评估代码：

```text
src/fireclaw_core/rag/dense_eval.py
src/fireclaw_core/rag/rag_cli.py
```

对应测试：

```text
tests/test_rag_dense_eval.py
tests/test_rag_dense_cli.py
```

## 一句话流程

当前流程是：

```text
先从已有语料 chunk 中选 10 个高质量 parent chunk
-> 每个 parent chunk 写 1 个中文任务式 query
-> 把这个 parent_id 作为标准答案 gold_parent_id
-> 用中文 query 去跑 BGE-M3 dense 检索
-> 得到 top-k small chunk 结果
-> 看 top-k 结果里有没有命中 gold_parent_id
-> 计算 Hit@1 / Hit@5 / Hit@10 / MRR@10 / gold_recall@10
```

重点是：这一版不是让你人工标注 5774 个 small chunks。它采用的是 evidence-first gold set：先选定少量确定相关的证据，再测试检索器能不能把它找回来。

## 第 1 步：先选 gold evidence

语料已经被切成两层：

```text
parent chunk: 较大的上下文片段，适合作为证据单位
small chunk: 较小的检索单位，适合向量检索排序
```

第一版评估选的是 parent chunk 作为 gold evidence，也就是标准答案单位是：

```text
gold_parent_id
```

不是用 `score > 某个阈值` 来判断相关，也不是让模型判断相关。我们只问一个简单问题：

```text
检索返回的 top-k small chunks 中，有没有某个 hit 的 parent_id 等于 gold_parent_id？
```

如果有，就算这个 query 检索到了标准证据。

## 第 2 步：根据 gold evidence 写中文 query

每条 case 大概长这样：

```json
{
  "case_id": "dense_zh_001",
  "topic": "thermal_victim_search",
  "query": "在浓烟遮挡、光照差的室内火灾里，为什么应优先用热成像而不是普通RGB相机搜人？",
  "gold_parent_ids": [
    "arxiv_1910_03617_thermal_image_target_detection_firefighting__parent_00002"
  ],
  "gold_chunk_ids": [
    "arxiv_1910_03617_thermal_image_target_detection_firefighting__parent_00002__small_001",
    "arxiv_1910_03617_thermal_image_target_detection_firefighting__parent_00002__small_002"
  ],
  "expected_evidence_summary": "...",
  "source_doc_id": "arxiv_1910_03617_thermal_image_target_detection_firefighting",
  "notes": "..."
}
```

这里的逻辑是：

```text
我已经知道这个 parent chunk 讲的是热成像在消防搜救中的作用
-> 我写一个中文消防机器人任务问题
-> 这个 parent_id 就是这道题的 gold evidence
```

这和普通考试有点像：

```text
标准答案先确定
再看检索系统能不能把标准答案找出来
```

## 第 3 步：用 dense retriever 检索

实际命令是：

```powershell
$env:PYTHONPATH = "src"
.\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --top-k 10 --output data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json
```

检索内部过程是：

```text
中文 query
-> BGE-M3 编码成 query vector
-> 和 dense index 里的 5774 个 chunk vectors 做点积
-> 按点积分数从高到低排序
-> 返回 top-k small chunks
```

当前 dense index 的向量是 normalized 的，所以点积可以近似理解成 cosine-similarity-style 的相似度排序。

注意：分数只用于排序。当前评估没有设置“分数超过多少才相关”的阈值。

## 第 4 步：每条 query 怎么判断命中

假设某条 query 的 gold 是：

```text
gold_parent_id = parent_A
```

检索返回 top 10：

```text
rank 1: chunk_x, parent_id = parent_X
rank 2: chunk_y, parent_id = parent_A
rank 3: chunk_z, parent_id = parent_Z
...
```

因为 rank 2 的 `parent_id` 等于 `parent_A`，所以：

```text
first_gold_rank = 2
Hit@1 = 0
Hit@5 = 1
Hit@10 = 1
MRR@10 = 1 / 2 = 0.5
gold_recall@10 = 1 / 1 = 1.0
```

如果 top 10 里完全没有 `parent_A`：

```text
first_gold_rank = null
Hit@1 = 0
Hit@5 = 0
Hit@10 = 0
MRR@10 = 0
gold_recall@10 = 0
```

## 第 5 步：指标怎么算

### Hit@K

`Hit@K` 看的是：

```text
前 K 个结果里，有没有至少一个 gold parent？
```

例如 gold 在 rank 2：

```text
Hit@1 = 0
Hit@5 = 1
Hit@10 = 1
```

gold 在 rank 8：

```text
Hit@1 = 0
Hit@5 = 0
Hit@10 = 1
```

gold 没进 top 10：

```text
Hit@1 = 0
Hit@5 = 0
Hit@10 = 0
```

最后所有 case 取平均。

### MRR@10

`MRR@10` 看的是第一个 gold hit 出现得有多靠前。

公式是：

```text
MRR@10 = 1 / first_gold_rank
```

但只看 top 10。如果 gold 没进 top 10，就是 0。

例子：

```text
gold rank 1 -> MRR@10 = 1.0
gold rank 2 -> MRR@10 = 0.5
gold rank 8 -> MRR@10 = 0.125
gold rank 9 -> MRR@10 = 0.111111
not found -> MRR@10 = 0
```

最后所有 case 取平均。

### gold_recall@10

`gold_recall@10` 看的是：

```text
top 10 中找回了多少个 gold parent / 这个 case 一共有多少个 gold parent
```

当前 v1 每条 case 只有 1 个 `gold_parent_id`，所以单条 case 的 `gold_recall@10` 和 `Hit@10` 数值一样：

```text
找到了 -> 1.0
没找到 -> 0.0
```

保留这个指标是为了以后支持多个正确证据，例如：

```text
gold_parent_ids = [parent_A, parent_B, parent_C]
```

如果 top 10 找回了其中 2 个：

```text
gold_recall@10 = 2 / 3 = 0.6667
```

## 这次 10 条 case 的实际结果

| case_id | topic | gold rank | Hit@10 |
|---|---|---:|---:|
| dense_zh_001 | thermal_victim_search | 2 | 1 |
| dense_zh_002 | remote_gas_source_detection | not found | 0 |
| dense_zh_003 | smoke_obscured_flame_detection | 1 | 1 |
| dense_zh_004 | usar_void_space_robot | 1 | 1 |
| dense_zh_005 | usar_void_entry_risk | 2 | 1 |
| dense_zh_006 | response_robot_test_methods | not found | 0 |
| dense_zh_007 | building_fire_service_features | 8 | 1 |
| dense_zh_008 | firefighter_rehab_thresholds | not found | 0 |
| dense_zh_009 | usar_hazmat_entry_safety | not found | 0 |
| dense_zh_010 | thermal_radiation_path_planning | 9 | 1 |

汇总指标：

```text
case_count = 10
Hit@1 = 0.2
Hit@5 = 0.4
Hit@10 = 0.6
MRR@10 = 0.323611
gold_recall@10 = 0.6
```

手算一下 `Hit@10`：

```text
命中的 case: 001, 003, 004, 005, 007, 010
命中数量: 6
总 case 数: 10
Hit@10 = 6 / 10 = 0.6
```

手算一下 `Hit@1`：

```text
rank 1 命中的 case: 003, 004
命中数量: 2
总 case 数: 10
Hit@1 = 2 / 10 = 0.2
```

手算一下 `MRR@10`：

```text
dense_zh_001: 1/2 = 0.5
dense_zh_002: 0
dense_zh_003: 1/1 = 1.0
dense_zh_004: 1/1 = 1.0
dense_zh_005: 1/2 = 0.5
dense_zh_006: 0
dense_zh_007: 1/8 = 0.125
dense_zh_008: 0
dense_zh_009: 0
dense_zh_010: 1/9 = 0.111111

MRR@10 = (0.5 + 0 + 1.0 + 1.0 + 0.5 + 0 + 0.125 + 0 + 0 + 0.111111) / 10
       = 0.323611
```

## 一个命中的例子

`dense_zh_001` 的 query 是：

```text
在浓烟遮挡、光照差的室内火灾里，为什么应优先用热成像而不是普通RGB相机搜人？
```

它的标准答案 parent 是：

```text
arxiv_1910_03617_thermal_image_target_detection_firefighting__parent_00002
```

检索结果中，这个 parent 出现在 rank 2。

所以：

```text
Hit@1 = 0
Hit@5 = 1
Hit@10 = 1
MRR@10 = 1 / 2 = 0.5
gold_recall@10 = 1.0
```

这说明 dense retriever 没把它排到第一，但已经在前 5 里找回了对应证据。

## 一个没命中的例子

`dense_zh_008` 的 query 是：

```text
消防员连续作业多久、用完几瓶空呼后，必须进入rehab做补水和医疗评估？
```

它的标准答案 parent 是：

```text
usfa_emergency_incident_rehabilitation_fa_314__parent_00102
```

但是这个 parent 没有出现在 top 10。

所以：

```text
Hit@1 = 0
Hit@5 = 0
Hit@10 = 0
MRR@10 = 0
gold_recall@10 = 0
```

这不一定说明 dense index 完全不懂这个问题，也可能是：

```text
中文 query 和英文资料术语没有对齐
rehab / SCBA / vital signs 这类关键词 dense-only 不够敏感
gold parent 选得太窄
top 10 里有语义相关但不是预设 gold parent 的材料
```

所以下一步要看 miss cases 的 top 10 内容，而不是直接下结论说模型不行。

## 为什么不用相关性分数阈值

当前不设计类似：

```text
score > 0.6 才算相关
```

原因是 dense 分数不是绝对语义概率。不同 query、不同主题、不同 chunk 长度下，分数分布会变。第一版更稳的是只看排序：

```text
gold evidence 有没有被排进前 K？
```

这就是 `Hit@K`、`MRR@10` 这类指标适合第一层 retrieval evaluation 的原因。

## 当前结果怎么理解

这次结果说明：

```text
当前 BGE-M3 dense index 能跑通；
对一部分中文消防任务 query，能找回英文语料中的 gold parent；
但 dense-only 效果不稳定，尤其对精确术语和标准条文类 query 较弱。
```

具体表现：

```text
Hit@10 = 0.6
```

意味着：

```text
10 条题里，6 条能在前 10 个检索结果中找回标准证据。
```

```text
Hit@1 = 0.2
```

意味着：

```text
10 条题里，只有 2 条把标准证据排在第一。
```

所以这不是最终 RAG 质量，只是 dense retrieval 第一层基线。

## 下一步最该看什么

下一步建议分析这 4 个 miss cases：

```text
dense_zh_002 remote_gas_source_detection
dense_zh_006 response_robot_test_methods
dense_zh_008 firefighter_rehab_thresholds
dense_zh_009 usar_hazmat_entry_safety
```

每个 miss case 应该看：

```text
1. top 10 到底返回了什么？
2. 返回结果是不是其实相关，只是没有命中预设 gold parent？
3. query 是否需要英文术语扩展？
4. gold parent 是否选得太窄？
5. dense-only 是否对这类精确术语确实弱？
```

如果很多 miss 是术语问题，那么 BM25 / hybrid 的价值就会很明显。

## 可选优化评估：Query Expansion 与 Parent Aggregation

当前 strict dense baseline 不变，默认仍然只用中文 query 和 small chunk 排名。

新增优化只在显式打开参数时生效：

```text
--query-expansions data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl
--query-variants zh,en,terms
--ranking-view parent
--small-top-k 50
```

含义是：

```text
中文原 query
+ 缓存的英文翻译 query
+ reviewed 专业术语 query
-> 分别 dense 检索
-> 用 RRF 融合
-> 按 parent_id 聚合排序
-> 仍然用原始 strict gold_parent_ids 计算 Hit@K / MRR@10 / gold_recall@10
```

注意：

```text
query_expansions_zh_v1.jsonl 和 glossary_candidates_zh_v1.jsonl 初始状态是 candidate。
candidate 表示这些翻译和术语由 LLM/agent 辅助生成，还需要人工审核。
当前 v1 已经过 agent review，并把偏答案文档名或具体系统名的词从 reviewed 术语中移除。
正式论文实验应记录 reviewer、review 时间、移除规则，以及是否使用 --require-reviewed-expansions。
```

## 可选优化评估：BM25 和 Hybrid Retrieval

BM25 是关键词检索，不使用 embedding。它更像是在问：“query 里的这些词，哪些 small chunk 里也出现了，而且这些词在整个语料里是不是比较稀有？”所以它对 `SCBA`、`NFPA 1584`、`TDLAS`、`hazmat`、`PPE` 这类精确术语通常比 dense-only 更敏感。

第一版 BM25 index 建在 small chunk 上，输入仍然是：

```text
data/rag/fire_rescue/index_inputs/small_index_records.jsonl
```

BM25 主要使用 reviewed English query 和 reviewed terms query，因为当前消防救援语料主要是英文。如果直接拿中文 query 做 BM25，词面匹配会很弱。

BM25-only 评估命令形态是：

```powershell
$env:PYTHONPATH = "src"
python -m fireclaw_core.rag.rag_cli eval-bm25-index --index-dir data/rag/fire_rescue/indexes/bm25/small_v1 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --query-expansions data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl --query-variants en,terms --ranking-view parent --small-top-k 50 --top-k 10 --require-reviewed-expansions --output data/rag/fire_rescue/eval/runs/bm25_small_v1_expanded_parent_report.json
```

Hybrid 使用 RRF 融合 dense parent list 和 BM25 parent list。这里不能把 BM25 score 和 dense score 直接相加，因为两个分数来自完全不同的计算空间：dense score 是向量相似度，BM25 score 是词频、文档长度和逆文档频率共同算出的词面匹配分数。第一版 hybrid 因此只融合“排序”，不融合原始分数。

Hybrid 评估命令形态是：

```powershell
$env:PYTHONPATH = "src"
.\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-hybrid-index --provider bge-m3 --model-path .cache/models/bge-m3 --dense-index-dir data/rag/fire_rescue/indexes/dense/bge-m3 --bm25-index-dir data/rag/fire_rescue/indexes/bm25/small_v1 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --query-expansions data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl --dense-query-variants zh,en,terms --bm25-query-variants en,terms --small-top-k 50 --top-k 10 --require-reviewed-expansions --output data/rag/fire_rescue/eval/runs/hybrid_bge_m3_bm25_v1_expanded_parent_report.json
```

这两个报告仍然使用同一套 strict `gold_parent_ids`，所以可以和 dense strict、dense parent、dense expanded-parent 结果直接做 ablation 对比。

## 可选评估：Hybrid Reranking

Reranking 是第二阶段排序步骤。它不替代 dense retrieval、BM25、query expansion、
parent aggregation 或 RRF。它先让 hybrid retrieval 产生更大的 parent 候选池，
再用 reranker model 对每个 `(query, parent_text)` pair 打分，并按 reranker score
重新排序。

第一版默认评估设置：

```text
hybrid parent candidates: top 50
rerank query variant: reviewed English query
reranker input text: parent_chunks.jsonl 中的完整 parent text
final report cutoff: top 10
```

第一版 reranker 实验应该被理解为 ranking-quality ablation。也就是说，当正确
parent 已经在候选池里时，它可能提升 `Hit@1`、`Hit@5` 和 `MRR@10`；但如果
hybrid retrieval 一开始就没有把 gold parent 召回到 rerank pool 里，reranker
本身不能把它凭空找回来。

### Multi-Query Rerank RRF

`hybrid` 阶段的 RRF 和 `rerank` 阶段的 RRF 是两层不同的融合。
第一层仍然负责召回候选：

```text
dense: zh + en + terms
bm25: en + terms
-> RRF
-> top50 parent candidates
```

第二层只在这些候选内部重新排序：

```text
rerank: zh
rerank: en
rerank: terms
-> per-query cross-encoder ranks
-> RRF
-> final top10 parent candidates
```

这避免直接相加不同 query 下的 cross-encoder 原始分数，也保留中文意图、英文语义和专业术语三种信号。

## 下一层消融怎么看

到 `next rerank ablations` 这一步，评估里多了三组很容易混在一起的开关。这里把它们拆开说清楚。

### 1. strict v2 vs agent-reviewed graded v3

`strict v2` 仍然是当前最保守、最适合做主结论的基线视角。它只认 case 文件里原始的 strict `gold_parent_ids`，所以更接近“系统有没有把那条预先指定的标准证据找回来”。

`graded v3` 则是额外加的一层评估标注文件：`data/rag/fire_rescue/eval/relevance_judgments_zh_v3_agent_reviewed.jsonl`。它允许一个 case 对多个 parent 给出 `grade`，并把 `grade >= 2` 视为相关，同时额外计算 `nDCG@10`。

但这里要非常克制地解释结果：

- `v3` 是 **agent-reviewed evaluation labels**，不是 human gold。
- 它适合回答“strict 单一 gold 会不会漏算了一些其实可用的证据”。
- 它不等于“系统真实能力就一定更强了”，所以研究主结论仍应优先看 `strict v2`，把 `graded v3` 当成补充审计视角。

### 2. parent rerank vs small-chunk rerank

`parent rerank` 是当前默认路径：

```text
hybrid small hits
-> aggregate to parent
-> rerank parent text
-> final top10 parents
```

它的优点是流程简单，reranker 直接看完整 parent 上下文；缺点是 parent 往往更长，真正的证据句可能被大段无关文本稀释。

`small-chunk rerank` 是新加的可选消融：

```text
hybrid small hits
-> rerank full small chunk text
-> aggregate reranked small hits to parent
-> final top10 parents
```

它不是只看 `text_preview`，而是显式读取 `small_chunks.jsonl` 里的完整 small chunk 文本后再 rerank。它测试的是：当证据只落在 parent 的某个局部片段里时，先在更细粒度上排序，再回聚到 parent，会不会更稳。

所以这两条路径的差别，不在于最终都输出 parent，而在于 **reranker 是先看长 parent，还是先看短证据块再回到 parent**。

### 3. rerank-only vs joint hybrid+rerank rank fusion

`rerank-only` 的意思是：先让 hybrid 负责召回一个 parent 候选池，然后最后的前十名完全按 reranker 排序决定。

```text
hybrid parent ranking
-> reranker reorders candidates
-> final top10 = rerank ranking
```

这条路通常更有机会提升 `Hit@1`、`MRR@10`，但如果 reranker 把某些本来由 hybrid 召回得不错的相关 parent 压下去，`Hit@10` / `Recall@10` 也可能掉。

`joint hybrid+rerank` 则不是把 hybrid score 和 reranker score 直接相加，而是只在“名次”层面做一次额外 RRF：

```text
hybrid parent ranking
+ rerank parent ranking
-> rank-level RRF
-> final top10
```

这里要注意两点：

- 融合的是 `rank`，不是原始分数。
- 目的不是替代 rerank，而是尽量保住 hybrid 的召回覆盖，同时吸收 reranker 的前排排序能力。

如果 `joint fusion` 在 `strict v2` 上提升了 `Hit@10` / `Recall@10`，更像是在说明“保留 hybrid 排名记忆”有帮助；如果它在 `graded v3` 上也提升 `nDCG@10`，才更能说明这种保守融合没有只是机械保 recall，而是连整体相关性排序也更好。
