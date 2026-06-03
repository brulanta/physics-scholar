# src/utils/logger.py
import logging
import sys


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)

    # 避免重复添加 handler（模块被多次 import 时）
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    # 终端输出
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    # 不向 root logger 传播，避免重复打印
    logger.propagate = False

    return logger
