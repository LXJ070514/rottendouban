"""网站数据生成模块 — 生成 JSON / CSV / 统计数据"""
import os
import json
import shutil
import logging
from datetime import datetime

from crawler.config import POSTERS_DIR


def generate_site_data(db, output_dir: str) -> str:
    """生成网站所需的 JSON、CSV 和统计数据文件"""
    logger = logging.getLogger("main")

    # 导出 JSON
    json_data = db.export_json()
    json_dir = os.path.join(output_dir, "data")
    os.makedirs(json_dir, exist_ok=True)
    json_path = os.path.join(json_dir, "movies.json")
    with open(json_path, 'w', encoding='utf-8') as f:
        f.write(json_data)
    logger.info(f"JSON 数据导出: {json_path}")

    # 导出 CSV
    csv_data = db.export_csv()
    csv_path = os.path.join(json_dir, "movies.csv")
    with open(csv_path, 'w', encoding='utf-8', newline='') as f:
        f.write(csv_data)

    # 导出统计数据
    stats = db.get_statistics()
    stats["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    stats_path = os.path.join(json_dir, "stats.json")
    with open(stats_path, 'w', encoding='utf-8') as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    logger.info(f"统计数据导出: {stats_path}")

    # 复制海报到网站目录
    poster_src = POSTERS_DIR
    poster_dst = os.path.join(output_dir, "posters")
    if os.path.exists(poster_src):
        os.makedirs(poster_dst, exist_ok=True)
        for f_name in os.listdir(poster_src):
            src = os.path.join(poster_src, f_name)
            dst = os.path.join(poster_dst, f_name)
            if os.path.isfile(src) and f_name.lower().endswith(
                ('.jpg', '.jpeg', '.png', '.webp')):
                shutil.copy2(src, dst)
        logger.info(f"海报复制: {poster_dst}")

    return json_path
