import io
import os
import sys
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.model_manager import ModelManager


def main() -> None:
    img = Image.new("RGB", (100, 50), (255, 0, 0))
    exif = img.getexif()
    exif[274] = 6
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes())
    data = buf.getvalue()

    class DummyEngine:
        names = {}

        def predict(self, pil_img, **kwargs):
            print("predict_image_size=", pil_img.size)
            return []

    m = ModelManager(models_dir="models")
    m.engine = DummyEngine()
    m.current_version_id = "dummy"
    m.ensure_current_loaded = lambda force=False: None

    result, version_id, source = m.infer_image(data)
    print("boxes_len=", len(result.boxes), "version_id=", version_id, "source=", source)


if __name__ == "__main__":
    main()
