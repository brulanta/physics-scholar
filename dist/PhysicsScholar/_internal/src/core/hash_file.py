# hash_file.py
def get_pdf_hash(source) -> str:
    import hashlib

    if isinstance(source, bytes):
        return hashlib.md5(source).hexdigest()
    with open(source, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()
