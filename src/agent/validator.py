import re
from typing import Tuple
from pydantic import BaseModel, ConfigDict, Field, model_validator


class AnalysisParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    distance_meters: float = Field(default=500.0, gt=0, le=50000)
    min_capacity: int = Field(default=0, ge=0, le=10000000)


class AnalysisPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)

    @model_validator(mode="after")
    def validate_demo_area(self):
        if not (113.5 <= self.lon <= 115.5 and 29.5 <= self.lat <= 31.5):
            raise ValueError("演示区域限于东经113.5–115.5、北纬29.5–31.5")
        return self


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
