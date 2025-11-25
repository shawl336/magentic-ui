# api/app.py
import os
import yaml
import urllib.parse
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
# Dynamically compute allowed origins based on runtime host/port and common dev hosts
_host = os.environ.get("_HOST", "127.0.0.1")
_port = os.environ.get("_PORT", "8081")

_allowed_origins = {
    # Common dev servers
    "http://localhost:8000",
    "http://localhost:8001",
    "http://127.0.0.1:8000",
    "http://0.0.0.0:8000",
    "http://0.0.0.0:8001",
}

def _get_local_ips() -> list[str]:
    ips: list[str] = []
    try:
        import socket
        # Primary outward-facing IP
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ips.append(s.getsockname()[0])
        finally:
            try:
                s.close()  # type: ignore[name-defined]
            except Exception:
                pass
        # Hostname resolved IPs
        try:
            hostname_ips = socket.gethostbyname_ex(socket.gethostname())[2]
            for ip in hostname_ips:
                if ip not in ips:
                    ips.append(ip)
        except Exception:
            pass
    except Exception:
        pass
    return ips

def _add_origin(host: str, port: str) -> None:
    try:
        _allowed_origins.add(f"http://{host}:{port}")
    except Exception:
        pass

# Add the configured host/port
_add_origin(_host, _port)

# If binding to 0.0.0.0 or localhost variants, add the typical aliases for the same port
if _host in {"0.0.0.0", "127.0.0.1", "localhost"}:
    for _h in ("127.0.0.1", "localhost", "0.0.0.0"):
        _add_origin(_h, _port)

# Also allow direct access to common docker bridge IPs when applicable
_add_origin("172.17.0.1", _port)

# Add server LAN IPs so clients visiting http://<lan-ip>:<port> are allowed
for _ip in _get_local_ips():
    _add_origin(_ip, _port)
    # Also add frontend port (8000) for the same IPs
    _add_origin(_ip, "8000")

# Add any extra origins from env (comma-separated), e.g. http://192.168.1.10:3000
_extra = os.environ.get("EXTRA_CORS_ORIGINS", "").strip()
if _extra:
    for origin in [o.strip() for o in _extra.split(",") if o.strip()]:
        try:
            _allowed_origins.add(origin)
        except Exception:
            pass

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(_allowed_origins),
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
    """OnlyOffice document callback endpoint for handling document saves"""
    try:
        body = await request.json()
        logger.info(f"OnlyOffice callback received: {body}")

        status = body.get('status')
        file_url = body.get('url')
        changes_url = body.get('changesurl')
        
        # Get the original file path from the callback URL parameters
        original_file_path = request.query_params.get('filepath')
        if original_file_path:
            original_file_path = urllib.parse.unquote(original_file_path)
            logger.info(f"Original file path from callback URL: {original_file_path}")
        
        # OnlyOffice status codes:
        # 0 - Document not found
        # 1 - Document is being edited
        # 2 - Document is ready for saving (user closed editor)
        # 3 - Document saving error has occurred
        # 4 - Document is closed with no changes
        # 6 - Document is being edited, but the current document state is saved
        # 7 - Error has occurred while force saving the document
        
        if status in [2, 6] and file_url:
            # Status 2: Document is ready for saving (user closed editor)
            # Status 6: Document is being edited, but the current document state is saved (auto-save)
            logger.info(f"Document ready for saving (status: {status}), downloading from: {file_url}")
            
            try:
                # Download the updated document from OnlyOffice
                import httpx
                async with httpx.AsyncClient() as client:
                    response = await client.get(file_url)
                    response.raise_for_status()
                    document_content = response.content
                
                # Use the original file path from callback URL parameters
                if not original_file_path:
                    logger.error("No original file path provided in callback URL")
                    return {"error": 1, "message": "No original file path provided"}
                
                # Ensure the file path is safe and within static root
                decoded_file_path = urllib.parse.unquote(str(original_file_path))
                full_path = os.path.join(initializer.static_root, decoded_file_path)
                
                # Security check: ensure the file is within the static root
                if not os.path.abspath(full_path).startswith(os.path.abspath(initializer.static_root)):
                    logger.warning(f"Attempted to save file outside static root: {decoded_file_path}")
                    return {"error": 1, "message": "Access denied"}
                
                # Create directory if it doesn't exist
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                
                # Save the updated document
                with open(full_path, 'wb') as f:
                    f.write(document_content)
                
                logger.info(f"Document successfully saved to: {full_path}")
                
                # Also save changes if available (for tracking purposes)
                if changes_url and status == 2:
                    try:
                        async with httpx.AsyncClient() as client:
                            changes_response = await client.get(changes_url)
                            changes_response.raise_for_status()
                            changes_content = changes_response.content
                        
                        changes_path = full_path + ".changes"
                        with open(changes_path, 'wb') as f:
                            f.write(changes_content)
                        logger.info(f"Document changes saved to: {changes_path}")
                    except Exception as e:
                        logger.warning(f"Failed to save changes: {str(e)}")
                
                return {"error": 0}
                
            except Exception as e:
                logger.error(f"Error downloading/saving document: {str(e)}")
                return {"error": 1, "message": f"Failed to save document: {str(e)}"}
        
        elif status == 3:
            # Document saving error
            logger.error(f"OnlyOffice reported document saving error: {body}")
            return {"error": 1, "message": "Document saving error occurred"}
        
        elif status == 7:
            # Error occurred while force saving
            logger.error(f"OnlyOffice reported force saving error: {body}")
            return {"error": 1, "message": "Force saving error occurred"}
        
        else:
            # Other statuses (0, 1, 4) - just acknowledge
            logger.info(f"OnlyOffice callback acknowledged (status: {status})")
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
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                    "Last-Modified": str(os.path.getmtime(full_path)),
                    "ETag": f'"{os.path.getmtime(full_path)}"',
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
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
                "Last-Modified": str(os.path.getmtime(full_path)),
                "ETag": f'"{os.path.getmtime(full_path)}"',
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
