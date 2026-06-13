"""海报图片下载模块
===================
- 带指数退避重试的图片下载
- 线程池并发下载海报
"""
import os
import re
import ssl
import time
import logging
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import requests as _requests_lib
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

from crawler.config import (
    POSTERS_DIR, IMAGE_DOWNLOAD_TIMEOUT, IMAGE_RETRY_MAX,
    IMAGE_THREAD_POOL_SIZE,
)


def download_image(url: str, filename: str, retries: int = None) -> str:
    """带指数退避重试的图片下载"""
    if not url:
        return None
    if retries is None:
        retries = IMAGE_RETRY_MAX
    filepath = os.path.join(POSTERS_DIR, filename)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    if os.path.exists(filepath):
        return filepath

    for attempt in range(retries):
        try:
            if REQUESTS_AVAILABLE:
                resp = _requests_lib.get(
                    url, timeout=IMAGE_DOWNLOAD_TIMEOUT,
                    allow_redirects=True, verify=False
                )
                resp.raise_for_status()
                with open(filepath, 'wb') as f:
                    f.write(resp.content)
                logging.getLogger("main").info(f"图片下载成功: {filename}")
                return filepath

            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(url)
            data = urllib.request.urlopen(
                req, timeout=IMAGE_DOWNLOAD_TIMEOUT, context=ssl_ctx
            ).read()
            with open(filepath, 'wb') as f:
                f.write(data)
            logging.getLogger("main").info(f"图片下载成功: {filename}")
            return filepath

        except Exception as err:
            if attempt < retries - 1:
                wait = 2 ** attempt
                logging.getLogger("main").warning(
                    f"图片重试 {attempt+1}/{retries}: {filename}, 等待{wait}s")
                time.sleep(wait)
            else:
                logging.getLogger("main").error(
                    f"图片下载最终失败 {filename}: {err}")
    return None


def download_posters_concurrent(movies_list: list) -> dict:
    """线程池并发下载海报 (使用 enumerate 避免 O(n²) 查找)"""
    logger = logging.getLogger("main")
    logger.info(f"开始并发下载海报: {len(movies_list)} 部电影")
    executor = ThreadPoolExecutor(max_workers=IMAGE_THREAD_POOL_SIZE)
    results = {}

    futures = {}
    for idx, movie in enumerate(movies_list):
        poster_url = movie.get("poster_url") or movie.get("douban_poster")
        if poster_url:
            title = movie.get("title", "unknown")
            no_str = str(idx + 1).zfill(6)
            ext = '.jpg'
            if poster_url:
                match = re.search(r'\.(jpg|jpeg|png|webp)(?:\?|$)', poster_url, re.I)
                if match:
                    ext = match.group(0).split('?')[0]
            filename = f"{no_str}{ext}"
            future = executor.submit(download_image, poster_url, filename)
            futures[future] = (title, filename)

    for future in as_completed(futures):
        title, filename = futures[future]
        try:
            local_path = future.result()
            if local_path:
                results[title] = (local_path, filename)
        except Exception as e:
            logger.error(f"海报下载线程异常: {title} - {e}")

    executor.shutdown(wait=True)
    logger.info(f"海报下载完成: 成功 {len(results)}/{len(movies_list)}")
    return results
