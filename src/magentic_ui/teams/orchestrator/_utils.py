import json
import re
import os
from pathlib import Path
from typing import Any, Optional, List
from urllib.parse import urljoin
from typing_extensions import Annotated


def is_accepted_str(user_input: str) -> bool:
    LIST_OF_ACCEPTED_STRS = [
        "accept",
        "accepted",
        "acept",
        "run",
        "execute plan",
        "execute",
        "looks good",
        "do it",
        "accept plan",
        "accpt",
        "run plan",
        "sounds good",
        "i don't know. use your best judgment.",
        "i don't know, you figure it out, don't ask me again.",
        "接受",
        "已接受",
        "接受计划",
        "接受执行计划",
        "接受执行",
        "接受执行计划",
        "接受执行",
        "接收",
        "已接收",
        "接收计划",
        "接收执行计划",
        "接收执行",
        "接收执行计划",
        "接收执行",
        "同意",
        "同意计划",
        "同意执行计划",
        "同意执行",
        "同意执行计划",
        "同意执行",
        "执行计划",
        "执行",
        "去做吧",
        "继续"
        "听上去不错"
    ]
    user_input = user_input.lower()
    user_input = user_input.strip()
    if user_input in LIST_OF_ACCEPTED_STRS:
        return True
    return False


def extract_json_from_string(s: str) -> Optional[Any]:
    """
    Searches for a JSON object within the string and returns the loaded JSON if found, otherwise returns None.
    """
    # Regex to find JSON objects (greedy, matches first { to last })
    match = re.search(r"\{.*\}", s, re.DOTALL)
    if match:
        json_str = match.group(0)
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            return None
    return None

def download_files_and_diretories(file_directory_list: Annotated[List[str], "需要被发送给客户端的文件路径或文件夹路径的列表，可以同时包含文件路径和文件夹路径"], 
                                  from_path: Annotated[str, "文件和文件夹在服务器上的根目录，所有的文件夹都应该位于该父目录下"],
                                  target_directory: Annotated[str, "客户端下载文件的目标文件夹路径，用于生成下载链接"]) -> str:
    """
    将file_directory_list列表中给定的文件和文件夹，从服务器上的from_path路径发送给客户端。
    此函数在FastAPI服务器端运行，用于准备文件供客户端下载。
    
    参数:
        file_directory_list: 需要发送给客户端的文件/文件夹路径列表
        from_path: 服务器上文件和文件夹所在的根目录
        target_directory: 客户端下载文件的目标路径（用于生成下载链接）
        
    Returns:
        str: 包含下载链接和状态信息的消息
    """
    try:
        # Parse the file directory list
        if not isinstance(file_directory_list, list):
            return f"错误: file_directory_list 必须是一个列表(list), 但是得到的类型是: {type(file_directory_list)}"
        
        # Get server URL from environment or use default
        server_url = os.environ.get("MAGENTIC_UI_SERVER_URL", "http://localhost:8081")
        if not server_url.startswith(("http://", "https://")):
            server_url = f"http://{server_url}"
        
        available_files: list[str] = []
        failed_files: list[str] = []
        
        for file_path in file_directory_list:
            if not isinstance(file_path, str):
                failed_files.append(f"Invalid file path type: {file_path}")
                continue
            
            # Clean the file path (remove leading slash if present)
            clean_path: str = file_path.lstrip('/')
            
            # Construct the full server file path
            server_file_path = Path(from_path) / clean_path
            
            # Check if file exists on server
            if not server_file_path.exists():
                failed_files.append(f"File not found on server: {file_path}")
                continue
            
            # Generate download URL for client
            download_url = urljoin(server_url, f"/files/{clean_path}")
            
            # Check if it's a file (not directory)
            if server_file_path.is_file():
                available_files.append(f"{file_path} -> {download_url}")
            elif server_file_path.is_dir():
                # For directories, we can list the contents or provide a zip download
                available_files.append(f"Directory: {file_path} -> {download_url}")
            else:
                failed_files.append(f"Path is neither file nor directory: {file_path}")
        
        # Prepare result message
        result_parts: list[str] = []
        
        if available_files:
            result_parts.append(f"Successfully prepared {len(available_files)} files for client download:")
            for file_info in available_files:
                result_parts.append(f"  - {file_info}")
        
        if failed_files:
            result_parts.append(f"Failed to prepare {len(failed_files)} files:")
            for error in failed_files:
                result_parts.append(f"  - {error}")
        
        if not available_files and not failed_files:
            result_parts.append("No files to prepare for download.")
        
        return "\n".join(result_parts)
        
    except Exception as e:
        return f"Error in download_files_and_diretories: {str(e)}"
    