# workers/tools/connectivity_test.py
from services.api.app.core.celery_app import celery_app
from services.api.app.core.config import settings
from google.cloud import storage
import logging

@celery_app.task(queue="default")
def gcs_read_write_test(bucket_name: str, test_content: str):
    """一个简单的Celery任务，用于测试GCS的读写权限。"""
    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob_name = "mvp_test_file.txt"
        blob = bucket.blob(blob_name)

        # 写
        blob.upload_from_string(test_content)
        logging.info(f"成功写入文件 '{blob_name}' 到桶 '{bucket_name}'。")

        # 读
        downloaded_content = blob.download_as_text()
        logging.info(f"成功从桶 '{bucket_name}' 读取文件 '{blob_name}'。")

        # 验证
        if downloaded_content == test_content:
            return "SUCCESS: GCS read/write test passed."
        else:
            return "FAILED: Content mismatch."
    except Exception as e:
        logging.error(f"GCS 测试失败: {e}")
        return f"FAILED: {str(e)}"