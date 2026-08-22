"""轻量工具箱：registry + 审批 + read_file/edit_file/fetch。无 bash、无 write_file。"""

from pen.agent.permissions import READ_FIRST_MSG, decide, read_first_block
from pen.agent.registry import TOOLS, dispatch, schemas

__all__ = ["READ_FIRST_MSG", "TOOLS", "decide", "dispatch", "read_first_block", "schemas"]

