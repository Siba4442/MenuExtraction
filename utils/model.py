from __future__ import annotations
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, ConfigDict
from uuid import uuid4



def _id() -> str:
    return uuid4().hex


# ---------- COMMON ----------
class Money(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: float
    currency: Optional[str] = None


class PriceByVariation(BaseModel):
    """
    If price depends on size/variation.
    variation_name should match Phase-2 variation names exactly (e.g. Small/Large).
    """
    model_config = ConfigDict(extra="forbid")
    variation_name: str                     # +$2 for Large
    price: Optional[Money] = None           # absolute price if menu gives it

# phase 1 

# class Category(BaseModel):
#     name: str = Field(..., description="Category name")
#     subcategories: List[str] = Field(default_factory=list, description="List of subcategories")


# class CategoryDetails(BaseModel):
#     categories: List[Category] = Field(default_factory=list, description="List of categories with subcategories")


# ---------- PHASE 1 ----------

class SubcategoryRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name_raw: str

class CategoryRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name_raw: str
    subcategories: List[SubcategoryRef] = Field(default_factory=list)  # can be empty

class Categories(BaseModel):
    model_config = ConfigDict(extra="forbid")
    categories: List[CategoryRef] = Field(default_factory=list)


# ---------- PHASE 2 ----------
class Variation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name_raw: str  # e.g., Small / Medium / Large
    price: Optional[Money] = None  # if variation has explicit price
    size: Optional[str] = None  # e.g., 12oz, 16oz

class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name_raw: str
    description_raw: Optional[str] = None

    # If item has sizes, this is filled; else empty
    variations: List[Variation] = Field(default_factory=list)

    # If menu lists a single base price (no variations), keep it here
    base_price: Optional[Money] = None
    size: Optional[str] = None  # e.g., 12oz, 16oz

class CategoryItems(BaseModel):
    """
    Items can be directly under category (no subcategory).
    """
    model_config = ConfigDict(extra="forbid")
    items: List[Item] = Field(default_factory=list)
    description_raw: Optional[str] = None

class SubcategoryItems(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name_raw: str 
    items: List[Item] = Field(default_factory=list)
    description_raw: Optional[str] = None

class CategorywithItems(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name_raw: str
    category_items: List[CategoryItems] = Field(default_factory=list)
    subcategory_items: List[SubcategoryItems] = Field(default_factory=list)
    note: str
    







# phase 2 

# class Variation(BaseModel):
#     name: str = Field(..., description="Variation name")
#     price: float = Field(..., description="Variation price")
#     price_unit: Optional[str] = Field(None, description="Unit of the price")


# class Item(BaseModel):
#     name: str = Field(..., description="Item name")
#     description: Optional[str] = Field(None, description="Item description")
#     variations: List[Variation] = Field(default_factory=list, description="List of item variations")


# class subcategoryitem(BaseModel):
#     name: Optional[str] = Field(None, description="Subcategory item name")
#     items: List[Item] = Field(default_factory=list, description="List of items in the subcategory")


# class categoryitem(BaseModel):
#     name: str = Field(..., description="Category item name")
#     subcategoryitems: List[subcategoryitem] = Field(default_factory=list, description="List of subcategory items in the category")
#     description: Optional[str] = Field(None, description="Category item description")


# class categorywithitems(BaseModel):
#     categoryitems: List[categoryitem] = Field(default_factory=list, description="List of category items with subcategory items")


# phase 3

class BaseOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name_raw: str
    price: Optional[Money] = None 
    default: bool = False                  # included by default in the item
    price_by_variation: Optional[List[PriceByVariation]] = None

class CategoryBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name_raw: str
    base_options: Optional[List[BaseOption]] = Field(default_factory=list)
    subcategories_base: Optional[List[BaseOption]] = Field(default_factory=list)


# phase 4

class AddonOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name_raw: str
    default: bool = False                  # included by default in the item
    price: Optional[Money] = None
    price_by_variation: Optional[List[PriceByVariation]] = Field(default_factory=list)

class ItemsAddons(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name_raw: str
    addons: List[AddonOption] = Field(default_factory=list)

class SubcategoryAddons(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name_raw: str
    items_addons: Optional[List[ItemsAddons]] = Field(default_factory=list)

class CategoryItemAddons(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name_raw: str
    subcategory_items : Optional[List[SubcategoryAddons]] = Field(default_factory=list)
    items_addons: Optional[List[ItemsAddons]] = Field(default_factory=list)