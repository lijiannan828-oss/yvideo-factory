# services/api/app/core/db.py

import datetime
from typing import List, Optional

# 关键修正：从 sqlalchemy 导入 Column
from sqlalchemy import create_engine, event, JSON, Column
from sqlalchemy.engine import Engine
from sqlmodel import Field, Session, SQLModel, Relationship

# 从我们即将创建的config.py文件中导入settings实例
from .config import settings

# -----------------------------------------------------------------------------
# 1. 数据库引擎配置 (Database Engine Setup)
# -----------------------------------------------------------------------------
engine = create_engine(settings.DATABASE_URL, echo=True)

# -----------------------------------------------------------------------------
# 2. 数据库会话管理 (Session Management)
# -----------------------------------------------------------------------------
def get_db_session():
    """
    FastAPI 依赖项，用于获取数据库会话。
    """
    with Session(engine) as session:
        yield session

# -----------------------------------------------------------------------------
# 4. 核心业务模型定义 (Core Business Models)
# -----------------------------------------------------------------------------
class Video(SQLModel, table=True):
    """
    代表一个完整的视频项目，这是我们所有工作的顶级实体。
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    
    title: str = Field(index=True)
    status: str = Field(default="planning", index=True)
    language: str = Field(default="zh-CN")
    budget_cents: int = Field(default=400)
    
    # 计划发布的平台列表
    # 关键修正：明确告诉SQLAlchemy在数据库中使用JSON类型来存储这个Python列表
    publish_targets: List[str] = Field(default=["youtube"], sa_column=Column(JSON))

    created_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow, nullable=False)
    updated_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow, nullable=False, sa_column_kwargs={"onupdate": datetime.datetime.utcnow})

    shots: List["Shot"] = Relationship(back_populates="video")
    task_runs: List["TaskRun"] = Relationship(back_populates="video")


class Shot(SQLModel, table=True):
    """
    代表视频中的一个镜头（或场景），是生产的基本单位。
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    video_id: int = Field(foreign_key="video.id")
    idx: int
    spec: dict = Field(sa_column=Column(JSON))
    status: str = Field(default="pending", index=True)
    provider: Optional[str] = Field(default=None)
    preset: Optional[str] = Field(default=None)
    output_uri: Optional[str] = Field(default=None)
    cost_cents: int = Field(default=0)
    retries: int = Field(default=0)
    created_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow, nullable=False)
    updated_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow, nullable=False, sa_column_kwargs={"onupdate": datetime.datetime.utcnow})

    video: "Video" = Relationship(back_populates="shots")


class TaskRun(SQLModel, table=True):
    """
    记录每一次Celery任务的执行情况，用于追踪、调试和成本归因。
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    video_id: int = Field(foreign_key="video.id")
    celery_task_id: str = Field(unique=True, index=True)
    task_name: str = Field(index=True)
    status: str = Field(default="PENDING", index=True)
    started_at: Optional[datetime.datetime] = Field(default=None)
    ended_at: Optional[datetime.datetime] = Field(default=None)
    metrics: Optional[dict] = Field(sa_column=Column(JSON), default=None)

    video: "Video" = Relationship(back_populates="task_runs")


# -----------------------------------------------------------------------------
# 5. 数据库初始化函数 (Database Initializer)
# -----------------------------------------------------------------------------
def create_db_and_tables():
    """
    创建数据库和所有定义的表。
    这个函数应该在应用首次启动时被调用。
    """
    print("正在创建数据库表...")
    SQLModel.metadata.create_all(engine)
    print("数据库表创建完成。")