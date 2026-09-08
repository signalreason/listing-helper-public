import json

import pytest
from pydantic import ValidationError

from app.models import (
    GeneratedListingDraft,
    ListingDraft,
    PackageDimensions,
    PackageWeight,
    PricingGuidance,
)
from tests.support import listing_draft


def test_listing_title_allows_ebay_boundary() -> None:
    data = listing_draft().model_dump()
    data["title"] = "x" * 80

    assert len(ListingDraft.model_validate(data).title) == 80


def test_listing_title_rejects_more_than_80_characters() -> None:
    data = listing_draft().model_dump()
    data["title"] = "x" * 81

    with pytest.raises(ValidationError):
        ListingDraft.model_validate(data)


def test_pricing_range_must_be_ordered() -> None:
    with pytest.raises(ValidationError):
        PricingGuidance(
            suggested_price=20,
            expected_sale_price_low=30,
            expected_sale_price_high=10,
            currency="USD",
            confidence="low",
            rationale="Limited evidence.",
        )


def test_openai_wire_schema_omits_unsupported_string_length_keywords() -> None:
    schema = json.dumps(GeneratedListingDraft.model_json_schema())

    assert "minLength" not in schema
    assert "maxLength" not in schema


def test_listing_draft_allows_condition_to_be_omitted_when_unsupported() -> None:
    data = listing_draft().model_dump()
    data["condition"] = ""
    data["condition_description"] = ""
    data["missing_facts"] = ["Condition"]

    draft = ListingDraft.model_validate(data)

    assert draft.condition == ""
    assert draft.condition_description == ""
    assert draft.missing_facts == ["Condition"]


def test_package_weight_requires_positive_pounds_or_ounces() -> None:
    with pytest.raises(ValidationError):
        PackageWeight(pounds=0, ounces=0)

    with pytest.raises(ValidationError):
        PackageWeight(pounds=1, ounces=16)

    assert PackageWeight(pounds=0, ounces=8).model_dump() == {
        "pounds": 0,
        "ounces": 8,
    }


def test_package_dimensions_require_positive_whole_inches() -> None:
    with pytest.raises(ValidationError):
        PackageDimensions(length=0, width=8, height=3)

    with pytest.raises(ValidationError):
        PackageDimensions(length=12.5, width=8, height=3)

    assert PackageDimensions(length=12, width=8, height=3).model_dump() == {
        "length": 12,
        "width": 8,
        "height": 3,
    }
