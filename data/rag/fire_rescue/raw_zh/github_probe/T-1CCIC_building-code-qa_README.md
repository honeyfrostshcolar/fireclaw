# 🏗️ 建筑规范智能问答助手
[![Streamlit App](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://building-code-app-6d3svdpanjwqhsrzpgk95u.streamlit.app/)

一个基于 **RAG（检索增强生成）** 的建筑规范智能问答系统。用户可以用自然语言提问，系统从多本建筑设计规范（如《住宅设计规范》《建筑设计防火规范》）中检索相关条款，并由大模型生成准确、可溯源的答案。项目结合了建筑行业背景与前沿 AI 技术，旨在帮助设计师、工程师快速查阅规范。

## ✨ 核心特性

- 📚 **多文档支持**：内置多本建筑设计规范 PDF，自动 OCR 识别并构建向量知识库。
- 🔍 **混合检索策略**：结合 MMR、查询扩展、重排序等技术，提升召回准确率。
- 📅 **版本优先**：自动从文件名提取年份，优先引用最新版规范（例如 2025 版优先于 2011 版）。
- 🧠 **多源采样**：强制从不同文档中选取最相关段落，答案更全面。
- 💬 **多轮对话**：支持上下文记忆，可连续追问（如“那阳台栏杆高度呢？”）。
- 📎 **答案溯源**：每条回答均附带来源文件及页码，方便验证。
- 🚀 **一键部署**：可快速部署至 Streamlit Cloud，在线体验。

## 🛠️ 技术栈

- **前端/界面**：Streamlit
- **后端框架**：FastAPI（本项目中主要用于单机，但核心逻辑可迁移）
- **向量数据库**：ChromaDB
- **嵌入模型**：`BAAI/bge-small-zh-v1.5`（中文优化）
- **重排序模型**：`BAAI/bge-reranker-base`
- **大语言模型**：智谱AI `glm-4`
- **OCR引擎**：RapidOCR + PyMuPDF
- **依赖管理**：Python + requirements.txt

## 🚀 快速开始

### 环境要求
- Python 3.9+
- 智谱AI API Key（[注册获取](https://open.bigmodel.cn/)）

### 本地安装

1. **克隆仓库**
   ```bash
   git clone https://github.com/你的用户名/building-code-qa.git
   cd building-code-qa

2. **安装依赖**
   ```bash
   pip install -r requirements.txt

3. **配置 API Key**
- 在项目根目录创建 .env 文件（不要提交到 Git）：
   ```text
   ZHIPU_API_KEY=你的密钥
- 或者直接在终端设置环境变量。

4. **运行应用**
   ```bash
   streamlit run app.py
   浏览器会自动打开 http://localhost:8501。

### 云端部署（Streamlit Cloud）
1. 将代码推送到 GitHub 仓库（确保 .env 已加入 .gitignore）。
2. 登录 Streamlit Cloud，点击 New app，选择你的仓库。
3. 在 Advanced settings 中添加 Secrets：
   ```text
   ZHIPU_API_KEY = "你的密钥"
4. 点击 Deploy，等待几分钟即可获得公网链接。

## 📖 使用示例
你可以输入以下类型的问题：

| 问题 | 预期回答来源 |
|------|-------------|
| 厨房最小面积是多少？ | 住宅设计规范 |
| 阳台栏杆高度要求？ | 住宅设计规范 |
| 高层建筑与多层建筑防火间距？ | 建筑设计防火规范 |
| 楼梯踏步宽度和高度？ | 住宅设计规范 / 民用建筑设计统一标准 |

系统会返回类似：
   ```text
   📝 问题：厨房最小面积是多少？
   💬 回答：根据《住宅设计规范》GB 50096-2011，由卧室、起居室、厨房和卫生间等组成的住宅套型，其厨房使用面积不应小于4.0平方米；由兼起居的卧室、厨房和卫生间等组成的住宅套型，其厨房使用面积不应小于3.5平方米。
   📎 来源：住宅设计规范GB50096-2011.pdf 第 8 页
   ```

## 🗂️ 项目结构
   ```text
   building-code-qa/
   ├── app.py # Streamlit 应用入口
   ├── qa_engine.py # 核心问答逻辑
   ├── requirements.txt # Python 依赖
   ├── .gitignore # Git 忽略文件
   ├── chroma_db_zh/ # 预先构建的向量数据库
   ├── README.md # 本文件
   └── LICENSE # MIT 许可证
   ```

## 🤝 贡献
本项目为个人学习作品，欢迎提出建议或 Issue。如需贡献代码，请先开 Issue 讨论。

## 📄 许可证
本项目采用 MIT 许可证。详见 [LICENSE](https://license/) 文件。

## 🙏 致谢
感谢 [智谱AI](https://www.zhipuai.cn/) 提供的大模型 API。
感谢 [LangChain](https://www.langchain.com/) 和 [Chroma](https://www.trychroma.com/) 提供的优秀开源工具。
特别感谢 建筑规范 公开的标准文档。

## 📫 联系
作者：T-1CCIC
GitHub：@T-1CCIC
项目链接：https://github.com/T-1CCIC/building-code-qa