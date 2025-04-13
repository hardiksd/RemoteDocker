# RemoteDocker

RemoteDocker is an intermediary service that acts as a bridge between agentic AI systems and Docker Desktop. It exposes a secure HTTP API to perform common Docker operations, allowing AI systems to trigger builds, manage images, and control containers without direct access to the Docker daemon.

## Features

- **JWT Authentication**: All endpoints are protected with JWT authentication
- **Docker Management**: Comprehensive API for managing Docker resources
- **Container Operations**: Create, start, stop, and remove containers
- **Image Management**: Build, list, and pull Docker images
- **Network Management**: Create and list Docker networks
- **Docker Compose Support**: Bring up and tear down Docker Compose applications

## Installation

### Prerequisites

- Python 3.8+
- Docker Engine
- Docker Compose (for compose-related endpoints)

### Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/hardiksd/RemoteDocker.git
   cd RemoteDocker
   ```

2. Install dependencies:
   ```bash
   pip install fastapi uvicorn python-jose[cryptography] python-multipart docker
   ```

3. Run the server:
   ```bash
   python app.py
   ```

The server will start on `http://0.0.0.0:5000`.

## Authentication

All API endpoints are protected with JWT authentication. You need to include a valid JWT token in the Authorization header of your requests.

### Generating a Token

Use the provided token generation script:

```bash
python generate_token.py --username your_username --expires 60
```

This will generate a token valid for 60 minutes.

### Using the Token

Include the token in your API requests:

```bash
curl -X GET http://localhost:5000/version \
  -H "Authorization: Bearer your_token_here"
```

## API Documentation

Once the server is running, you can access the interactive API documentation at:

```
http://localhost:5000/docs
```

### Key Endpoints

#### Core Docker Information
- `GET /instructions` - Detailed API documentation
- `GET /version` - Docker engine version information
- `GET /info` - Detailed Docker system information
- `GET /system/df` - Docker disk usage information
- `POST /system/prune` - Clean up unused Docker resources
- `GET /system/events` - Get real-time events from Docker daemon

#### Container Management
- `GET /containers` - List all containers
- `POST /containers/create` - Create a new container
- `POST /containers/{container_id}/start` - Start a container
- `POST /containers/{container_id}/stop` - Stop a container
- `POST /containers/{container_id}/restart` - Restart a container
- `POST /containers/{container_id}/pause` - Pause a container
- `POST /containers/{container_id}/unpause` - Unpause a container
- `POST /containers/{container_id}/remove` - Remove a container
- `GET /containers/{container_id}/logs` - Get container logs

#### Container Debugging
- `GET /containers/{container_id}/inspect` - Detailed container information
- `GET /containers/{container_id}/top` - List running processes in a container
- `GET /containers/{container_id}/stats` - Container resource usage statistics
- `POST /containers/{container_id}/exec` - Execute a command in a container
- `GET /containers/{container_id}/changes` - Get filesystem changes in a container
- `GET /containers/{container_id}/file` - Get a file from a container
- `GET /containers/{container_id}/health` - Container health status
- `POST /containers/{container_id}/network_test` - Test network connectivity from a container

#### Image Management
- `GET /images` - List all Docker images
- `POST /images/pull` - Pull a Docker image
- `POST /build` - Build a Docker image
- `POST /images/{image_id}/tag` - Tag an image
- `POST /images/{image_id}/remove` - Remove an image
- `POST /images/prune` - Clean up unused images

#### Image Debugging
- `GET /images/{image_id}/inspect` - Detailed image information
- `GET /images/{image_id}/history` - Image history and layers
- `GET /images/search` - Search for images on Docker Hub
- `GET /images/{image_id}/layers` - Detailed layer information

#### Network Management
- `GET /networks` - List all Docker networks
- `POST /networks/create` - Create a Docker network
- `GET /networks/{network_id}` - Inspect a network
- `POST /networks/{network_id}/connect` - Connect a container to a network
- `POST /networks/{network_id}/disconnect` - Disconnect a container from a network
- `POST /networks/{network_id}/remove` - Remove a network
- `GET /networks/{network_id}/containers` - List containers in a network

#### Network Debugging
- `POST /networks/prune` - Clean up unused networks
- `GET /networks/topology` - Network topology map
- `GET /networks/dns` - DNS configuration information

#### Volume Management
- `GET /volumes` - List all Docker volumes
- `POST /volumes/create` - Create a Docker volume
- `GET /volumes/{volume_id}` - Inspect a volume
- `POST /volumes/{volume_id}/remove` - Remove a volume
- `POST /volumes/prune` - Clean up unused volumes

#### Volume Debugging
- `GET /volumes/{volume_id}/usage` - Volume disk usage
- `GET /volumes/{volume_id}/containers` - List containers using a volume
- `GET /volumes/{volume_id}/ls` - List files in a volume

#### Docker Compose
- `POST /compose/up` - Bring up a Docker Compose application
- `POST /compose/down` - Tear down a Docker Compose application
- `POST /compose/ps` - List containers in a Docker Compose application
- `POST /compose/logs` - Get logs from a Docker Compose application
- `POST /compose/config` - Validate and view a Compose file

## Security Considerations

- The default SECRET_KEY in the code is for demonstration purposes only. In production, you should:
  - Generate a secure random key
  - Store it in an environment variable or secure vault
  - Never commit secrets to version control

- Consider implementing additional security measures:
  - Rate limiting
  - IP whitelisting
  - More granular permissions
