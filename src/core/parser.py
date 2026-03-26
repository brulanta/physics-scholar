import fitz  # pip install PyMuPDF

def parse_pdf(path:str) -> list[dict]:
    """
    解析PDF，返回按页的文本块列表
    每个元素：{"page": index, "blocks": list[block_str], "full_text": str}
    """
    doc = fitz.open(path)
    pages = []
    for i,page in enumerate(doc):
        blocks = page.get_text("blocks")
        text_blocks = [
           b[4].strip() for b in blocks if b[6] == 0 and b[4].strip()
        ]
        if text_blocks:
            pages.append({
                "page":i+1,
                "blocks":text_blocks,
                "full_text":"\n".join(text_blocks)
            })
    doc.close()
    return pages