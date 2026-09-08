import xml.etree.ElementTree as ET

import httpx
import pytest

from app.ebay_api import EbayApiError
from app.ebay_models import BusinessPolicies, EbayItemSpecific, TradingResult
from app.models import PackageDimensions, PackageWeight
from app.trading_api import NS, TradingApiAdapter
from tests.test_ebay_api import ebay_listing


class CapturingTradingAdapter(TradingApiAdapter):
    def __init__(self) -> None:
        super().__init__(environment="sandbox")
        self.calls = []

    def _send(self, name, body, token):
        self.calls.append((name, body, token))
        return TradingResult(acknowledged=True, item_id="123")


def listing_with_policies():
    return ebay_listing().model_copy(
        update={
            "business_policies": BusinessPolicies(
                fulfillment_policy_id="ship-1",
                payment_policy_id="pay-1",
                return_policy_id="return-1",
            ),
            "package_weight": PackageWeight(pounds=1, ounces=4),
            "package_dimensions": PackageDimensions(length=12, width=8, height=3),
        }
    )


def test_verify_and_publish_include_all_fields_and_stable_uuid(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_ITEM_POSTAL_CODE", "85001")
    adapter = CapturingTradingAdapter()
    listing = listing_with_policies().model_copy(
        update={
            "item_specifics": [
                EbayItemSpecific(name="Brand", value="Everlane"),
                EbayItemSpecific(name="Material", values=["Cotton", "Wool"]),
            ]
        }
    )
    image_urls = [
        "https://img.test/a~b.jpg",
        *[f"https://img.test/{number}.jpg" for number in range(2, 25)],
    ]

    adapter.verify("token", listing, "LA-1-abc", image_urls)
    adapter.publish(
        "token",
        listing,
        "LA-1-abc",
        image_urls,
        "17360520-fdb7-41a7-916c-8e9b585f8180",
    )

    assert [call[0] for call in adapter.calls] == [
        "VerifyAddFixedPriceItem",
        "AddFixedPriceItem",
    ]
    publish_item = adapter.calls[1][1].find(f"{{{NS}}}Item")
    assert publish_item.findtext(f"{{{NS}}}UUID") == "17360520fdb741a7916c8e9b585f8180"
    assert publish_item.findtext(f"{{{NS}}}SKU") == "LA-1-abc"
    assert publish_item.findtext(f"{{{NS}}}Title") == listing.title
    assert publish_item.findtext(f"{{{NS}}}Description") == listing.description
    assert publish_item.findtext(f"{{{NS}}}PrimaryCategory/{{{NS}}}CategoryID") == str(
        listing.category_id
    )
    assert publish_item.findtext(f"{{{NS}}}ConditionID") == str(listing.condition_id)
    assert publish_item.findtext(f"{{{NS}}}ConditionDescription") == listing.condition_description
    assert publish_item.findtext(f"{{{NS}}}StartPrice") == f"{listing.price:.2f}"
    assert publish_item.findtext(f"{{{NS}}}ListingType") == "FixedPriceItem"
    assert publish_item.findtext(f"{{{NS}}}Quantity") == "1"
    assert publish_item.findtext(f"{{{NS}}}PostalCode") == "85001"
    assert (
        publish_item.findtext(f"{{{NS}}}ShippingPackageDetails/{{{NS}}}MeasurementUnit")
        == "English"
    )
    assert publish_item.findtext(f"{{{NS}}}ShippingPackageDetails/{{{NS}}}WeightMajor") == "1"
    assert publish_item.findtext(f"{{{NS}}}ShippingPackageDetails/{{{NS}}}WeightMinor") == "4"
    assert publish_item.findtext(f"{{{NS}}}ShippingPackageDetails/{{{NS}}}PackageLength") == "12"
    assert publish_item.findtext(f"{{{NS}}}ShippingPackageDetails/{{{NS}}}PackageWidth") == "8"
    assert publish_item.findtext(f"{{{NS}}}ShippingPackageDetails/{{{NS}}}PackageDepth") == "3"
    assert [
        pair.findtext(f"{{{NS}}}Name")
        for pair in publish_item.findall(f"{{{NS}}}ItemSpecifics/{{{NS}}}NameValueList")
    ] == ["Brand", "Material"]
    material = publish_item.findall(f"{{{NS}}}ItemSpecifics/{{{NS}}}NameValueList")[1]
    assert [value.text for value in material.findall(f"{{{NS}}}Value")] == ["Cotton", "Wool"]
    assert [
        node.text for node in publish_item.findall(f"{{{NS}}}PictureDetails/{{{NS}}}PictureURL")
    ] == image_urls
    assert (
        publish_item.findtext(
            f"{{{NS}}}SellerProfiles/{{{NS}}}SellerShippingProfile/{{{NS}}}ShippingProfileID"
        )
        == "ship-1"
    )
    assert (
        publish_item.findtext(
            f"{{{NS}}}SellerProfiles/{{{NS}}}SellerPaymentProfile/{{{NS}}}PaymentProfileID"
        )
        == "pay-1"
    )
    assert (
        publish_item.findtext(
            f"{{{NS}}}SellerProfiles/{{{NS}}}SellerReturnProfile/{{{NS}}}ReturnProfileID"
        )
        == "return-1"
    )


def test_revision_preserves_fields_that_the_app_does_not_edit(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_ITEM_POSTAL_CODE", "85001")
    adapter = CapturingTradingAdapter()
    current = """<Item xmlns="urn:ebay:apis:eBLBaseComponents">
      <ItemID>123</ItemID><Title>Old title</Title>
      <ShippingDetails><ShippingType>Flat</ShippingType></ShippingDetails>
      <ShippingPackageDetails><PackageDepth>2</PackageDepth><PackageLength>10</PackageLength>
      <PackageWidth>6</PackageWidth><WeightMajor>2</WeightMajor>
      <WeightMinor>0</WeightMinor></ShippingPackageDetails>
    </Item>"""

    adapter.revise(
        "token",
        current,
        listing_with_policies(),
        ["https://img.test/1.jpg"],
        "invocation-1",
    )

    request = adapter.calls[0][1]
    item = request.find(f"{{{NS}}}Item")
    assert item.find(f"{{{NS}}}ShippingDetails/{{{NS}}}ShippingType").text == "Flat"
    assert item.find(f"{{{NS}}}ShippingPackageDetails/{{{NS}}}PackageDepth").text == "3"
    assert item.find(f"{{{NS}}}ShippingPackageDetails/{{{NS}}}PackageLength").text == "12"
    assert item.find(f"{{{NS}}}ShippingPackageDetails/{{{NS}}}PackageWidth").text == "8"
    assert item.find(f"{{{NS}}}ShippingPackageDetails/{{{NS}}}WeightMajor").text == "1"
    assert item.find(f"{{{NS}}}ShippingPackageDetails/{{{NS}}}WeightMinor").text == "4"
    assert item.find(f"{{{NS}}}Title").text == "Green cotton sweater"
    assert item.find(f"{{{NS}}}InvocationID").text == "invocation-1"


def test_trading_response_parses_warnings_fees_and_revision() -> None:
    adapter = TradingApiAdapter(environment="sandbox")
    response = ET.fromstring(
        f"""<GetItemResponse xmlns="{NS}"><Ack>Warning</Ack><Item><ItemID>123</ItemID>
        <Title>Current</Title></Item><Fees><Fee><Name>InsertionFee</Name>
        <Fee currencyID="USD">0.35</Fee></Fee></Fees><Errors><SeverityCode>Warning</SeverityCode>
        <ErrorCode>219</ErrorCode><LongMessage>Check the category.</LongMessage></Errors>
        </GetItemResponse>"""
    )

    parsed = adapter._parse(response)

    assert parsed.acknowledged is True
    assert parsed.item_id == "123"
    assert parsed.fees == {"InsertionFee": "0.35"}
    assert parsed.issues[0].severity == "warning"
    assert parsed.revision
    assert parsed.raw_item_xml


def test_get_item_response_builds_current_app_listing_and_photo_order() -> None:
    adapter = TradingApiAdapter(environment="sandbox")
    response = ET.fromstring(
        f"""<GetItemResponse xmlns="{NS}"><Ack>Success</Ack><Item><ItemID>123</ItemID>
        <Title>Seller Hub title</Title><Description>Current description</Description>
        <PrimaryCategory><CategoryID>175786</CategoryID><CategoryName>Sweaters</CategoryName>
        </PrimaryCategory><ConditionID>3000</ConditionID><ConditionDescription>Used</ConditionDescription>
        <StartPrice currencyID="USD">34.00</StartPrice><ItemSpecifics><NameValueList>
        <Name>Material</Name><Value>Cotton</Value><Value>Wool</Value>
        </NameValueList></ItemSpecifics>
        <PictureDetails><PictureURL>https://img.test/new-1.jpg</PictureURL>
        <PictureURL>https://img.test/new-2.jpg</PictureURL></PictureDetails><SellerProfiles>
        <SellerShippingProfile><ShippingProfileID>ship</ShippingProfileID></SellerShippingProfile>
        <SellerPaymentProfile><PaymentProfileID>pay</PaymentProfileID></SellerPaymentProfile>
        <SellerReturnProfile><ReturnProfileID>returns</ReturnProfileID></SellerReturnProfile>
        </SellerProfiles><ShippingPackageDetails><MeasurementUnit>English</MeasurementUnit>
        <PackageDepth>3</PackageDepth><PackageLength>12</PackageLength><PackageWidth>8</PackageWidth>
        <WeightMajor>2</WeightMajor><WeightMinor>6</WeightMinor>
        </ShippingPackageDetails></Item></GetItemResponse>"""
    )

    parsed = adapter._parse(response)

    assert parsed.current_listing.title == "Seller Hub title"
    assert parsed.current_listing.price == 34
    assert parsed.current_listing.business_policies.fulfillment_policy_id == "ship"
    assert parsed.current_listing.package_weight == PackageWeight(pounds=2, ounces=6)
    assert parsed.current_listing.package_dimensions == PackageDimensions(
        length=12, width=8, height=3
    )
    assert parsed.current_listing.item_specifics[0].values == ["Cotton", "Wool"]
    assert parsed.current_image_urls == [
        "https://img.test/new-1.jpg",
        "https://img.test/new-2.jpg",
    ]


def test_duplicate_uuid_response_recovers_the_first_listing_id() -> None:
    adapter = TradingApiAdapter(environment="sandbox")
    response = ET.fromstring(
        f"""<AddFixedPriceItemResponse xmlns="{NS}"><Ack>Failure</Ack>
        <DuplicateInvocationDetails><Status>Success</Status></DuplicateInvocationDetails>
        <Errors><SeverityCode>Error</SeverityCode><ErrorCode>488</ErrorCode>
        <ErrorParameters ParamID="ItemID"><Value>123456789012</Value></ErrorParameters>
        <LongMessage>This UUID was already used.</LongMessage></Errors>
        </AddFixedPriceItemResponse>"""
    )

    parsed = adapter._parse(response)

    assert parsed.acknowledged is True
    assert parsed.item_id == "123456789012"


def test_network_timeout_returns_safe_retry_error(monkeypatch) -> None:
    def timeout(*_args, **_kwargs):
        raise httpx.ReadTimeout("private provider detail")

    monkeypatch.setattr(httpx, "post", timeout)
    adapter = TradingApiAdapter(environment="production")
    request = ET.Element(f"{{{NS}}}VerifyAddFixedPriceItemRequest")

    with pytest.raises(EbayApiError, match="eBay did not confirm the listing request") as caught:
        adapter._send("VerifyAddFixedPriceItem", request, "secret-token")

    assert "private provider detail" not in str(caught.value)
