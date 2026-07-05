from langchain_community.document_loaders import PyPDFLoader

# 加载 PDF
loader = PyPDFLoader("data/gb55037.pdf")
pages = loader.load()

# 看一下加载结果
print(f"总页数: {len(pages)}")
print(f"\n第 1 页前 300 字预览:")
print(pages[0].page_content[:300])
print(f"\n第 1 页 metadata: {pages[0].metadata}")