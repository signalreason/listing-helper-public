"""Category-aware validation for eBay item specifics."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.ebay_models import CategoryRequirements, EbayAspect, EbayItemSpecific

MAX_ITEM_SPECIFICS = 45
MAX_VALUES_PER_ASPECT = 30
DEFAULT_VALUE_MAX_LENGTH = 65
SUPPORTED_MODES = {"free_text", "selection"}
SUPPORTED_CARDINALITIES = {"single", "multi"}
SUPPORTED_DATA_TYPES = {"string", "number", "date"}
SUPPORTED_USAGES = {"optional", "recommended"}
SUPPORTED_ADVANCED_DATA_TYPES = {None, "numeric_range"}
NUMERIC_RANGE = re.compile(
    r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*-\s*"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*$"
)


@dataclass(frozen=True)
class ItemSpecificError:
    aspect: str
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"aspect": self.aspect, "code": self.code, "message": self.message}


@dataclass(frozen=True)
class ItemSpecificValidation:
    specifics: list[EbayItemSpecific]
    errors: list[ItemSpecificError]


def validate_item_specifics(
    specifics: list[EbayItemSpecific], requirements: CategoryRequirements
) -> ItemSpecificValidation:
    """Return canonical item specifics and every category validation error."""
    errors: list[ItemSpecificError] = []
    aspects = {aspect.name.casefold(): aspect for aspect in requirements.aspects}
    normalized: list[EbayItemSpecific] = []

    if len(specifics) > MAX_ITEM_SPECIFICS:
        errors.append(
            ItemSpecificError(
                "Item specifics",
                "too_many_aspects",
                f"Use no more than {MAX_ITEM_SPECIFICS} item specifics.",
            )
        )

    canonical_groups: dict[str, list[EbayItemSpecific]] = {}
    for specific in specifics:
        aspect = aspects.get(specific.name.strip().casefold())
        canonical_name = aspect.name if aspect else specific.name.strip()
        clean_values = [value.strip() for value in specific.values]
        clean = EbayItemSpecific(name=canonical_name, values=clean_values)
        normalized.append(clean)
        canonical_groups.setdefault(canonical_name.casefold(), []).append(clean)

    for group in canonical_groups.values():
        if len(group) > 1:
            errors.append(
                ItemSpecificError(
                    group[0].name,
                    "duplicate_aspect",
                    f"Use {group[0].name} only once.",
                )
            )

    values_by_aspect = {
        name: [value for item in group for value in item.values if value]
        for name, group in canonical_groups.items()
    }

    for aspect in requirements.aspects:
        errors.extend(_unsupported_rule_errors(aspect))

    for specific in normalized:
        aspect = aspects.get(specific.name.casefold())
        if aspect is None:
            errors.append(
                ItemSpecificError(
                    specific.name,
                    "unknown_aspect",
                    f"Remove {specific.name}. eBay does not list it for this category.",
                )
            )
            continue
        errors.extend(_validate_specific(specific, aspect, values_by_aspect))

    for aspect in requirements.aspects:
        if aspect.required and aspect.name.casefold() not in canonical_groups:
            errors.append(ItemSpecificError(aspect.name, "required", f"Enter {aspect.name}."))

    return ItemSpecificValidation(specifics=normalized, errors=_unique_errors(errors))


def _unsupported_rule_errors(aspect: EbayAspect) -> list[ItemSpecificError]:
    errors = []
    unsupported = None
    if aspect.mode not in SUPPORTED_MODES:
        unsupported = f"mode {aspect.mode}"
    elif aspect.cardinality not in SUPPORTED_CARDINALITIES:
        unsupported = f"cardinality {aspect.cardinality}"
    elif aspect.data_type not in SUPPORTED_DATA_TYPES:
        unsupported = f"data type {aspect.data_type}"
    elif aspect.usage not in SUPPORTED_USAGES:
        unsupported = f"usage {aspect.usage}"
    elif aspect.advanced_data_type not in SUPPORTED_ADVANCED_DATA_TYPES:
        unsupported = f"advanced data type {aspect.advanced_data_type}"
    elif aspect.mode == "selection" and not aspect.values:
        unsupported = "selection rule without allowed values"
    elif not _format_is_supported(aspect):
        unsupported = f"format {aspect.format or '(missing)'}"
    elif any(item not in {"item", "product"} for item in aspect.applicable_to):
        unsupported = f"applicability {', '.join(aspect.applicable_to)}"
    if unsupported:
        errors.append(
            ItemSpecificError(
                aspect.name,
                "unsupported_rule",
                f"eBay returned an unsupported {unsupported} rule for {aspect.name}.",
            )
        )
    return errors


def _validate_specific(
    specific: EbayItemSpecific,
    aspect: EbayAspect,
    values_by_aspect: dict[str, list[str]],
) -> list[ItemSpecificError]:
    errors: list[ItemSpecificError] = []
    if not specific.values or any(not value for value in specific.values):
        errors.append(
            ItemSpecificError(aspect.name, "empty_value", f"Enter a value for {aspect.name}.")
        )
    if len(specific.values) > MAX_VALUES_PER_ASPECT:
        errors.append(
            ItemSpecificError(
                aspect.name,
                "too_many_values",
                f"Use no more than {MAX_VALUES_PER_ASPECT} values for {aspect.name}.",
            )
        )
    if aspect.cardinality == "single" and len(specific.values) > 1:
        errors.append(
            ItemSpecificError(
                aspect.name,
                "wrong_cardinality",
                f"Use one value for {aspect.name}.",
            )
        )
    if len(specific.values) != len(set(specific.values)):
        errors.append(
            ItemSpecificError(
                aspect.name,
                "duplicate_value",
                f"Do not repeat a value for {aspect.name}.",
            )
        )

    max_length = aspect.max_length or DEFAULT_VALUE_MAX_LENGTH
    allowed = {item.value: item for item in aspect.values}
    for value in specific.values:
        if not value:
            continue
        if len(value) > max_length:
            errors.append(
                ItemSpecificError(
                    aspect.name,
                    "value_too_long",
                    f"Keep each {aspect.name} value at {max_length} characters or fewer.",
                )
            )
        if aspect.mode == "selection" and value not in allowed:
            errors.append(
                ItemSpecificError(
                    aspect.name,
                    "invalid_value",
                    f"Choose an allowed value for {aspect.name}.",
                )
            )
            continue
        selected = allowed.get(value)
        if (
            selected
            and selected.constraints
            and not all(
                set(values_by_aspect.get(constraint.aspect_name.casefold(), []))
                & set(constraint.values)
                for constraint in selected.constraints
            )
        ):
            errors.append(
                ItemSpecificError(
                    aspect.name,
                    "value_constraint",
                    f"{value} is not available with the selected related item specifics.",
                )
            )
        if not _value_matches_format(value, aspect):
            errors.append(
                ItemSpecificError(
                    aspect.name,
                    "invalid_format",
                    f"Enter {aspect.name} in eBay's required "
                    f"{aspect.format or aspect.data_type} format.",
                )
            )
    return errors


def _format_is_supported(aspect: EbayAspect) -> bool:
    if aspect.advanced_data_type == "numeric_range":
        return True
    if aspect.data_type == "string":
        return aspect.format is None
    if aspect.data_type == "number":
        return aspect.format in {None, "int32", "double"}
    if aspect.data_type == "date":
        return aspect.format in {"YYYY", "YYYYMM", "YYYYMMDD"}
    return False


def _value_matches_format(value: str, aspect: EbayAspect) -> bool:
    if aspect.advanced_data_type == "numeric_range":
        match = NUMERIC_RANGE.fullmatch(value)
        if not match:
            return False
        return Decimal(match.group(1)) <= Decimal(match.group(2))
    if aspect.data_type == "string":
        return True
    if aspect.data_type == "number":
        try:
            number = Decimal(value)
        except InvalidOperation:
            return False
        if not number.is_finite():
            return False
        if aspect.format == "int32":
            return number == number.to_integral_value() and -(2**31) <= number < 2**31
        return True
    if aspect.data_type == "date" and aspect.format:
        formats = {"YYYY": "%Y", "YYYYMM": "%Y%m", "YYYYMMDD": "%Y%m%d"}
        expected_lengths = {"YYYY": 4, "YYYYMM": 6, "YYYYMMDD": 8}
        if len(value) != expected_lengths[aspect.format] or not value.isdigit():
            return False
        try:
            datetime.strptime(value, formats[aspect.format])
        except ValueError:
            return False
        return True
    return False


def _unique_errors(errors: list[ItemSpecificError]) -> list[ItemSpecificError]:
    seen: set[tuple[str, str, str]] = set()
    result = []
    for error in errors:
        key = (error.aspect, error.code, error.message)
        if key not in seen:
            seen.add(key)
            result.append(error)
    return result


def generatable_aspects(requirements: CategoryRequirements) -> list[EbayAspect]:
    """Return only seller-set aspects whose rules this app can check."""
    return [aspect for aspect in requirements.aspects if not _unsupported_rule_errors(aspect)]
