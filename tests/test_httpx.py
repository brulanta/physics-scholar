import logging
import httpx
from src.llm import main_llm

# 开启 httpx 的底层调试日志
logging.basicConfig(level=logging.DEBUG)
httpx_logger = logging.getLogger("httpx")
httpx_logger.setLevel(logging.DEBUG)

# 随便发起一次对话，观察控制台输出的 Request Payload
response = main_llm.invoke("你好，请测试。")
print(response.content)
