from io import BytesIO

from PIL import Image


def mpo_bytes(secondary_size=(32, 32)):
    """Build a small two-picture JPEG container without using personal photos."""
    buffer = BytesIO()
    with Image.new("RGB", (100, 100), "blue") as primary:
        with Image.new("RGB", secondary_size, "red") as secondary:
            primary.save(buffer, format="MPO", save_all=True, append_images=[secondary])
    return buffer.getvalue()
