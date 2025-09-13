# services/api/app/core/db.py

import datetime
from typing import List, Optional

from sqlalchemy import create_engine, event, JSON
from sqlalchemy.engine import Engine
from sqlmodel import Field, Session, SQLModel, Relationship


# 从我们即将创建的config.py文件中导入settings实例
from .config import settings

# -----------------------------------------------------------------------------
# 1. 数据库引擎配置 (Database Engine Setup)
# -----------------------------------------------------------------------------
# 使用从settings中读取的DATABASE_URL创建SQLAlchemy引擎。
# echo=True会在控制台打印出所有执行的SQL语句，非常适合在开发环境中进行调试。
# 在生产环境中，我们会将其设置为False。
engine = create_engine(settings.DATABASE_URL, echo=True)


# -----------------------------------------------------------------------------
# 2. 数据库会话管理 (Session Management)
# -----------------------------------------------------------------------------
# 这个函数是一个依赖项（Dependency），FastAPI的路由函数可以通过它来获取一个数据库会话。
# 使用'yield'可以在请求处理结束后自动关闭会话，确保资源被正确释放。
def get_db_session():
    """
    FastAPI 依赖项，用于获取数据库会话。
    """
    with Session(engine) as session:
        yield session

# -----------------------------------------------------------------------------
# 3. 基础模型定义 (Base Models)
# -----------------------------------------------------------------------------
# 这里我们没有创建显式的基类，因为SQLModel的默认行为已经很好了。
# 但请注意，所有模型都应该继承自SQLModel。


# -----------------------------------------------------------------------------
# 4. 核心业务模型定义 (Core Business Models)
# -----------------------------------------------------------------------------
# 这些模型直接映射到数据库中的表。
# 我们首先为MVP（最小可行产品）定义最核心的三个模型：Video, Shot, 和 TaskRun。

class Video(SQLModel, table=True):
    """
    代表一个完整的视频项目，这是我们所有工作的顶级实体。
    """
    # 主键，数据库会自动生成并填充
    id: Optional[int] = Field(default=None, primary_key=True)
    
    # 视频标题
    title: str = Field(index=True)
    
    # 视频的当前状态，例如：planning, scripting, rendering, done, failed
    status: str = Field(default="planning", index=True)
    
    # 视频的语言代码, e.g., "en-US", "zh-CN"
    language: str = Field(default="zh-CN")

    # 预算（美分），用于成本控制
    budget_cents: int = Field(default=400)
    
    # 计划发布的平台列表
    publish_targets: List[str] = Field(sa_column=JSON, default=["youtube"])

    # 创建和更新时间戳
    created_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow, nullable=False)
    updated_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow, nullable=False, sa_column_kwargs={"onupdate": datetime.datetime.utcnow})

    # --- 关系定义 ---
    # 一个Video项目包含多个Shot（镜头）
    shots: List["Shot"] = Relationship(back_populates="video")
    # 一个Video项目会触发多个TaskRun（任务执行记录）
    task_runs: List["TaskRun"] = Relationship(back_populates="video")


class Shot(SQLModel, table=True):
    """
    代表视频中的一个镜头（或场景），是生产的基本单位。
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    
    # 外键，关联到它所属的Video项目
    video_id: int = Field(foreign_key="video.id")
    
    # 镜头在视频中的顺序索引
    idx: int
    
    # 镜头的规格描述 (来自LLM)，以JSON格式存储
    spec: dict = Field(sa_column=JSON)
    
    # 镜头的当前状态, e.g., pending, rendering, done, failed
    status: str = Field(default="pending", index=True)
    
    # 渲染此镜头所选择的供应商 (e.g., "flux_local", "external_api")
    provider: Optional[str] = Field(default=None)
    
    # 渲染时使用的预设 (e.g., "portrait_high", "anime_fast")
    preset: Optional[str] = Field(default=None)
    
    # 最终生成的素材在GCS等对象存储中的URI
    output_uri: Optional[str] = Field(default=None)
    
    # 此镜头的成本（美分）
    cost_cents: int = Field(default=0)
    
    # 重试次数
    retries: int = Field(default=0)

    # 创建和更新时间戳
    created_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow, nullable=False)
    updated_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow, nullable=False, sa_column_kwargs={"onupdate": datetime.datetime.utcnow})

    # --- 关系定义 ---
    # 每个Shot都属于一个Video
    video: Video = Relationship(back_populates="shots")


class TaskRun(SQLModel, table=True):
    """
    记录每一次Celery任务的执行情况，用于追踪、调试和成本归因。
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    
    # 关联到它所属的Video项目 (为了方便查询)
    video_id: int = Field(foreign_key="video.id")

    # Celery返回的任务ID，非常重要，用于反向查询任务状态
    celery_task_id: str = Field(unique=True, index=True)
    
    # 任务类型/名称 (e.g., "workers.llm.storyboard.run_round1")
    task_name: str = Field(index=True)
    
    # 任务状态 (e.g., PENDING, STARTED, SUCCESS, FAILURE)
    status: str = Field(default="PENDING", index=True)

    # 任务开始和结束时间
    started_at: Optional[datetime.datetime] = Field(default=None)
    ended_at: Optional[datetime.datetime] = Field(default=None)
    
    # 存储任务的输入参数或输出结果的简要信息
    metrics: Optional[dict] = Field(sa_column=JSON, default=None)

    # --- 关系定义 ---
    # 每个TaskRun都属于一个Video
    video: Video = Relationship(back_populates="task_runs")


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