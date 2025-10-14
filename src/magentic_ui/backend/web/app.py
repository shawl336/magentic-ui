# api/app.py
import os
import yaml
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Any

# import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger

from ...version import VERSION
from .config import settings
from .deps import cleanup_managers, init_managers
from .initialization import AppInitializer
from .routes import (
    plans,
    runs,
    sessions,
    settingsroute,
    teams,
    validation,
    ws,
    mcp,
)

# Initialize application
app_file_path = os.path.dirname(os.path.abspath(__file__))
initializer = AppInitializer(settings, app_file_path)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Lifecycle manager for the FastAPI application.
    Handles initialization and cleanup of application resources.
    """
    import logging
    from autogen_agentchat import TRACE_LOGGER_NAME

    logger = logging.getLogger(TRACE_LOGGER_NAME)
    logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.INFO)
    try:
        # Load the config if provided
        config: dict[str, Any] = {}
        config_file = os.environ.get("_CONFIG")
        if config_file:
            logger.info(f"Loading config from file: {config_file}")
            with open(config_file, "r") as f:
                config = yaml.safe_load(f)
        else:
            logger.info("No config file provided, using defaults.")

        # Initialize managers (DB, Connection, Team)
        await init_managers(
            initializer.database_uri,
            initializer.config_dir,
            initializer.app_root,
            os.environ["INTERNAL_WORKSPACE_ROOT"],
            os.environ["EXTERNAL_WORKSPACE_ROOT"],
            os.environ["INSIDE_DOCKER"] == "1",
            config,
            os.environ.get("RUN_WITHOUT_DOCKER", "") == "True",
        )
                
        # Any other initialization code
        logger.info(
            f"Application startup complete. Navigate to http://{os.environ.get('_HOST', '127.0.0.1')}:{os.environ.get('_PORT', '8081')}"
        )

    except Exception as e:
        logger.error(f"Failed to initialize application: {str(e)}")
        raise

    yield  # Application runs here

    # Shutdown
    try:
        logger.info("Cleaning up application resources...")
        await cleanup_managers()
        logger.info("Application shutdown complete")
    except Exception as e:
        logger.error(f"Error during shutdown: {str(e)}")


# Create FastAPI application
app = FastAPI(lifespan=lifespan, debug=True)

# CORS middleware configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:8001",
        "http://localhost:8081",
        "http://localhost:8099",       # OnlyOffice server (localhost access)
        "http://192.168.52.183:8099",   # Keep old IP for backward compatibility
        "*",  # Allow all origins for development
        "http://127.0.0.1:8081",
        "http://0.0.0.0:8000",
        "http://0.0.0.0:8001",
        "http://0.0.0.0:8081",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create API router with version and documentation
api = FastAPI(
    root_path="/api",
    title="Magentic-UI API",
    version=VERSION,
    description="Magentic-UI is an application to interact with web agents.",
    docs_url="/docs" if settings.API_DOCS else None,
)

# Include all routers with their prefixes
api.include_router(
    sessions.router,
    prefix="/sessions",
    tags=["sessions"],
    responses={404: {"description": "Not found"}},
)

api.include_router(
    plans.router,
    prefix="/plans",
    tags=["plans"],
    responses={404: {"description": "Not found"}},
)

api.include_router(
    runs.router,
    prefix="/runs",
    tags=["runs"],
    responses={404: {"description": "Not found"}},
)

api.include_router(
    teams.router,
    prefix="/teams",
    tags=["teams"],
    responses={404: {"description": "Not found"}},
)


api.include_router(
    ws.router,
    prefix="/ws",
    tags=["websocket"],
    responses={404: {"description": "Not found"}},
)

api.include_router(
    validation.router,
    prefix="/validate",
    tags=["validation"],
    responses={404: {"description": "Not found"}},
)

api.include_router(
    settingsroute.router,
    prefix="/settings",
    tags=["settings"],
    responses={404: {"description": "Not found"}},
)

api.include_router(
    mcp.router,
    prefix="/mcp",
    tags=["mcp"],
)


# Version endpoint


@api.get("/version")
async def get_version():
    """Get API version"""
    return {
        "status": True,
        "message": "Version retrieved successfully",
        "data": {"version": VERSION},
    }


# Health check endpoint


@api.get("/health")
async def health_check():
    """API health check endpoint"""
    return {
        "status": True,
        "message": "Service is healthy",
    }


# OnlyOffice callback endpoint
@api.post("/callback")
async def onlyoffice_callback(request: Request):
    """OnlyOffice document callback endpoint"""
    try:
        body = await request.json()
        logger.info(f"OnlyOffice callback received: {body}")

        # For read-only mode, we just acknowledge the callback
        # In the future, if edit mode is needed, implement document saving logic here
        if body.get('status') == 2:  # Document is ready for saving
            logger.info("Document ready for saving, but operating in read-only mode")

        return {"error": 0}
    except Exception as e:
        logger.error(f"Error in OnlyOffice callback: {str(e)}")
        return {"error": 1, "message": str(e)}


# OnlyOffice document access endpoint
@api.get("/document/{file_path:path}")
@api.head("/document/{file_path:path}")
async def serve_document_for_onlyoffice(file_path: str, request: Request):
    """Serve documents for OnlyOffice access with proper headers"""
    try:
        # URL decode the file path to handle encoded characters (like Chinese filenames)
        import urllib.parse
        decoded_file_path = urllib.parse.unquote(file_path)

        # Construct the full file path
        full_path = os.path.join(initializer.static_root, decoded_file_path)

        # Security check: ensure the file is within the static root
        if not os.path.abspath(full_path).startswith(os.path.abspath(initializer.static_root)):
            logger.warning(f"Attempted access to file outside static root: {decoded_file_path} (original: {file_path})")
            return {"error": "Access denied"}

        # Check if file exists
        if not os.path.exists(full_path):
            logger.warning(f"File not found: {full_path} (decoded path: {decoded_file_path})")
            return {"error": "File not found"}

        # Read and serve the file with proper headers for OnlyOffice
        with open(full_path, 'rb') as f:
            content = f.read()

        # Determine content type based on file extension
        content_type = "application/octet-stream"  # Default
        if file_path.lower().endswith('.docx'):
            content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif file_path.lower().endswith('.doc'):
            content_type = "application/msword"
        elif file_path.lower().endswith('.xlsx'):
            content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        elif file_path.lower().endswith('.xls'):
            content_type = "application/vnd.ms-excel"
        elif file_path.lower().endswith('.pptx'):
            content_type = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        elif file_path.lower().endswith('.ppt'):
            content_type = "application/vnd.ms-powerpoint"

        # Return file with proper headers
        from fastapi.responses import Response

        # For HEAD requests, only return headers without content
        if request.method == "HEAD":
            return Response(
                content=b"",
                media_type=content_type,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
                    "Access-Control-Allow-Headers": "*",
                    "Cache-Control": "no-cache",
                    "Content-Length": str(len(content)),  # Include content length in headers
                }
            )

        # For GET requests, return content
        return Response(
            content=content,
            media_type=content_type,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
                "Access-Control-Allow-Headers": "*",
                "Cache-Control": "no-cache",
            }
        )

    except Exception as e:
        logger.error(f"Error serving document {file_path}: {str(e)}")
        return {"error": f"Failed to serve document: {str(e)}"}


# Mount static file directories
app.mount("/api", api)
app.mount(
    "/files",
    StaticFiles(directory=initializer.static_root, html=True),
    name="files",
)
app.mount("/", StaticFiles(directory=initializer.ui_root, html=True), name="ui")

# Error handlers


@app.exception_handler(500)
async def internal_error_handler(request: Request, exc: Exception):
    logger.error(f"Internal error: {str(exc)}")
    return {
        "status": False,
        "message": "Internal server error",
        "detail": str(exc) if settings.API_DOCS else "Internal server error",
    }


def create_app() -> FastAPI:
    """
    Factory function to create and configure the FastAPI application.
    Useful for testing and different deployment scenarios.
    """
    return app
