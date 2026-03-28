import fitz
import re
from src.config import PDF_DIR

MATH_CHARS = set("αβγδεζηθικλμνξπρστυφχψωΑΒΓΔΕΖΗΘΙΚΛΜΝΞΠΡΣΤΥΦΧΨΩ∑∫∂∇×·⊗≈≤≥≠∞")


def remove_margin(raw_blocks: list[tuple], h: float) -> list[tuple]:
    threshold = 0.08
    top = h * threshold
    bottom = h * (1 - threshold)
    return [b for b in raw_blocks if b[6] == 0 and b[3] > top and b[1] < bottom]


def intermediate_shaft(block: tuple) -> tuple:
    return ((block[0] + block[2]) / 2, (block[1] + block[3]) / 2)


def insert_blocks(b1, b2):
    i = j = 0
    res = []
    while i < len(b1) and j < len(b2):
        if b1[i][1] <= b2[j][1]:
            res.append(b1[i])
            i += 1
        else:
            res.append(b2[j])
            j += 1
    res.extend(b1[i:])
    res.extend(b2[j:])
    return res


def sort_blocks(raw_blocks: list[tuple], w: float) -> list[tuple]:
    center_left = w * 0.45
    center_right = w * 0.55
    left_blocks = []
    right_blocks = []
    cross_lines = []

    for b in raw_blocks:
        x_center, _ = intermediate_shaft(b)
        if center_left < x_center < center_right:
            cross_lines.append(b)
        elif x_center < center_left:
            left_blocks.append(b)
        else:
            right_blocks.append(b)

    cross_lines = sorted(cross_lines, key=lambda x: x[1])
    left_blocks = sorted(left_blocks, key=lambda x: x[1])
    right_blocks = sorted(right_blocks, key=lambda x: x[1])

    return insert_blocks(cross_lines, left_blocks) + right_blocks


def is_noise_block(text: str) -> bool:
    stripped = text.strip()
    # 第一层：长度过滤
    if len(stripped) < 8:
        return True
    # 第二层：数学符号占比
    math_ratio = sum(1 for c in stripped if c in MATH_CHARS) / len(stripped)
    if math_ratio > 0.1:
        return True
    return False


def split_reference(full_text: str) -> tuple[str, str]:
    # 参考文献：四个字之间允许空格或回车
    # References：字母之间允许空格或回车，大小写不限
    pattern = r"(?i)\n\s*r\s*e\s*f\s*e\s*r\s*e\s*n\s*c\s*e\s*s?\s*\n|参\s*考\s*文\s*献"

    # finditer拿到所有匹配，取最后一个
    matches = list(re.finditer(pattern, full_text))

    if matches:
        last_match = matches[-1]  # 取最后一次命中
        body = full_text[: last_match.start()].strip()
        reference = full_text[last_match.start() :].strip()
        return body, reference

    return full_text.strip(), ""


def parse_pdf(pdf_path: str) -> dict:
    doc = fitz.open(pdf_path)
    all_text_blocks = []

    for page in doc:
        w = page.rect.width
        h = page.rect.height
        raw_blocks = page.get_text("blocks")
        cleaned = remove_margin(raw_blocks, h)
        sorted_blocks = sort_blocks(cleaned, w)
        all_text_blocks.extend(sorted_blocks)

    # 先join
    full_text = "\n".join(b[4] for b in all_text_blocks)
    # 先切引用
    body_raw, reference_raw = split_reference(full_text)
    # 再分别过滤噪声
    body = "\n".join(line for line in body_raw.split("\n") if not is_noise_block(line))
    reference = reference_raw  # 引用部分可以不过滤，保留原始条目

    return {"body": body, "reference": reference}


res = parse_pdf(
    str(
        PDF_DIR
        / "Zhong Dongzhou 等 - 2022 - 基于光学储备池计算的高速混沌保密通信的研究.pdf"
    )
)
print(res["reference"][0:100])
