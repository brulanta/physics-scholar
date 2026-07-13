CONTEXT_BLOCK = """
## Conversation History

{history}
"""

from ...builder import PromptModule

module = PromptModule(name="CONTEXT_BLOCK", content=CONTEXT_BLOCK, order=20)
