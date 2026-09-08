"""Validated application and model-output contracts."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ItemSpecific(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    values: list[Annotated[str, Field(max_length=800)]] = Field(
        default_factory=list, max_length=100
    )
    source: Literal["photo", "seller_note", "inference", "unknown"]
    confidence: Literal["low", "medium", "high"]

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_value(cls, value):
        if isinstance(value, dict) and "value" in value and "values" not in value:
            converted = dict(value)
            converted["values"] = [converted.pop("value")]
            return converted
        return value


class PricingGuidance(StrictModel):
    suggested_price: float = Field(ge=0)
    expected_sale_price_low: float = Field(ge=0)
    expected_sale_price_high: float = Field(ge=0)
    currency: Literal["USD"]
    confidence: Literal["low", "medium", "high"]
    rationale: str = Field(min_length=1, max_length=600)

    @model_validator(mode="after")
    def validate_price_range(self) -> "PricingGuidance":
        if self.expected_sale_price_low > self.expected_sale_price_high:
            raise ValueError("expected sale-price low must not exceed high")
        return self


class PackageWeight(StrictModel):
    pounds: int = Field(ge=0)
    ounces: int = Field(ge=0, le=15)

    @model_validator(mode="after")
    def require_positive_weight(self) -> "PackageWeight":
        if self.pounds == 0 and self.ounces == 0:
            raise ValueError("package weight must be greater than zero")
        return self


class PackageDimensions(StrictModel):
    length: int = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class ListingDraft(StrictModel):
    generation_required: bool = False
    title: str = Field(min_length=1, max_length=80)
    suggested_category: str = Field(min_length=1, max_length=160)
    search_terms: list[str] = Field(max_length=12)
    condition: str = Field(max_length=80)
    condition_description: str = Field(max_length=800)
    description: str = Field(min_length=1, max_length=4_000)
    item_specifics: list[ItemSpecific] = Field(max_length=100)
    quantity: int = Field(ge=1, le=999)
    recommended_listing_format: Literal["fixed_price", "auction"]
    observed_flaws: list[str] = Field(max_length=20)
    missing_facts: list[str] = Field(max_length=20)
    pricing: PricingGuidance
    package_weight: PackageWeight | None = None
    package_dimensions: PackageDimensions | None = None


class DraftEbayCategory(StrictModel):
    group_id: Literal["260012", "260010", "171146"] | None = None
    category_id: str = Field(pattern=r"^[0-9]+$")
    name: str = Field(min_length=1, max_length=200)
    path: str = Field(min_length=1, max_length=500)


class DraftEbayCondition(StrictModel):
    condition_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=120)


class SavedDraftUpdate(StrictModel):
    revision: int | None = Field(default=None, ge=1)
    draft: ListingDraft
    ebay_category: DraftEbayCategory | None = None
    ebay_condition: DraftEbayCondition | None = None
    photo_count: int = Field(ge=1, le=24)


class SavedDraftSummary(StrictModel):
    id: str
    title: str
    photo_count: int = Field(ge=1, le=24)
    updated_at: datetime
    status: Literal["draft", "published"]
    ebay_item_id: str | None = None
    listing_url: str | None = None
    seller_hub_url: str | None = None


class SavedDraftListResponse(StrictModel):
    drafts: list[SavedDraftSummary]


class PublicationRecoveryPolicies(StrictModel):
    fulfillment_policy_id: str = Field(min_length=1, max_length=100)
    payment_policy_id: str = Field(min_length=1, max_length=100)
    return_policy_id: str = Field(min_length=1, max_length=100)


class SavedDraftResponse(SavedDraftUpdate):
    id: str
    revision: int = Field(ge=1)
    updated_at: datetime
    status: Literal["draft", "published"]
    ebay_item_id: str | None = None
    listing_url: str | None = None
    seller_hub_url: str | None = None
    publication_recovery_pending: bool = False
    publication_recovery_policies: PublicationRecoveryPolicies | None = None
    publication_recovery_preparation_id: str | None = None


class ImageWarning(StrictModel):
    photo_number: int = Field(ge=1, le=24)
    message: str


class GenerationResponse(StrictModel):
    draft_id: str
    saved: bool
    draft_revision: int | None = Field(default=None, ge=1)
    draft: ListingDraft
    ebay_category: DraftEbayCategory
    warnings: list[ImageWarning]


# OpenAI Structured Outputs supports only a subset of JSON Schema. Keep unsupported
# string-length constraints in ListingDraft and revalidate parsed output into it.
class GeneratedItemSpecific(StrictModel):
    name: str
    values: list[str]
    source: Literal["photo", "seller_note", "inference", "unknown"]
    confidence: Literal["low", "medium", "high"]


class GeneratedPricingGuidance(StrictModel):
    suggested_price: float
    expected_sale_price_low: float
    expected_sale_price_high: float
    currency: Literal["USD"]
    confidence: Literal["low", "medium", "high"]
    rationale: str


class GeneratedListingDraft(StrictModel):
    title: str
    suggested_category: str
    search_terms: list[str]
    condition: str = Field(
        description=(
            "Concise seller-facing condition label stated directly, or an empty string when "
            "the supplied evidence does not support a condition"
        )
    )
    condition_description: str = Field(
        description=(
            "Concise seller-facing condition facts stated directly, without evidence-source "
            "attribution or temporary storage and photography artifacts; empty when unsupported"
        )
    )
    description: str
    item_specifics: list[GeneratedItemSpecific]
    quantity: int
    recommended_listing_format: Literal["fixed_price", "auction"]
    observed_flaws: list[str]
    missing_facts: list[str]
    pricing: GeneratedPricingGuidance
