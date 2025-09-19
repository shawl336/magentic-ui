"""
Docker Manager for managing container lifecycle.

This module provides a DockerManager class that handles the lifecycle of Docker containers,
ensuring that containers are running when needed and managing their state.
"""

import asyncio
import logging
from typing import Optional, Dict, Union, List, Sequence, Any, Callable, ParamSpec
from autogen_ext.code_executors.docker import DockerCommandLineCodeExecutor
from pathlib import Path
from docker.types import DeviceRequest
from autogen_core.code_executor import (
    FunctionWithRequirements,
    FunctionWithRequirementsStr,
)
try:
    import docker
    from docker.errors import DockerException, ImageNotFound, NotFound
    from docker.models.containers import Container
except ImportError as e:
    raise RuntimeError(
        "Missing dependencies for DockerManager. Please ensure the docker package is installed."
    ) from e

logger = logging.getLogger(__name__)

A = ParamSpec("A")

class DockerManager(DockerCommandLineCodeExecutor):
    """
    A manager for Docker containers that handles container lifecycle.
    
    This manager ensures that a container with a specific image is running.
    If no container with the given name is running, it will start a new one.
    If a container with the given name exists but is not running, it will start it.
    Otherwise, it does nothing.
    """
    
    def __init__(
        self,
        image: str,
        container_name: Optional[str] = None,
        environment: Optional[Dict[str, str]] = None,
        volumes: Optional[Dict[str, Dict[str, str]]] = None,
        ports: Optional[Dict[str, str]] = None,
        working_dir: Optional[str] = None,
        *,
        timeout: int = 60,
        auto_remove: bool = True,
        stop_container: bool = True,
        device_requests: Optional[List[DeviceRequest]] = None,
        functions: Sequence[
            Union[
                FunctionWithRequirements[Any, A],
                Callable[..., Any],
                FunctionWithRequirementsStr,
            ]
        ] = [],
        functions_module: str = "functions",
        extra_hosts: Optional[Dict[str, str]] = None,
        init_command: Optional[str] = None,
        delete_tmp_files: bool = False,
        **kwargs,
    ):
        """
        Initialize the Docker Manager.
        
        Args:
            image (str): Docker image to use
            container_name (Optional[str]): Name for the container. If None, will use image name
            auto_remove (bool): Whether to automatically remove the container when stopped
            environment (Optional[Dict[str, str]]): Environment variables for the container
            volumes (Optional[Dict[str, Dict[str, str]]]): Volume mounts for the container
            ports (Optional[Dict[str, str]]): Port mappings for the container
            working_dir (Optional[str]): Working directory inside the container,
                This differs from the work_dir of DockerCommandLineCodeExecutor.
            extra_hosts (Optional[Dict[str, str]]): Extra host mappings
            init_command (Optional[str]): Initialization command to run before main command
            **kwargs: Any additional Docker create arguments (e.g., detach=True, tty=True, 
                     stdin_open=True, network_mode="host", privileged=True, etc.)
        """
        super().__init__(
            image=image,
            container_name=container_name,
            work_dir=Path("~/.magentic-ui").expanduser(), # not used
            bind_dir=None,
            timeout=timeout,
            auto_remove=auto_remove,
            stop_container=stop_container,
            device_requests=device_requests,
            functions=functions,
            functions_module=functions_module,
            extra_volumes=None,
            extra_hosts=extra_hosts,
            init_command=init_command,
            delete_tmp_files=delete_tmp_files,
        )
        
        self.environment = environment or {}
        # Expand user paths in volumes and merge with extra_volumes from parent
        self.volumes: Dict[str, Dict[str, str]] = {}
        
        # Then, add user-provided volumes (these can override extra_volumes if needed)
        if volumes:
            for host_path, mount_config in volumes.items():
                # Convert to Path and expand user if it's a string
                expanded_host_path = str(Path(host_path).expanduser())
                
                # Expand user paths in mount config
                expanded_mount_config = {}
                for key, value in mount_config.items():
                    expanded_key = key
                    expanded_mount_config[expanded_key] = value
                
                self.volumes[expanded_host_path] = expanded_mount_config
        
        self.ports = ports or {}
        self.auto_remove = auto_remove
        self.extra_hosts = extra_hosts or {}
        self._client: Optional[docker.DockerClient] = None
        self._container: Optional[Container] = None
        self._running = False
        self.working_dir = working_dir
        self._kwargs = kwargs or {}
    
    async def start(self) -> bool:
        """Start the container."""
        return await self.ensure_running()
    
    async def _get_docker_client(self) -> docker.DockerClient:
        """Get or create Docker client."""
        if self._client is None:
            try:
                self._client = docker.from_env()
                # Test connection
                await asyncio.to_thread(self._client.ping)
            except DockerException as e:
                if "FileNotFoundError" in str(e):
                    raise RuntimeError("Failed to connect to Docker. Please ensure Docker is installed and running.") from e
                raise
            except Exception as e:
                raise RuntimeError(f"Unexpected error while connecting to Docker: {str(e)}") from e
        return self._client
    
    async def _check_image_exists(self) -> bool:
        """Check if the Docker image exists locally."""
        try:
            client = await self._get_docker_client()
            await asyncio.to_thread(client.images.get, self._image)
            return True
        except ImageNotFound:
            return False
    
    async def _pull_image(self) -> None:
        """Pull the Docker image if it doesn't exist locally."""
        logger.info(f"Pulling image {self._image}...")
        try:
            client = await self._get_docker_client()
            await asyncio.to_thread(client.images.pull, self._image)
            logger.info(f"Successfully pulled image {self._image}")
        except Exception as e:
            logger.error(f"Failed to pull image {self._image}: {e}")
            raise
    
    async def _get_container_status(self) -> Optional[str]:
        """
        Get the status of the container with the given name.
        
        Returns:
            Optional[str]: Container status if found, None if not found
        """
        try:
            client = await self._get_docker_client()
            container = await asyncio.to_thread(client.containers.get, self.container_name)
            await asyncio.to_thread(container.reload)
            return container.status
        except NotFound:
            return None
        except Exception as e:
            logger.error(f"Error checking container status: {e}")
            return None
    
    async def _start_existing_container(self) -> bool:
        """
        Start an existing container.
        
        Returns:
            bool: True if successfully started, False otherwise
        """
        try:
            client = await self._get_docker_client()
            container = await asyncio.to_thread(client.containers.get, self.container_name)
            await asyncio.to_thread(container.start)
            await asyncio.to_thread(container.reload)
            
            if container.status == "running":
                self._container = container
                self._running = True
                logger.info(f"Successfully started existing container {self.container_name}")
                return True
            else:
                logger.error(f"Failed to start container {self.container_name}. Status: {container.status}")
                return False
        except Exception as e:
            logger.error(f"Error starting existing container {self.container_name}: {e}")
            return False
    
    async def _create_and_start_container(self) -> bool:
        """
        Create and start a new container.
        
        Returns:
            bool: True if successfully created and started, False otherwise
        """
        try:
            # Ensure image exists
            if not await self._check_image_exists():
                await self._pull_image()
            
            client = await self._get_docker_client()
            
            # Prepare container configuration with all Docker create arguments
            container_config: Dict[str, Any] = {
                "name": self.container_name,
                "auto_remove": self.auto_remove,
                "detach": True,  # equivalent to -d
                "tty": True,     # equivalent to -t
                "stdin_open": True,  # equivalent to -i
            }
            
            # Add standard Docker arguments
            if self.environment:
                container_config["environment"] = self.environment
            if self.volumes:
                container_config["volumes"] = self.volumes
            if self.ports:
                container_config["ports"] = self.ports
            if self.working_dir:
                container_config["working_dir"] = self.working_dir
            if self.extra_hosts:
                container_config["extra_hosts"] = self.extra_hosts
            if self._device_requests:
                container_config["device_requests"] = self._device_requests

            # Include any custom Docker arguments passed via kwargs
            # This allows users to pass any valid docker create argument
            container_config.update(self._kwargs)
            
            # Handle init command
            if self._init_command:
                command = f"{self._init_command}"
            else:
                command = f"/bin/bash"

            # Create container
            container = await asyncio.to_thread(client.containers.create, 
                                                image=self._image,
                                                command=command,
                                                **container_config)
            
            # Start container
            await asyncio.to_thread(container.start)
            await asyncio.to_thread(container.reload)
            if container.status == "running":
                self._container = container
                self._running = True
                logger.info(f"Successfully created and started container {self.container_name}")
                return True
            else:
                logger.error(f"Failed to start new container {self.container_name}. Status: {container.status}")
                return False
                
        except Exception as e:
            logger.error(f"Error creating and starting container {self.container_name}: {e}")
            return False
    
    async def ensure_running(self) -> bool:
        """
        Ensure the container is running.
        
        This method:
        1. Checks if a container with the given name exists and is running
        2. If not running but exists, starts it
        3. If doesn't exist, creates and starts a new one
        4. If already running, does nothing
        
        Returns:
            bool: True if container is running, False otherwise
        """
        try:
            status = await self._get_container_status()
            
            if status == "running":
                logger.info(f"Container {self.container_name} is already running")
                # Get the container reference
                client = await self._get_docker_client()
                self._container = await asyncio.to_thread(client.containers.get, self.container_name)
                self._running = True
                return True
            elif status is not None:
                # Container exists but not running
                logger.info(f"Container {self.container_name} exists but is not running. Starting it...")
                return await self._start_existing_container()
            else:
                # Container doesn't exist, create and start it
                logger.info(f"Container {self.container_name} doesn't exist. Creating and starting it...")
                return await self._create_and_start_container()
                
        except Exception as e:
            logger.error(f"Error ensuring container {self.container_name} is running: {e}")
            return False
    
    async def stop(self) -> bool:
        """
        Stop the container.
        
        Returns:
            bool: True if successfully stopped, False otherwise
        """
        if not self._running or self._container is None:
            logger.info(f"Container {self.container_name} is not running")
            return True
        
        try:
            await asyncio.to_thread(self._container.stop)
            await asyncio.to_thread(self._container.reload)
            
            if self._container.status in ["exited", "stopped"]:
                self._running = False
                logger.info(f"Successfully stopped container {self.container_name}")
                return True
            else:
                logger.error(f"Failed to stop container {self.container_name}. Status: {self._container.status}")
                return False
                
        except Exception as e:
            logger.error(f"Error stopping container {self.container_name}: {e}")
            return False
    
    async def remove(self) -> bool:
        """
        Remove the container.
        
        Returns:
            bool: True if successfully removed, False otherwise
        """
        try:
            client = await self._get_docker_client()
            container = await asyncio.to_thread(client.containers.get, self.container_name)
            await asyncio.to_thread(container.remove, force=True)
            
            self._container = None
            self._running = False
            logger.info(f"Successfully removed container {self.container_name}")
            return True
            
        except NotFound:
            logger.info(f"Container {self.container_name} not found for removal")
            self._container = None
            self._running = False
            return True
        except Exception as e:
            logger.error(f"Error removing container {self.container_name}: {e}")
            return False
    
    async def get_logs(self, tail: int = 100) -> str:
        """
        Get container logs.
        
        Args:
            tail (int): Number of lines to return from the end of logs
            
        Returns:
            str: Container logs
        """
        if self._container is None:
            return ""
        
        try:
            logs = await asyncio.to_thread(self._container.logs, tail=tail)
            return logs.decode("utf-8")
        except Exception as e:
            logger.error(f"Error getting logs for container {self.container_name}: {e}")
            return ""
    
    @property
    def is_running(self) -> bool:
        """Check if the container is running."""
        return self._running and self._container is not None
    
    @property
    def container_id(self) -> Optional[str]:
        """Get the container ID if running."""
        return self._container.id if self._container else None
    
    async def close(self) -> None:
        """Clean up resources."""
        if self._client:
            try:
                await asyncio.to_thread(self._client.close)
            except Exception as e:
                logger.error(f"Error closing Docker client: {e}")
            finally:
                self._client = None
        
        self._container = None
        self._running = False