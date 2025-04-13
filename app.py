from fastapi import FastAPI, Depends, HTTPException, status, File, UploadFile, Form, Request, BackgroundTasks
from fastapi.security import OAuth2PasswordBearer
from fastapi.responses import JSONResponse, StreamingResponse
import jwt
from jwt.exceptions import PyJWTError
from typing import Optional, List, Dict, Any, Union, Generator
import docker
import uuid
import threading
import io
import subprocess
import tempfile
import os
import json
import time
import psutil
import shutil
import tarfile
import base64
from datetime import datetime, timedelta
from pydantic import BaseModel, Field

# JWT Configuration
SECRET_KEY = "09d25e094faa6ca2556c818166b7a9563b93f7099f6f0f4caa6cf63b88e8d3e7"  # Change this in production!
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 43200  # 30 days (30 * 24 * 60)

# Pydantic models for request validation
class ContainerCreateRequest(BaseModel):
    image: str
    command: Optional[str] = None
    name: Optional[str] = None
    ports: Optional[Dict[str, str]] = None
    volumes: Optional[Dict[str, Dict[str, str]]] = None
    environment: Optional[Dict[str, str]] = None
    network: Optional[str] = None
    restart_policy: Optional[Dict[str, Any]] = None
    
class NetworkCreateRequest(BaseModel):
    name: str
    driver: Optional[str] = "bridge"
    options: Optional[Dict[str, str]] = None
    labels: Optional[Dict[str, str]] = None
    
class ImagePullRequest(BaseModel):
    repository: str
    tag: Optional[str] = "latest"
    
class ContainerExecRequest(BaseModel):
    cmd: List[str]
    
class ContainerCopyRequest(BaseModel):
    path: str
    
class NetworkConnectRequest(BaseModel):
    container: str
    ipv4_address: Optional[str] = None
    ipv6_address: Optional[str] = None
    links: Optional[List[str]] = None
    aliases: Optional[List[str]] = None

# FastAPI app
app = FastAPI(
    title="RemoteDocker API", 
    description="Docker API for agents to access Docker desktop and execute docker actions",
    version="2.0.0"
)
docker_client = docker.from_env()

# OAuth2 scheme for JWT
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# Global store for asynchronous build logs and statuses (indexed by build_id)
build_logs_store = {}

# JWT Authentication functions
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        return username
    except PyJWTError:
        raise credentials_exception

# Helper function for build process
def run_build(build_id, file_bytes, tag, dockerfile):
    try:
        stream_obj = io.BytesIO(file_bytes)
        image, build_logs = docker_client.images.build(
            fileobj=stream_obj,
            tag=tag,
            dockerfile=dockerfile,
            rm=True,
            custom_context=True
        )
        for chunk in build_logs:
            if 'stream' in chunk:
                build_logs_store[build_id]["logs"].append(chunk["stream"])
            elif 'error' in chunk:
                build_logs_store[build_id]["logs"].append(chunk["error"])
        build_logs_store[build_id]["status"] = "completed"
        build_logs_store[build_id]["image_id"] = image.id
    except docker.errors.BuildError as be:
        logs = []
        for chunk in be.build_log:
            if 'stream' in chunk:
                logs.append(chunk['stream'])
            elif 'error' in chunk:
                logs.append(chunk['error'])
        build_logs_store[build_id]["logs"].extend(logs)
        build_logs_store[build_id]["status"] = "failed"
        build_logs_store[build_id]["error"] = str(be)
    except Exception as e:
        build_logs_store[build_id]["logs"].append(str(e))
        build_logs_store[build_id]["status"] = "failed"
        build_logs_store[build_id]["error"] = str(e)

# ===========================================
# Instructions Endpoint
# ===========================================
@app.get("/instructions", tags=["Documentation"])
async def get_instructions(_: str = Depends(get_current_user)):
    """
    Returns detailed instructions about the RemoteDocker API.
    """
    instructions = {
        "description": "This intermediary service acts as a bridge between your agentic AI system and Docker Desktop. It exposes an HTTP API to perform common Docker operations. The AI system can call these endpoints to trigger builds, manage images, and control containers without having direct access to the Docker daemon.",
        "endpoints": {
            "GET /version": {
                "description": "Returns Docker engine version and system information.",
                "example_response": {
                    "Version": "20.10.7",
                    "ApiVersion": "1.41"
                }
            },
            "GET /images": {
                "description": "Lists all available Docker images on the host.",
                "example_response": [
                    {"id": "sha256:12345", "tags": ["myimage:latest"]},
                    {"id": "sha256:67890", "tags": ["anotherimage:1.0"]}
                ]
            },
            "POST /build": {
                "description": "Builds a Docker image from a provided tarball containing the build context.",
                "request": {
                    "content_type": "multipart/form-data",
                    "fields": {
                        "context": "(file): Tarball of the build context (must include your Dockerfile and related files).",
                        "tag": "(string, optional): Image tag (default is latest).",
                        "dockerfile": "(string, optional): Path to the Dockerfile inside the tarball (default is Dockerfile)."
                    }
                },
                "example_response": {
                    "message": "Build completed",
                    "image_id": "sha256:abcde",
                    "logs": ["Step 1/4 : FROM python:3.8", "Step 2/4 : COPY . /app", "..."]
                }
            },
            "GET /containers": {
                "description": "Lists all containers (both running and stopped).",
                "example_response": [
                    {"id": "container123", "name": "mycontainer", "status": "exited", "image": ["myimage:latest"]},
                    {"id": "container456", "name": "anothercontainer", "status": "running", "image": ["anotherimage:1.0"]}
                ]
            },
            "POST /containers/create": {
                "description": "Creates a new container from a specified image.",
                "request": {
                    "content_type": "application/json",
                    "payload_example": {
                        "image": "myimage:latest",
                        "command": "python app.py",
                        "name": "my_new_container"
                    }
                },
                "example_response": {
                    "message": "Container created",
                    "container_id": "container789"
                }
            },
            "POST /containers/{container_id}/start": {
                "description": "Starts a specified container.",
                "example_response": {
                    "message": "Container started",
                    "container_id": "container789"
                }
            },
            "POST /containers/{container_id}/stop": {
                "description": "Stops a running container.",
                "example_response": {
                    "message": "Container stopped",
                    "container_id": "container789"
                }
            },
            "POST /containers/{container_id}/remove": {
                "description": "Removes a specified container (force removal is applied).",
                "example_response": {
                    "message": "Container removed",
                    "container_id": "container789"
                }
            },
            "GET /containers/{container_id}/logs": {
                "description": "Retrieves the logs from a specified container.",
                "example_response": {
                    "container_id": "container789",
                    "logs": "Log output from the container..."
                }
            },
            "POST /images/pull": {
                "description": "Pulls a Docker image from a registry.",
                "request": {
                    "content_type": "application/json",
                    "payload_example": {
                        "repository": "python",
                        "tag": "3.8-slim"
                    }
                },
                "example_response": {
                    "message": "Image pulled successfully",
                    "image_id": "sha256:abcdef..."
                }
            },
            "POST /networks/create": {
                "description": "Create a Docker network.",
                "request": {
                    "content_type": "application/json",
                    "payload_example": {
                        "name": "mynetwork",
                        "driver": "bridge"
                    }
                }
            },
            "GET /networks": {
                "description": "List all Docker networks."
            },
            "POST /compose/up": {
                "description": "Bring up a Docker Compose application.",
                "request": {
                    "content_type": "multipart/form-data",
                    "fields": {
                        "compose_file": "(file): Your docker-compose YAML file",
                        "project_name": "(string, optional): Project name for the compose application"
                    }
                },
                "action": "Runs docker compose -f <compose_file> up -d (with -p <project_name> if provided)."
            },
            "POST /compose/down": {
                "description": "Tear down a Docker Compose application.",
                "request": {
                    "content_type": "multipart/form-data",
                    "fields": {
                        "compose_file": "(file): Your docker-compose YAML file",
                        "project_name": "(string, optional): Project name for the compose application"
                    }
                },
                "action": "Runs docker compose -f <compose_file> down (optionally using -p <project_name>)."
            }
        },
        "authentication": {
            "description": "All endpoints are protected with JWT authentication. You need to include a valid JWT token in the Authorization header of your requests.",
            "header_format": "Authorization: Bearer <your_jwt_token>",
            "token_generation": "Use the provided token generation script to create a valid JWT token."
        }
    }
    return instructions

# ===========================================
# Core Docker API Endpoints
# ===========================================
@app.get("/version", tags=["Docker Info"])
async def version(_: str = Depends(get_current_user)):
    """
    Returns Docker engine version and system information.
    """
    try:
        version_info = docker_client.version()
        return version_info
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/info", tags=["Docker Info"])
async def docker_info(_: str = Depends(get_current_user)):
    """
    Returns detailed information about the Docker system.
    """
    try:
        info = docker_client.info()
        return info
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/system/df", tags=["Docker Info"])
async def system_disk_usage(_: str = Depends(get_current_user)):
    """
    Get disk usage information for Docker.
    """
    try:
        df = docker_client.df()
        return df
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.get("/system/events", tags=["Docker Info"])
async def system_events(
    since: Optional[str] = None,
    until: Optional[str] = None,
    filters: Optional[str] = None,
    _: str = Depends(get_current_user)
):
    """
    Get real-time events from the Docker daemon.
    
    - **since**: Show events since timestamp (RFC3339 or Unix timestamp)
    - **until**: Show events until timestamp (RFC3339 or Unix timestamp)
    - **filters**: JSON-encoded filters (e.g., {"container": ["container_id"], "event": ["start", "stop"]})
    """
    try:
        # Parse filters if provided
        filter_dict = {}
        if filters:
            filter_dict = json.loads(filters)
        
        # Get events
        events = docker_client.events(
            since=since,
            until=until,
            filters=filter_dict,
            decode=True
        )
        
        # Convert generator to list (limited to avoid memory issues)
        event_list = []
        for i, event in enumerate(events):
            event_list.append(event)
            if i >= 100:  # Limit to 100 events
                break
        
        return event_list
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/images", tags=["Images"])
async def list_images(
    all: bool = False,
    filters: Optional[str] = None,
    _: str = Depends(get_current_user)
):
    """
    Lists all available Docker images on the host with filtering options.
    
    - **all**: Show intermediate images (default: False)
    - **filters**: JSON-encoded filters (e.g., {"dangling": ["true"], "label": ["maintainer=someone"]})
    """
    try:
        # Parse filters if provided
        filter_dict = {}
        if filters:
            filter_dict = json.loads(filters)
        
        images = docker_client.images.list(all=all, filters=filter_dict)
        result = []
        
        for image in images:
            image_info = {
                'id': image.id,
                'tags': image.tags,
                'short_id': image.short_id,
                'created': image.attrs.get('Created'),
                'size': image.attrs.get('Size'),
                'virtual_size': image.attrs.get('VirtualSize'),
                'labels': image.attrs.get('Config', {}).get('Labels', {}) if image.attrs.get('Config') else {},
                'repo_digests': image.attrs.get('RepoDigests', [])
            }
            result.append(image_info)
            
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/images/pull", tags=["Images"])
async def pull_image(
    pull_data: ImagePullRequest,
    _: str = Depends(get_current_user)
):
    """
    Pulls a Docker image from a registry.
    
    - **repository**: Image repository (e.g., 'ubuntu', 'nginx')
    - **tag**: Image tag (default: 'latest')
    """
    try:
        image = docker_client.images.pull(pull_data.repository, tag=pull_data.tag)
        return {
            "message": "Image pulled successfully", 
            "image_id": image.id,
            "tags": image.tags
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/images/{image_id}/tag", tags=["Images"])
async def tag_image(
    image_id: str,
    repository: str,
    tag: Optional[str] = None,
    _: str = Depends(get_current_user)
):
    """
    Tag an image with a repository and tag.
    
    - **repository**: Repository to tag the image with
    - **tag**: Tag to apply (default: latest)
    """
    try:
        image = docker_client.images.get(image_id)
        image.tag(repository, tag=tag)
        return {
            "message": "Image tagged successfully",
            "image_id": image.id,
            "repository": repository,
            "tag": tag or "latest"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/images/{image_id}/remove", tags=["Images"])
async def remove_image(
    image_id: str,
    force: bool = False,
    noprune: bool = False,
    _: str = Depends(get_current_user)
):
    """
    Remove an image.
    
    - **force**: Force removal even if the image is being used (default: False)
    - **noprune**: Do not delete untagged parents (default: False)
    """
    try:
        # Get image info before removal for the response
        image = docker_client.images.get(image_id)
        tags = image.tags
        
        # Remove the image
        docker_client.images.remove(image_id, force=force, noprune=noprune)
        
        return {
            "message": "Image removed successfully",
            "image_id": image_id,
            "tags": tags
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/images/prune", tags=["Images"])
async def prune_images(
    all: bool = False,
    filters: Optional[str] = None,
    _: str = Depends(get_current_user)
):
    """
    Remove unused images.
    
    - **all**: Remove all unused images, not just dangling ones (default: False)
    - **filters**: JSON-encoded filters (e.g., {"until": ["24h"]})
    """
    try:
        # Parse filters if provided
        filter_dict = {}
        if filters:
            filter_dict = json.loads(filters)
        
        # Prune images
        pruned = docker_client.images.prune(filters=filter_dict)
        
        return {
            "message": "Images pruned",
            "images_deleted": pruned.get("ImagesDeleted", []),
            "space_reclaimed": pruned.get("SpaceReclaimed", 0)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ===========================================
# Image Debugging Endpoints
# ===========================================
@app.get("/images/{image_id}/inspect", tags=["Image Debugging"])
async def inspect_image(image_id: str, _: str = Depends(get_current_user)):
    """
    Get detailed information about an image.
    """
    try:
        image = docker_client.images.get(image_id)
        return image.attrs
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/images/{image_id}/history", tags=["Image Debugging"])
async def image_history(image_id: str, _: str = Depends(get_current_user)):
    """
    Get the history of an image showing the layers.
    """
    try:
        image = docker_client.images.get(image_id)
        history = docker_client.api.history(image_id)
        
        # Format the history for better readability
        formatted_history = []
        for layer in history:
            formatted_layer = {
                "id": layer.get("Id", ""),
                "created": layer.get("Created", 0),
                "created_by": layer.get("CreatedBy", ""),
                "size": layer.get("Size", 0),
                "comment": layer.get("Comment", ""),
                "tags": layer.get("Tags", [])
            }
            formatted_history.append(formatted_layer)
        
        return {
            "image_id": image_id,
            "tags": image.tags,
            "layers": formatted_history
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/images/search", tags=["Image Debugging"])
async def search_images(
    term: str,
    limit: int = 25,
    filters: Optional[str] = None,
    _: str = Depends(get_current_user)
):
    """
    Search for images on Docker Hub.
    
    - **term**: Search term
    - **limit**: Maximum number of results (default: 25)
    - **filters**: JSON-encoded filters (e.g., {"is-official": ["true"]})
    """
    try:
        # Parse filters if provided
        filter_dict = {}
        if filters:
            filter_dict = json.loads(filters)
        
        # Search for images
        results = docker_client.api.search(term, limit=limit, filters=filter_dict)
        
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/images/{image_id}/layers", tags=["Image Debugging"])
async def image_layers(image_id: str, _: str = Depends(get_current_user)):
    """
    Get detailed information about image layers.
    """
    try:
        # Get image info
        image = docker_client.images.get(image_id)
        
        # Get layer info from the image attributes
        layers = []
        if "RootFS" in image.attrs and "Layers" in image.attrs["RootFS"]:
            raw_layers = image.attrs["RootFS"]["Layers"]
            
            # Get history for additional layer info
            history = docker_client.api.history(image_id)
            
            # Combine layer and history information
            for i, layer_id in enumerate(raw_layers):
                layer_info = {
                    "id": layer_id,
                    "index": i,
                }
                
                # Add history info if available
                if i < len(history):
                    layer_info.update({
                        "created": history[i].get("Created", 0),
                        "created_by": history[i].get("CreatedBy", ""),
                        "size": history[i].get("Size", 0),
                        "comment": history[i].get("Comment", "")
                    })
                
                layers.append(layer_info)
        
        return {
            "image_id": image_id,
            "tags": image.tags,
            "layer_count": len(layers),
            "layers": layers
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ===========================================
# Asynchronous Build Endpoints
# ===========================================
@app.post("/build", tags=["Images"])
async def build_image(
    context: UploadFile = File(...),
    tag: str = Form("latest"),
    dockerfile: str = Form("Dockerfile"),
    _: str = Depends(get_current_user)
):
    """
    Builds a Docker image from a provided tarball containing the build context.
    """
    try:
        build_id = str(uuid.uuid4())
        build_logs_store[build_id] = {"status": "in_progress", "logs": [], "image_id": None}
        file_bytes = await context.read()
        thread = threading.Thread(target=run_build, args=(build_id, file_bytes, tag, dockerfile))
        thread.start()
        return {"message": "Build started", "build_id": build_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/builds/{build_id}/logs", tags=["Images"])
async def get_build_logs(build_id: str, _: str = Depends(get_current_user)):
    """
    Retrieves logs for a specific build process.
    """
    if build_id in build_logs_store:
        entry = build_logs_store[build_id]
        return {
            "build_id": build_id,
            "status": entry["status"],
            "logs": entry["logs"],
            "image_id": entry["image_id"],
            "error": entry.get("error")
        }
    else:
        raise HTTPException(status_code=404, detail="Build ID not found")

# ===========================================
# Container Management Endpoints
# ===========================================
@app.get("/containers", tags=["Containers"])
async def list_containers(all: bool = True, _: str = Depends(get_current_user)):
    """
    Lists all containers (both running and stopped).
    
    - **all**: Include stopped containers (default: True)
    """
    try:
        containers = docker_client.containers.list(all=all)
        result = [{
            'id': container.id,
            'name': container.name,
            'status': container.status,
            'image': container.image.tags,
            'created': container.attrs.get('Created'),
            'ports': container.attrs.get('NetworkSettings', {}).get('Ports', {}),
            'labels': container.attrs.get('Config', {}).get('Labels', {})
        } for container in containers]
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/containers/create", tags=["Containers"])
async def create_container(container_data: ContainerCreateRequest, _: str = Depends(get_current_user)):
    """
    Creates a new container from a specified image with advanced configuration options.
    """
    try:
        # Convert the Pydantic model to a dictionary and remove None values
        container_config = {k: v for k, v in container_data.dict().items() if v is not None}
        
        # Create the container with the provided configuration
        container = docker_client.containers.create(**container_config, detach=True)
        
        return {
            "message": "Container created", 
            "container_id": container.id,
            "name": container.name
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/containers/{container_id}/start", tags=["Containers"])
async def start_container(container_id: str, _: str = Depends(get_current_user)):
    """
    Starts a specified container.
    """
    try:
        container = docker_client.containers.get(container_id)
        container.start()
        return {"message": "Container started", "container_id": container.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/containers/{container_id}/stop", tags=["Containers"])
async def stop_container(container_id: str, timeout: int = 10, _: str = Depends(get_current_user)):
    """
    Stops a running container.
    
    - **timeout**: Seconds to wait before killing the container (default: 10)
    """
    try:
        container = docker_client.containers.get(container_id)
        container.stop(timeout=timeout)
        return {"message": "Container stopped", "container_id": container.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/containers/{container_id}/restart", tags=["Containers"])
async def restart_container(container_id: str, timeout: int = 10, _: str = Depends(get_current_user)):
    """
    Restarts a container.
    
    - **timeout**: Seconds to wait before killing the container (default: 10)
    """
    try:
        container = docker_client.containers.get(container_id)
        container.restart(timeout=timeout)
        return {"message": "Container restarted", "container_id": container.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/containers/{container_id}/pause", tags=["Containers"])
async def pause_container(container_id: str, _: str = Depends(get_current_user)):
    """
    Pauses a running container.
    """
    try:
        container = docker_client.containers.get(container_id)
        container.pause()
        return {"message": "Container paused", "container_id": container.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/containers/{container_id}/unpause", tags=["Containers"])
async def unpause_container(container_id: str, _: str = Depends(get_current_user)):
    """
    Unpauses a paused container.
    """
    try:
        container = docker_client.containers.get(container_id)
        container.unpause()
        return {"message": "Container unpaused", "container_id": container.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/containers/{container_id}/remove", tags=["Containers"])
async def remove_container(
    container_id: str, 
    force: bool = True, 
    volumes: bool = False, 
    _: str = Depends(get_current_user)
):
    """
    Removes a specified container.
    
    - **force**: Force removal even if container is running (default: True)
    - **volumes**: Remove associated volumes (default: False)
    """
    try:
        container = docker_client.containers.get(container_id)
        container.remove(force=force, v=volumes)
        return {
            "message": "Container removed", 
            "container_id": container_id,
            "volumes_removed": volumes
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/containers/{container_id}/logs", tags=["Containers"])
async def container_logs(
    container_id: str, 
    tail: int = 100, 
    timestamps: bool = False, 
    since: Optional[int] = None,
    until: Optional[int] = None,
    follow: bool = False,
    _: str = Depends(get_current_user)
):
    """
    Retrieves the logs from a specified container with filtering options.
    
    - **tail**: Number of lines to show from the end (default: 100, use 'all' for all logs)
    - **timestamps**: Add timestamps to logs (default: False)
    - **since**: Show logs since timestamp (Unix timestamp)
    - **until**: Show logs until timestamp (Unix timestamp)
    - **follow**: Follow log output (default: False)
    """
    try:
        container = docker_client.containers.get(container_id)
        
        # Handle 'all' for tail
        if tail == 'all':
            tail = 'all'
        
        logs = container.logs(
            stdout=True, 
            stderr=True,
            stream=follow,
            timestamps=timestamps,
            tail=tail,
            since=since,
            until=until
        ).decode('utf-8')
        
        return {"container_id": container_id, "logs": logs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ===========================================
# Container Debugging Endpoints
# ===========================================
@app.get("/containers/{container_id}/inspect", tags=["Container Debugging"])
async def inspect_container(container_id: str, _: str = Depends(get_current_user)):
    """
    Returns detailed information about a container.
    """
    try:
        container = docker_client.containers.get(container_id)
        return container.attrs
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/containers/{container_id}/top", tags=["Container Debugging"])
async def container_top(container_id: str, ps_args: str = "", _: str = Depends(get_current_user)):
    """
    Display the running processes of a container.
    
    - **ps_args**: Arguments to pass to ps command (e.g., 'aux')
    """
    try:
        container = docker_client.containers.get(container_id)
        if container.status != "running":
            raise HTTPException(status_code=400, detail="Container is not running")
        
        top_result = docker_client.api.top(container_id, ps_args=ps_args)
        return top_result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/containers/{container_id}/stats", tags=["Container Debugging"])
async def container_stats(container_id: str, stream: bool = False, _: str = Depends(get_current_user)):
    """
    Get container resource usage statistics.
    
    - **stream**: Stream statistics (default: False)
    """
    try:
        container = docker_client.containers.get(container_id)
        if container.status != "running":
            raise HTTPException(status_code=400, detail="Container is not running")
        
        stats = docker_client.api.stats(container_id, stream=False)
        
        # Extract and format the most relevant stats
        formatted_stats = {
            "cpu_usage": stats.get("cpu_stats", {}).get("cpu_usage", {}).get("total_usage"),
            "cpu_system_usage": stats.get("cpu_stats", {}).get("system_cpu_usage"),
            "memory_usage": stats.get("memory_stats", {}).get("usage"),
            "memory_limit": stats.get("memory_stats", {}).get("limit"),
            "memory_percent": (
                stats.get("memory_stats", {}).get("usage", 0) / 
                stats.get("memory_stats", {}).get("limit", 1) * 100
                if stats.get("memory_stats", {}).get("limit", 0) > 0 else 0
            ),
            "network_rx_bytes": sum(
                interface.get("rx_bytes", 0) 
                for interface in stats.get("networks", {}).values()
            ),
            "network_tx_bytes": sum(
                interface.get("tx_bytes", 0) 
                for interface in stats.get("networks", {}).values()
            ),
            "block_read_bytes": stats.get("blkio_stats", {}).get("io_service_bytes_recursive", [{}])[0].get("value", 0) 
                if stats.get("blkio_stats", {}).get("io_service_bytes_recursive") else 0,
            "block_write_bytes": stats.get("blkio_stats", {}).get("io_service_bytes_recursive", [{}])[1].get("value", 0)
                if stats.get("blkio_stats", {}).get("io_service_bytes_recursive") and len(stats.get("blkio_stats", {}).get("io_service_bytes_recursive", [])) > 1 else 0,
        }
        
        # Include raw stats for advanced debugging
        formatted_stats["raw_stats"] = stats
        
        return formatted_stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/containers/{container_id}/exec", tags=["Container Debugging"])
async def exec_in_container(
    container_id: str, 
    exec_data: ContainerExecRequest,
    _: str = Depends(get_current_user)
):
    """
    Execute a command inside a running container.
    
    - **cmd**: Command to execute as a list of strings
    """
    try:
        container = docker_client.containers.get(container_id)
        if container.status != "running":
            raise HTTPException(status_code=400, detail="Container is not running")
        
        # Execute the command
        exec_id = docker_client.api.exec_create(
            container_id, 
            exec_data.cmd,
            stdout=True,
            stderr=True
        )
        
        output = docker_client.api.exec_start(exec_id).decode('utf-8')
        exec_info = docker_client.api.exec_inspect(exec_id)
        
        return {
            "container_id": container_id,
            "exit_code": exec_info.get("ExitCode"),
            "output": output,
            "exec_id": exec_id
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/containers/{container_id}/changes", tags=["Container Debugging"])
async def container_changes(container_id: str, _: str = Depends(get_current_user)):
    """
    Get filesystem changes in a container.
    """
    try:
        container = docker_client.containers.get(container_id)
        changes = container.diff()
        
        # Format the changes for better readability
        formatted_changes = []
        for change in changes:
            change_type = {0: "modified", 1: "added", 2: "deleted"}.get(change.get("Kind"), "unknown")
            formatted_changes.append({
                "path": change.get("Path"),
                "type": change_type
            })
        
        return formatted_changes
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/containers/{container_id}/file", tags=["Container Debugging"])
async def get_container_file(
    container_id: str, 
    path: str,
    _: str = Depends(get_current_user)
):
    """
    Get a file from a container.
    
    - **path**: Path to the file inside the container
    """
    try:
        container = docker_client.containers.get(container_id)
        
        # Create a temporary directory to store the file
        with tempfile.TemporaryDirectory() as tmpdirname:
            # Get the file from the container
            bits, stat = container.get_archive(path)
            
            # Save the tar archive to a temporary file
            tar_path = os.path.join(tmpdirname, "archive.tar")
            with open(tar_path, 'wb') as f:
                for chunk in bits:
                    f.write(chunk)
            
            # Extract the file from the tar archive
            with tarfile.open(tar_path) as tar:
                # Get the name of the file in the archive
                file_name = os.path.basename(path)
                
                # Extract the file to the temporary directory
                tar.extractall(path=tmpdirname)
                
                # Find the extracted file
                extracted_files = os.listdir(tmpdirname)
                extracted_file_path = None
                
                for extracted_file in extracted_files:
                    if extracted_file != "archive.tar":
                        extracted_file_path = os.path.join(tmpdirname, extracted_file)
                        break
                
                if not extracted_file_path:
                    raise HTTPException(status_code=404, detail="File not found in container")
                
                # Read the file content
                with open(extracted_file_path, 'rb') as f:
                    file_content = f.read()
                
                # Determine if the file is binary or text
                try:
                    text_content = file_content.decode('utf-8')
                    is_binary = False
                except UnicodeDecodeError:
                    text_content = None
                    is_binary = True
                
                # Return the file content
                if is_binary:
                    # For binary files, return base64 encoded content
                    return {
                        "container_id": container_id,
                        "path": path,
                        "file_name": file_name,
                        "is_binary": True,
                        "content": base64.b64encode(file_content).decode('utf-8'),
                        "size": len(file_content),
                        "file_info": stat
                    }
                else:
                    # For text files, return the content as is
                    return {
                        "container_id": container_id,
                        "path": path,
                        "file_name": file_name,
                        "is_binary": False,
                        "content": text_content,
                        "size": len(file_content),
                        "file_info": stat
                    }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/containers/{container_id}/health", tags=["Container Debugging"])
async def container_health(container_id: str, _: str = Depends(get_current_user)):
    """
    Get container health status.
    """
    try:
        container = docker_client.containers.get(container_id)
        health_info = container.attrs.get("State", {}).get("Health", {})
        
        if not health_info:
            return {
                "container_id": container_id,
                "has_healthcheck": False,
                "status": "no healthcheck configured"
            }
        
        return {
            "container_id": container_id,
            "has_healthcheck": True,
            "status": health_info.get("Status"),
            "failing_streak": health_info.get("FailingStreak"),
            "log": health_info.get("Log", [])
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ===========================================
# Network Management Endpoints
# ===========================================
@app.post("/networks/create", tags=["Networks"])
async def create_network(network_data: NetworkCreateRequest, _: str = Depends(get_current_user)):
    """
    Create a Docker network with advanced configuration options.
    """
    try:
        # Convert the Pydantic model to a dictionary and remove None values
        network_config = {k: v for k, v in network_data.dict().items() if v is not None}
        
        # Create the network with the provided configuration
        network = docker_client.networks.create(**network_config)
        
        return {
            "message": "Network created", 
            "network_id": network.id,
            "name": network.name
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/networks", tags=["Networks"])
async def list_networks(_: str = Depends(get_current_user)):
    """
    List all Docker networks with detailed information.
    """
    try:
        networks = docker_client.networks.list()
        result = []
        for net in networks:
            result.append({
                "id": net.id,
                "name": net.name,
                "driver": net.attrs.get("Driver", ""),
                "scope": net.attrs.get("Scope", ""),
                "ipam": net.attrs.get("IPAM", {}),
                "internal": net.attrs.get("Internal", False),
                "attachable": net.attrs.get("Attachable", False),
                "ingress": net.attrs.get("Ingress", False),
                "containers": len(net.attrs.get("Containers", {})),
                "options": net.attrs.get("Options", {})
            })
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/networks/{network_id}", tags=["Networks"])
async def inspect_network(network_id: str, _: str = Depends(get_current_user)):
    """
    Inspect a Docker network with full details.
    """
    try:
        network = docker_client.networks.get(network_id)
        return network.attrs
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/networks/{network_id}/connect", tags=["Networks"])
async def connect_container_to_network(
    network_id: str, 
    connect_data: NetworkConnectRequest,
    _: str = Depends(get_current_user)
):
    """
    Connect a container to a network with advanced options.
    
    - **container**: Container ID or name to connect
    - **ipv4_address**: Optional IPv4 address to assign to the container
    - **ipv6_address**: Optional IPv6 address to assign to the container
    - **links**: Optional container links
    - **aliases**: Optional network-scoped aliases
    """
    try:
        network = docker_client.networks.get(network_id)
        
        # Prepare endpoint config
        endpoint_config = {}
        if connect_data.ipv4_address or connect_data.ipv6_address:
            endpoint_config["ipam"] = {}
            if connect_data.ipv4_address:
                endpoint_config["ipam"]["ipv4_address"] = connect_data.ipv4_address
            if connect_data.ipv6_address:
                endpoint_config["ipam"]["ipv6_address"] = connect_data.ipv6_address
        
        if connect_data.links:
            endpoint_config["links"] = connect_data.links
        
        if connect_data.aliases:
            endpoint_config["aliases"] = connect_data.aliases
        
        # Connect the container to the network
        network.connect(connect_data.container, **endpoint_config)
        
        return {
            "message": "Container connected to network",
            "network_id": network_id,
            "container": connect_data.container
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/networks/{network_id}/disconnect", tags=["Networks"])
async def disconnect_container_from_network(
    network_id: str, 
    container: str,
    force: bool = False,
    _: str = Depends(get_current_user)
):
    """
    Disconnect a container from a network.
    
    - **container**: Container ID or name to disconnect
    - **force**: Force disconnection even if the container is not connected (default: False)
    """
    try:
        network = docker_client.networks.get(network_id)
        network.disconnect(container, force=force)
        
        return {
            "message": "Container disconnected from network",
            "network_id": network_id,
            "container": container
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/networks/{network_id}/remove", tags=["Networks"])
async def remove_network(network_id: str, _: str = Depends(get_current_user)):
    """
    Remove a Docker network.
    """
    try:
        network = docker_client.networks.get(network_id)
        network.remove()
        
        return {
            "message": "Network removed",
            "network_id": network_id
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/networks/{network_id}/containers", tags=["Networks"])
async def network_containers(network_id: str, _: str = Depends(get_current_user)):
    """
    List containers in a Docker network with detailed network configuration.
    """
    try:
        network = docker_client.networks.get(network_id)
        containers_info = network.attrs.get("Containers", {})
        result = []
        for cid, data in containers_info.items():
            # Get more detailed container info
            try:
                container = docker_client.containers.get(cid)
                container_status = container.status
                container_name = container.name
                container_image = container.image.tags
            except:
                container_status = "unknown"
                container_name = data.get("Name", "unknown")
                container_image = []
            
            result.append({
                "container_id": cid,
                "name": container_name,
                "status": container_status,
                "image": container_image,
                "ipv4_address": data.get("IPv4Address"),
                "ipv6_address": data.get("IPv6Address"),
                "mac_address": data.get("MacAddress"),
                "endpoint_id": data.get("EndpointID")
            })
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ===========================================
# Network Debugging Endpoints
# ===========================================
@app.post("/networks/prune", tags=["Network Debugging"])
async def prune_networks(_: str = Depends(get_current_user)):
    """
    Remove all unused networks.
    """
    try:
        pruned = docker_client.networks.prune()
        return {
            "message": "Unused networks pruned",
            "networks_deleted": pruned.get("NetworksDeleted", []),
            "space_reclaimed": pruned.get("SpaceReclaimed", 0)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/containers/{container_id}/network_test", tags=["Network Debugging"])
async def container_network_test(
    container_id: str,
    target: str,
    test_type: str = "ping",
    count: int = 4,
    _: str = Depends(get_current_user)
):
    """
    Test network connectivity from a container to a target.
    
    - **target**: Target host or IP address to test connectivity to
    - **test_type**: Type of test to perform (ping, curl, wget, traceroute, nslookup, dig)
    - **count**: Number of packets to send for ping test
    """
    try:
        container = docker_client.containers.get(container_id)
        if container.status != "running":
            raise HTTPException(status_code=400, detail="Container is not running")
        
        # Prepare the command based on the test type
        if test_type == "ping":
            cmd = ["ping", "-c", str(count), target]
        elif test_type == "curl":
            cmd = ["curl", "-v", "-s", target]
        elif test_type == "wget":
            cmd = ["wget", "-O", "/dev/null", target]
        elif test_type == "traceroute":
            cmd = ["traceroute", target]
        elif test_type == "nslookup":
            cmd = ["nslookup", target]
        elif test_type == "dig":
            cmd = ["dig", target]
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported test type: {test_type}")
        
        # Execute the command in the container
        exec_id = docker_client.api.exec_create(
            container_id, 
            cmd,
            stdout=True,
            stderr=True
        )
        
        output = docker_client.api.exec_start(exec_id).decode('utf-8')
        exec_info = docker_client.api.exec_inspect(exec_id)
        
        return {
            "container_id": container_id,
            "target": target,
            "test_type": test_type,
            "exit_code": exec_info.get("ExitCode"),
            "output": output
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/networks/topology", tags=["Network Debugging"])
async def network_topology(_: str = Depends(get_current_user)):
    """
    Generate a network topology map showing networks and connected containers.
    """
    try:
        networks = docker_client.networks.list()
        topology = []
        
        for network in networks:
            network_info = {
                "network_id": network.id,
                "name": network.name,
                "driver": network.attrs.get("Driver", ""),
                "scope": network.attrs.get("Scope", ""),
                "subnet": network.attrs.get("IPAM", {}).get("Config", [{}])[0].get("Subnet", "") 
                    if network.attrs.get("IPAM", {}).get("Config") else "",
                "gateway": network.attrs.get("IPAM", {}).get("Config", [{}])[0].get("Gateway", "")
                    if network.attrs.get("IPAM", {}).get("Config") else "",
                "containers": []
            }
            
            # Add connected containers
            containers_info = network.attrs.get("Containers", {})
            for cid, data in containers_info.items():
                try:
                    container = docker_client.containers.get(cid)
                    container_info = {
                        "id": cid,
                        "name": container.name,
                        "status": container.status,
                        "ip_address": data.get("IPv4Address", "").split("/")[0] if data.get("IPv4Address") else "",
                        "mac_address": data.get("MacAddress", "")
                    }
                    network_info["containers"].append(container_info)
                except:
                    # Container might have been removed
                    network_info["containers"].append({
                        "id": cid,
                        "name": data.get("Name", "unknown"),
                        "status": "unknown",
                        "ip_address": data.get("IPv4Address", "").split("/")[0] if data.get("IPv4Address") else "",
                        "mac_address": data.get("MacAddress", "")
                    })
            
            topology.append(network_info)
        
        return topology
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/networks/dns", tags=["Network Debugging"])
async def network_dns_info(_: str = Depends(get_current_user)):
    """
    Get DNS configuration information for Docker networks.
    """
    try:
        # Get Docker daemon info
        daemon_info = docker_client.info()
        
        # Get all networks
        networks = docker_client.networks.list()
        
        # Collect DNS information
        dns_info = {
            "daemon_dns": {
                "dns": daemon_info.get("DNSConfig", {}).get("DNS", []),
                "dns_options": daemon_info.get("DNSConfig", {}).get("Options", []),
                "dns_search": daemon_info.get("DNSConfig", {}).get("Search", [])
            },
            "networks": []
        }
        
        for network in networks:
            network_dns = {
                "network_id": network.id,
                "name": network.name,
                "driver": network.attrs.get("Driver", ""),
                "dns_enabled": network.attrs.get("EnableIPv6", False),
                "internal": network.attrs.get("Internal", False),
                "options": network.attrs.get("Options", {})
            }
            
            # Add DNS-related options
            dns_options = {}
            for key, value in network.attrs.get("Options", {}).items():
                if "dns" in key.lower():
                    dns_options[key] = value
            
            network_dns["dns_options"] = dns_options
            dns_info["networks"].append(network_dns)
        
        return dns_info
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ===========================================
# Volume Management Endpoints
# ===========================================
@app.get("/volumes", tags=["Volumes"])
async def list_volumes(
    filters: Optional[str] = None,
    _: str = Depends(get_current_user)
):
    """
    List all Docker volumes with filtering options.
    
    - **filters**: JSON-encoded filters (e.g., {"dangling": ["true"], "name": ["my-volume"]})
    """
    try:
        # Parse filters if provided
        filter_dict = {}
        if filters:
            filter_dict = json.loads(filters)
        
        volumes = docker_client.volumes.list(filters=filter_dict)
        result = []
        
        for volume in volumes:
            volume_info = {
                'id': volume.id,
                'name': volume.name,
                'driver': volume.attrs.get('Driver', ''),
                'mountpoint': volume.attrs.get('Mountpoint', ''),
                'created': volume.attrs.get('CreatedAt', ''),
                'labels': volume.attrs.get('Labels', {}),
                'scope': volume.attrs.get('Scope', ''),
                'options': volume.attrs.get('Options', {})
            }
            result.append(volume_info)
            
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/volumes/create", tags=["Volumes"])
async def create_volume(
    name: str,
    driver: str = "local",
    driver_opts: Optional[Dict[str, str]] = None,
    labels: Optional[Dict[str, str]] = None,
    _: str = Depends(get_current_user)
):
    """
    Create a Docker volume.
    
    - **name**: Volume name
    - **driver**: Volume driver (default: local)
    - **driver_opts**: Driver-specific options
    - **labels**: Volume labels
    """
    try:
        volume = docker_client.volumes.create(
            name=name,
            driver=driver,
            driver_opts=driver_opts,
            labels=labels
        )
        
        return {
            "message": "Volume created successfully",
            "volume_id": volume.id,
            "name": volume.name,
            "driver": volume.attrs.get('Driver', ''),
            "mountpoint": volume.attrs.get('Mountpoint', '')
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/volumes/{volume_id}", tags=["Volumes"])
async def inspect_volume(volume_id: str, _: str = Depends(get_current_user)):
    """
    Get detailed information about a volume.
    """
    try:
        volume = docker_client.volumes.get(volume_id)
        return volume.attrs
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/volumes/{volume_id}/remove", tags=["Volumes"])
async def remove_volume(
    volume_id: str,
    force: bool = False,
    _: str = Depends(get_current_user)
):
    """
    Remove a Docker volume.
    
    - **force**: Force removal even if the volume is in use (default: False)
    """
    try:
        volume = docker_client.volumes.get(volume_id)
        volume.remove(force=force)
        
        return {
            "message": "Volume removed successfully",
            "volume_id": volume_id
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/volumes/prune", tags=["Volumes"])
async def prune_volumes(
    filters: Optional[str] = None,
    _: str = Depends(get_current_user)
):
    """
    Remove all unused volumes.
    
    - **filters**: JSON-encoded filters (e.g., {"label": ["environment=test"]})
    """
    try:
        # Parse filters if provided
        filter_dict = {}
        if filters:
            filter_dict = json.loads(filters)
        
        pruned = docker_client.volumes.prune(filters=filter_dict)
        
        return {
            "message": "Volumes pruned",
            "volumes_deleted": pruned.get("VolumesDeleted", []),
            "space_reclaimed": pruned.get("SpaceReclaimed", 0)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ===========================================
# Volume Debugging Endpoints
# ===========================================
@app.get("/volumes/{volume_id}/usage", tags=["Volume Debugging"])
async def volume_usage(volume_id: str, _: str = Depends(get_current_user)):
    """
    Get disk usage information for a volume.
    """
    try:
        volume = docker_client.volumes.get(volume_id)
        mountpoint = volume.attrs.get('Mountpoint', '')
        
        if not mountpoint:
            raise HTTPException(status_code=404, detail="Volume mountpoint not found")
        
        # Create a temporary container to access the volume
        container = docker_client.containers.create(
            "alpine:latest",
            ["du", "-sh", "/data"],
            volumes={volume_id: {"bind": "/data", "mode": "ro"}},
            detach=True
        )
        
        try:
            # Start the container
            container.start()
            
            # Wait for the container to finish
            exit_code = container.wait()["StatusCode"]
            
            if exit_code != 0:
                raise HTTPException(status_code=500, detail="Failed to get volume usage")
            
            # Get the output
            logs = container.logs().decode('utf-8').strip()
            
            # Parse the output (format: "size /data")
            size = logs.split()[0] if logs else "unknown"
            
            return {
                "volume_id": volume_id,
                "name": volume.name,
                "size": size,
                "mountpoint": mountpoint
            }
        finally:
            # Clean up the container
            container.remove(force=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/volumes/{volume_id}/containers", tags=["Volume Debugging"])
async def volume_containers(volume_id: str, _: str = Depends(get_current_user)):
    """
    List containers using a specific volume.
    """
    try:
        volume = docker_client.volumes.get(volume_id)
        
        # Get all containers
        containers = docker_client.containers.list(all=True)
        
        # Find containers using this volume
        using_containers = []
        for container in containers:
            mounts = container.attrs.get("Mounts", [])
            for mount in mounts:
                if mount.get("Type") == "volume" and (mount.get("Name") == volume_id or mount.get("Source") == volume.attrs.get("Mountpoint")):
                    using_containers.append({
                        "container_id": container.id,
                        "name": container.name,
                        "status": container.status,
                        "mount_destination": mount.get("Destination"),
                        "mount_mode": mount.get("Mode", ""),
                        "mount_rw": mount.get("RW", True)
                    })
        
        return {
            "volume_id": volume_id,
            "name": volume.name,
            "containers": using_containers
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/volumes/{volume_id}/ls", tags=["Volume Debugging"])
async def volume_ls(
    volume_id: str,
    path: str = "/",
    _: str = Depends(get_current_user)
):
    """
    List files in a volume.
    
    - **path**: Path inside the volume to list (default: /)
    """
    try:
        volume = docker_client.volumes.get(volume_id)
        
        # Create a temporary container to access the volume
        container = docker_client.containers.create(
            "alpine:latest",
            ["ls", "-la", os.path.join("/data", path.lstrip("/"))],
            volumes={volume_id: {"bind": "/data", "mode": "ro"}},
            detach=True
        )
        
        try:
            # Start the container
            container.start()
            
            # Wait for the container to finish
            exit_code = container.wait()["StatusCode"]
            
            if exit_code != 0:
                raise HTTPException(status_code=500, detail=f"Failed to list files in volume at path: {path}")
            
            # Get the output
            logs = container.logs().decode('utf-8')
            
            # Parse the output into a list of files
            lines = logs.strip().split("\n")
            files = []
            
            for line in lines[1:]:  # Skip the first line (total)
                parts = line.split()
                if len(parts) >= 9:
                    file_info = {
                        "permissions": parts[0],
                        "links": parts[1],
                        "owner": parts[2],
                        "group": parts[3],
                        "size": parts[4],
                        "date": " ".join(parts[5:8]),
                        "name": " ".join(parts[8:])
                    }
                    files.append(file_info)
            
            return {
                "volume_id": volume_id,
                "name": volume.name,
                "path": path,
                "files": files
            }
        finally:
            # Clean up the container
            container.remove(force=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ===========================================
# Docker Compose Endpoints
# ===========================================
@app.post("/compose/up", tags=["Docker Compose"])
async def compose_up(
    compose_file: UploadFile = File(...),
    project_name: Optional[str] = Form(None),
    _: str = Depends(get_current_user)
):
    """
    Bring up a Docker Compose application.
    """
    try:
        with tempfile.TemporaryDirectory() as tmpdirname:
            file_path = os.path.join(tmpdirname, "docker-compose.yml")
            content = await compose_file.read()
            with open(file_path, "wb") as f:
                f.write(content)
            cmd = ["docker", "compose", "-f", file_path, "up", "-d"]
            if project_name:
                cmd = ["docker", "compose", "-p", project_name, "-f", file_path, "up", "-d"]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                raise HTTPException(status_code=500, detail=f"Compose up failed: {result.stderr}")
            return {"message": "Compose up executed successfully", "stdout": result.stdout}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/compose/down", tags=["Docker Compose"])
async def compose_down(
    compose_file: UploadFile = File(...),
    project_name: Optional[str] = Form(None),
    volumes: bool = False,
    remove_orphans: bool = False,
    _: str = Depends(get_current_user)
):
    """
    Tear down a Docker Compose application.
    
    - **volumes**: Remove named volumes declared in the volumes section (default: False)
    - **remove_orphans**: Remove containers for services not defined in the Compose file (default: False)
    """
    try:
        with tempfile.TemporaryDirectory() as tmpdirname:
            file_path = os.path.join(tmpdirname, "docker-compose.yml")
            content = await compose_file.read()
            with open(file_path, "wb") as f:
                f.write(content)
            
            cmd = ["docker", "compose", "-f", file_path, "down"]
            
            if project_name:
                cmd = ["docker", "compose", "-p", project_name, "-f", file_path, "down"]
            
            if volumes:
                cmd.append("-v")
            
            if remove_orphans:
                cmd.append("--remove-orphans")
            
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                raise HTTPException(status_code=500, detail=f"Compose down failed: {result.stderr}")
            return {"message": "Compose down executed successfully", "stdout": result.stdout}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/compose/ps", tags=["Docker Compose"])
async def compose_ps(
    compose_file: UploadFile = File(...),
    project_name: Optional[str] = Form(None),
    _: str = Depends(get_current_user)
):
    """
    List containers in a Docker Compose application.
    """
    try:
        with tempfile.TemporaryDirectory() as tmpdirname:
            file_path = os.path.join(tmpdirname, "docker-compose.yml")
            content = await compose_file.read()
            with open(file_path, "wb") as f:
                f.write(content)
            cmd = ["docker", "compose", "-f", file_path, "ps"]
            if project_name:
                cmd = ["docker", "compose", "-p", project_name, "-f", file_path, "ps"]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                raise HTTPException(status_code=500, detail=f"Compose ps failed: {result.stderr}")
            return {"message": "Compose ps executed successfully", "stdout": result.stdout}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/compose/logs", tags=["Docker Compose"])
async def compose_logs(
    compose_file: UploadFile = File(...),
    project_name: Optional[str] = Form(None),
    service: Optional[str] = Form(None),
    tail: Optional[str] = Form("all"),
    _: str = Depends(get_current_user)
):
    """
    Get logs from a Docker Compose application.
    
    - **service**: Name of the service to get logs for (default: all services)
    - **tail**: Number of lines to show from the end (default: all)
    """
    try:
        with tempfile.TemporaryDirectory() as tmpdirname:
            file_path = os.path.join(tmpdirname, "docker-compose.yml")
            content = await compose_file.read()
            with open(file_path, "wb") as f:
                f.write(content)
            
            cmd = ["docker", "compose", "-f", file_path, "logs", "--tail", tail]
            
            if project_name:
                cmd = ["docker", "compose", "-p", project_name, "-f", file_path, "logs", "--tail", tail]
            
            if service:
                cmd.append(service)
            
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                raise HTTPException(status_code=500, detail=f"Compose logs failed: {result.stderr}")
            return {"message": "Compose logs executed successfully", "stdout": result.stdout}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/compose/config", tags=["Docker Compose"])
async def compose_config(
    compose_file: UploadFile = File(...),
    project_name: Optional[str] = Form(None),
    _: str = Depends(get_current_user)
):
    """
    Validate and view the Compose file.
    """
    try:
        with tempfile.TemporaryDirectory() as tmpdirname:
            file_path = os.path.join(tmpdirname, "docker-compose.yml")
            content = await compose_file.read()
            with open(file_path, "wb") as f:
                f.write(content)
            
            cmd = ["docker", "compose", "-f", file_path, "config"]
            
            if project_name:
                cmd = ["docker", "compose", "-p", project_name, "-f", file_path, "config"]
            
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                raise HTTPException(status_code=500, detail=f"Compose config failed: {result.stderr}")
            
            # Parse the YAML output to return as structured data
            try:
                import yaml
                parsed_config = yaml.safe_load(result.stdout)
                return {
                    "message": "Compose config validated successfully",
                    "config": parsed_config
                }
            except:
                # If YAML parsing fails, return the raw output
                return {
                    "message": "Compose config validated successfully",
                    "stdout": result.stdout
                }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=5000, reload=True)