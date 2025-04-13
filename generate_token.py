#!/usr/bin/env python3
"""
JWT Token Generator for RemoteDocker API

This script generates a JWT token that can be used to authenticate with the RemoteDocker API.
"""

import argparse
from datetime import datetime, timedelta, UTC
import jwt
import sys

# JWT Configuration - MUST match the configuration in app.py
SECRET_KEY = "09d25e094faa6ca2556c818166b7a9563b93f7099f6f0f4caa6cf63b88e8d3e7"  # Change this in production!
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 43200  # 30 days (30 * 24 * 60)

def create_access_token(username: str, expires_delta: timedelta = None):
    """
    Create a JWT token for the given username.
    
    Args:
        username: The username to include in the token
        expires_delta: Optional expiration time delta
        
    Returns:
        str: The encoded JWT token
    """
    to_encode = {"sub": username}
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def main():
    parser = argparse.ArgumentParser(description="Generate JWT token for RemoteDocker API")
    parser.add_argument("--username", "-u", type=str, default="api_user", 
                        help="Username to include in the token (default: api_user)")
    parser.add_argument("--expires", "-e", type=int, default=ACCESS_TOKEN_EXPIRE_MINUTES,
                        help=f"Token expiration time in minutes (default: {ACCESS_TOKEN_EXPIRE_MINUTES}, which is 30 days)")
    
    args = parser.parse_args()
    
    try:
        token = create_access_token(
            username=args.username,
            expires_delta=timedelta(minutes=args.expires)
        )
        
        print("\n=== JWT Token ===")
        print(token)
        print("\n=== Usage Example ===")
        print("curl -X GET http://localhost:5000/version \\")
        print(f"  -H \"Authorization: Bearer {token}\"")
        print("\n=== Python Example ===")
        print("import requests")
        print("headers = {")
        print(f"    \"Authorization\": \"Bearer {token}\"")
        print("}")
        print("response = requests.get(\"http://localhost:5000/version\", headers=headers)")
        print("print(response.json())")
        
    except Exception as e:
        print(f"Error generating token: {e}", file=sys.stderr)
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())