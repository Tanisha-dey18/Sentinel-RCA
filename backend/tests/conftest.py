import os
import tempfile

# Configure an isolated environment BEFORE the app is imported.
# By default tests use SQLite + in-process fallbacks. Set TEST_DATABASE_URL / TEST_REDIS_URL /
# TEST_QDRANT_URL to run the same suite against real PostgreSQL, Redis and Qdrant servers
# (use a dedicated database: tables are dropped between tests).
_tmp = tempfile.mkdtemp(prefix="sentinel-test-")
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{_tmp}/test.db"
os.environ["QDRANT_URL"] = os.environ.get("TEST_QDRANT_URL", "")
os.environ["QDRANT_PATH"] = ":memory:"
os.environ["QDRANT_COLLECTION"] = "sentinel_test_incidents"
os.environ["REDIS_URL"] = os.environ.get("TEST_REDIS_URL", "")
os.environ["EMBEDDING_PROVIDER"] = "hash"
os.environ["GROQ_API_KEY"] = ""
os.environ["SEED_SAMPLE_DATA"] = "false"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import vector_store  # noqa: E402
from app.services.cache import get_cache  # noqa: E402
from app.services.signatures import seed_default_signatures  # noqa: E402


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(bind=engine)
    init_db()
    with SessionLocal() as db:
        seed_default_signatures(db)
    get_cache().delete_prefix("")
    vector_store._store = None  # fresh in-memory Qdrant collection per test
    if os.environ["QDRANT_URL"]:  # a server keeps collections across tests: drop the test collection
        from qdrant_client import QdrantClient

        QdrantClient(url=os.environ["QDRANT_URL"], check_compatibility=False).delete_collection(os.environ["QDRANT_COLLECTION"])
    yield


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
