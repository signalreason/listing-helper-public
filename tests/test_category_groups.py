from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext

import pytest

from app.ebay_api import EbayApiError, EbayHttpGateway
from app.ebay_models import EbayCategory
from app.ebay_routes import CATEGORY_GROUPS
from app.ebay_store import MemoryEbayStore, PostgresEbayStore
from tests.support import StubEbayGateway, authenticated_client


@pytest.mark.parametrize("group", CATEGORY_GROUPS)
def test_group_miss_stores_all_inventory_and_later_request_reuses_it(group):
    gateway = StubEbayGateway()
    calls = []

    def subtree(group_id):
        calls.append(group_id)
        return [
            EbayCategory(category_id=str(index), name=name, path=f"{group['name']} > {name}")
            for index, name in enumerate(["Clothing", "Shoes", "Bags", "Accessories"], 1)
        ]

    gateway.category_subtree = subtree
    store = MemoryEbayStore()
    client, _, _ = authenticated_client(ebay_gateway=gateway, ebay_store=store)
    url = f"/api/ebay/category-groups/{group['group_id']}/categories"
    first = client.get(url)
    assert first.status_code == 200
    assert first.json()["group_id"] == group["group_id"]
    assert len(first.json()["categories"]) == 4
    assert all(item["path"].startswith(group["name"]) for item in first.json()["categories"])
    # A new app instance still uses the durable store interface.
    second_client, _, _ = authenticated_client(ebay_gateway=gateway, ebay_store=store)
    assert second_client.get(url).json() == first.json()
    assert calls == [group["group_id"]]
    gateway.environment = "production"
    assert client.get(url).status_code == 200
    assert calls == [group["group_id"], group["group_id"]]


def test_group_failures_retry_and_groups_do_not_share_lists():
    gateway = StubEbayGateway()
    original = gateway.category_subtree
    store = MemoryEbayStore()
    client, _, _ = authenticated_client(ebay_gateway=gateway, ebay_store=store)
    url = "/api/ebay/category-groups/260010/categories"

    def fail(_group):
        raise EbayApiError("failed")

    gateway.category_subtree = fail
    assert client.get(url).status_code == 502
    assert store.category_lists == {}
    gateway.category_subtree = lambda _: []
    assert client.get(url).status_code == 502
    assert store.category_lists == {}
    gateway.category_subtree = original
    assert client.get(url).status_code == 200
    assert (
        client.get("/api/ebay/category-groups/260012/categories").json()["categories"]
        != client.get(url).json()["categories"]
    )
    assert client.get("/api/ebay/category-groups/nope/categories").status_code == 422
    assert client.get("/api/ebay/category-groups").json()["groups"] == list(CATEGORY_GROUPS)
    client.delete("/api/sessions/current")
    client.cookies.clear()
    assert client.get(url).status_code == 401


def test_concurrent_group_misses_fetch_once():
    gateway = StubEbayGateway()
    original = gateway.category_subtree
    calls = []

    def subtree(group):
        calls.append(group)
        return original(group)

    gateway.category_subtree = subtree
    client, _, _ = authenticated_client(ebay_gateway=gateway)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: client.get("/api/ebay/category-groups/260010/categories"), range(2))
        )
    assert [result.status_code for result in results] == [200, 200]
    assert calls == ["260010"]


def test_subtree_retains_only_leaves_and_full_group_path(monkeypatch):
    gateway = EbayHttpGateway()
    calls = []
    payload = {
        "categorySubtreeNode": {
            "category": {"categoryId": "260010", "categoryName": "Women"},
            "childCategoryTreeNodes": [
                {
                    "category": {"categoryId": "2", "categoryName": "Clothing"},
                    "childCategoryTreeNodes": [
                        {
                            "category": {"categoryId": "175786", "categoryName": "Sweaters"},
                            "leafCategoryTreeNode": True,
                        }
                    ],
                }
            ],
        }
    }

    def get(operation, path, **params):
        calls.append((operation, path, params))
        return payload

    monkeypatch.setattr(gateway, "_application_get", get)
    assert gateway.category_subtree("260010") == [
        EbayCategory(category_id="175786", name="Sweaters", path="Women > Clothing > Sweaters")
    ]
    assert calls == [
        (
            "category_subtree",
            "/commerce/taxonomy/v1/category_tree/0/get_category_subtree",
            {"category_id": "260010"},
        )
    ]
    with pytest.raises(EbayApiError):
        gateway.category_subtree("260012")
    payload["categorySubtreeNode"]["childCategoryTreeNodes"] = []
    with pytest.raises(EbayApiError):
        gateway.category_subtree("260010")


def test_postgres_category_list_round_trip(monkeypatch):
    import json

    rows = {}

    class Connection:
        def execute(self, query, params):
            if query.startswith("INSERT"):
                rows.setdefault(params[0], json.loads(params[1]))
            self.result = (rows[params[0]],) if params[0] in rows else None
            return self

        def fetchone(self):
            return self.result

    store = PostgresEbayStore()
    monkeypatch.setattr(store, "_connect", lambda: nullcontext(Connection()))
    assert store.get_category_list("sandbox:0:260010") is None
    categories = [{"category_id": "175786", "name": "Sweaters", "path": "Women > Sweaters"}]
    store.save_category_list("sandbox:0:260010", categories)
    assert store.get_category_list("sandbox:0:260010") == categories
    assert store.get_category_list("production:0:260010") is None
