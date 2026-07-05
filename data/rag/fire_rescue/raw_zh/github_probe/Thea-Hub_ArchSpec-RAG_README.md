# ArchSpec-RAG
这是我的第一个项目：建筑规范智能问答 RAG 系统，支持规范检索、问答与合规审查。<br>
This is my first project: ArchCode-RAG, A RAG system for Chinese architectural codes, supporting regulation retrieval, Q&amp;A and compliance inspection.
## 核心功能
✅ 建筑规范 PDF 自动解析与结构化存储<br>
✅ 基于语义的规范条文检索与问答<br>
✅ 支持建筑设计场景的合规性审查辅助<br>
✅ 可扩展的 MCP / 微调接口，适配不同规范库<br>
架构图示意：
<img width="1613" height="579" alt="Snipaste_2026-05-26_10-20-37" src="https://github.com/user-attachments/assets/e0c35f21-f545-4881-a7e6-416562dc855e" />
### 系统分层架构
| 层级 | 职责 | 对应模块 |
| :--- | :--- | :--- |
| 前端交互层 | 用户请求入口与结果展示 | 网页端（问答/多人会话/图纸上传） |
| 核心控制层 | 请求路由、会话管理、业务编排 | LangGraph Agent |
| 业务执行层 | 业务逻辑流水线 | 问答业务流 / 多人会话 / 图纸审查 |
| 工具与数据层 | 数据与能力支撑 | RAG知识库 / CAD解析工具 / 数据库 |
### rag demo演示
在线演示：https://archspec-rag.streamlit.app/<br>
离线演示：https://github.com/user-attachments/assets/4ac9de67-d726-4c7f-a777-db179362432c<br>
## 技术栈
语言：Python<br>
核心库：llama index, Chroma, FastAPI,langgragh<br>
数据处理：PyPDF2, Sentence-Transformers<br>
其他：Git, GitHub<br>
### 项目亮点
• 垂直领域数据治理：针对建筑防火规范表格复杂、条文严谨、数值敏感特点，完成表格结构化、多级表头还原、文本无损清洗，构建高质量专业知识库。<br>
• 检索全链路优化：混合检索 + Rerank + 阈值过滤 + 类型过滤，解决“答非所问、弱相关干扰、数值错误”等行业难题。<br>
• 强约束生成机制：严格遵循规范原文，不推导、不脑补、不混淆建筑类型，答案附带条文溯源，满足审查合规要求。<br>
• 效率与稳定性提升：量化模型 + 精简生成 + 单次 LLM 调用，查询速度从百秒级优化至 < 20 秒，回答准确率显著提升。<br>
• 架构对比验证：完成极简 RAG 与 LangGraph 智能体对比，形成垂直规范领域最优技术选型方案。<br>
### 快速上手说明
克隆仓库<br>
git clone https://github.com/Thea-Hub/ArchSpec-RAG.git<br>
安装依赖<br>
pip install -r requirements.txt<br>
运行项目<br>
python main.py<br>
