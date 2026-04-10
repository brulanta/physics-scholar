from src.rag.graph import chat


def ask(
    question: str,
    conv_id: str,
    translation: bool = False,
    user_id: str = "default",
    mode: str = "normal",
) -> dict:
    respone = chat(
        user_message=question,
        conv_id=conv_id,
        user_id=user_id,
        translation=translation,
        mode=mode,
    )
    # 保持和之前一样的返回结构
    return respone
