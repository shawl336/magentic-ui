import asyncio
import shlex
import os
from typing import List, Tuple, Dict, Any
import aiofiles
from typing_extensions import Annotated

from autogen_core import CancellationToken
from autogen_ext.code_executors.docker import DockerCommandLineCodeExecutor


async def exec_command_umask_patched(
    self: DockerCommandLineCodeExecutor,
    command: List[str],
    cancellation_token: CancellationToken,
) -> Tuple[str, int]:
    if self._container is None or not self._running:  # type: ignore
        raise ValueError(
            "Container is not running. Must first be started with either start or a context manager."
        )

    # wrap the original command in a shell so `umask` (a shell builtin) runs
    joined = shlex.join(command)
    shell_cmd = f"umask 000 && {joined}"
    command = ["sh", "-c", shell_cmd]

    exec_task = asyncio.create_task(
        asyncio.to_thread(self._container.exec_run, command)  # type: ignore
    )
    cancellation_token.link_future(exec_task)

    # Wait for the exec task to finish.
    try:
        result = await exec_task
        exit_code = result.exit_code
        output = result.output.decode("utf-8")
        if exit_code == 124:
            output += "\n Timeout"
        return output, exit_code
    except asyncio.CancelledError:
        # Schedule a task to kill the running command in the background.
        self._cancellation_tasks.append(  # type: ignore
            asyncio.create_task(self._kill_running_command(command))  # type: ignore
        )
        return "Code execution was cancelled.", 1


async def notify_to_download(
    run_dir: Annotated[str, "当前会话的所有文件的根目录"],
    file_and_directory_list: Annotated[List[str], "用户(客户端)可以下载的文件路径或文件夹路径的列表，可以同时包含文件路径和文件夹路径"], 
    target_directory: Annotated[str | None, "用户指定的下载存放路径，是客户端上的路径，与服务端无关。如果没有给定下载则不要指定，如果给空字符串也等价于没有指定下载路径"] = None
) -> Dict[str, Any]:
    r"""
    通知用户(客户端)下载file_and_directory_list列表中给定的文件和文件夹。target_directory是用户(客户端)上的下载保存路径，如果用户指定了则为用户指定的路径，否则为空字符串。
    此函数在FastAPI服务器端运行，用于准备文件供客户端下载。
    
    参数:
        file_and_directory_list: 需要发送给客户端的文件/文件夹路径列表
        target_directory: 客户端下载文件的目标路径（用于生成下载链接）
        
    返回:
        json: {
            "available_files": [{"name": "filename.mme", "type": "file" or "directory"}, ...],
            "nonexist_files": [{"name": "filename.mme", "type": "file" or "directory"}, ...],
            "target_directory": target_directory
        }
    """

    available_files: list[dict[str, str]] = []
    nonexist_files: list[dict[str, str]] = []
    
    for path in file_and_directory_list:
        # Remove the run_dir prefix, 'run_dir/file/dir/...' -> 'file/dir/...'
        full_path = ''
        relative_path = ''
        if path.startswith(run_dir):
            full_path = path
            relative_path = path[len(run_dir):]
        # Remove any expected prefix and run_dir, 'unexpected_prefix/run_dir/file/dir/...' -> 'file/dir/...'
        elif run_dir in path:
            start_idx = path.find(run_dir)
            full_path = path[start_idx:]
            relative_path = path[start_idx + len(run_dir):]
        else:
            # is this a single filename?
            filename = os.path.basename(path)
            if filename == path:
                full_path = os.path.join(run_dir, filename)
                relative_path = filename
            else:
                full_path = path
                relative_path = path
        
        if os.path.exists(full_path):
            available_files.append({"name": relative_path, "type": "file" if os.path.isfile(full_path) else "directory"})
        else:
            nonexist_files.append({"name": relative_path, "type": "unknown"})
            
    return {
        "available_files": available_files,
        "nonexist_files": nonexist_files,
        "target_directory": target_directory if target_directory else ""
    }
    
async def read_file(
    file_path: Annotated[str, "文件的路径或名字"], 
):
    """
    读取文件，并返回文件内容。只有UTF-8可解码的文件才会返回内容。
    如果文件不是UTF-8编码，则返回错误信息。
    """
    try:
        # First try to read as binary to check if it's UTF-8 decodable
        async with aiofiles.open(file_path, "rb") as f:
            binary_content = await f.read()
        
        # Try to decode as UTF-8
        try:
            content = binary_content.decode('utf-8')
            return content
        except UnicodeDecodeError:
            return f"错误：文件 '{file_path}' 不是UTF-8编码，无法读取内容。"
            
    except FileNotFoundError:
        return f"错误：文件 '{file_path}' 不存在。"
    except PermissionError:
        return f"错误：没有权限读取文件 '{file_path}'。"
    except Exception as e:
        return f"错误：读取文件 '{file_path}' 时发生异常：{str(e)}"