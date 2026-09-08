"""Replaceable Trading API XML adapter for live listing operations."""

from __future__ import annotations

import hashlib
import os
import uuid
import xml.etree.ElementTree as ET
from copy import deepcopy

import httpx
from pydantic import ValidationError

from app.ebay_api import EbayApiError
from app.ebay_models import (
    BusinessPolicies,
    EbayIssue,
    EbayItemSpecific,
    EbayReadyListing,
    TradingResult,
)
from app.models import PackageDimensions, PackageWeight

NS = "urn:ebay:apis:eBLBaseComponents"
ET.register_namespace("", NS)


def _q(name: str) -> str:
    return f"{{{NS}}}{name}"


class TradingApiAdapter:
    def __init__(self, *, environment: str | None = None, timeout: float = 30) -> None:
        self.environment = environment or os.environ.get("EBAY_ENVIRONMENT", "sandbox")
        self.timeout = timeout

    @property
    def endpoint(self) -> str:
        if self.environment == "sandbox":
            return "https://api.sandbox.ebay.com/ws/api.dll"
        return "https://api.ebay.com/ws/api.dll"

    def verify(
        self,
        token: str,
        listing: EbayReadyListing,
        custom_label: str,
        image_urls: list[str],
    ) -> TradingResult:
        item = self._new_item(listing, custom_label, image_urls)
        return self._call("VerifyAddFixedPriceItem", item, token)

    def publish(
        self,
        token: str,
        listing: EbayReadyListing,
        custom_label: str,
        image_urls: list[str],
        request_id: str,
    ) -> TradingResult:
        item = self._new_item(listing, custom_label, image_urls)
        ET.SubElement(item, _q("UUID")).text = uuid.UUID(request_id).hex
        return self._call("AddFixedPriceItem", item, token)

    def get(self, token: str, item_id: str) -> TradingResult:
        body = ET.Element(_q("GetItemRequest"))
        ET.SubElement(body, _q("ItemID")).text = item_id
        ET.SubElement(body, _q("DetailLevel")).text = "ReturnAll"
        return self._send("GetItem", body, token)

    def revise(
        self,
        token: str,
        current_item_xml: str,
        listing: EbayReadyListing,
        image_urls: list[str],
        invocation_id: str,
    ) -> TradingResult:
        current = ET.fromstring(current_item_xml)
        item = deepcopy(current)
        self._replace_app_fields(item, listing, image_urls)
        return self._call("ReviseFixedPriceItem", item, token, invocation_id=invocation_id)

    def end(self, token: str, item_id: str, reason: str, invocation_id: str) -> TradingResult:
        body = ET.Element(_q("EndFixedPriceItemRequest"))
        ET.SubElement(body, _q("ItemID")).text = item_id
        ET.SubElement(body, _q("EndingReason")).text = reason
        ET.SubElement(body, _q("MessageID")).text = invocation_id
        return self._send("EndFixedPriceItem", body, token)

    def relist(
        self,
        token: str,
        current_item_xml: str,
        listing: EbayReadyListing,
        image_urls: list[str],
        request_id: str,
    ) -> TradingResult:
        item = deepcopy(ET.fromstring(current_item_xml))
        self._replace_app_fields(item, listing, image_urls)
        self._drop(item, "ItemID")
        self._drop(item, "UUID")
        ET.SubElement(item, _q("UUID")).text = uuid.UUID(request_id).hex
        return self._call("RelistFixedPriceItem", item, token)

    def _call(
        self, name: str, item: ET.Element, token: str, *, invocation_id: str | None = None
    ) -> TradingResult:
        body = ET.Element(_q(f"{name}Request"))
        if invocation_id:
            self._drop(item, "InvocationID")
            ET.SubElement(item, _q("InvocationID")).text = invocation_id
        body.append(item)
        return self._send(name, body, token)

    def _send(self, name: str, body: ET.Element, token: str) -> TradingResult:
        try:
            response = httpx.post(
                self.endpoint,
                headers={
                    "X-EBAY-API-CALL-NAME": name,
                    "X-EBAY-API-COMPATIBILITY-LEVEL": "1455",
                    "X-EBAY-API-SITEID": "0",
                    "X-EBAY-API-IAF-TOKEN": token,
                    "Content-Type": "text/xml",
                },
                content=ET.tostring(body, encoding="utf-8", xml_declaration=True),
                timeout=self.timeout,
            )
        except httpx.RequestError as error:
            raise EbayApiError("eBay did not confirm the listing request") from error
        if response.status_code == 401:
            raise EbayApiError("eBay authorization expired", authorization_expired=True)
        if response.status_code >= 400:
            raise EbayApiError("eBay did not confirm the listing request")
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as error:
            raise EbayApiError("eBay returned an unreadable listing response") from error
        return self._parse(root)

    def _new_item(
        self, listing: EbayReadyListing, custom_label: str, image_urls: list[str]
    ) -> ET.Element:
        item = ET.Element(_q("Item"))
        ET.SubElement(item, _q("SKU")).text = custom_label
        ET.SubElement(item, _q("InventoryTrackingMethod")).text = "SKU"
        self._replace_app_fields(item, listing, image_urls)
        return item

    def _replace_app_fields(
        self, item: ET.Element, listing: EbayReadyListing, image_urls: list[str]
    ) -> None:
        for name in (
            "Title",
            "Description",
            "PrimaryCategory",
            "ConditionID",
            "ConditionDescription",
            "Currency",
            "Country",
            "PostalCode",
            "ListingType",
            "ListingDuration",
            "Quantity",
            "StartPrice",
            "ItemSpecifics",
            "PictureDetails",
            "SellerProfiles",
        ):
            self._drop(item, name)
        ET.SubElement(item, _q("Title")).text = listing.title
        ET.SubElement(item, _q("Description")).text = listing.description
        category = ET.SubElement(item, _q("PrimaryCategory"))
        ET.SubElement(category, _q("CategoryID")).text = listing.category_id
        ET.SubElement(item, _q("ConditionID")).text = str(listing.condition_id)
        if listing.condition_description:
            ET.SubElement(item, _q("ConditionDescription")).text = listing.condition_description
        ET.SubElement(item, _q("Currency")).text = "USD"
        ET.SubElement(item, _q("Country")).text = "US"
        postal_code = os.environ.get("EBAY_ITEM_POSTAL_CODE", "").strip()
        if not postal_code:
            raise EbayApiError("The eBay item postal code is not configured")
        ET.SubElement(item, _q("PostalCode")).text = postal_code
        ET.SubElement(item, _q("ListingType")).text = "FixedPriceItem"
        ET.SubElement(item, _q("ListingDuration")).text = "GTC"
        ET.SubElement(item, _q("Quantity")).text = "1"
        ET.SubElement(item, _q("StartPrice"), currencyID="USD").text = f"{listing.price:.2f}"
        specifics = ET.SubElement(item, _q("ItemSpecifics"))
        for specific in listing.item_specifics:
            pair = ET.SubElement(specifics, _q("NameValueList"))
            ET.SubElement(pair, _q("Name")).text = specific.name
            for value in specific.values:
                ET.SubElement(pair, _q("Value")).text = value
        pictures = ET.SubElement(item, _q("PictureDetails"))
        for url in image_urls:
            ET.SubElement(pictures, _q("PictureURL")).text = url
        if listing.business_policies:
            self._add_policies(item, listing.business_policies)
        if listing.package_weight or listing.package_dimensions:
            package = item.find(_q("ShippingPackageDetails"))
            if package is None:
                package = ET.SubElement(item, _q("ShippingPackageDetails"))
            for name in (
                "MeasurementUnit",
                "PackageDepth",
                "PackageLength",
                "PackageWidth",
                "WeightMajor",
                "WeightMinor",
            ):
                self._drop(package, name)
            ET.SubElement(package, _q("MeasurementUnit")).text = "English"
            if listing.package_dimensions:
                ET.SubElement(package, _q("PackageDepth")).text = str(
                    listing.package_dimensions.height
                )
                ET.SubElement(package, _q("PackageLength")).text = str(
                    listing.package_dimensions.length
                )
                ET.SubElement(package, _q("PackageWidth")).text = str(
                    listing.package_dimensions.width
                )
            if listing.package_weight:
                ET.SubElement(package, _q("WeightMajor")).text = str(listing.package_weight.pounds)
                ET.SubElement(package, _q("WeightMinor")).text = str(listing.package_weight.ounces)

    @staticmethod
    def _add_policies(item: ET.Element, policies: BusinessPolicies) -> None:
        profiles = ET.SubElement(item, _q("SellerProfiles"))
        shipping = ET.SubElement(profiles, _q("SellerShippingProfile"))
        ET.SubElement(shipping, _q("ShippingProfileID")).text = policies.fulfillment_policy_id
        payment = ET.SubElement(profiles, _q("SellerPaymentProfile"))
        ET.SubElement(payment, _q("PaymentProfileID")).text = policies.payment_policy_id
        returns = ET.SubElement(profiles, _q("SellerReturnProfile"))
        ET.SubElement(returns, _q("ReturnProfileID")).text = policies.return_policy_id

    @staticmethod
    def _drop(item: ET.Element, local_name: str) -> None:
        for child in list(item):
            if child.tag.rsplit("}", 1)[-1] == local_name:
                item.remove(child)

    @staticmethod
    def _text(root: ET.Element, path: str) -> str | None:
        node = root.find(path, {"e": NS})
        return node.text if node is not None else None

    def _parse(self, root: ET.Element) -> TradingResult:
        ack = self._text(root, "e:Ack") or "Failure"
        item_id = self._text(root, "e:ItemID") or self._text(root, "e:Item/e:ItemID")
        if not item_id:
            for parameter in root.findall("e:Errors/e:ErrorParameters", {"e": NS}):
                if parameter.attrib.get("ParamID", "").casefold() in {
                    "itemid",
                    "item_id",
                    "listingid",
                }:
                    item_id = self._text(parameter, "e:Value")
                    if item_id:
                        break
        duplicate_status = self._text(root, "e:DuplicateInvocationDetails/e:Status")
        issues = []
        for error in root.findall("e:Errors", {"e": NS}):
            severity = (self._text(error, "e:SeverityCode") or "Error").lower()
            issues.append(
                EbayIssue(
                    severity="warning" if severity == "warning" else "error",
                    code=self._text(error, "e:ErrorCode") or "",
                    message=self._text(error, "e:LongMessage")
                    or self._text(error, "e:ShortMessage")
                    or "eBay rejected part of the listing.",
                )
            )
        fees = {}
        for fee in root.findall(".//e:Fee", {"e": NS}):
            name = self._text(fee, "e:Name")
            amount = self._text(fee, "e:Fee")
            if name and amount:
                fees[name] = amount
        item = root.find("e:Item", {"e": NS})
        raw_item_xml = ET.tostring(item, encoding="unicode") if item is not None else None
        revision = hashlib.sha256(raw_item_xml.encode()).hexdigest() if raw_item_xml else None
        current_listing = self._listing_from_item(item) if item is not None else None
        current_image_urls = (
            [
                node.text
                for node in item.findall("e:PictureDetails/e:PictureURL", {"e": NS})
                if node.text
            ]
            if item is not None
            else []
        )
        return TradingResult(
            acknowledged=ack in {"Success", "Warning"}
            or bool(duplicate_status == "Success" and item_id),
            item_id=item_id,
            fees=fees,
            issues=issues,
            revision=revision,
            raw_item_xml=raw_item_xml,
            current_listing=current_listing,
            current_image_urls=current_image_urls,
        )

    def _listing_from_item(self, item: ET.Element) -> EbayReadyListing | None:
        def item_text(path: str, default: str = "") -> str:
            return self._text(item, path) or default

        try:
            specifics = []
            for pair in item.findall("e:ItemSpecifics/e:NameValueList", {"e": NS}):
                name = item_text_from(pair, "e:Name")
                values = [value.text for value in pair.findall("e:Value", {"e": NS}) if value.text]
                if name and values:
                    specifics.append(EbayItemSpecific(name=name, values=values))
            policy_values = {
                "fulfillment_policy_id": item_text(
                    "e:SellerProfiles/e:SellerShippingProfile/e:ShippingProfileID"
                ),
                "payment_policy_id": item_text(
                    "e:SellerProfiles/e:SellerPaymentProfile/e:PaymentProfileID"
                ),
                "return_policy_id": item_text(
                    "e:SellerProfiles/e:SellerReturnProfile/e:ReturnProfileID"
                ),
            }
            policies = BusinessPolicies(**policy_values) if all(policy_values.values()) else None
            weight_major = item_text("e:ShippingPackageDetails/e:WeightMajor")
            weight_minor = item_text("e:ShippingPackageDetails/e:WeightMinor")
            package_weight = (
                PackageWeight(pounds=weight_major, ounces=weight_minor)
                if weight_major and weight_minor
                else None
            )
            package_depth = item_text("e:ShippingPackageDetails/e:PackageDepth")
            package_length = item_text("e:ShippingPackageDetails/e:PackageLength")
            package_width = item_text("e:ShippingPackageDetails/e:PackageWidth")
            package_dimensions = (
                PackageDimensions(
                    length=package_length,
                    width=package_width,
                    height=package_depth,
                )
                if package_depth and package_length and package_width
                else None
            )
            category_id = item_text("e:PrimaryCategory/e:CategoryID")
            return EbayReadyListing(
                title=item_text("e:Title"),
                category_id=category_id,
                category_name=item_text("e:PrimaryCategory/e:CategoryName", category_id),
                condition_id=int(item_text("e:ConditionID")),
                condition_description=item_text("e:ConditionDescription"),
                description=item_text("e:Description"),
                item_specifics=specifics,
                price=float(item_text("e:StartPrice")),
                business_policies=policies,
                package_weight=package_weight,
                package_dimensions=package_dimensions,
            )
        except (ValueError, ValidationError):
            return None


def item_text_from(element: ET.Element, path: str) -> str:
    node = element.find(path, {"e": NS})
    return node.text if node is not None and node.text else ""
