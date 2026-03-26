from src.core.parser import parse_pdf
from src.config import PDF_DIR

res = parse_pdf(str(PDF_DIR/"不同类型胶原蛋白在皮肤衰老中的作用及其研究进展.pdf"))

print(len(res))
print("\n")
print(res[1])