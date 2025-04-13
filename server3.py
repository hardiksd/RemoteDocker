from flask import Flask, request, jsonify
import docker
import uuid
import threading
import io
import subprocess
import tempfile
import os
import json

app = Flask(__name__)
client = docker.from_env()

# Global store for asynchronous build logs and statuses (indexed by build_id).
build_logs_store = {}

def run_build(build_id, file_bytes, tag, dockerfile):
    try:
        stream_obj = io.BytesIO(file_bytes)
        image, build_logs = client.images.build(
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
# Core Docker API Endpoints
# ===========================================
@app.route('/version', methods=['GET'])
def version():
    try:
        version_info = client.version()
        return jsonify(version_info)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/images', methods=['GET'])
def list_images():
    try:
        images = client.images.list()
        result = [{'id': image.id, 'tags': image.tags} for image in images]
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/images/pull', methods=['POST'])
def pull_image():
    try:
        data = request.get_json()
        repository = data.get('repository')
        if not repository:
            return jsonify({'error': 'Repository is required'}), 400
        tag = data.get('tag', 'latest')
        image = client.images.pull(repository, tag=tag)
        return jsonify({'message': 'Image pulled successfully', 'image_id': image.id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ===========================================
# Asynchronous Build Endpoints
# ===========================================
@app.route('/build', methods=['POST'])
def build_image():
    if 'context' not in request.files:
        return jsonify({'error': 'No context file uploaded. Use field "context".'}), 400
    context_file = request.files['context']
    tag = request.form.get('tag', 'latest')
    dockerfile = request.form.get('dockerfile', 'Dockerfile')
    build_id = str(uuid.uuid4())
    build_logs_store[build_id] = {"status": "in_progress", "logs": [], "image_id": None}
    file_bytes = context_file.read()
    thread = threading.Thread(target=run_build, args=(build_id, file_bytes, tag, dockerfile))
    thread.start()
    return jsonify({"message": "Build started", "build_id": build_id}), 202

@app.route('/builds/<build_id>/logs', methods=['GET'])
def get_build_logs(build_id):
    if build_id in build_logs_store:
        entry = build_logs_store[build_id]
        return jsonify({
            "build_id": build_id,
            "status": entry["status"],
            "logs": entry["logs"],
            "image_id": entry["image_id"],
            "error": entry.get("error")
        })
    else:
        return jsonify({'error': 'Build ID not found'}), 404

# ===========================================
# Container Management Endpoints
# ===========================================
@app.route('/containers', methods=['GET'])
def list_containers():
    try:
        containers = client.containers.list(all=True)
        result = [{
            'id': container.id,
            'name': container.name,
            'status': container.status,
            'image': container.image.tags
        } for container in containers]
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/containers/create', methods=['POST'])
def create_container():
    try:
        data = request.get_json()
        image = data.get('image')
        if not image:
            return jsonify({'error': 'Image name is required'}), 400
        command = data.get('command')
        name = data.get('name')
        container = client.containers.create(image, command, name=name, detach=True)
        return jsonify({'message': 'Container created', 'container_id': container.id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/containers/<container_id>/start', methods=['POST'])
def start_container(container_id):
    try:
        container = client.containers.get(container_id)
        container.start()
        return jsonify({'message': 'Container started', 'container_id': container.id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/containers/<container_id>/stop', methods=['POST'])
def stop_container(container_id):
    try:
        container = client.containers.get(container_id)
        container.stop()
        return jsonify({'message': 'Container stopped', 'container_id': container.id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/containers/<container_id>/remove', methods=['POST'])
def remove_container(container_id):
    try:
        container = client.containers.get(container_id)
        container.remove(force=True)
        return jsonify({'message': 'Container removed', 'container_id': container_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/containers/<container_id>/logs', methods=['GET'])
def container_logs(container_id):
    try:
        container = client.containers.get(container_id)
        logs = container.logs().decode('utf-8')
        return jsonify({'container_id': container_id, 'logs': logs})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ===========================================
# Network Management Endpoints
# ===========================================
@app.route('/networks/create', methods=['POST'])
def create_network():
    try:
        data = request.get_json()
        name = data.get("name")
        if not name:
            return jsonify({"error": "Network name is required"}), 400
        driver = data.get("driver", "bridge")
        options = data.get("options")
        labels = data.get("labels")
        network = client.networks.create(name=name, driver=driver, options=options, labels=labels)
        return jsonify({"message": "Network created", "network_id": network.id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/networks', methods=['GET'])
def list_networks():
    try:
        networks = client.networks.list()
        result = []
        for net in networks:
            result.append({
                "id": net.id,
                "name": net.name,
                "driver": net.attrs.get("Driver", "")
            })
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ===========================================
# Network Debugging Endpoints
# ===========================================
@app.route('/networks/<network_id>/inspect', methods=['GET'])
def inspect_network(network_id):
    try:
        network = client.networks.get(network_id)
        return jsonify(network.attrs)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/networks/<network_id>/containers', methods=['GET'])
def network_containers(network_id):
    try:
        network = client.networks.get(network_id)
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
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ===========================================
# Docker Compose Endpoints
# ===========================================
@app.route('/compose/up', methods=['POST'])
def compose_up():
    if 'compose_file' not in request.files:
        return jsonify({"error": "No compose file provided. Use 'compose_file'."}), 400
    compose_file = request.files['compose_file']
    project_name = request.form.get('project_name')
    try:
        with tempfile.TemporaryDirectory() as tmpdirname:
            file_path = os.path.join(tmpdirname, "docker-compose.yml")
            compose_file.save(file_path)
            cmd = ["docker", "compose", "-f", file_path, "up", "-d"]
            if project_name:
                cmd = ["docker", "compose", "-p", project_name, "-f", file_path, "up", "-d"]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                return jsonify({"error": "Compose up failed", "stderr": result.stderr}), 500
            return jsonify({"message": "Compose up executed successfully", "stdout": result.stdout})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/compose/down', methods=['POST'])
def compose_down():
    if 'compose_file' not in request.files:
        return jsonify({"error": "No compose file provided. Use 'compose_file'."}), 400
    compose_file = request.files['compose_file']
    project_name = request.form.get('project_name')
    try:
        with tempfile.TemporaryDirectory() as tmpdirname:
            file_path = os.path.join(tmpdirname, "docker-compose.yml")
            compose_file.save(file_path)
            cmd = ["docker", "compose", "-f", file_path, "down"]
            if project_name:
                cmd = ["docker", "compose", "-p", project_name, "-f", file_path, "down"]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                return jsonify({"error": "Compose down failed", "stderr": result.stderr}), 500
            return jsonify({"message": "Compose down executed successfully", "stdout": result.stdout})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ===========================================
# Docker Compose Debugging Endpoints
# ===========================================
@app.route('/compose/ps', methods=['POST'])
def compose_ps():
    if 'compose_file' not in request.files:
        return jsonify({"error": "No compose file provided. Use 'compose_file'."}), 400
    compose_file = request.files['compose_file']
    project_name = request.form.get('project_name')
    try:
        with tempfile.TemporaryDirectory() as tmpdirname:
            file_path = os.path.join(tmpdirname, "docker-compose.yml")
            compose_file.save(file_path)
            cmd = ["docker", "compose", "-f", file_path, "ps"]
            if project_name:
                cmd = ["docker", "compose", "-p", project_name, "-f", file_path, "ps"]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                return jsonify({"error": "Compose ps failed", "stderr": result.stderr}), 500
            return jsonify({"message": "Compose ps executed successfully", "stdout": result.stdout})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/compose/config', methods=['POST'])
def compose_config():
    if 'compose_file' not in request.files:
        return jsonify({"error": "No compose file provided. Use 'compose_file'."}), 400
    compose_file = request.files['compose_file']
    try:
        with tempfile.TemporaryDirectory() as tmpdirname:
            file_path = os.path.join(tmpdirname, "docker-compose.yml")
            compose_file.save(file_path)
            cmd = ["docker", "compose", "-f", file_path, "config"]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                return jsonify({"error": "Compose config failed", "stderr": result.stderr}), 500
            return jsonify({"message": "Compose config executed successfully", "stdout": result.stdout})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/compose/logs', methods=['POST'])
def compose_logs_debug():
    if 'compose_file' not in request.files:
        return jsonify({"error": "No compose file provided. Use 'compose_file'."}), 400
    compose_file = request.files['compose_file']
    project_name = request.form.get('project_name')
    try:
        with tempfile.TemporaryDirectory() as tmpdirname:
            file_path = os.path.join(tmpdirname, "docker-compose.yml")
            compose_file.save(file_path)
            cmd = ["docker", "compose", "-f", file_path, "logs"]
            if project_name:
                cmd = ["docker", "compose", "-p", project_name, "-f", file_path, "logs"]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                return jsonify({"error": "Compose logs failed", "stderr": result.stderr}), 500
            return jsonify({"message": "Compose logs executed successfully", "stdout": result.stdout})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/compose/inspect', methods=['POST'])
def compose_inspect():
    if 'compose_file' not in request.files:
        return jsonify({"error": "No compose file provided. Use 'compose_file'."}), 400
    compose_file = request.files['compose_file']
    project_name = request.form.get('project_name')
    try:
        with tempfile.TemporaryDirectory() as tmpdirname:
            file_path = os.path.join(tmpdirname, "docker-compose.yml")
            compose_file.save(file_path)
            cmd = ["docker", "compose", "-f", file_path, "ps", "-q"]
            if project_name:
                cmd = ["docker", "compose", "-p", project_name, "-f", file_path, "ps", "-q"]
            ps_result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if ps_result.returncode != 0:
                return jsonify({"error": "Compose ps failed", "stderr": ps_result.stderr}), 500
            container_ids = [cid.strip() for cid in ps_result.stdout.splitlines() if cid.strip()]
            inspections = {}
            for cid in container_ids:
                inspect_cmd = ["docker", "inspect", cid]
                inspect_result = subprocess.run(inspect_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if inspect_result.returncode != 0:
                    inspections[cid] = {"error": inspect_result.stderr}
                else:
                    try:
                        inspections[cid] = json.loads(inspect_result.stdout)
                    except Exception as ex:
                        inspections[cid] = {"raw": inspect_result.stdout}
            return jsonify({"message": "Compose inspect executed successfully", "inspections": inspections})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ===========================================
# Run the Service
# ===========================================
if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000)
