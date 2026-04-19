"""Global configuration + module schema endpoints."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src import logutil
from src.webui.context import WebUIContext, discover_modules
from src.webui.schemas import GLOBAL_CONFIG_SCHEMAS, MODULE_SCHEMAS

logger = logutil.init_logger("webui.routes.config")


class GlobalConfigUpdate(BaseModel):
    section: str
    config: dict


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/config")
    async def api_get_config(request: Request):
        """Get the full configuration."""
        ctx.require_admin(request)
        data = ctx.get_full_config()
        return JSONResponse(data)

    @router.get("/api/modules")
    async def api_get_modules(request: Request):
        """Get all discovered module names with their schemas."""
        ctx.require_admin(request)
        discovered = discover_modules()
        modules = {}
        for mod_name in discovered:
            schema = MODULE_SCHEMAS.get(mod_name, {})
            modules[mod_name] = {
                "label": schema.get("label", mod_name),
                "description": schema.get("description", ""),
                "icon": schema.get("icon", "🧩"),
                "has_schema": bool(schema),
            }
        return JSONResponse({"modules": modules})

    @router.get("/api/schemas/modules")
    async def api_module_schemas(request: Request):
        """Get all module configuration schemas."""
        ctx.require_admin(request)
        return JSONResponse(MODULE_SCHEMAS)

    @router.get("/api/schemas/modules/{module_name}")
    async def api_module_schema(request: Request, module_name: str):
        """Get schema for a specific module."""
        ctx.require_admin(request)
        schema = MODULE_SCHEMAS.get(module_name)
        if not schema:
            return JSONResponse({"fields": {}, "label": module_name})
        return JSONResponse(schema)

    @router.get("/api/schemas/global")
    async def api_global_schemas(request: Request):
        """Get all global config section schemas."""
        ctx.require_admin(request)
        return JSONResponse(GLOBAL_CONFIG_SCHEMAS)

    @router.get("/api/global-config")
    async def api_get_global_config(request: Request):
        """Get global (non-server-specific) configuration."""
        ctx.require_admin(request)
        data = ctx.get_full_config()
        return JSONResponse(data.get("config", {}))

    @router.put("/api/global-config/{section}")
    async def api_update_global_config(request: Request, section: str, body: GlobalConfigUpdate):
        """Update a section of the global configuration."""
        ctx.require_admin(request)
        data = ctx.get_full_config()
        data.setdefault("config", {})[section] = body.config
        ctx.save_config(data)
        logger.info(f"Updated global config section: {section}")
        return JSONResponse({"status": "ok"})

    @router.post("/api/cleanup-config")
    async def api_cleanup_config(request: Request, dry_run: bool = False):
        """Remove config keys not present in the schemas.

        Query params:
            dry_run: if true, return what would be removed without saving.
        """
        ctx.require_admin(request)
        data = ctx.get_full_config()
        removed: list[dict] = []

        # Clean up per-server module configs
        servers = data.get("servers", {})
        for server_id, server_config in servers.items():
            for module_name, module_config in list(server_config.items()):
                if not isinstance(module_config, dict):
                    continue
                schema = MODULE_SCHEMAS.get(module_name)
                if not schema or not schema.get("fields"):
                    continue
                # directValue modules store raw data, not field-based config
                if schema.get("directValue"):
                    continue
                allowed_keys = set(schema["fields"].keys())
                # Always keep "enabled" even if not explicitly in schema
                allowed_keys.add("enabled")
                for key in list(module_config.keys()):
                    if key not in allowed_keys:
                        removed.append(
                            {
                                "location": f"servers.{server_id}.{module_name}",
                                "key": key,
                                "value": module_config[key],
                            }
                        )
                        if not dry_run:
                            del module_config[key]

        # Clean up global config sections
        global_config = data.get("config", {})
        for section_name, section_data in global_config.items():
            if not isinstance(section_data, dict):
                continue
            schema = GLOBAL_CONFIG_SCHEMAS.get(section_name)
            if not schema or not schema.get("fields"):
                continue
            allowed_keys = set(schema["fields"].keys())
            for key in list(section_data.keys()):
                if key not in allowed_keys:
                    removed.append(
                        {
                            "location": f"config.{section_name}",
                            "key": key,
                            "value": section_data[key],
                        }
                    )
                    if not dry_run:
                        del section_data[key]

        if not dry_run and removed:
            ctx.save_config(data)
            logger.info("Config cleanup: removed %d key(s)", len(removed))

        # Sanitise values for JSON response (avoid huge blobs)
        for entry in removed:
            v = entry["value"]
            if isinstance(v, (dict, list)):
                entry["value"] = f"({type(v).__name__}, {len(v)} items)"
            else:
                entry["value"] = str(v)[:120]

        return JSONResponse(
            {
                "status": "ok",
                "dry_run": dry_run,
                "removed_count": len(removed),
                "removed": removed,
            }
        )

    return router
