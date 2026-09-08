"""Category selection and generated-specific validation use no external services."""

import json
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from app.ebay_api import EbayApiError
from app.ebay_models import CategoryRequirements, EbayAspect, EbayItemSpecific
from app.generation import (
    CategorySchemaUnsupported,
    category_output_model,
    clean_generated_specifics,
)
from app.item_specifics import validate_item_specifics
from app.models import ItemSpecific
from tests.support import (
    GENERATION_CATEGORY,
    StubEbayGateway,
    StubGenerator,
    authenticated_client,
    jpeg_bytes,
    listing_draft,
)


def rules(*aspects):
    return CategoryRequirements(
        category={key: value for key, value in GENERATION_CATEGORY.items() if key != "group_id"},
        conditions=[],
        aspects=list(aspects),
    )


def specific(name, *values):
    return ItemSpecific(name=name, values=list(values), source="photo", confidence="high")


def test_schema_uses_only_supported_item_names_and_value_arrays():
    requirements = rules(
        EbayAspect(name="Brand"),
        EbayAspect(name="Material", cardinality="multi"),
        EbayAspect(name="Catalog", applicable_to=["product"]),
        EbayAspect(name="Future", mode="future"),
    )
    model = category_output_model(requirements)
    draft = listing_draft().model_dump(
        exclude={"package_weight", "package_dimensions", "generation_required"}
    )
    draft["item_specifics"] = [specific("Material", "Cotton", "Wool").model_dump()]
    assert model.model_validate(draft).item_specifics[0].values == ["Cotton", "Wool"]
    draft["item_specifics"][0]["name"] = "Catalog"
    assert model.model_validate(draft).item_specifics[0].name == "Catalog"
    for name in ["Unknown", "Future"]:
        draft["item_specifics"][0]["name"] = name
        with pytest.raises(ValidationError):
            model.model_validate(draft)


def test_no_supported_aspects_requires_empty_specifics():
    model = category_output_model(rules(EbayAspect(name="Future", mode="future")))
    draft = listing_draft().model_dump(
        exclude={"package_weight", "package_dimensions", "generation_required"}
    )
    with pytest.raises(ValidationError):
        model.model_validate(draft)
    draft["item_specifics"] = []
    assert model.model_validate(draft).item_specifics == []


def test_schema_rejects_excessive_enum_count_and_string_length():
    for names in [
        [f"Aspect {index}" for index in range(981)],
        [f"{index:03}" + "a" * 62 for index in range(251)],
    ]:
        with pytest.raises(CategorySchemaUnsupported):
            category_output_model(rules(*(EbayAspect(name=name) for name in names)))


def test_cleanup_keeps_valid_data_and_removes_all_invalid_specifics():
    requirements = rules(
        EbayAspect(name="Brand", required=True),
        EbayAspect(name="Size", required=True, mode="selection", values=["M"]),
        EbayAspect(
            name="Material", cardinality="multi", mode="selection", values=["Cotton", "Wool"]
        ),
        EbayAspect(name="Count", data_type="number", format="int32"),
        EbayAspect(name="Short", max_length=3),
        EbayAspect(name="Duplicate"),
        EbayAspect(name="Single"),
        EbayAspect(name="Catalog", applicable_to=["product"]),
    )
    original = listing_draft().model_copy(
        update={
            "item_specifics": [
                specific(" brand ", " Everlane "),
                specific("Size", "medium"),
                specific("Material", "Cotton", "Wool"),
                specific("Count", "several"),
                specific("Short", "long"),
                specific("Duplicate", "A"),
                specific("duplicate", "B"),
                specific("Unknown", "X"),
                specific("Single", "A", "B"),
                specific("Catalog", "Y"),
            ],
            "missing_facts": ["Enter Size.", "Enter Size."],
        }
    )
    cleaned = clean_generated_specifics(original, requirements)
    assert [(item.name, item.values) for item in cleaned.item_specifics] == [
        ("Brand", ["Everlane"]),
        ("Material", ["Cotton", "Wool"]),
        ("Catalog", ["Y"]),
    ]
    assert cleaned.item_specifics[0].source == "photo"
    assert cleaned.missing_facts == ["Enter Size."]
    assert len(original.item_specifics) == 10  # Never mutate seller data through aliasing.
    result = validate_item_specifics(
        [EbayItemSpecific(name=item.name, values=item.values) for item in cleaned.item_specifics],
        requirements,
    )
    assert [error.code for error in result.errors] == ["required"]


def test_cleanup_rechecks_dependencies_after_removing_an_invalid_control():
    requirements = rules(
        EbayAspect(name="A", max_length=1),
        EbayAspect(
            name="B",
            mode="selection",
            values=[
                {
                    "value": "b",
                    "constraints": [{"aspect_name": "A", "values": ["invalid"]}],
                }
            ],
        ),
        EbayAspect(
            name="C",
            required=True,
            mode="selection",
            values=[
                {
                    "value": "c",
                    "constraints": [{"aspect_name": "B", "values": ["b"]}],
                }
            ],
        ),
    )
    draft = listing_draft().model_copy(
        update={
            "item_specifics": [
                specific("A", "invalid"),
                specific("B", "b"),
                specific("C", "c"),
            ]
        }
    )
    cleaned = clean_generated_specifics(draft, requirements)
    assert cleaned.item_specifics == []
    assert "Enter C." in cleaned.missing_facts


def test_missing_required_facts_are_not_lost_at_the_draft_message_limit():
    requirements = rules(
        *(EbayAspect(name=f"Required {index}", required=True) for index in range(25))
    )
    cleaned = clean_generated_specifics(listing_draft(), requirements)
    assert len(cleaned.missing_facts) <= 20
    assert all(f"Enter Required {index}." in " ".join(cleaned.missing_facts) for index in range(25))


@pytest.mark.parametrize("category", [None, "not json", "{}", '{"category_id":"bad"}'])
def test_invalid_category_never_starts_generation_or_consumes_quota(category):
    generator = StubGenerator()
    client, _, _ = authenticated_client(generator)
    client.app.state.generation_limits.start = Mock(side_effect=AssertionError("quota consumed"))
    client.app.state.ebay_gateway.category_requirements = Mock(side_effect=AssertionError("lookup"))
    response = client.post(
        "/api/listings/generate",
        data={} if category is None else {"ebay_category": category},
        files={"photos": ("item.jpg", jpeg_bytes(), "image/jpeg")},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_category"
    assert generator.calls == []


@pytest.mark.parametrize("oversize", [False, True])
def test_category_lookup_and_schema_failures_do_not_consume_quota(oversize):
    generator = StubGenerator()
    gateway = StubEbayGateway()
    if oversize:
        gateway.category_requirements = Mock(
            return_value=rules(*(EbayAspect(name=f"Aspect {index}") for index in range(981)))
        )
    else:
        gateway.category_requirements = Mock(side_effect=EbayApiError("unavailable"))
    client, _, _ = authenticated_client(generator, ebay_gateway=gateway)
    client.app.state.generation_limits.start = Mock(side_effect=AssertionError("quota consumed"))
    response = client.post(
        "/api/listings/generate",
        data={"ebay_category": json.dumps(GENERATION_CATEGORY)},
        files={"photos": ("item.jpg", jpeg_bytes(), "image/jpeg")},
    )
    assert response.status_code == (422 if oversize else 502)
    assert generator.calls == []


def test_selected_category_reaches_generator_and_saved_response_with_clean_specifics():
    draft = listing_draft().model_copy(
        update={
            "item_specifics": [
                specific("Brand", "Everlane"),
                specific("Size", "Medium"),
                specific("Unknown", "X"),
            ]
        }
    )
    generator = StubGenerator(draft)
    client, _, user = authenticated_client(generator)
    response = client.post(
        "/api/listings/generate",
        data={"ebay_category": json.dumps(GENERATION_CATEGORY)},
        files={"photos": ("item.jpg", jpeg_bytes(), "image/jpeg")},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ebay_category"] == GENERATION_CATEGORY
    assert generator.category.model_dump() == GENERATION_CATEGORY
    assert generator.requirements.category.category_id == GENERATION_CATEGORY["category_id"]
    assert payload["draft"]["suggested_category"] == "Sweaters"
    assert [item["name"] for item in payload["draft"]["item_specifics"]] == ["Brand"]
    assert "Enter Size." in payload["draft"]["missing_facts"]
    stored = client.app.state.ebay_store.get_draft(user.id, payload["draft_id"])
    assert stored.ebay_category == GENERATION_CATEGORY
    assert (
        client.get(f"/api/drafts/{payload['draft_id']}").json()["ebay_category"]
        == GENERATION_CATEGORY
    )


@pytest.mark.parametrize("group_index,group", list(enumerate(["260012", "260010", "171146"])))
@pytest.mark.parametrize(
    "kind_index,kind,value",
    [
        (0, "Clothing", "M"),
        (1, "Shoes", "9"),
        (2, "Bags", "Medium"),
        (3, "Accessories", "One size"),
    ],
)
def test_inventory_rules_control_the_model_and_returned_draft(
    monkeypatch, group_index, group, kind_index, kind, value
):
    from types import SimpleNamespace

    from app.generation import OpenAIListingGenerator
    from app.models import GeneratedListingDraft

    category = {
        "category_id": f"98{group_index}{kind_index}",
        "name": kind,
        "path": f"{group} > {kind}",
        "group_id": group,
    }
    size_name = f"{kind} size"
    requirements = rules(
        EbayAspect(name="Brand", required=True),
        EbayAspect(name=size_name, required=True, mode="selection", values=[value]),
        EbayAspect(name="Color", cardinality="multi", mode="selection", values=["Blue", "Green"]),
        EbayAspect(name="Required fact", required=True),
    )
    requirements.category = requirements.category.model_copy(
        update={key: item for key, item in category.items() if key != "group_id"}
    )
    gateway = StubEbayGateway()
    gateway.category_requirements = Mock(return_value=requirements)
    requests = []

    class FakeResponses:
        def parse(self, **kwargs):
            requests.append(kwargs)
            data = listing_draft().model_dump(
                exclude={"package_weight", "package_dimensions", "generation_required"}
            )
            data["item_specifics"] = [
                specific("Brand", "Test brand").model_dump(),
                specific(size_name, value)
                .model_copy(update={"source": "seller_note"})
                .model_dump(),
                specific("Color", "Blue", "Green").model_dump(),
                specific("Prior category field", "Invalid").model_dump(),
                specific("Required fact", "Guessed")
                .model_copy(update={"source": "unknown"})
                .model_dump(),
            ]
            return SimpleNamespace(output_parsed=GeneratedListingDraft.model_validate(data))

    class FakeClient:
        def __init__(self, **_kwargs):
            self.responses = FakeResponses()

    monkeypatch.setenv("OPENAI_API_KEY", "fake-test-key")
    monkeypatch.setattr("app.generation.OpenAI", FakeClient)
    client, _, user = authenticated_client(OpenAIListingGenerator(), ebay_gateway=gateway)
    response = client.post(
        "/api/listings/generate",
        data={"ebay_category": json.dumps(category), "notes": f"{size_name}: {value}"},
        files={"photos": ("test.jpg", jpeg_bytes(), "image/jpeg")},
    )
    assert response.status_code == 200
    gateway.category_requirements.assert_called_once_with(category["category_id"])
    assert len(requests) == 1
    model_rules = json.loads(requests[0]["input"][1]["content"].split("\n", 1)[1])
    assert model_rules["category_id"] == category["category_id"]
    assert [aspect["name"] for aspect in model_rules["aspects"]] == [
        "Brand",
        size_name,
        "Color",
        "Required fact",
    ]
    payload = response.json()
    assert [(item["name"], item["values"]) for item in payload["draft"]["item_specifics"]] == [
        ("Brand", ["Test brand"]),
        (size_name, [value]),
        ("Color", ["Blue", "Green"]),
    ]
    assert "Enter Required fact." in payload["draft"]["missing_facts"]
    assert (
        client.app.state.ebay_store.get_draft(user.id, payload["draft_id"]).draft
        == payload["draft"]
    )


@pytest.mark.parametrize(
    "aspect,values",
    [
        (EbayAspect(name="Test", cardinality="multi"), ["same", "same"]),
        (EbayAspect(name="Test", cardinality="multi"), [str(index) for index in range(31)]),
        (EbayAspect(name="Test"), ["x" * 66]),
        (EbayAspect(name="Test", data_type="date", format="YYYYMMDD"), ["20260230"]),
        (EbayAspect(name="Test", data_type="number", format="int32"), ["2147483648"]),
        (EbayAspect(name="Test", data_type="number", format="double"), ["NaN"]),
        (EbayAspect(name="Test", advanced_data_type="numeric_range"), ["9-3"]),
    ],
)
def test_generated_invalid_value_boundaries_are_omitted(aspect, values):
    requirements = rules(aspect)
    draft = listing_draft().model_copy(update={"item_specifics": [specific("Test", *values)]})
    assert clean_generated_specifics(draft, requirements).item_specifics == []


def test_unknown_control_fact_removes_dependent_generated_value():
    requirements = rules(
        EbayAspect(name="Control"),
        EbayAspect(
            name="Dependent",
            required=True,
            mode="selection",
            values=[
                {"value": "Yes", "constraints": [{"aspect_name": "Control", "values": ["Guess"]}]}
            ],
        ),
    )
    draft = listing_draft().model_copy(
        update={
            "item_specifics": [
                specific("Control", "Guess").model_copy(update={"source": "unknown"}),
                specific("Dependent", "Yes"),
            ]
        }
    )
    cleaned = clean_generated_specifics(draft, requirements)
    assert cleaned.item_specifics == []
    assert "Enter Dependent." in cleaned.missing_facts
