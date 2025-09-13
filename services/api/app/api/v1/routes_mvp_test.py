# services/api/app/api/v1/routes_mvp_test.py
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from services.api.app.core.db import get_db_session, Video
from workers.tools.connectivity_test import gcs_read_write_test
from services.api.app.core.config import settings
import uuid

router = APIRouter()

@router.get("/mvp-test")
def run_mvp_test(db: Session = Depends(get_db_session)):
    """一个用于端到端测试的API接口"""
    try:
        # 1. 数据库写入测试
        test_video_title = f"MVP Test Video {uuid.uuid4()}"
        new_video = Video(title=test_video_title, status="test")
        db.add(new_video)
        db.commit()
        db.refresh(new_video)

        # 2. 数据库读取测试
        retrieved_video = db.get(Video, new_video.id)
        if not retrieved_video or retrieved_video.title != test_video_title:
            raise HTTPException(status_code=500, detail="DB Test Failed: Read/Write mismatch.")

        # 3. GCS & Celery-Redis 测试
        test_content = f"Hello GCS from MVP test {uuid.uuid4()}"
        task = gcs_read_write_test.delay(settings.GCS_BUCKET_NAME, test_content)

        return {
            "status": "SUCCESS",
            "db_test": f"Successfully created and read video titled: '{test_video_title}'",
            "celery_redis_test": f"Successfully sent GCS test task to Celery. Task ID: {task.id}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An error occurred: {str(e)}")