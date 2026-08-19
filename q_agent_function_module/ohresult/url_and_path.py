import io
import requests
import os

def get_file_stream(file_path):
    """通用流处理器：识别 URL 或 本地路径并返回 BytesIO 流"""
    if file_path.startswith(("http://", "https://")):
        response = requests.get(file_path, timeout=30)
        response.raise_for_status()
        return io.BytesIO(response.content)
    else:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"未找到本地文件: {file_path}")
        with open(file_path, 'rb') as f:
            return io.BytesIO(f.read())