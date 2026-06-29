# src/utils/logger.py
import logging
import sys


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)

    # 避免重复添加 handler（模块被多次 import 时）
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    # 终端输出走 stderr：stdio MCP server 把 stdout 当作 JSON-RPC 专用通道，
    # 任何写入 stdout 的日志都会污染协议帧（触发 JSONRPCMessage 校验失败、会话崩溃）。
    # 日志输出到 stderr 是惯例（uvicorn 亦然），MCP stdio client 会把子进程 stderr
    # 转发到父进程 stderr，开发态照常可见。
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    # 不向 root logger 传播，避免重复打印
    logger.propagate = False

    return logger
