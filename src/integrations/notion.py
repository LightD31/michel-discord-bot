"""Pure Notion API client (no Discord imports).

Wraps ``notion_client.AsyncClient`` with a small, task-focused surface (data
source resolution, paginated query, schema lookup, page CRUD) for the
``2025-09-03`` API version. Handles the API's indirection through data sources
so callers can pass database IDs.
"""

import time
from typing import Any

from notion_client import APIResponseError, AsyncClient
from notion_client.helpers import async_collect_paginated_api

from src.core.errors import IntegrationError

# Data-source schemas (property names, select options) change rarely; an hour
# keeps autocomplete in step with edits made in Notion without a lookup per
# keystroke.
SCHEMA_TTL_SECONDS = 3600


class NotionAPIError(IntegrationError):
    """Raised when a Notion API call fails."""


class DataSourceNotFoundError(NotionAPIError):
    """Raised when a database has no associated data source."""


class NotionClient:
    """Thin wrapper around ``notion_client.AsyncClient`` with data-source caching.

    The ``2025-09-03`` API requires a ``data_source_id`` for queries and for page
    creation parents; this client resolves and caches it per database id.
    """

    def __init__(self, auth_token: str):
        self._client = AsyncClient(auth=auth_token)
        self._data_source_cache: dict[str, str] = {}
        self._schema_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    async def get_data_source_id(self, database_id: str) -> str:
        """Return the (cached) data source id for ``database_id``."""
        if database_id in self._data_source_cache:
            return self._data_source_cache[database_id]

        try:
            database = await self._client.databases.retrieve(database_id=database_id)
        except APIResponseError as e:
            raise NotionAPIError(f"Failed to retrieve database: {e}") from e
        except Exception as e:
            raise NotionAPIError(f"Unexpected error: {e}") from e

        data_sources = database.get("data_sources", [])
        if not data_sources:
            raise DataSourceNotFoundError(f"No data sources found for database {database_id}")

        data_source_id = data_sources[0]["id"]
        self._data_source_cache[database_id] = data_source_id
        return data_source_id

    async def query_data_source(
        self,
        database_id: str,
        filter_params: dict[str, Any] | None = None,
        sorts: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Return every page of ``database_id`` matching ``filter_params``.

        Follows ``next_cursor`` until the result set is exhausted — a single
        query call stops at 100 results.
        """
        data_source_id = await self.get_data_source_id(database_id)
        kwargs: dict[str, Any] = {"data_source_id": data_source_id}
        if filter_params:
            kwargs["filter"] = filter_params
        if sorts:
            kwargs["sorts"] = sorts
        try:
            return await async_collect_paginated_api(self._client.data_sources.query, **kwargs)
        except APIResponseError as e:
            raise NotionAPIError(f"Erreur lors de la requête Notion: {e}") from e
        except Exception as e:
            raise NotionAPIError(f"Erreur inattendue: {e}") from e

    async def get_properties_schema(self, database_id: str) -> dict[str, Any]:
        """Return the ``properties`` schema of ``database_id``'s data source.

        Keys are property names; each value carries the property ``type`` and,
        for select-like properties, the configured ``options``. Cached for
        :data:`SCHEMA_TTL_SECONDS`.
        """
        cached = self._schema_cache.get(database_id)
        if cached and time.monotonic() - cached[0] < SCHEMA_TTL_SECONDS:
            return cached[1]

        data_source_id = await self.get_data_source_id(database_id)
        try:
            data_source = await self._client.data_sources.retrieve(data_source_id=data_source_id)
        except APIResponseError as e:
            raise NotionAPIError(f"Impossible de récupérer le schéma: {e}") from e
        except Exception as e:
            raise NotionAPIError(f"Erreur inattendue: {e}") from e

        properties: dict[str, Any] = data_source.get("properties", {})
        self._schema_cache[database_id] = (time.monotonic(), properties)
        return properties

    async def retrieve_page(self, page_id: str) -> dict[str, Any]:
        """Fetch full page properties for ``page_id``."""
        try:
            return await self._client.pages.retrieve(page_id=page_id)
        except APIResponseError as e:
            raise NotionAPIError(f"Impossible de récupérer la page: {e}") from e
        except Exception as e:
            raise NotionAPIError(f"Erreur inattendue: {e}") from e

    async def update_page_properties(
        self, page_id: str, properties: dict[str, Any]
    ) -> dict[str, Any]:
        """Patch ``properties`` on an existing page."""
        try:
            return await self._client.pages.update(page_id=page_id, properties=properties)
        except APIResponseError as e:
            raise NotionAPIError(f"Impossible de mettre à jour la page: {e}") from e
        except Exception as e:
            raise NotionAPIError(f"Erreur inattendue: {e}") from e

    async def create_page(self, database_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        """Create a new page under ``database_id``'s data source."""
        data_source_id = await self.get_data_source_id(database_id)
        try:
            return await self._client.pages.create(
                parent={"type": "data_source_id", "data_source_id": data_source_id},
                properties=properties,
            )
        except APIResponseError as e:
            raise NotionAPIError(f"Impossible de créer la page: {e}") from e
        except Exception as e:
            raise NotionAPIError(f"Erreur inattendue: {e}") from e
