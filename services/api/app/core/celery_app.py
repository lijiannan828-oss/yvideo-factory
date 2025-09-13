# services/api/app/core/celery_app.py

import os
from pathlib import Path
from celery import Celery
from kombu import Queue

# 从当前目录的config模块中导入settings实例
from .config import settings

# -----------------------------------------------------------------------------
# 辅助函数: 自动发现任务模块 (Auto-discover tasks)
# -----------------------------------------------------------------------------
def find_task_modules(base_path="workers"):
    """
    自动扫描指定路径下的所有Python模块，并将其转换为Celery可识别的导入路径。
    
    这样做的好处是，未来任何在 'workers' 目录下新增的Python文件（只要其中包含Celery任务），
    都会被自动发现，无需再手动修改此处的 'include' 列表。
    
    :param base_path: 相对于项目根目录的任务模块基础路径。
    :return: 一个包含所有任务模块导入路径的列表。
    """
    # 获取项目根目录的绝对路径
    root_dir = Path(__file__).parent.parent.parent.parent
    tasks_dir = root_dir / base_path
    
    module_paths = []
    # 遍历workers目录下的所有文件和子目录
    for path in tasks_dir.rglob("*.py"):
        # 忽略__init__.py文件
        if path.name == "__init__.py":
            continue
        
        # 将文件路径转换为Python的模块导入路径
        # 例如: .../workers/llm/storyboard.py -> workers.llm.storyboard
        relative_path = path.relative_to(root_dir)
        module_path = ".".join(relative_path.with_suffix("").parts)
        module_paths.append(module_path)
        
    return module_paths

# -----------------------------------------------------------------------------
# 1. 初始化Celery应用
# -----------------------------------------------------------------------------
# 我们调用 find_task_modules() 函数来动态生成需要包含的任务模块列表。
celery_app = Celery(
    "yvideo_factory",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=find_task_modules(),
)

# -----------------------------------------------------------------------------
# 2. 定义任务队列 (Task Queues) - 已采纳你的建议
# -----------------------------------------------------------------------------
# 新增了一个 'analysis_queue' 用于处理分析类任务，使其与生产任务隔离。
celery_app.conf.task_queues = (
    Queue("default", routing_key="task.default"),
    # 新增：分析类任务队列，用于耗时较长的策略分析、爆款解构等
    Queue("analysis_queue", routing_key="task.analysis"),
    Queue("cpu_queue", routing_key="task.cpu"),
    Queue("gpu_queue", routing_key="task.gpu"),
    Queue("api_queue", routing_key="task.api"),
)

# -----------------------------------------------------------------------------
# 3. 任务路由 (Task Routing)
# -----------------------------------------------------------------------------
# 我们可以预先定义一些路由规则，让特定类型的任务自动进入正确的队列。
celery_app.conf.task_routes = {
    # 将所有在 'workers.analysis' 和 'workers.strategy' 模块下的任务自动路由到分析队列
    'workers.analysis.*': {'queue': 'analysis_queue'},
    'workers.strategy.*': {'queue': 'analysis_queue'},
    # 示例：未来我们可以将视频生成任务路由到GPU队列
    # 'workers.video.generate.*': {'queue': 'gpu_queue'},
}

# -----------------------------------------------------------------------------
# 4. Celery核心配置 (保持不变)
# -----------------------------------------------------------------------------
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone=settings.APP_TIMEZONE,
    enable_utc=True,
    worker_prefetch_multiplier=1,
    result_expires=86400,
    task_track_started=True,
    task_acks_late=True,
)