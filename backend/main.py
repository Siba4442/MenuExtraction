import json
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Import extraction phases from your extractor.py
from extractor import run_phase1, run_phase2, run_phase3, run_phase4
from utils.model import Categories, CategorywithItems, CategoryBase, CategoryItemAddons 

app = FastAPI(title="Menu Extractor API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

OUTPUT_DIR = Path("outputs")
OUTPUT_DIR.mkdir(exist_ok=True)

# Standardized file paths to keep Extractor and API in sync
UPLOADED_PDF_PATH = OUTPUT_DIR / "uploaded_menu.pdf"
PHASE1_RAW_OUTPUT = OUTPUT_DIR / "categories_all_pages.json"
PHASE2_INPUT_REVIEWED = OUTPUT_DIR / "reviewed_categories.json"
PHASE2_FINAL_ITEMS = OUTPUT_DIR / "category_items_all_pages.json"
PHASE3_FINAL_BASES = OUTPUT_DIR / "category_bases_all_pages.json"
PHASE4_FINAL_ITEMS_ADDONS = OUTPUT_DIR / "category_items_addons_all_pages.json"

class Phase2CategoriesPayload(BaseModel):
    restaurant_name: str
    pages: List[Dict[str, Any]]

class Phase3ItemsPayload(BaseModel):
    restaurant_name: str
    pages: List[Dict[str, Any]]

# ---------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------
def load_json_file(file_path: Path, error_message: str) -> Dict[str, Any]:
    """Load and parse a JSON file with error handling."""
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=error_message)
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load file: {str(e)}")

def save_json_file(file_path: Path, data: Dict[str, Any]) -> None:
    """Save data to a JSON file."""
    file_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

def check_required_files(*file_paths: Path) -> None:
    """Check if required files exist."""
    for file_path in file_paths:
        if not file_path.exists():
            if file_path == UPLOADED_PDF_PATH:
                raise HTTPException(status_code=400, detail="PDF not found. Please upload PDF first.")
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Required file not found: {file_path.name}. Please complete previous phases."
                )

# ---------------------------------------------------------
# GET Endpoints: Retrieve Data for Editing
# ---------------------------------------------------------
@app.get("/api/phase1/get-categories")
async def get_phase1_categories():
    """Retrieve the phase 1 categories output for frontend editing."""
    data = load_json_file(PHASE1_RAW_OUTPUT, "Phase 1 data not found. Run phase 1 first.")
    return {"success": True, "data": data}

@app.get("/api/phase2/get-items")
async def get_phase2_items():
    """Retrieve the phase 2 items output for frontend editing."""
    data = load_json_file(PHASE2_FINAL_ITEMS, "Phase 2 data not found. Run phase 2 first.")
    return {"success": True, "data": data}

@app.get("/api/phase3/get-bases")
async def get_phase3_bases():
    """Retrieve the phase 3 bases output for frontend editing."""
    data = load_json_file(PHASE3_FINAL_BASES, "Phase 3 data not found. Run phase 3 first.")
    return {"success": True, "data": data}

@app.get("/api/phase4/get-addons")
async def get_phase4_addons():
    """Retrieve the phase 4 addons output for frontend display."""
    data = load_json_file(PHASE4_FINAL_ITEMS_ADDONS, "Phase 4 data not found. Run phase 4 first.")
    return {"success": True, "data": data}

# ---------------------------------------------------------
# PUT Endpoints: Save Edited Data
# ---------------------------------------------------------
@app.put("/api/phase1/update-categories")
async def update_phase1_categories(payload: Phase2CategoriesPayload):
    """Save user-edited phase 1 categories. This becomes the input for phase 2."""
    # Validate structure
    for page in payload.pages:
        if "data" not in page:
            raise HTTPException(status_code=400, detail="Invalid payload structure")
        Categories.model_validate(page["data"])
    
    save_json_file(PHASE2_INPUT_REVIEWED, payload.model_dump())
    return {"success": True, "message": "Categories updated successfully"}

@app.put("/api/phase2/update-items")
async def update_phase2_items(payload: Phase3ItemsPayload):
    """Save user-edited phase 2 items. This becomes the input for phase 3."""
    # Validate structure
    for page in payload.pages:
        if "categories" not in page:
            raise HTTPException(status_code=400, detail="Invalid payload structure")
        for cat in page["categories"]:
            CategorywithItems.model_validate(cat)
    
    save_json_file(PHASE2_FINAL_ITEMS, payload.model_dump())
    return {"success": True, "message": "Items updated successfully"}

@app.put("/api/phase3/update-bases")
async def update_phase3_bases(payload: Dict[str, Any]):
    """Save user-edited phase 3 bases. This becomes the input for phase 4."""
    # Validate structure
    if "pages" not in payload:
        raise HTTPException(status_code=400, detail="Invalid payload structure")
    
    for page in payload["pages"]:
        if "categories" not in page:
            raise HTTPException(status_code=400, detail="Invalid payload structure")
        for cat in page["categories"]:
            CategoryBase.model_validate(cat)
    
    save_json_file(PHASE3_FINAL_BASES, payload)
    return {"success": True, "message": "Bases updated successfully"}

# ---------------------------------------------------------
# POST Endpoints: Extraction Phases
# ---------------------------------------------------------
@app.post("/api/phase1/extract-categories")
async def extract_categories(
    restaurant_name: str = Form(...),
    pdf: UploadFile = File(...)
):
    """Receives PDF, saves it locally, and runs Phase 1 to find headers."""
    pdf_bytes = await pdf.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Empty PDF file")

    UPLOADED_PDF_PATH.write_bytes(pdf_bytes)
    result = await run_phase1(restaurant_name, str(UPLOADED_PDF_PATH))
    save_json_file(PHASE1_RAW_OUTPUT, result)

    return {"success": True, "phase1_result": result}

@app.post("/api/phase2/extract-items")
async def extract_items():
    """Uses the reviewed/edited categories from phase 1 to extract menu items."""
    check_required_files(PHASE2_INPUT_REVIEWED, UPLOADED_PDF_PATH)
    
    reviewed_data = load_json_file(PHASE2_INPUT_REVIEWED, "No reviewed categories found.")
    result = await run_phase2(
        reviewed_data["restaurant_name"], 
        reviewed_data, 
        str(UPLOADED_PDF_PATH)
    )

    return {"success": True, "message": "Item list extraction complete", "phase2_result": result}

@app.post("/api/phase3/extract-bases")
async def extract_bases():
    """Uses the reviewed/edited items from phase 2 to extract category bases."""
    check_required_files(PHASE2_FINAL_ITEMS, UPLOADED_PDF_PATH)
    
    items_data = load_json_file(PHASE2_FINAL_ITEMS, "No items found.")
    result = await run_phase3(
        items_data["restaurant_name"],
        items_data,
        str(UPLOADED_PDF_PATH)
    )

    return {"success": True, "message": "Category bases extraction complete", "phase3_result": result}

@app.post("/api/phase4/extract-addons")
async def extract_addons():
    """Uses reviewed items (phase 2) and bases (phase 3) to extract full item details."""
    check_required_files(PHASE2_FINAL_ITEMS, PHASE3_FINAL_BASES, UPLOADED_PDF_PATH)
    
    items_data = load_json_file(PHASE2_FINAL_ITEMS, "No items found.")
    bases_data = load_json_file(PHASE3_FINAL_BASES, "No bases found.")
    
    result = await run_phase4(
        items_data["restaurant_name"],
        items_data,
        bases_data,
        str(UPLOADED_PDF_PATH)
    )

    return {"success": True, "message": "Items with addons extraction complete", "phase4_result": result}

# ---------------------------------------------------------
# PATCH Endpoints: Partial Re-extraction
# ---------------------------------------------------------
@app.patch("/api/phase2/reextract-category")
async def reextract_category_items(
    category_name: str = Form(...),
    page_number: int = Form(...)
):
    """
    Re-extract items for a specific category on a specific page.
    Useful when user manually adds/edits a category and wants fresh extraction.
    """
    check_required_files(PHASE2_INPUT_REVIEWED, UPLOADED_PDF_PATH)
    
    reviewed_data = load_json_file(PHASE2_INPUT_REVIEWED, "No reviewed categories found.")
    
    # Find the specific page and category
    target_page = None
    for page in reviewed_data["pages"]:
        if page["page_number"] == page_number:
            target_page = page
            break
    
    if not target_page:
        raise HTTPException(status_code=404, detail=f"Page {page_number} not found")
    
    # Find the category in that page
    target_category = None
    for cat in target_page["data"]["categories"]:
        if cat["name"] == category_name:
            target_category = cat
            break
    
    if not target_category:
        raise HTTPException(status_code=404, detail=f"Category '{category_name}' not found on page {page_number}")
    
    # Re-run extraction for just this category
    from extractor import call_openrouter, phase2_prompt, json_schema_format
    from utils.processing import convert_pdf_into_images
    
    images = await convert_pdf_into_images(str(UPLOADED_PDF_PATH))
    page_image = images[page_number - 1]
    
    prompt = phase2_prompt(reviewed_data["restaurant_name"], page_number, target_category)
    messages = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{page_image}"}}
    ]
    
    response = await call_openrouter(messages, json_schema_format(CategorywithItems))
    raw = response.choices[0].message.content
    obj = json.loads(raw)
    validated = CategorywithItems.model_validate(obj)
    
    return {
        "success": True,
        "message": f"Re-extracted items for category '{category_name}'",
        "category_items": validated.model_dump()
    }

@app.patch("/api/phase3/reextract-category-base")
async def reextract_category_base(
    category_name: str = Form(...),
    page_number: int = Form(...)
):
    """
    Re-extract base information for a specific category.
    Useful when user manually modifies items and wants updated base extraction.
    """
    check_required_files(PHASE2_FINAL_ITEMS, UPLOADED_PDF_PATH)
    
    items_data = load_json_file(PHASE2_FINAL_ITEMS, "No items found.")
    
    # Find the specific page and category
    target_page = None
    for page in items_data["pages"]:
        if page["page_number"] == page_number:
            target_page = page
            break
    
    if not target_page:
        raise HTTPException(status_code=404, detail=f"Page {page_number} not found")
    
    target_category = None
    for cat in target_page["categories"]:
        if cat["name"] == category_name:
            target_category = cat
            break
    
    if not target_category:
        raise HTTPException(status_code=404, detail=f"Category '{category_name}' not found on page {page_number}")
    
    # Re-run extraction for just this category base
    from extractor import call_openrouter, phase3_prompt, json_schema_format
    from utils.processing import convert_pdf_into_images
    
    images = await convert_pdf_into_images(str(UPLOADED_PDF_PATH))
    page_image = images[page_number - 1]
    
    category_wrapper = {"category": target_category}
    prompt = phase3_prompt(items_data["restaurant_name"], page_number, category_wrapper)
    messages = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{page_image}"}}
    ]
    
    response = await call_openrouter(messages, json_schema_format(CategoryBase))
    raw = response.choices[0].message.content
    obj = json.loads(raw)
    validated = CategoryBase.model_validate(obj)
    
    return {
        "success": True,
        "message": f"Re-extracted base for category '{category_name}'",
        "category_base": validated.model_dump()
    }

@app.patch("/api/phase4/reextract-category-addons")
async def reextract_category_addons(
    category_name: str = Form(...),
    page_number: int = Form(...)
):
    """
    Re-extract full item details with addons for a specific category.
    """
    check_required_files(PHASE2_FINAL_ITEMS, PHASE3_FINAL_BASES, UPLOADED_PDF_PATH)
    
    items_data = load_json_file(PHASE2_FINAL_ITEMS, "No items found.")
    bases_data = load_json_file(PHASE3_FINAL_BASES, "No bases found.")
    
    # Find category in items
    target_item_cat = None
    for page in items_data["pages"]:
        if page["page_number"] == page_number:
            for cat in page["categories"]:
                if cat["name"] == category_name:
                    target_item_cat = cat
                    break
    
    # Find category in bases
    target_base_cat = None
    for page in bases_data["pages"]:
        if page["page_number"] == page_number:
            for cat in page["categories"]:
                if cat["name"] == category_name:
                    target_base_cat = cat
                    break
    
    if not target_item_cat or not target_base_cat:
        raise HTTPException(status_code=404, detail=f"Category '{category_name}' not found on page {page_number}")
    
    # Re-run extraction
    from extractor import call_openrouter, phase4_prompt, json_schema_format
    from utils.processing import convert_pdf_into_images
    
    images = await convert_pdf_into_images(str(UPLOADED_PDF_PATH))
    page_image = images[page_number - 1]
    
    category_wrapper = {"category": target_item_cat}
    base_wrapper = {"category_base": target_base_cat}
    prompt = phase4_prompt(items_data["restaurant_name"], page_number, category_wrapper, base_wrapper)
    messages = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{page_image}"}}
    ]
    
    response = await call_openrouter(messages, json_schema_format(CategoryItemAddons))
    raw = response.choices[0].message.content
    obj = json.loads(raw)
    validated = CategoryItemAddons.model_validate(obj)
    
    return {
        "success": True,
        "message": f"Re-extracted addons for category '{category_name}'",
        "category_addons": validated.model_dump()
    }