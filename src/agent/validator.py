import re
from typing import Tuple


class QueryValidator:
    FORBIDDEN_KEYWORDS = [
        "drop", "delete", "truncate", "alter", "update", "insert",
        "__import__", "eval", "exec", "os.system", "subprocess"
    ]

    def validate_sql(self, sql: str) -> Tuple[bool, str]:
        normalized = sql.lower()
        for kw in self.FORBIDDEN_KEYWORDS:
            pattern = rf"\b{re.escape(kw)}\b"
            if re.search(pattern, normalized):
                return False, f"安全拦截: 禁止执行危险关键词 '{kw}'"
        if not normalized.strip().startswith("select"):
            return False, "安全拦截: 仅允许执行只读 SELECT 空间分析语句"
        return True, "校验通过"
