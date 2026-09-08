"""Validated, eBay-ready contracts that do not depend on one listing API."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import PackageDimensions, PackageWeight


class EbayModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EbayCategory(EbayModel):
    category_id: str = Field(pattern=r"^[0-9]+$")
    name: str = Field(min_length=1, max_length=200)
    path: str = Field(min_length=1, max_length=500)


class EbayCondition(EbayModel):
    condition_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)


class EbayAspectValueConstraint(EbayModel):
    aspect_name: str = Field(min_length=1, max_length=65)
    values: list[str] = Field(min_length=1)


class EbayAspectValue(EbayModel):
    value: str = Field(min_length=1, max_length=800)
    constraints: list[EbayAspectValueConstraint] = Field(default_factory=list)


class EbayAspect(EbayModel):
    name: str = Field(min_length=1, max_length=65)
    values: list[EbayAspectValue] = Field(default_factory=list)
    required: bool = False
    recommended: bool = False
    usage: str = "optional"
    mode: str = "free_text"
    cardinality: str = "single"
    max_length: int | None = Field(default=None, gt=0)
    data_type: str = "string"
    format: str | None = None
    advanced_data_type: str | None = None
    applicable_to: list[str] = Field(default_factory=list)
    expected_required_by_date: str | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_values(cls, value):
        if isinstance(value, dict):
            converted = dict(value)
            if isinstance(converted.get("values"), list):
                converted["values"] = [
                    {"value": item} if isinstance(item, str) else item
                    for item in converted["values"]
                ]
            applicability = converted.get("applicable_to")
            if isinstance(applicability, str):
                converted["applicable_to"] = [applicability]
            elif applicability is None:
                converted["applicable_to"] = []
            return converted
        return value


class CategoryRequirements(EbayModel):
    category: EbayCategory
    conditions: list[EbayCondition]
    aspects: list[EbayAspect]


class EbayItemSpecific(EbayModel):
    name: str = Field(min_length=1, max_length=80)
    values: list[Annotated[str, Field(max_length=800)]] = Field(
        default_factory=list, max_length=100
    )

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_value(cls, value):
        if isinstance(value, dict) and "value" in value and "values" not in value:
            converted = dict(value)
            converted["values"] = [converted.pop("value")]
            return converted
        return value


class BusinessPolicies(EbayModel):
    fulfillment_policy_id: str = Field(min_length=1, max_length=100)
    payment_policy_id: str = Field(min_length=1, max_length=100)
    return_policy_id: str = Field(min_length=1, max_length=100)


class EbayReadyListing(EbayModel):
    """The app-owned listing shape used by both Feed and Trading adapters."""

    title: str = Field(min_length=1, max_length=80)
    category_id: str = Field(pattern=r"^[0-9]+$")
    category_name: str = Field(min_length=1, max_length=200)
    condition_id: int = Field(gt=0)
    condition_description: str = Field(default="", max_length=1000)
    description: str = Field(min_length=1, max_length=500_000)
    item_specifics: list[EbayItemSpecific] = Field(default_factory=list, max_length=100)
    price: float = Field(gt=0, le=1_000_000)
    currency: Literal["USD"] = "USD"
    quantity: Literal[1] = 1
    marketplace_id: Literal["EBAY_US"] = "EBAY_US"
    listing_format: Literal["fixed_price"] = "fixed_price"
    business_policies: BusinessPolicies | None = None
    package_weight: PackageWeight | None = None
    package_dimensions: PackageDimensions | None = None

    @field_validator("price")
    @classmethod
    def two_decimal_price(cls, value: float) -> float:
        return round(value, 2)


class MediaReference(EbayModel):
    image_id: str = Field(min_length=1, max_length=200)
    image_url: str = Field(min_length=1, max_length=2000)
    expires_at: datetime | None = None


class FeedTask(EbayModel):
    task_id: str = Field(min_length=1, max_length=200)
    status: Literal[
        "CREATED",
        "QUEUED",
        "IN_PROCESS",
        "COMPLETED",
        "COMPLETED_WITH_ERROR",
        "FAILED",
    ]


class EbayIssue(EbayModel):
    severity: Literal["warning", "error"]
    code: str = ""
    message: str


class TradingResult(EbayModel):
    acknowledged: bool
    item_id: str | None = None
    fees: dict[str, str] = Field(default_factory=dict)
    issues: list[EbayIssue] = Field(default_factory=list)
    revision: str | None = None
    raw_item_xml: str | None = Field(default=None, exclude=True)
    current_listing: EbayReadyListing | None = None
    current_image_urls: list[str] = Field(default_factory=list)


class PublishRequest(EbayModel):
    listing_id: str


class ReviseRequest(EbayModel):
    expected_revision: str
    listing: EbayReadyListing
    replace_photos: bool = False


class EndRequest(EbayModel):
    reason: Literal[
        "NotAvailable",
        "Incorrect",
        "LostOrBroken",
        "OtherListingError",
        "SellToHighBidder",
    ] = "NotAvailable"
