"""NotionClient: pagination past 100 results and the cached schema lookup."""

import pytest

from src.integrations.notion import NotionAPIError, NotionClient


class _FakeDataSources:
    def __init__(self, total: int, page_size: int = 100) -> None:
        self.total = total
        self.page_size = page_size
        self.query_calls: list[dict] = []
        self.retrieve_calls = 0

    async def query(self, data_source_id: str, **kwargs):
        self.query_calls.append(kwargs)
        start = int(kwargs.get("start_cursor") or 0)
        end = min(start + self.page_size, self.total)
        has_more = end < self.total
        return {
            "results": [{"id": str(i)} for i in range(start, end)],
            "has_more": has_more,
            "next_cursor": str(end) if has_more else None,
        }

    async def retrieve(self, data_source_id: str, **kwargs):
        self.retrieve_calls += 1
        return {"properties": {"Titre": {"type": "title", "title": {}}}}


class _FakeDatabases:
    async def retrieve(self, database_id: str):
        return {"data_sources": [{"id": f"ds-{database_id}"}]}


class _FakeAsyncClient:
    def __init__(self, total: int) -> None:
        self.data_sources = _FakeDataSources(total)
        self.databases = _FakeDatabases()


def _client(total: int) -> tuple[NotionClient, _FakeAsyncClient]:
    client = NotionClient(auth_token="secret")
    fake = _FakeAsyncClient(total)
    client._client = fake  # type: ignore[assignment]
    return client, fake


async def test_query_follows_cursor_past_first_hundred_results():
    client, fake = _client(total=250)
    results = await client.query_data_source("db", {"property": "Défi", "select": {}})
    assert [r["id"] for r in results] == [str(i) for i in range(250)]
    assert len(fake.data_sources.query_calls) == 3
    assert all("filter" in call for call in fake.data_sources.query_calls)


async def test_query_without_filter_sends_none():
    client, fake = _client(total=3)
    assert len(await client.query_data_source("db")) == 3
    assert "filter" not in fake.data_sources.query_calls[0]


async def test_schema_is_cached():
    client, fake = _client(total=0)
    first = await client.get_properties_schema("db")
    second = await client.get_properties_schema("db")
    assert first == second == {"Titre": {"type": "title", "title": {}}}
    assert fake.data_sources.retrieve_calls == 1


async def test_driver_errors_are_wrapped():
    client, fake = _client(total=0)

    async def boom(**kwargs):
        raise RuntimeError("network down")

    fake.data_sources.query = boom  # type: ignore[method-assign]
    with pytest.raises(NotionAPIError):
        await client.query_data_source("db")
