from langchain_text_splitters import RecursiveCharacterTextSplitter


def chunker(blocks_str: str):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=350,
        chunk_overlap=55,
        separators=["。", ".", "！", "!？", "?", "，", ",", "\n", " "],
    )
    chunks = splitter.split_text(blocks_str)
    return chunks
