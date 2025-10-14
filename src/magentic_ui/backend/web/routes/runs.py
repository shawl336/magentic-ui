# /api/runs routes
from typing import Dict, List, Any
from datetime import datetime
import os
import zipfile
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from loguru import logger

from ...datamodel import Message, Run, RunStatus, Session
from ..deps import get_db, get_websocket_manager
from ...teammanager import TeamManager

router = APIRouter()


class CreateRunRequest(BaseModel):
    session_id: int
    user_id: str


@router.post("/")
async def create_run(
    request: CreateRunRequest,
    db=Depends(get_db),
) -> Dict[str, Any]:
    """Return the existing run for a session or create a new one"""
    # First check if session exists and belongs to user
    session_response = db.get(
        Session,
        filters={"id": request.session_id, "user_id": request.user_id},
        return_json=False,
    )
    if not session_response.status or not session_response.data:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get the latest run for this session
    run_response = db.get(
        Run,
        filters={"session_id": request.session_id},
        return_json=False,
    )

    if not run_response.status or not run_response.data:
        # Create a new run if one doesn't exist
        try:
            run_response = db.upsert(
                Run(
                    created_at=datetime.now(),
                    session_id=request.session_id,
                    status=RunStatus.CREATED,
                    user_id=request.user_id,
                ),
                return_json=False,
            )
            if not run_response.status:
                raise HTTPException(status_code=400, detail=run_response.message)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    # Return the run (either existing or newly created)
    run = None
    if isinstance(run_response.data, list):
        # get the run with the latest created_at
        run = max(run_response.data, key=lambda x: x.created_at)
    else:
        run = run_response.data
    return {"status": run_response.status, "data": {"run_id": str(run.id)}}


# We might want to add these endpoints:


@router.get("/{run_id}")
async def get_run(run_id: int, db=Depends(get_db)) -> Dict[str, Any]:
    """Get run details including task and result"""
    run = db.get(Run, filters={"id": run_id}, return_json=False)
    if not run.status or not run.data:
        raise HTTPException(status_code=404, detail="Run not found")

    return {"status": True, "data": run.data[0]}


@router.get("/{run_id}/messages")
async def get_run_messages(run_id: int, db=Depends(get_db)) -> Dict[str, Any]:
    """Get all messages for a run"""
    messages = db.get(
        Message, filters={"run_id": run_id}, order="created_at asc", return_json=False
    )

    return {"status": True, "data": messages.data}


@router.post("/{run_id}/upload")
async def upload_files(
    run_id: int,
    files: List[UploadFile] = File(...),
    db=Depends(get_db),
    ws_manager=Depends(get_websocket_manager),
) -> Dict[str, Any]:
    """Upload files for a specific run and save them to the run directory"""
    # Verify run exists
    run_response = db.get(Run, filters={"id": run_id}, return_json=False)
    if not run_response.status or not run_response.data:
        raise HTTPException(status_code=404, detail="Run not found")

    run = run_response.data[0]

    # Create a temporary team manager to get run paths, this does not create any ressources
    team_manager = TeamManager(
        internal_workspace_root=ws_manager.internal_workspace_root,
        external_workspace_root=ws_manager.external_workspace_root,
        inside_docker=ws_manager.inside_docker,
        config=ws_manager.config,
        run_id=run_id,
        run_without_docker=ws_manager.run_without_docker,
    )

    # Prepare run paths (this creates the directories)
    paths = team_manager.prepare_run_paths(run=run)

    uploaded_files: List[Dict[str, Any]] = []

    for file in files:
        # Save file to run directory
        filename = file.filename
        try:
            assert filename is not None
        except Exception as e:
            logger.error(f"Error getting filename: {e}")
            continue

        file_path = paths.internal_run_dir / filename

        # Ensure the directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Write the file
        with open(file_path, "wb") as buffer:
            content = await file.read()
            buffer.write(content)

        uploaded_files.append(
            {
                "name": filename,
                "size": len(content),
                "path": str(file_path),
                "relative_path": f"files/user/{run.user_id}/{run.session_id}/{run.id}/{filename}",
            }
        )

    # Notify the team manager about the uploaded files so they don't get marked as generated
    if hasattr(ws_manager, "_team_managers") and run_id in ws_manager._team_managers:
        team_manager = ws_manager._team_managers[run_id]
        uploaded_file_names = {file["name"] for file in uploaded_files}
        team_manager.add_uploaded_files(uploaded_file_names)
    else:
        logger.warning(
            f"Team manager not found for run {run_id}, files uploaded but not tracked"
        )

    return {
        "status": True,
        "message": f"Successfully uploaded {len(uploaded_files)} files",
        "files": uploaded_files,
    }


@router.get("/{run_id}/download/{file_path:path}")
async def download_file(
    run_id: int,
    file_path: str,
    user_id: str,
    db=Depends(get_db),
    ws_manager=Depends(get_websocket_manager),
) -> FileResponse:
    """Download a specific file from a run"""
    # Verify run exists and belongs to user
    run_response = db.get(Run, filters={"id": run_id, "user_id": user_id}, return_json=False)
    if not run_response.status or not run_response.data:
        raise HTTPException(status_code=404, detail="Run not found or access denied")

    run = run_response.data[0]
    
    # Create team manager to get run paths
    team_manager = TeamManager(
        internal_workspace_root=ws_manager.internal_workspace_root,
        external_workspace_root=ws_manager.external_workspace_root,
        inside_docker=ws_manager.inside_docker,
        config=ws_manager.config,
        run_id=run_id,
        run_without_docker=ws_manager.run_without_docker,
    )
    
    # Prepare run paths
    paths = team_manager.prepare_run_paths(run=run)
    
    # Construct the full file path
    full_file_path = paths.internal_run_dir / file_path
    
    # Security check: ensure the file is within the run directory
    try:
        full_file_path = full_file_path.resolve()
        run_dir_resolved = paths.internal_run_dir.resolve()
        if not str(full_file_path).startswith(str(run_dir_resolved)):
            raise HTTPException(status_code=403, detail="Access denied: file outside run directory")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid file path: {str(e)}")
    
    # Check if file exists
    if not full_file_path.exists() or not full_file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    
    # Return the file
    return FileResponse(
        path=str(full_file_path),
        filename=full_file_path.name,
        media_type='application/octet-stream'
    )


@router.get("/{run_id}/download-dir")
async def download_directory(
    run_id: int,
    user_id: str,
    db=Depends(get_db),
    ws_manager=Depends(get_websocket_manager),
) -> StreamingResponse:
    """Download all files from a run as a zip archive"""
    # Verify run exists and belongs to user
    run_response = db.get(Run, filters={"id": run_id, "user_id": user_id}, return_json=False)
    if not run_response.status or not run_response.data:
        raise HTTPException(status_code=404, detail="Run not found or access denied")

    run = run_response.data[0]
    
    # Create team manager to get run paths
    team_manager = TeamManager(
        internal_workspace_root=ws_manager.internal_workspace_root,
        external_workspace_root=ws_manager.external_workspace_root,
        inside_docker=ws_manager.inside_docker,
        config=ws_manager.config,
        run_id=run_id,
        run_without_docker=ws_manager.run_without_docker,
    )
    
    # Prepare run paths
    paths = team_manager.prepare_run_paths(run=run)
    
    # Check if run directory exists
    if not paths.internal_run_dir.exists() or not paths.internal_run_dir.is_dir():
        raise HTTPException(status_code=404, detail="Run directory not found")
    
    def create_zip():
        """Create a zip file containing all files in the run directory"""
        # Create a temporary file for the zip
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
        temp_file.close()
        
        try:
            with zipfile.ZipFile(temp_file.name, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # Walk through all files in the run directory
                for root, _, files in os.walk(paths.internal_run_dir):
                    for file in files:
                        file_path = Path(root) / file
                        # Calculate relative path from run directory
                        relative_path = file_path.relative_to(paths.internal_run_dir)
                        # Add file to zip
                        zipf.write(file_path, relative_path)
            
            # Read the zip file and yield chunks
            with open(temp_file.name, 'rb') as f:
                while True:
                    chunk = f.read(8192)  # Read in 8KB chunks
                    if not chunk:
                        break
                    yield chunk
        finally:
            # Clean up the temporary file
            try:
                os.unlink(temp_file.name)
            except OSError:
                pass
    
    # Return the zip file as a streaming response
    return StreamingResponse(
        create_zip(),
        media_type='application/zip',
        headers={
            'Content-Disposition': f'attachment; filename="run_{run_id}_files.zip"'
        }
    )


@router.get("/{run_id}/files")
async def list_run_files(
    run_id: int,
    user_id: str,
    db=Depends(get_db),
    ws_manager=Depends(get_websocket_manager),
) -> Dict[str, Any]:
    """List all files in a run directory"""
    # Verify run exists and belongs to user
    run_response = db.get(Run, filters={"id": run_id, "user_id": user_id}, return_json=False)
    if not run_response.status or not run_response.data:
        raise HTTPException(status_code=404, detail="Run not found or access denied")

    run = run_response.data[0]
    
    # Create team manager to get run paths
    team_manager = TeamManager(
        internal_workspace_root=ws_manager.internal_workspace_root,
        external_workspace_root=ws_manager.external_workspace_root,
        inside_docker=ws_manager.inside_docker,
        config=ws_manager.config,
        run_id=run_id,
        run_without_docker=ws_manager.run_without_docker,
    )
    
    # Prepare run paths
    paths = team_manager.prepare_run_paths(run=run)
    
    # Check if run directory exists
    if not paths.internal_run_dir.exists() or not paths.internal_run_dir.is_dir():
        return {"status": True, "data": {"files": []}}
    
    files = []
    try:
        # Walk through all files in the run directory
        for root, _, filenames in os.walk(paths.internal_run_dir):
            for filename in filenames:
                file_path = Path(root) / filename
                # Calculate relative path from run directory
                relative_path = file_path.relative_to(paths.internal_run_dir)
                
                # Get file stats
                stat = file_path.stat()
                files.append({
                    "name": filename,
                    "path": str(relative_path),
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                    "is_file": file_path.is_file(),
                })
    except Exception as e:
        logger.error(f"Error listing files for run {run_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error listing files: {str(e)}")
    
    return {
        "status": True,
        "data": {
            "files": files,
            "run_id": run_id,
            "directory": str(paths.internal_run_dir)
        }
    }
