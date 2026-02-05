import asyncio
import json
import os
import shutil
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.main import save_annotation
from app.storage import DATASETS_DIR


def _mkdir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


async def _run() -> None:
    dataset_id = f"__tmp_anno_{uuid.uuid4().hex[:8]}"
    dataset_dir = os.path.join(DATASETS_DIR, dataset_id)
    _mkdir(os.path.join(dataset_dir, "images", "train"))
    _mkdir(os.path.join(dataset_dir, "labels", "train"))
    _mkdir(os.path.join(dataset_dir, "labels_json", "train"))

    image_name = "test.jpg"
    subset = "train"
    width = 100
    height = 100

    ann = {
        "type": "Annotation",
        "body": [],
        "target": {
            "source": "http://localhost/test.jpg",
            "selector": {
                "type": "FragmentSelector",
                "value": "xywh=pixel:10,20,30,40",
            },
        },
    }

    try:
        res = await save_annotation(
            dataset_id=dataset_id,
            annotations_json=json.dumps([ann], ensure_ascii=False),
            width=width,
            height=height,
            file=None,
            image_name=image_name,
            subset=subset,
            class_id=0,
            val_split=0.1,
        )
        assert isinstance(res, dict) and res.get("status") == "ok"

        base = os.path.splitext(image_name)[0]
        label_path = os.path.join(dataset_dir, "labels", subset, base + ".txt")
        json_path = os.path.join(dataset_dir, "labels_json", subset, base + ".json")
        assert os.path.isfile(label_path)
        assert os.path.isfile(json_path)
        with open(label_path, "r", encoding="utf-8") as f:
            line = f.readline().strip()
        assert line.startswith("0 ")

        res2 = await save_annotation(
            dataset_id=dataset_id,
            annotations_json="[]",
            width=width,
            height=height,
            file=None,
            image_name=image_name,
            subset=subset,
            class_id=0,
            val_split=0.1,
        )
        assert isinstance(res2, dict) and res2.get("deleted") is True
        assert not os.path.exists(label_path)
        assert not os.path.exists(json_path)
    finally:
        shutil.rmtree(dataset_dir, ignore_errors=True)


def main() -> None:
    asyncio.run(_run())
    print("ok")


if __name__ == "__main__":
    main()
