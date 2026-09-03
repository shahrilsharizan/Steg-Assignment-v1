from email.parser import BytesParser
from email.policy import default
from io import BytesIO
import json
import mimetypes
import shutil
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from stego_tool import StegoError, capacity_bytes, compare_images, extract_file, hide_file, write_histogram


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
RUNS_DIR = BASE_DIR / "web_outputs"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


class Upload:
    def __init__(self, filename, data):
        self.filename = filename
        self.file = BytesIO(data)


class MultipartForm(dict):
    def getfirst(self, name, default=None):
        value = self.get(name, default)
        if isinstance(value, list):
            return value[0] if value else default
        return value


def _safe_name(name, fallback):
    cleaned = Path(name or fallback).name.replace("\x00", "")
    return cleaned or fallback


def _json_bytes(payload):
    return json.dumps(payload, indent=2).encode("utf-8")


class StegoRequestHandler(BaseHTTPRequestHandler):
    server_version = "StegoHTTP/1.0"

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return
        if parsed.path.startswith("/outputs/"):
            self._send_file(RUNS_DIR / parsed.path.removeprefix("/outputs/"))
            return
        if parsed.path.startswith("/static/"):
            self._send_file(STATIC_DIR / parsed.path.removeprefix("/static/"))
            return
        self._send_json({"error": "Page not found."}, status=404)

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/hide":
                self._handle_hide()
            elif parsed.path == "/api/extract":
                self._handle_extract()
            else:
                self._send_json({"error": "Action not found."}, status=404)
        except (OSError, StegoError, ValueError) as exc:
            self._send_json({"error": str(exc)}, status=400)

    def _handle_hide(self):
        form = self._read_form()
        cover_item = form["cover"]
        secret_mode = self._field_value(form, "secret_mode", "text")
        run_dir = self._new_run_dir()

        cover_name = _safe_name(getattr(cover_item, "filename", ""), "cover.png")
        cover_path = run_dir / cover_name
        self._save_upload(cover_item, cover_path)

        if secret_mode == "file":
            secret_item = form.get("secret_file")
            if secret_item is None or not getattr(secret_item, "filename", ""):
                raise ValueError("Please choose a secret file.")
            secret_name = _safe_name(secret_item.filename, "secret.bin")
            secret_path = run_dir / secret_name
            self._save_upload(secret_item, secret_path)
        else:
            secret_text = self._field_value(form, "secret_text", "")
            if not secret_text:
                raise ValueError("Please enter secret text or choose a secret file.")
            secret_path = run_dir / "extracted_secret.txt"
            secret_path.write_text(secret_text, encoding="utf-8")

        stego_path = run_dir / "stego.png"
        histogram_path = run_dir / "histogram_comparison.png"
        stats_path = run_dir / "stats.json"

        payload_bytes, changed_values = hide_file(cover_path, secret_path, stego_path)
        write_histogram(cover_path, stego_path, histogram_path)
        stats = compare_images(cover_path, stego_path)
        stats["payload_bytes"] = payload_bytes
        stats["changed_color_values"] = changed_values
        stats["capacity_bytes"] = capacity_bytes(cover_path)
        stats_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")

        rel = run_dir.relative_to(RUNS_DIR).as_posix()
        self._send_json({
            "run_id": rel,
            "cover_url": f"/outputs/{rel}/{cover_path.name}",
            "stego_url": f"/outputs/{rel}/stego.png",
            "histogram_url": f"/outputs/{rel}/histogram_comparison.png",
            "stats_url": f"/outputs/{rel}/stats.json",
            "download_name": "stego.png",
            "stats": stats,
        })

    def _handle_extract(self):
        form = self._read_form()
        stego_item = form["stego"]
        run_dir = self._new_run_dir()
        stego_path = run_dir / _safe_name(stego_item.filename, "stego.png")
        self._save_upload(stego_item, stego_path)
        extracted_dir = run_dir / "extracted"
        extracted_path = extract_file(stego_path, extracted_dir)
        rel = run_dir.relative_to(RUNS_DIR).as_posix()
        extracted_rel = extracted_path.relative_to(RUNS_DIR).as_posix()
        self._send_json({
            "file_name": extracted_path.name,
            "file_size_bytes": extracted_path.stat().st_size,
            "download_url": f"/outputs/{extracted_rel}",
            "run_id": rel,
        })

    def _read_form(self):
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > MAX_UPLOAD_BYTES:
            raise ValueError("Upload is too large. Maximum size is 50 MB.")
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("multipart/form-data"):
            raise ValueError("Expected a form upload.")
        body = self.rfile.read(content_length)
        message = BytesParser(policy=default).parsebytes(
            b"Content-Type: " + content_type.encode("utf-8") + b"\r\n"
            b"MIME-Version: 1.0\r\n\r\n" + body
        )
        form = MultipartForm()
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if not name:
                continue
            filename = part.get_filename()
            data = part.get_payload(decode=True) or b""
            value = Upload(filename, data) if filename is not None else data.decode("utf-8")
            if name in form:
                existing = form[name]
                if not isinstance(existing, list):
                    form[name] = [existing]
                form[name].append(value)
            else:
                form[name] = value
        return form

    def _field_value(self, form, name, default):
        item = form.getfirst(name)
        return default if item is None else item

    def _save_upload(self, item, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as output:
            shutil.copyfileobj(item.file, output)

    def _new_run_dir(self):
        RUNS_DIR.mkdir(exist_ok=True)
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        return Path(tempfile.mkdtemp(prefix=f"{timestamp}-", dir=RUNS_DIR))

    def _send_json(self, payload, status=200):
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, content_type=None):
        path = Path(path).resolve()
        allowed_roots = (STATIC_DIR.resolve(), RUNS_DIR.resolve())
        if not any(path == root or root in path.parents for root in allowed_roots):
            self._send_json({"error": "Access denied."}, status=403)
            return
        if not path.exists() or not path.is_file():
            self._send_json({"error": "File not found."}, status=404)
            return

        if content_type is None:
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        print("%s - %s" % (self.address_string(), format % args))


def main():
    STATIC_DIR.mkdir(exist_ok=True)
    RUNS_DIR.mkdir(exist_ok=True)
    host = "127.0.0.1"
    port = 8000
    server = ThreadingHTTPServer((host, port), StegoRequestHandler)
    print(f"Steganography web app running at http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    server.serve_forever()


if __name__ == "__main__":
    main()
