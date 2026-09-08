import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from openai import OpenAIError

from app.generation import (
    DEFAULT_OPENAI_MODEL,
    IMAGE_DETAIL,
    SYSTEM_PROMPT,
    GenerationFailed,
    GenerationUnavailable,
    OpenAIListingGenerator,
)
from app.images import PreparedImage
from app.models import DraftEbayCategory, GeneratedListingDraft
from tests.support import GENERATION_CATEGORY, StubEbayGateway, jpeg_bytes, listing_draft


def category_args():
    return (
        DraftEbayCategory(**GENERATION_CATEGORY),
        StubEbayGateway().category_requirements("175786"),
    )


def prepared_image(tmp_path: Path) -> PreparedImage:
    path = tmp_path / "photo.jpg"
    path.write_bytes(jpeg_bytes())
    return PreparedImage(
        path=path,
        mime_type="image/jpeg",
        original_format="JPEG",
        width=800,
        height=800,
    )


def test_key_is_read_only_when_generation_starts(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    generator = OpenAIListingGenerator()

    with pytest.raises(GenerationUnavailable):
        generator.generate([prepared_image(tmp_path)], "", *category_args())


def test_known_good_generation_baseline_is_locked() -> None:
    assert DEFAULT_OPENAI_MODEL == "gpt-5.5"
    assert IMAGE_DETAIL == "high"


def test_condition_prompt_requires_direct_supported_seller_copy() -> None:
    prompt = " ".join(SYSTEM_PROMPT.lower().split())

    assert "state supported condition facts directly" in prompt
    assert "do not attribute them to seller notes" in prompt
    assert "lint, loose fibers, dust, wrinkles, creases, folds" in prompt
    assert "never infer an unsupported condition" in prompt
    assert "return empty strings for condition and condition_description" in prompt


def test_responses_api_receives_structured_multimodal_request(monkeypatch, tmp_path) -> None:
    captured: dict = {}

    class FakeResponses:
        def parse(self, **kwargs):
            captured["request"] = kwargs
            generated = GeneratedListingDraft.model_validate(
                listing_draft().model_dump(
                    exclude={"package_weight", "package_dimensions", "generation_required"}
                )
            )
            return SimpleNamespace(output_parsed=generated)

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.responses = FakeResponses()

    monkeypatch.setenv("OPENAI_API_KEY", "test-only-secret")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.setattr("app.generation.OpenAI", FakeClient)

    result = OpenAIListingGenerator().generate(
        [prepared_image(tmp_path)], "Chest: 40 inches", *category_args()
    )

    assert result.title == listing_draft().title
    assert captured["client"]["api_key"] == "test-only-secret"
    assert captured["request"]["model"] == DEFAULT_OPENAI_MODEL
    assert captured["request"]["text_format"].__name__ == "CategoryListingDraft"
    assert captured["request"]["store"] is False
    category_rules = json.loads(captured["request"]["input"][1]["content"].split("\n", 1)[1])
    assert category_rules["category_id"] == "175786"
    assert category_rules["aspects"] == [
        {**aspect.model_dump(mode="json"), "max_length": aspect.max_length or 65}
        for aspect in category_args()[1].aspects
    ]

    user_content = captured["request"]["input"][2]["content"]
    assert user_content[0]["type"] == "input_text"
    assert "Chest: 40 inches" in user_content[0]["text"]
    assert user_content[1]["detail"] == IMAGE_DETAIL
    assert user_content[1]["image_url"].startswith("data:image/jpeg;base64,")


def test_openai_error_is_replaced_with_safe_domain_error(monkeypatch, tmp_path) -> None:
    class FailingResponses:
        def parse(self, **_kwargs):
            raise OpenAIError("test-only-secret should never escape")

    class FakeClient:
        def __init__(self, **_kwargs):
            self.responses = FailingResponses()

    monkeypatch.setenv("OPENAI_API_KEY", "test-only-secret")
    monkeypatch.setattr("app.generation.OpenAI", FakeClient)

    with pytest.raises(GenerationFailed, match="OpenAI generation failed") as error:
        OpenAIListingGenerator().generate([prepared_image(tmp_path)], "", *category_args())

    assert "test-only-secret" not in str(error.value)


def test_missing_parsed_output_fails_closed(monkeypatch, tmp_path) -> None:
    class EmptyResponses:
        def parse(self, **_kwargs):
            return SimpleNamespace(output_parsed=None)

    class FakeClient:
        def __init__(self, **_kwargs):
            self.responses = EmptyResponses()

    monkeypatch.setenv("OPENAI_API_KEY", "test-only-secret")
    monkeypatch.setattr("app.generation.OpenAI", FakeClient)

    with pytest.raises(GenerationFailed, match="no structured listing"):
        OpenAIListingGenerator().generate([prepared_image(tmp_path)], "", *category_args())


def test_malformed_parsed_output_fails_closed(monkeypatch, tmp_path) -> None:
    class InvalidResponses:
        def parse(self, **_kwargs):
            invalid = GeneratedListingDraft.model_validate(
                listing_draft().model_dump(
                    exclude={"package_weight", "package_dimensions", "generation_required"}
                )
            )
            invalid.title = "x" * 81
            return SimpleNamespace(output_parsed=invalid)

    class FakeClient:
        def __init__(self, **_kwargs):
            self.responses = InvalidResponses()

    monkeypatch.setenv("OPENAI_API_KEY", "test-only-secret")
    monkeypatch.setattr("app.generation.OpenAI", FakeClient)

    with pytest.raises(GenerationFailed, match="invalid structured listing"):
        OpenAIListingGenerator().generate([prepared_image(tmp_path)], "", *category_args())


def test_oversized_generated_specific_does_not_discard_the_draft(monkeypatch, tmp_path):
    class FakeResponses:
        def parse(self, **_kwargs):
            data = listing_draft().model_dump(
                exclude={"package_weight", "package_dimensions", "generation_required"}
            )
            data["item_specifics"][0]["values"] = ["x" * 801]
            return SimpleNamespace(output_parsed=GeneratedListingDraft.model_validate(data))

    class FakeClient:
        def __init__(self, **_kwargs):
            self.responses = FakeResponses()

    monkeypatch.setenv("OPENAI_API_KEY", "test-only-secret")
    monkeypatch.setattr("app.generation.OpenAI", FakeClient)
    result = OpenAIListingGenerator().generate([prepared_image(tmp_path)], "", *category_args())
    assert result.title == listing_draft().title
    assert [item.name for item in result.item_specifics] == ["Size"]
