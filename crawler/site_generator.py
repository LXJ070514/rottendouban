"""网站数据生成模块 — 输出 site/data/ 下的 JSON 与统计文件"""
import os
import json
import logging
from datetime import datetime


def generate_site_data(db, output_dir: str) -> str:
    """生成网站所需的 movies.json / movies.csv / stats.json"""
    logger = logging.getLogger("main")

    data_dir = os.path.join(output_dir, "data")
    os.makedirs(data_dir, exist_ok=True)

    json_path = os.path.join(data_dir, "movies.json")
    with open(json_path, "w", encoding="utf-8") as f:
        f.write(db.export_json())
    logger.info(f"JSON 数据导出: {json_path}")

    with open(os.path.join(data_dir, "movies.csv"), "w", encoding="utf-8", newline="") as f:
        f.write(db.export_csv())

    stats = db.get_statistics()
    stats["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    stats_path = os.path.join(data_dir, "stats.json")
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    logger.info(f"统计数据导出: {stats_path}")

    return json_path
