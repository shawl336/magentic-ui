# /api/runs routes
from typing import Dict, List, Any
from datetime import datetime
import os
import zipfile
import tempfile
from pathlib import Path
import hashlib
import hmac
import base64
import json
import time
import threading
from urllib.parse import urlencode
from time import mktime
from wsgiref.handlers import format_date_time

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


class SpeechToTextClient:
    """语音转文字客户端，基于讯飞IAT API（语音听写流式）"""
    
    # 音频帧状态标识
    STATUS_FIRST_FRAME = 0  # 第一帧的标识
    STATUS_CONTINUE_FRAME = 1  # 中间帧标识
    STATUS_LAST_FRAME = 2  # 最后一帧的标识
    
    def __init__(self, app_id: str, api_key: str, api_secret: str):
        self.app_id = app_id
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "ws://iat.xf-yun.com/v1"
        # IAT参数配置
        self.iat_params = {
            "domain": "slm",  # 使用大模型领域
            "language": "zh_cn",
            "accent": "mandarin",
            "dwa": "wpgs",  # 动态修正
            "result": {
                "encoding": "utf8",
                "compress": "raw",
                "format": "plain"
            }
        }
    
    def create_url(self) -> str:
        """生成IAT API的WebSocket URL（参考demo代码）"""
        # 生成RFC1123格式的时间戳
        now = datetime.now()
        date = format_date_time(mktime(now.timetuple()))
        
        # 拼接签名字符串
        signature_origin = "host: " + "iat.xf-yun.com" + "\n"
        signature_origin += "date: " + date + "\n"
        signature_origin += "GET " + "/v1 " + "HTTP/1.1"
        
        # 进行hmac-sha256加密
        signature_sha = hmac.new(
            self.api_secret.encode('utf-8'),
            signature_origin.encode('utf-8'),
            digestmod=hashlib.sha256
        ).digest()
        signature_sha = base64.b64encode(signature_sha).decode(encoding='utf-8')
        
        # 生成authorization
        authorization_origin = (
            f'api_key="{self.api_key}", algorithm="hmac-sha256", '
            f'headers="host date request-line", signature="{signature_sha}"'
        )
        authorization = base64.b64encode(authorization_origin.encode('utf-8')).decode(encoding='utf-8')
        
        # 将请求的鉴权参数组合为字典
        v = {
            "authorization": authorization,
            "date": date,
            "host": "iat.xf-yun.com"
        }
        
        # 拼接鉴权参数，生成url
        url = self.base_url + '?' + urlencode(v)
        return url
    
    def transcribe_audio(self, audio_file_path: str) -> str:
        """将音频文件转换为文字（使用IAT API）"""
        logger.info(f"[IAT客户端] ========== 开始语音转文字 ==========")
        logger.info(f"[IAT客户端] 音频文件路径: {audio_file_path}")
        
        try:
            import websocket
            logger.info(f"[IAT客户端] ✓ websocket-client 模块导入成功")
        except ImportError as e:
            logger.error(f"[IAT客户端] ✗ websocket-client 模块导入失败: {e}")
            logger.error("websocket-client package is required for speech-to-text. Install it with: pip install websocket-client")
            raise RuntimeError("websocket-client package is required. Please install it.")
        
        # 生成WebSocket URL
        logger.info(f"[IAT客户端] 步骤1: 生成WebSocket URL...")
        ws_url = self.create_url()
        logger.info(f"[IAT客户端] 步骤1: ✓ URL生成成功")
        
        # 存储识别结果（IAT API每次消息返回"到当前为止的完整结果"，使用最后一个最完整的结果）
        final_result = [""]  # 存储最终识别结果（使用列表以便在线程间共享）
        result_lock = threading.Lock()
        error_occurred = threading.Event()
        error_message = [None]
        ws_connected = [False]  # 使用列表以便在线程间共享
        recognition_complete = threading.Event()  # 标记识别是否完成
        message_count = [0]  # 收到的消息计数
        
        def on_message(ws, message):
            """收到websocket消息的处理（参考demo代码）"""
            try:
                message_count[0] += 1
                message_dict = json.loads(message)
                code = message_dict.get("header", {}).get("code", -1)
                status = message_dict.get("header", {}).get("status", -1)
                
                logger.debug(f"[IAT客户端] [消息处理] 收到第 {message_count[0]} 条消息 - code: {code}, status: {status}")
                
                if code != 0:
                    error_msg = f"请求错误：{code}"
                    logger.error(f"[IAT客户端] [消息处理] ✗ {error_msg}")
                    logger.error(f"[IAT客户端] [消息处理] 完整消息: {message[:500]}")
                    error_message[0] = error_msg
                    error_occurred.set()
                    ws.close()
                    return
                
                # 处理payload中的识别结果（参考demo代码逻辑）
                payload = message_dict.get("payload")
                if payload:
                    result = payload.get("result", {})
                    text = result.get("text", "")
                    
                    if text:
                        # 解码base64并解析JSON（完全按照demo代码的逻辑）
                        try:
                            # 先base64解码
                            text_decoded_bytes = base64.b64decode(text)
                            text_decoded_str = str(text_decoded_bytes, "utf8")
                            
                            # 解析JSON
                            text_decoded = json.loads(text_decoded_str)
                            text_ws = text_decoded.get('ws', [])
                            
                            result_text = ''
                            for i in text_ws:
                                cw_list = i.get("cw", [])
                                for j in cw_list:
                                    w = j.get("w", "")
                                    result_text += w
                            
                            if result_text:
                                with result_lock:
                                    # IAT API每次消息返回"到当前为止的完整识别结果"（递增的完整结果）
                                    # 只有当新结果比当前结果更长时，才更新（避免最后一条只有标点的消息覆盖完整结果）
                                    current_result = final_result[0]
                                    if len(result_text) > len(current_result):
                                        final_result[0] = result_text
                                        logger.info(f"[IAT客户端] [消息处理] ✓ 更新识别结果: '{result_text}' (长度: {len(result_text)})")
                        except Exception as e:
                            logger.warning(f"[IAT客户端] [消息处理] ⚠ 解析结果失败: {e}")
                
                # 只有当status==2时才标记识别完成并关闭连接（参考demo代码）
                if status == 2:
                    logger.info(f"[IAT客户端] [消息处理] ✓ 识别完成标记 (status=2), 已收到 {message_count[0]} 条消息")
                    recognition_complete.set()
                    # 等待一小段时间确保所有消息都已到达
                    time.sleep(0.1)
                    # 然后关闭连接
                    try:
                        ws.close()
                    except:
                        pass
                    
            except json.JSONDecodeError as e:
                logger.warning(f"[IAT客户端] [消息处理] ⚠ JSON解析失败: {e}")
            except Exception as e:
                logger.error(f"[IAT客户端] [消息处理] ✗ 处理消息错误: {e}")
                import traceback
                logger.error(f"[IAT客户端] [消息处理] 错误堆栈:\n{traceback.format_exc()}")
                error_message[0] = str(e)
                error_occurred.set()
        
        def on_error(ws, error):
            """收到websocket错误的处理"""
            logger.error(f"[IAT客户端] [错误处理] ✗ WebSocket错误: {error}")
            error_message[0] = str(error)
            error_occurred.set()
        
        def on_close(ws, close_status_code, close_msg):
            """收到websocket关闭的处理"""
            logger.info(f"[IAT客户端] [关闭处理] ✓ WebSocket连接已关闭")
            ws_connected[0] = False
        
        def on_open(ws):
            """收到websocket连接建立的处理（参考demo代码）"""
            logger.info(f"[IAT客户端] [连接处理] ✓ WebSocket连接已建立")
            ws_connected[0] = True
            
            def run(*args):
                frameSize = 1280  # 每一帧的音频大小
                intervel = 0.04  # 发送音频间隔(单位:s)
                status = self.STATUS_FIRST_FRAME  # 音频的状态信息
                
                try:
                    # 检查文件是否存在
                    if not os.path.exists(audio_file_path):
                        raise FileNotFoundError(f"音频文件不存在: {audio_file_path}")
                    
                    file_size = os.path.getsize(audio_file_path)
                    logger.info(f"[IAT客户端] [发送线程] 开始发送音频, 文件大小: {file_size} bytes")
                    
                    # 检查文件大小
                    if file_size == 0:
                        raise ValueError("音频文件为空")
                    if file_size < 1000:
                        logger.warning(f"[IAT客户端] [发送线程] ⚠ 音频文件较小 ({file_size} bytes)，可能无法识别")
                    
                    with open(audio_file_path, "rb") as fp:
                        frame_count = 0
                        while True:
                            # 检查连接状态
                            if not ws_connected[0]:
                                logger.warning(f"[IAT客户端] [发送线程] ⚠ 连接已断开，停止发送")
                                break
                            
                            buf = fp.read(frameSize)
                            
                            # 文件结束
                            if not buf:
                                # 发送最后一帧（空的）
                                if status != self.STATUS_LAST_FRAME:
                                    status = self.STATUS_LAST_FRAME
                                    audio_base64 = str(base64.b64encode(b''), 'utf-8')
                                    d = {
                                        "header": {
                                            "status": 2,
                                            "app_id": self.app_id
                                        },
                                        "parameter": {
                                            "iat": self.iat_params
                                        },
                                        "payload": {
                                            "audio": {
                                                "audio": audio_base64,
                                                "sample_rate": 16000,
                                                "encoding": "raw"
                                            }
                                        }
                                    }
                                    try:
                                        ws.send(json.dumps(d))
                                        logger.info(f"[IAT客户端] [发送线程] ✓ 发送最后一帧")
                                    except Exception as e:
                                        logger.warning(f"[IAT客户端] [发送线程] ⚠ 发送最后一帧失败（连接可能已关闭）: {e}")
                                break
                            
                            audio_base64 = str(base64.b64encode(buf), 'utf-8')
                            
                            # 第一帧处理
                            if status == self.STATUS_FIRST_FRAME:
                                d = {
                                    "header": {
                                        "status": 0,
                                        "app_id": self.app_id
                                    },
                                    "parameter": {
                                        "iat": self.iat_params
                                    },
                                    "payload": {
                                        "audio": {
                                            "audio": audio_base64,
                                            "sample_rate": 16000,
                                            "encoding": "raw"
                                        }
                                    }
                                }
                                ws.send(json.dumps(d))
                                status = self.STATUS_CONTINUE_FRAME
                                frame_count += 1
                                logger.debug(f"[IAT客户端] [发送线程] 发送第一帧, 大小: {len(buf)} bytes")
                            
                            # 中间帧处理
                            elif status == self.STATUS_CONTINUE_FRAME:
                                d = {
                                    "header": {
                                        "status": 1,
                                        "app_id": self.app_id
                                    },
                                    "parameter": {
                                        "iat": self.iat_params
                                    },
                                    "payload": {
                                        "audio": {
                                            "audio": audio_base64,
                                            "sample_rate": 16000,
                                            "encoding": "raw"
                                        }
                                    }
                                }
                                ws.send(json.dumps(d))
                                frame_count += 1
                                if frame_count % 10 == 0:
                                    logger.debug(f"[IAT客户端] [发送线程] 已发送 {frame_count} 帧")
                            
                            # 模拟音频采样间隔
                            time.sleep(intervel)
                    
                    logger.info(f"[IAT客户端] [发送线程] ✓ 音频数据发送完成, 共发送 {frame_count} 帧")
                    
                except Exception as e:
                    logger.error(f"[IAT客户端] [发送线程] ✗ 发送音频数据错误: {e}")
                    import traceback
                    logger.error(f"[IAT客户端] [发送线程] 错误堆栈:\n{traceback.format_exc()}")
                    error_message[0] = str(e)
                    error_occurred.set()
                    try:
                        ws.close()
                    except:
                        pass
            
            # 启动发送线程
            import _thread as thread
            thread.start_new_thread(run, ())
        
        # 创建WebSocket连接并运行
        logger.info(f"[IAT客户端] 步骤2: 建立WebSocket连接...")
        try:
            import ssl
            websocket.enableTrace(False)  # 禁用调试跟踪
            ws = websocket.WebSocketApp(
                ws_url,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close
            )
            ws.on_open = on_open
            logger.info(f"[IAT客户端] 步骤2: ✓ WebSocket客户端创建成功")
            
            # 运行WebSocket（阻塞直到连接关闭，设置超时）
            logger.info(f"[IAT客户端] 步骤3: 运行WebSocket连接...")
            # 设置超时：最多等待60秒
            timeout_occurred = [False]  # 使用列表以便在线程间共享
            
            def timeout_thread():
                """超时检测线程"""
                time.sleep(60)
                if not recognition_complete.is_set() and not error_occurred.is_set():
                    logger.error(f"[IAT客户端] 步骤3: ✗ WebSocket连接超时（60秒）")
                    timeout_occurred[0] = True
                    error_message[0] = "语音识别超时，请重试"
                    error_occurred.set()
                    try:
                        ws.close()
                    except:
                        pass
            
            # 启动超时检测线程
            timeout_t = threading.Thread(target=timeout_thread, daemon=True)
            timeout_t.start()
            
            try:
                ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})
            except Exception as e:
                if not timeout_occurred[0]:
                    logger.error(f"[IAT客户端] 步骤3: ✗ WebSocket运行异常: {e}")
                    raise
            
            logger.info(f"[IAT客户端] 步骤3: ✓ WebSocket连接已关闭")
            
            # 等待一小段时间确保所有消息都已处理
            time.sleep(0.1)
            
            # 检查是否有错误
            if error_occurred.is_set():
                error_msg = error_message[0] or "Speech recognition service returned an error"
                logger.error(f"[IAT客户端] 步骤3: ✗ 识别服务返回错误: {error_msg}")
                raise RuntimeError(f"Speech recognition service returned an error: {error_msg}")
            
            # 获取最终识别结果（IAT API每次消息返回完整结果，使用最后一个）
            with result_lock:
                full_text = final_result[0]
            
            logger.info(f"[IAT客户端] 步骤4: ✓ 识别结果处理完成")
            logger.info(f"[IAT客户端] 步骤4: - 共收到 {message_count[0]} 条消息")
            logger.info(f"[IAT客户端] 步骤4: - 最终文本长度: {len(full_text)} 字符")
            logger.info(f"[IAT客户端] 步骤4: - 最终文本内容: '{full_text}'")
            logger.info(f"[IAT客户端] ========== 语音转文字成功 ==========")
            
            return full_text if full_text else ""
            
        except Exception as e:
            logger.error(f"[IAT客户端] ========== 语音转文字失败 ==========")
            logger.error(f"[IAT客户端] 错误类型: {type(e).__name__}")
            logger.error(f"[IAT客户端] 错误消息: {str(e)}")
            import traceback
            logger.error(f"[IAT客户端] 错误堆栈:\n{traceback.format_exc()}")
            raise RuntimeError(f"Speech recognition failed: {str(e)}")


@router.post("/{run_id}/speech-to-text")
async def speech_to_text(
    run_id: int,
    audio_file: UploadFile = File(...),
    db=Depends(get_db),
    ws_manager=Depends(get_websocket_manager),
) -> Dict[str, Any]:
    """将上传的音频文件转换为文字"""
    logger.info(f"[语音转文字] ========== 开始处理语音转文字请求 ==========")
    logger.info(f"[语音转文字] run_id: {run_id}")
    
    try:
        # 验证run是否存在
        logger.info(f"[语音转文字] 步骤1: 验证run是否存在...")
        run_response = db.get(Run, filters={"id": run_id}, return_json=False)
        if not run_response.status or not run_response.data:
            logger.error(f"[语音转文字] 步骤1: ✗ Run {run_id} not found")
            raise HTTPException(status_code=404, detail="Run not found")
        
        run = run_response.data[0]
        logger.info(f"[语音转文字] 步骤1: ✓ Run {run_id} 验证成功")
        
        # 获取环境变量中的API配置（IAT API需要APPID、APIKey和APISecret）
        logger.info(f"[语音转文字] 步骤2: 检查环境变量...")
        
        # 检查所有可能的环境变量
        iat_app_id = os.getenv("IAT_APP_ID", "").strip()
        rtasr_app_id = os.getenv("RTASR_APP_ID", "").strip()
        app_id = iat_app_id or rtasr_app_id
        
        iat_api_key = os.getenv("IAT_API_KEY", "").strip()
        rtasr_api_key = os.getenv("RTASR_API_KEY", "").strip()
        api_key = iat_api_key or rtasr_api_key
        
        iat_api_secret = os.getenv("IAT_API_SECRET", "").strip()
        rtasr_api_secret = os.getenv("RTASR_API_SECRET", "").strip()
        api_secret = iat_api_secret or rtasr_api_secret
        
        # 调试日志：显示找到的环境变量（不显示完整值，只显示是否存在）
        logger.info(f"[语音转文字] 步骤2: - IAT_APP_ID: {'已设置' if iat_app_id else '未设置'}")
        logger.info(f"[语音转文字] 步骤2: - RTASR_APP_ID: {'已设置' if rtasr_app_id else '未设置'}")
        logger.info(f"[语音转文字] 步骤2: - IAT_API_KEY: {'已设置' if iat_api_key else '未设置'}")
        logger.info(f"[语音转文字] 步骤2: - RTASR_API_KEY: {'已设置' if rtasr_api_key else '未设置'}")
        logger.info(f"[语音转文字] 步骤2: - IAT_API_SECRET: {'已设置' if iat_api_secret else '未设置'}")
        logger.info(f"[语音转文字] 步骤2: - RTASR_API_SECRET: {'已设置' if rtasr_api_secret else '未设置'}")
        
        if not app_id or not api_key or not api_secret:
            missing_vars = []
            if not app_id:
                missing_vars.append("IAT_APP_ID (或 RTASR_APP_ID)")
            if not api_key:
                missing_vars.append("IAT_API_KEY (或 RTASR_API_KEY)")
            if not api_secret:
                missing_vars.append("IAT_API_SECRET (或 RTASR_API_SECRET)")
            
            error_msg = (
                f"语音转文字功能需要配置以下环境变量: {', '.join(missing_vars)}。"
                f"请设置这些环境变量后重启服务。"
                f"例如: export IAT_APP_ID='your_app_id' && export IAT_API_KEY='your_api_key' && export IAT_API_SECRET='your_api_secret'"
            )
            logger.error(f"[语音转文字] 步骤2: ✗ 环境变量缺失 - 缺失变量: {', '.join(missing_vars)}")
            raise HTTPException(
                status_code=500,
                detail=error_msg
            )
        
        # 验证环境变量格式（不记录敏感信息）
        logger.info(f"[语音转文字] 步骤2: ✓ 环境变量检查通过")
        logger.info(f"[语音转文字] 步骤2: - APP_ID 长度: {len(app_id)}")
        logger.info(f"[语音转文字] 步骤2: - API_KEY 长度: {len(api_key)}")
        logger.info(f"[语音转文字] 步骤2: - API_SECRET 长度: {len(api_secret)}")
        
        # 检查 app_id 格式
        if len(app_id) < 8:
            logger.warning(f"[语音转文字] 步骤2: ⚠ APP_ID 长度异常，可能格式不正确")
        
        # 创建team manager以获取run路径
        logger.info(f"[语音转文字] 步骤3: 创建TeamManager...")
        team_manager = TeamManager(
            internal_workspace_root=ws_manager.internal_workspace_root,
            external_workspace_root=ws_manager.external_workspace_root,
            inside_docker=ws_manager.inside_docker,
            config=ws_manager.config,
            run_id=run_id,
            run_without_docker=ws_manager.run_without_docker,
        )
        
        # 准备run路径
        logger.info(f"[语音转文字] 步骤4: 准备run路径...")
        paths = team_manager.prepare_run_paths(run=run)
        logger.info(f"[语音转文字] 步骤4: ✓ Run路径: {paths.internal_run_dir}")
        
        # 保存音频文件到run目录
        logger.info(f"[语音转文字] 步骤5: 保存音频文件...")
        audio_filename = audio_file.filename or "audio_recording.pcm"
        # 确保是PCM格式
        if not audio_filename.endswith('.pcm'):
            audio_filename = audio_filename.rsplit('.', 1)[0] + '.pcm'
        
        audio_file_path = paths.internal_run_dir / audio_filename
        logger.info(f"[语音转文字] 步骤5: - 文件名: {audio_filename}")
        logger.info(f"[语音转文字] 步骤5: - 保存路径: {audio_file_path}")
        
        # 确保目录存在
        audio_file_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 写入音频文件
        logger.info(f"[语音转文字] 步骤6: 读取并写入音频文件...")
        with open(audio_file_path, "wb") as buffer:
            content = await audio_file.read()
            buffer.write(content)
        
        file_size = os.path.getsize(audio_file_path)
        logger.info(f"[语音转文字] 步骤6: ✓ 音频文件保存成功, 大小: {file_size} bytes")
        
        # 验证音频文件大小（至少需要一定大小的数据才能识别）
        MIN_AUDIO_SIZE = 1000  # 至少1KB
        if file_size < MIN_AUDIO_SIZE:
            logger.warning(f"[语音转文字] 步骤6: ⚠ 音频文件过小 ({file_size} bytes < {MIN_AUDIO_SIZE} bytes)，可能无法识别")
            raise HTTPException(
                status_code=400,
                detail=f"音频文件过小（{file_size} bytes），请确保录音时长至少0.5秒"
            )
        
        # 调用语音转文字服务
        logger.info(f"[语音转文字] 步骤7: 调用语音转文字服务...")
        try:
            client = SpeechToTextClient(app_id=app_id, api_key=api_key, api_secret=api_secret)
            logger.info(f"[语音转文字] 步骤7: ✓ SpeechToTextClient 创建成功")
            
            # 使用 asyncio.to_thread 在异步上下文中运行同步的 transcribe_audio 方法
            import asyncio
            transcription = await asyncio.to_thread(client.transcribe_audio, str(audio_file_path))
            logger.info(f"[语音转文字] 步骤7: ✓ 语音转文字完成, 文本长度: {len(transcription)}")
            logger.info(f"[语音转文字] 步骤7: - 转换文本: {transcription[:100] if transcription else '空'}")
            
        except Exception as e:
            logger.error(f"[语音转文字] 步骤7: ✗ 语音转文字失败: {e}")
            logger.error(f"[语音转文字] 步骤7: - 错误类型: {type(e).__name__}")
            import traceback
            logger.error(f"[语音转文字] 步骤7: - 错误堆栈:\n{traceback.format_exc()}")
            raise
        
        logger.info(f"[语音转文字] ========== 语音转文字处理成功 ==========")
        return {
            "status": True,
            "message": "Speech to text conversion successful",
            "text": transcription,
            "audio_file": {
                "name": audio_filename,
                "path": str(audio_file_path),
                "relative_path": f"files/user/{run.user_id}/{run.session_id}/{run.id}/{audio_filename}",
            }
        }
        
    except HTTPException:
        logger.error(f"[语音转文字] ========== HTTP异常 ==========")
        raise
    except Exception as e:
        logger.error(f"[语音转文字] ========== 处理失败 ==========")
        logger.error(f"[语音转文字] 错误类型: {type(e).__name__}")
        logger.error(f"[语音转文字] 错误消息: {str(e)}")
        import traceback
        logger.error(f"[语音转文字] 错误堆栈:\n{traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to convert speech to text: {str(e)}"
        )
