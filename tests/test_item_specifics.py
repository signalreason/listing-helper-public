import pytest

from app.ebay_models import (
    CategoryRequirements,
    EbayAspect,
    EbayAspectValue,
    EbayAspectValueConstraint,
    EbayCategory,
    EbayItemSpecific,
)
from app.item_specifics import validate_item_specifics


def requirements(*aspects: EbayAspect) -> CategoryRequirements:
    return CategoryRequirements(
        category=EbayCategory(category_id="3001", name="Test", path="Test"),
        conditions=[],
        aspects=list(aspects),
    )


def error_codes(result) -> set[str]:
    return {error.code for error in result.errors}


def test_validation_normalizes_names_and_accepts_valid_multi_and_conditional_values() -> None:
    rules = requirements(
        EbayAspect(name="Metal", values=["Gold", "Silver"], mode="selection"),
        EbayAspect(
            name="Metal Purity",
            values=[
                EbayAspectValue(
                    value="10k",
                    constraints=[EbayAspectValueConstraint(aspect_name="Metal", values=["Gold"])],
                )
            ],
            mode="selection",
        ),
        EbayAspect(
            name="Material",
            values=["Cotton", "Wool"],
            mode="selection",
            cardinality="multi",
        ),
    )

    result = validate_item_specifics(
        [
            EbayItemSpecific(name="metal", values=["Gold"]),
            EbayItemSpecific(name="Metal Purity", values=["10k"]),
            EbayItemSpecific(name="material", values=["Cotton", "Wool"]),
        ],
        rules,
    )

    assert result.errors == []
    assert [item.name for item in result.specifics] == ["Metal", "Metal Purity", "Material"]
    assert result.specifics[2].values == ["Cotton", "Wool"]


def test_every_conditional_dependency_must_match() -> None:
    rules = requirements(
        EbayAspect(name="Metal", values=["Gold"], mode="selection"),
        EbayAspect(name="Department", values=["Women"], mode="selection"),
        EbayAspect(
            name="Metal Purity",
            values=[
                EbayAspectValue(
                    value="10k",
                    constraints=[
                        EbayAspectValueConstraint(aspect_name="Metal", values=["Gold"]),
                        EbayAspectValueConstraint(aspect_name="Department", values=["Women"]),
                    ],
                )
            ],
            mode="selection",
        ),
    )

    incomplete = validate_item_specifics(
        [
            EbayItemSpecific(name="Metal", values=["Gold"]),
            EbayItemSpecific(name="Metal Purity", values=["10k"]),
        ],
        rules,
    )
    complete = validate_item_specifics(
        [
            EbayItemSpecific(name="Metal", values=["Gold"]),
            EbayItemSpecific(name="Department", values=["Women"]),
            EbayItemSpecific(name="Metal Purity", values=["10k"]),
        ],
        rules,
    )

    assert error_codes(incomplete) == {"value_constraint"}
    assert complete.errors == []


def test_validation_reports_all_common_category_errors_together() -> None:
    rules = requirements(
        EbayAspect(name="Brand", required=True, max_length=5),
        EbayAspect(name="Size", values=["S", "M"], mode="selection"),
        EbayAspect(name="Material", cardinality="single"),
        EbayAspect(name="Metal", values=["Gold"], mode="selection"),
        EbayAspect(
            name="Metal Purity",
            values=[
                EbayAspectValue(
                    value="10k",
                    constraints=[EbayAspectValueConstraint(aspect_name="Metal", values=["Gold"])],
                )
            ],
            mode="selection",
        ),
    )
    result = validate_item_specifics(
        [
            EbayItemSpecific(name="Size", values=["s"]),
            EbayItemSpecific(name="Material", values=["", "Wool"]),
            EbayItemSpecific(name="material", values=["Cotton"]),
            EbayItemSpecific(name="Metal Purity", values=["10k"]),
            EbayItemSpecific(name="Seller field", values=["value"]),
        ],
        rules,
    )

    assert error_codes(result) == {
        "required",
        "invalid_value",
        "wrong_cardinality",
        "duplicate_aspect",
        "empty_value",
        "value_constraint",
        "unknown_aspect",
    }


def test_validation_enforces_lengths_counts_duplicates_and_formats() -> None:
    rules = requirements(
        EbayAspect(name="Brand", max_length=5),
        EbayAspect(name="Year", data_type="date", format="YYYY"),
        EbayAspect(name="Count", data_type="number", format="int32"),
        EbayAspect(name="Range", advanced_data_type="numeric_range"),
        EbayAspect(name="Tags", cardinality="multi"),
    )
    specifics = [
        EbayItemSpecific(name="Brand", values=["Longer"]),
        EbayItemSpecific(name="Year", values=["202A"]),
        EbayItemSpecific(name="Count", values=["1.5"]),
        EbayItemSpecific(name="Range", values=["20-10"]),
        EbayItemSpecific(name="Tags", values=["same", "same", *map(str, range(29))]),
        *[EbayItemSpecific(name=f"Unknown {number}", values=["value"]) for number in range(41)],
    ]

    result = validate_item_specifics(specifics, rules)

    assert {
        "too_many_aspects",
        "too_many_values",
        "duplicate_value",
        "value_too_long",
        "invalid_format",
    } <= error_codes(result)


@pytest.mark.parametrize("applicable_to", [["item"], ["product"], ["item", "product"]])
def test_supported_applicability_values_do_not_block(applicable_to: list[str]) -> None:
    rules = requirements(EbayAspect(name="Brand", applicable_to=applicable_to, required=True))

    result = validate_item_specifics([EbayItemSpecific(name="Brand", values=["Example"])], rules)

    assert result.errors == []


def test_missing_required_product_aspect_uses_standard_required_error() -> None:
    rules = requirements(EbayAspect(name="Brand", applicable_to=["product"], required=True))

    result = validate_item_specifics([], rules)

    assert [error.as_dict() for error in result.errors] == [
        {"aspect": "Brand", "code": "required", "message": "Enter Brand."}
    ]


def test_present_empty_required_aspect_reports_one_error() -> None:
    rules = requirements(EbayAspect(name="Inseam", required=True))

    result = validate_item_specifics([EbayItemSpecific(name="Inseam", values=[""])], rules)

    assert [error.as_dict() for error in result.errors] == [
        {
            "aspect": "Inseam",
            "code": "empty_value",
            "message": "Enter a value for Inseam.",
        }
    ]


def test_validation_blocks_unknown_taxonomy_rules() -> None:
    rules = requirements(
        EbayAspect(name="Future mode", mode="A_NEW_MODE"),
        EbayAspect(name="Future applicability", applicable_to=["listing"]),
    )

    result = validate_item_specifics([], rules)

    assert error_codes(result) == {"unsupported_rule"}


def test_recommended_and_future_required_aspects_do_not_block() -> None:
    rules = requirements(
        EbayAspect(name="Color", recommended=True, usage="recommended"),
        EbayAspect(name="Model", expected_required_by_date="2027-01-01"),
    )

    result = validate_item_specifics([], rules)

    assert result.errors == []


def test_default_value_limit_is_65_characters() -> None:
    rules = requirements(EbayAspect(name="Brand"))

    result = validate_item_specifics([EbayItemSpecific(name="Brand", values=["x" * 66])], rules)

    assert error_codes(result) == {"value_too_long"}
