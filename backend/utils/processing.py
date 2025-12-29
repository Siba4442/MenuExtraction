import fitz  # PyMuPDF
import base64
from typing import List, Union
import asyncio



async def convert_to_base64(page: fitz.Page) -> str:
    """Convert a PDF page to a base64 PNG image."""
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # Increase resolution
    img_data = pix.tobytes("png")
    base64_str = base64.b64encode(img_data).decode("utf-8")
    return base64_str



async def convert_pdf_into_images(pdf_input: Union[str, bytes]) -> List[str]:
    """Convert a PDF (path or file bytes) into base64 PNG images."""
    
    if isinstance(pdf_input, str):  # file path
        doc = fitz.open(pdf_input)
    else:  # bytes-like object (e.g., Streamlit upload)
        doc = fitz.open(stream=pdf_input, filetype="pdf")

    try:
        base64_images: List[str] = []
        tasks = [convert_to_base64(doc[page_num]) for page_num in range(len(doc))]
        awaitable_results = await asyncio.gather(*tasks)
        base64_images.extend(awaitable_results)
        return base64_images

    finally:
        doc.close()



def to_dict(obj):
    # Case 1: Pydantic v2
    if hasattr(obj, "model_dump"):
        return obj.model_dump()

    # Pydantic v1
    if hasattr(obj, "dict"):
        return obj.dict()

    # Case 2: dataclass
    if hasattr(obj, "__dataclass_fields__"):
        return {k: to_dict(v) for k, v in obj.__dict__.items()}

    # Case 3: custom Python objects
    if hasattr(obj, "__dict__"):
        return {k: to_dict(v) for k, v in obj.__dict__.items()}

    # Case 4: list of objects
    if isinstance(obj, list):
        return [to_dict(item) for item in obj]

    # Case 5: dict of objects
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}

    # Base case: primitive (str, int, None, bool)
    return obj