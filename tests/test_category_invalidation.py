import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.ebay_store import MemoryEbayStore, PostgresEbayStore
from app.generation import GenerationFailed
from tests.support import GENERATION_CATEGORY, StubGenerator, authenticated_client, jpeg_bytes
from tests.test_ebay_routes import live_preparation_data


@pytest.fixture(scope="module")
def postgres_url():
    """Use only a disposable local cluster; never connect to an operator database."""
    binary_dir = os.environ.get("POSTGRES_TEST_BIN")
    initdb = str(Path(binary_dir) / "initdb") if binary_dir else shutil.which("initdb")
    if not initdb:
        pytest.skip("Set POSTGRES_TEST_BIN to run the disposable PostgreSQL checks.")
    pg_ctl = str(Path(initdb).with_name("pg_ctl"))
    with tempfile.TemporaryDirectory(prefix="category-pg-", dir="/tmp") as directory:
        data = Path(directory) / "data"
        subprocess.run(
            [initdb, "-D", str(data), "-A", "trust", "-U", "category_test"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                pg_ctl,
                "-D",
                str(data),
                "-l",
                str(Path(directory) / "server.log"),
                "-o",
                f"-F -h '' -k {directory}",
                "-w",
                "start",
            ],
            check=True,
            capture_output=True,
        )
        try:
            yield f"dbname=postgres user=category_test host={directory}"
        finally:
            subprocess.run(
                [pg_ctl, "-D", str(data), "-m", "fast", "-w", "stop"],
                check=True,
                capture_output=True,
            )


@pytest.fixture(params=["memory", "postgres"])
def category_store(request, monkeypatch):
    if request.param == "memory":
        return MemoryEbayStore()
    import psycopg

    from app.migrate import migrate

    monkeypatch.setenv("DATABASE_URL", request.getfixturevalue("postgres_url"))
    migrate()
    with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
        connection.execute(
            "INSERT INTO users (id, email, password_hash, is_owner) "
            "VALUES (1, 'test@example.com', 'test-hash', true) ON CONFLICT (id) DO NOTHING"
        )
    return PostgresEbayStore()


def test_category_change_is_sticky_and_generation_is_explicit(category_store, monkeypatch):
    monkeypatch.setenv("EBAY_DIRECT_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("EBAY_ITEM_POSTAL_CODE", "85001")
    generator = StubGenerator()
    client, _, _ = authenticated_client(generator, ebay_store=category_store)
    response = client.post(
        "/api/listings/generate",
        data={"ebay_category": json.dumps(GENERATION_CATEGORY)},
        files={"photos": ("test.jpg", jpeg_bytes(), "image/jpeg")},
    )
    assert response.status_code == 200
    draft_id = response.json()["draft_id"]
    saved = client.get(f"/api/drafts/{draft_id}").json()
    assert not saved["draft"]["generation_required"]
    changed = {
        "category_id": "2600121",
        "group_id": "260012",
        "name": "Shirts",
        "path": "Men > Shirts",
    }

    def put(category, required=False):
        current = client.get(f"/api/drafts/{draft_id}").json()
        return client.put(
            f"/api/drafts/{draft_id}",
            json={
                "revision": current["revision"],
                "draft": {**current["draft"], "generation_required": required},
                "ebay_category": category,
                "ebay_condition": {"condition_id": 3000, "name": "Pre-owned"},
                "photo_count": 1,
            },
        )

    assert not put(GENERATION_CATEGORY).json()["draft"]["generation_required"]
    assert put(changed).json()["draft"]["generation_required"]
    assert len(generator.calls) == 1
    # Neither a forged false value nor a return to the original category clears it.
    assert put(GENERATION_CATEGORY).json()["draft"]["generation_required"]
    assert put(None).json()["draft"]["generation_required"]
    invalid = put(changed).json()
    assert invalid["draft"]["generation_required"]
    assert invalid["ebay_category"] == changed
    client.app.state.ebay_gateway.category_requirements = Mock(
        side_effect=AssertionError("publication must stop before eBay")
    )
    preparation = client.post(
        "/api/ebay/live-listing-preparations",
        data={**live_preparation_data(draft_id), "draft_revision": str(invalid["revision"])},
        files={"photos": ("test.jpg", jpeg_bytes(), "image/jpeg")},
    )
    assert preparation.status_code == 409
    assert preparation.json()["code"] == "generation_required"
    from tests.support import StubEbayGateway

    client.app.state.ebay_gateway = StubEbayGateway()
    original_generate = generator.generate
    generator.generate = Mock(side_effect=GenerationFailed("fake failure"))
    request_data = {"ebay_category": json.dumps(changed)}
    failed = client.post(
        "/api/listings/generate",
        data=request_data,
        files={"photos": ("test.jpg", jpeg_bytes(), "image/jpeg")},
    )
    assert failed.status_code == 502
    assert client.get(f"/api/drafts/{draft_id}").json()["draft"]["generation_required"]
    generator.generate = original_generate
    retried = client.post(
        "/api/listings/generate",
        data=request_data,
        files={"photos": ("test.jpg", jpeg_bytes(), "image/jpeg")},
    )
    assert retried.status_code == 200
    assert retried.json()["draft_id"] != draft_id
    assert not retried.json()["draft"]["generation_required"]
    assert retried.json()["ebay_category"] == changed
    assert client.get(f"/api/drafts/{draft_id}").json()["draft"]["generation_required"]


def test_category_lists_survive_store_recreation(category_store):
    key = f"sandbox:0:{uuid.uuid4()}"
    categories = [{"category_id": "175786", "name": "Sweaters", "path": "Women > Sweaters"}]
    assert category_store.get_category_list(key) is None
    category_store.save_category_list(key, categories)
    reader = (
        PostgresEbayStore() if isinstance(category_store, PostgresEbayStore) else category_store
    )
    assert reader.get_category_list(key) == categories
