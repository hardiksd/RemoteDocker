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

- `GET /instructions` - Detailed API documentation
- `GET /version` - Docker engine version information
- `GET /images` - List all Docker images
- `POST /build` - Build a Docker image
- `GET /containers` - List all containers
- `POST /containers/create` - Create a new container
- `POST /containers/{container_id}/start` - Start a container
- `POST /containers/{container_id}/stop` - Stop a container
- `POST /networks/create` - Create a Docker network
- `POST /compose/up` - Bring up a Docker Compose application

## Security Considerations

- The default SECRET_KEY in the code is for demonstration purposes only. In production, you should:
  - Generate a secure random key
  - Store it in an environment variable or secure vault
  - Never commit secrets to version control

- Consider implementing additional security measures:
  - Rate limiting
  - IP whitelisting
  - More granular permissions
