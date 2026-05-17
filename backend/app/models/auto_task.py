from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base
from backend.app.core.time import shanghai_now


class AutoTask(Base):
    __tablename__ = "auto_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    keywords: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    pc_account_id: Mapped[int] = mapped_column(ForeignKey("platform_accounts.id"))
    creator_account_id: Mapped[int] = mapped_column(ForeignKey("platform_accounts.id"))
    ai_instruction: Mapped[str] = mapped_column(Text, default="")
    
    # Auto Comment/Reply Config
    enable_auto_comment: Mapped[bool] = mapped_column(Integer, default=False)  # SQLite boolean compatibility
    comment_templates: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    comment_instruction: Mapped[str] = mapped_column(Text, default="请根据笔记内容，结合选中的评论模版进行改写。要求符合真实用户口吻，字数控制在20字左右。")
    
    enable_auto_reply: Mapped[bool] = mapped_column(Integer, default=False)
    reply_templates: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    reply_instruction: Mapped[str] = mapped_column(Text, default="请针对这篇笔记中的这条评论，结合选中的模版进行回复，制造话题感。")

    schedule_type: Mapped[str] = mapped_column(String(32), default="manual")
    schedule_time: Mapped[str] = mapped_column(String(32), default="09:00")
    schedule_days: Mapped[str] = mapped_column(String(64), default="")
    schedule_interval_hours: Mapped[int] = mapped_column(Integer, default=24)
    status: Mapped[str] = mapped_column(String(32), default="active")
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    total_published: Mapped[int] = mapped_column(Integer, default=0)
    total_comments: Mapped[int] = mapped_column(Integer, default=0)
    total_replies: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=shanghai_now)
