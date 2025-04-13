from fastapi import FastAPI, Depends, HTTPException, status, File, UploadFile, Form, Request
from fastapi.security import OAuth2PasswordBearer
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from typing import Optional, List, Dict, Any, Union
import docker
import uuid
import threading
import io
import subprocess
import tempfile
import os
import json
from datetime import datetime, timedelta

# JWT Configuration
SECRET_KEY = "09d25e094faa6ca2556c818166b7a9563b93f7099f6f0f4caa6cf63b88e8d3e7"  # Change this in production!
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# FastAPI app
app = FastAPI(title="RemoteDocker API", description="Docker API for agents to access Docker desktop and execute docker actions")
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
    except JWTError:
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

@app.get("/images", tags=["Images"])
async def list_images(_: str = Depends(get_current_user)):
    """
    Lists all available Docker images on the host.
    """
    try:
        images = docker_client.images.list()
        result = [{'id': image.id, 'tags': image.tags} for image in images]
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/images/pull", tags=["Images"])
async def pull_image(request: Request, _: str = Depends(get_current_user)):
    """
    Pulls a Docker image from a registry.
    """
    try:
        data = await request.json()
        repository = data.get('repository')
        if not repository:
            raise HTTPException(status_code=400, detail="Repository is required")
        tag = data.get('tag', 'latest')
        image = docker_client.images.pull(repository, tag=tag)
        return {"message": "Image pulled successfully", "image_id": image.id}
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
async def list_containers(_: str = Depends(get_current_user)):
    """
    Lists all containers (both running and stopped).
    """
    try:
        containers = docker_client.containers.list(all=True)
        result = [{
            'id': container.id,
            'name': container.name,
            'status': container.status,
            'image': container.image.tags
        } for container in containers]
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/containers/create", tags=["Containers"])
async def create_container(request: Request, _: str = Depends(get_current_user)):
    """
    Creates a new container from a specified image.
    """
    try:
        data = await request.json()
        image = data.get('image')
        if not image:
            raise HTTPException(status_code=400, detail="Image name is required")
        command = data.get('command')
        name = data.get('name')
        container = docker_client.containers.create(image, command, name=name, detach=True)
        return {"message": "Container created", "container_id": container.id}
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
async def stop_container(container_id: str, _: str = Depends(get_current_user)):
    """
    Stops a running container.
    """
    try:
        container = docker_client.containers.get(container_id)
        container.stop()
        return {"message": "Container stopped", "container_id": container.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/containers/{container_id}/remove", tags=["Containers"])
async def remove_container(container_id: str, _: str = Depends(get_current_user)):
    """
    Removes a specified container (force removal is applied).
    """
    try:
        container = docker_client.containers.get(container_id)
        container.remove(force=True)
        return {"message": "Container removed", "container_id": container_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/containers/{container_id}/logs", tags=["Containers"])
async def container_logs(container_id: str, _: str = Depends(get_current_user)):
    """
    Retrieves the logs from a specified container.
    """
    try:
        container = docker_client.containers.get(container_id)
        logs = container.logs().decode('utf-8')
        return {"container_id": container_id, "logs": logs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ===========================================
# Network Management Endpoints
# ===========================================
@app.post("/networks/create", tags=["Networks"])
async def create_network(request: Request, _: str = Depends(get_current_user)):
    """
    Create a Docker network.
    """
    try:
        data = await request.json()
        name = data.get("name")
        if not name:
            raise HTTPException(status_code=400, detail="Network name is required")
        driver = data.get("driver", "bridge")
        options = data.get("options")
        labels = data.get("labels")
        network = docker_client.networks.create(name=name, driver=driver, options=options, labels=labels)
        return {"message": "Network created", "network_id": network.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/networks", tags=["Networks"])
async def list_networks(_: str = Depends(get_current_user)):
    """
    List all Docker networks.
    """
    try:
        networks = docker_client.networks.list()
        result = []
        for net in networks:
            result.append({
                "id": net.id,
                "name": net.name,
                "driver": net.attrs.get("Driver", "")
            })
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/networks/{network_id}/inspect", tags=["Networks"])
async def inspect_network(network_id: str, _: str = Depends(get_current_user)):
    """
    Inspect a Docker network.
    """
    try:
        network = docker_client.networks.get(network_id)
        return network.attrs
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/networks/{network_id}/containers", tags=["Networks"])
async def network_containers(network_id: str, _: str = Depends(get_current_user)):
    """
    List containers in a Docker network.
    """
    try:
        network = docker_client.networks.get(network_id)
        containers_info = network.attrs.get("Containers", {})
        result = []
        for cid, data in containers_info.items():
            result.append({
                "container_id": cid,
                "name": data.get("Name"),
                "ipv4_address": data.get("IPv4Address"),
                "ipv6_address": data.get("IPv6Address"),
                "mac_address": data.get("MacAddress")
            })
        return result
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
    _: str = Depends(get_current_user)
):
    """
    Tear down a Docker Compose application.
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
    _: str = Depends(get_current_user)
):
    """
    Get logs from a Docker Compose application.
    """
    try:
        with tempfile.TemporaryDirectory() as tmpdirname:
            file_path = os.path.join(tmpdirname, "docker-compose.yml")
            content = await compose_file.read()
            with open(file_path, "wb") as f:
                f.write(content)
            cmd = ["docker", "compose", "-f", file_path, "logs"]
            if project_name:
                cmd = ["docker", "compose", "-p", project_name, "-f", file_path, "logs"]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                raise HTTPException(status_code=500, detail=f"Compose logs failed: {result.stderr}")
            return {"message": "Compose logs executed successfully", "stdout": result.stdout}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=5000, reload=True)