import fitz
from pydantic import BaseModel, Field
from typing import Literal
from src.config import PDF_DIR


class ParsedBlock(BaseModel):
    doc_id: str
    page_number: int
    block_index: int  # 合并后在页内的序号
    text: str
    font_size: float  # 主字号，合并依据
    bbox: tuple  # 位置，页眉页脚过滤用
    section: Literal["body", "abstract", "reference", "title", "caption"] = Field(
        default="unknown"
    )  # body | abstract | reference | title | caption
    is_figure: bool  # type=1的block标记，直接跳过不入库


def remove_margin(raw_blocks: list[tuple], h):
    threshold = 0.08
    top = h * threshold
    button = h * (1 - threshold)
    return [b for b in raw_blocks if b[6] == 0 and b[3] > top and b[1] < button]


def intermediate_shaft(blocks: tuple):
    return ((blocks[0] + blocks[2]) / 2, (blocks[1] + blocks[3]) / 2)


def merge_blocks(raw_blocks: list[tuple]):
    threshold = 30
    pointer = raw_blocks[0]
    results = []
    results.append(pointer)
    for index in range(1, len(raw_blocks)):
        if (
            len(pointer[4]) < threshold
            and len(raw_blocks[index][4]) < threshold
            and (pointer[3] - raw_blocks[index][1]) < 1.5 * (pointer[3] - pointer[1])
        ):
            last_block = results[-1]
            results[-1] = (
                last_block[0],
                last_block[1],
                max(raw_blocks[index][2], last_block[2]),
                raw_blocks[index][3],
                last_block[4] + raw_blocks[index][4],
                last_block[5],
                0,
            )
        else:
            results.append(raw_blocks[index])
        pointer = raw_blocks[index]
    return results


def insert_blocks(b1, b2):
    i = 0
    j = 0
    res = []

    while i < len(b1) and j < len(b2):
        if b1[i][1] <= b2[j][1]:  # 比较 y0
            res.append(b1[i])
            i += 1
        else:
            res.append(b2[j])
            j += 1

    # 剩余直接接上
    res.extend(b1[i:])
    res.extend(b2[j:])

    return res


def classification_blocks(raw_blocks: list[tuple], w):
    center_left = w * 0.45
    center_right = w * 0.55
    left_blocks = []
    right_blocks = []
    cross_lines = []
    for b in raw_blocks:
        x_center, y_center = intermediate_shaft(b)
        if x_center > center_left and x_center < center_right:
            cross_lines.append(b)
        elif x_center < center_left:
            left_blocks.append(b)
        else:
            right_blocks.append(b)
    cross_lines = sorted(left_blocks, key=lambda x: x[1])
    left_blocks = merge_blocks(sorted(left_blocks, key=lambda x: x[1]))
    right_blocks = merge_blocks(sorted(right_blocks, key=lambda x: x[1]))
    return right_blocks


def parse_pdf(pdf_path) -> list[ParsedBlock]:
    doc = fitz.open(pdf_path)
    page = doc[1]
    w = page.rect.width
    h = page.rect.height
    blocks = page.get_text("blocks")

    print(classification_blocks(remove_margin(blocks, h), w))


parse_pdf(str(PDF_DIR / "不同类型胶原蛋白在皮肤衰老中的作用及其研究进展.pdf"))
# print(len("UV)辐射和不良生活方式。UV 辐射在皮肤老化\n"))22,26,24,30?
