"""Pydantic schemas for /api/quant/* (M1)"""
from datetime import datetime

from pydantic import BaseModel, Field


class SystemBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = ""
    status: str = "draft"  # draft / active / paused
    universe_cfg: dict = Field(default_factory=dict)
    signal_cfg: dict = Field(default_factory=dict)
    risk_cfg: dict = Field(default_factory=dict)
    exec_cfg: dict = Field(default_factory=dict)
    initial_capital: float = 1000000.0


class SystemCreate(BaseModel):
    """创建：可只传 name，其余字段使用 Stage 2 默认模板。"""
    name: str | None = None
    # 显式传入则覆盖默认
    description: str | None = None
    status: str | None = None
    universe_cfg: dict | None = None
    signal_cfg: dict | None = None
    risk_cfg: dict | None = None
    exec_cfg: dict | None = None
    initial_capital: float | None = None


class SystemUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None
    universe_cfg: dict | None = None
    signal_cfg: dict | None = None
    risk_cfg: dict | None = None
    exec_cfg: dict | None = None
    initial_capital: float | None = None


class SystemOut(SystemBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SystemSummary(BaseModel):
    """列表视图：不返回完整 JSON 配置，省带宽。"""
    id: int
    name: str
    description: str
    status: str
    initial_capital: float
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
