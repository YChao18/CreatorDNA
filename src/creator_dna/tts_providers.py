"""Official HTTP adapters. API reference audit: docs/tts-api.md (2026-10-09)."""

import base64
import json
import math
import os
import re
import uuid
from pathlib import Path
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .tts import Capability, SynthesisReceipt, TTSError, require_consent, unsupported


def object_field(data, name):
    value = data.get(name)
    if not isinstance(value, dict):
        raise TTSError("InvalidResponse")
    return value


def retry_seconds(value):
    try:
        seconds = float(value) if value.isdigit() else (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        return max(0, seconds) if math.isfinite(seconds) else 0
    except (ValueError, TypeError, OverflowError):
        return 0


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward an Authorization header to an unverified redirect host.
        if fp is not None:
            fp.close()
        raise TTSError("UnexpectedRedirect")


class HTTPTransport:
    def __init__(self, env, timeout):
        self.env, self.timeout = env, timeout

    def check_credentials(self):
        if not os.getenv(self.env):
            raise TTSError("MissingAPIKey", rejected=True)

    def request(self, method, url, payload=None, multipart=None, authenticated=True):
        headers, body = {}, None
        if authenticated:
            key = os.getenv(self.env)
            if not key:
                raise TTSError("MissingAPIKey", rejected=True)
            headers["Authorization"] = "Bearer " + key
        if multipart:
            boundary = "CreatorDNA" + uuid.uuid4().hex
            path = Path(multipart)
            # Only a fixed safe filename goes into headers; original stays local.
            body = (f'--{boundary}\r\nContent-Disposition: form-data; name="purpose"\r\n\r\nvoice_clone\r\n'
                    f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="reference{path.suffix}"\r\n'
                    'Content-Type: application/octet-stream\r\n\r\n').encode() + path.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        elif payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        try:
            with build_opener(NoRedirect).open(Request(url, body, headers, method=method), timeout=self.timeout) as response:
                data = response.read(100 * 1024 * 1024 + 1)
            if len(data) > 100 * 1024 * 1024:
                raise TTSError("ResponseTooLarge")
            current_key = os.getenv(self.env)
            if current_key and current_key.encode() in data:
                raise TTSError("CredentialInResponse")
            if authenticated:
                result = json.loads(data)
                if not isinstance(result, dict):
                    raise TTSError("InvalidResponse")
                return result
            return data
        except HTTPError as exc:
            delay = (exc.headers or {}).get("Retry-After", "0")
            status = exc.code
            # Close the error response as well: destructor warnings can expose
            # its remote reason string even though our exception is sanitized.
            exc.close()
            raise TTSError(f"HTTP{status}", rejected=status == 429,
                           retry_after=retry_seconds(delay)) from None
        except (URLError, TimeoutError, OSError):
            raise TTSError("NetworkOutcomeUnknown") from None
        except (ValueError, TypeError):
            raise TTSError("InvalidResponse") from None


class CloudProvider:
    def __init__(self, settings, transport=None):
        self.settings = settings
        value = settings.get("base_url", self.default_base)
        if not isinstance(value, str):
            raise TTSError("InvalidEndpoint")
        self.base = value.rstrip("/")
        try:
            p = urlparse(self.base)
            valid = p.scheme == "https" and not (p.username or p.password or p.query or p.fragment or p.port) and self.allowed_host(p.hostname) and p.path == self.base_path
        except ValueError:
            valid = False
        if not valid:
            raise TTSError("InvalidEndpoint")
        self.settings = {**settings, "base_url": self.base}
        self.scope = f"{self.base}|{settings['api_key_env']}"
        self.transport = transport or HTTPTransport(settings["api_key_env"], settings["request_timeout"])
        self.validate_settings()

    def post(self, path, payload=None, multipart=None):
        data = self.transport.request("POST", self.base + path, payload, multipart)
        if not isinstance(data, dict):
            raise TTSError("InvalidResponse")
        if self.name == "minimax":
            status = object_field(data, "base_resp").get("status_code")
            if type(status) is not int or status != 0:
                # Only explicit quota rejection is safe for a paid automatic retry.
                raise TTSError(f"MiniMax{status}" if type(status) is int else "InvalidResponse",
                               rejected=type(status) is int and status in {1002, 1039, 1041, 2045})
        elif data.get("code") or data.get("status_code", 200) != 200:
            code = data.get("code")
            limited = isinstance(code, str) and code in {"Throttling", "Throttling.RateQuota", "Throttling.AllocationQuota"}
            raise TTSError(code if limited else "QwenRejected", rejected=limited)
        return data

    def check_credentials(self):
        if isinstance(self.transport, HTTPTransport):
            self.transport.check_credentials()

    def download_url(self, url):
        if not isinstance(url, str):
            raise TTSError("InvalidAudioURL")
        # Official Qwen examples still return HTTP OSS links; require TLS.
        if url.startswith("http://") and self.name == "qwen":
            url = "https://" + url[7:]
        p = urlparse(url)
        if p.scheme != "https" or p.username or p.password or not p.hostname or p.hostname in {"localhost", "127.0.0.1", "::1"}:
            raise TTSError("InvalidAudioURL")
        return self.transport.request("GET", url, authenticated=False)

    def download(self, receipt):
        try:
            audio = bytes.fromhex(receipt.audio_hex) if receipt.audio_hex else self.download_url(receipt.audio_url)
            timestamps = json.loads(self.download_url(receipt.subtitle_url)) if receipt.subtitle_url else None
        except (ValueError, TypeError):
            raise TTSError("InvalidAudioResponse") from None
        if not audio:
            raise TTSError("EmptyAudio")
        if timestamps is not None and not isinstance(timestamps, (list, dict)):
            raise TTSError("InvalidSubtitleResponse")
        return audio, timestamps

    def validate_settings(self):
        cap, s = self.capabilities(), self.settings
        if s["model"] not in cap.models:
            raise TTSError("UnsupportedModel")
        if not cap.speed_range[0] <= s["speed"] <= cap.speed_range[1] or (s["emotion"] is not None and s["emotion"] not in cap.emotions):
            unsupported()
        if s["subtitles"] and not cap.timestamps:
            unsupported()
        if s["sample_rate"] not in (8000, 16000, 22050, 24000, 32000, 44100, 48000):
            raise TTSError("InvalidSampleRate")

    def query_voice(self, voice_id):
        unsupported()

    def update_voice(self, voice_id, reference):
        unsupported()

    def query_task(self, task_id):
        unsupported()  # Adapters deliberately use bounded synchronous synthesis.

    def check_text(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > self.capabilities().max_characters:
            raise TTSError("InvalidText")


class QwenProvider(CloudProvider):
    name, default_base, base_path = "qwen", "https://dashscope.aliyuncs.com/api/v1", "/api/v1"
    customization = "/services/audio/tts/customization"

    def allowed_host(self, host):
        return host in {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"} or bool(re.fullmatch(r"[a-zA-Z0-9-]+\.(cn-beijing|ap-southeast-1)\.maas\.aliyuncs\.com", host or ""))

    def capabilities(self):
        return Capability("qwen", ("qwen3-tts-vc-2026-01-22",), 600, ("wav",), (24000,))

    def validate_voice_name(self, name):
        if not re.fullmatch(r"[A-Za-z0-9_]{1,16}", name):
            raise TTSError("InvalidVoiceName")

    def create_voice(self, reference, name, consent):
        require_consent(consent, self.name, self.scope)
        self.validate_voice_name(name)
        from .tts_audio import validate_reference
        validate_reference(reference, self.settings, "qwen")
        path = Path(reference)
        mime = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4"}[path.suffix.lower()]
        data = self.post(self.customization, {"model": "qwen-voice-enrollment", "input": {
            "action": "create", "target_model": self.settings["model"], "preferred_name": name,
            "audio": {"data": f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()}}})
        out = object_field(data, "output")
        if not isinstance(out.get("voice"), str) or not out["voice"] or out.get("target_model") != self.settings["model"]:
            raise TTSError("InvalidVoiceResponse")
        return {"voice_id": out["voice"], "models": [out["target_model"]],
                "status": "review_required" if out.get("fallback_mode") else "ready",
                "fallback_mode": bool(out.get("fallback_mode"))}

    def list_voices(self, page=0):
        if type(page) is not int or page < 0:
            raise TTSError("InvalidPage")
        data = self.post(self.customization, {"model": "qwen-voice-enrollment", "input": {
            "action": "list", "page_index": page, "page_size": 100}})
        out = object_field(data, "output")
        if not isinstance(out.get("voice_list"), list):
            raise TTSError("InvalidVoiceResponse")
        return out

    def delete_voice(self, voice_id):
        data = self.post(self.customization, {"model": "qwen-voice-enrollment", "input": {"action": "delete", "voice": voice_id}})
        if object_field(data, "output").get("voice") != voice_id:
            raise TTSError("InvalidVoiceResponse")

    def synthesize(self, text, voice_id):
        self.check_text(text)
        data = self.post("/services/aigc/multimodal-generation/generation", {
            "model": self.settings["model"], "input": {"text": text, "voice": voice_id}})
        url = object_field(object_field(data, "output"), "audio").get("url")
        if not isinstance(url, str) or not url:
            raise TTSError("InvalidAudioResponse")
        return SynthesisReceipt("wav", audio_url=url, request_id=data.get("request_id"))


class MiniMaxProvider(CloudProvider):
    name, default_base, base_path = "minimax", "https://api.minimax.io", ""
    models = ("speech-2.8-hd", "speech-2.8-turbo", "speech-2.6-hd", "speech-2.6-turbo",
              "speech-02-hd", "speech-02-turbo", "speech-01-hd", "speech-01-turbo")

    def allowed_host(self, host):
        return host in {"api.minimax.io", "api.minimax.cn"}

    def capabilities(self):
        emotions = ("happy", "sad", "angry", "fearful", "disgusted", "surprised", "calm")
        if self.settings["model"] in {"speech-2.6-hd", "speech-2.6-turbo"}:
            emotions += ("fluent", "whisper")
        return Capability("minimax", self.models, 9999, ("wav", "mp3", "flac"),
                          (8000, 16000, 22050, 24000, 32000, 44100), emotions, (.5, 2), timestamps=True)

    def validate_settings(self):
        super().validate_settings()
        if self.settings["sample_rate"] not in self.capabilities().sample_rates:
            raise TTSError("InvalidSampleRate")

    def validate_voice_name(self, name):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{6,254}[A-Za-z0-9]", name):
            raise TTSError("InvalidVoiceName")

    def create_voice(self, reference, name, consent):
        require_consent(consent, self.name, self.scope)
        self.validate_voice_name(name)
        from .tts_audio import validate_reference
        validate_reference(reference, self.settings, "minimax")
        file = object_field(self.post("/v1/files/upload", multipart=reference), "file")
        if type(file.get("file_id")) is not int or file["file_id"] <= 0:
            raise TTSError("InvalidUploadResponse")
        data = self.post("/v1/voice_clone", {"file_id": file["file_id"], "voice_id": name})
        if data.get("input_sensitive"):
            raise TTSError("VoiceRejected")
        # ID is client supplied, valid only after the real clone API succeeds.
        return {"voice_id": name, "models": list(self.models), "status": "pending_activation",
                "uploaded_file_id": file["file_id"], "activation_window_days": 7}

    def list_voices(self, page=0):
        if page != 0:
            unsupported()
        data = self.post("/v1/get_voice", {"voice_type": "voice_cloning"})
        return {"voice_list": data.get("voice_cloning", []), "inactive_voices_omitted": True}

    def delete_voice(self, voice_id):
        self.post("/v1/delete_voice", {"voice_type": "voice_cloning", "voice_id": voice_id})

    def synthesize(self, text, voice_id):
        self.check_text(text)
        s = self.settings
        voice = {"voice_id": voice_id, "speed": s["speed"]}
        if s["emotion"] is not None:
            voice["emotion"] = s["emotion"]
        data = self.post("/v1/t2a_v2", {"model": s["model"], "text": text, "stream": False,
            "output_format": "hex", "voice_setting": voice,
            "audio_setting": {"format": s["format"], "sample_rate": s["sample_rate"], "channel": 1},
            "subtitle_enable": s["subtitles"], "subtitle_type": "sentence"})
        out = data.get("data")
        if not isinstance(out, dict):
            raise TTSError("InvalidAudioResponse")
        if out.get("status") != 2 or not isinstance(out.get("audio"), str) or not out["audio"]:
            raise TTSError("InvalidAudioResponse")
        return SynthesisReceipt(s["format"], audio_hex=out["audio"], subtitle_url=out.get("subtitle_file"), request_id=data.get("trace_id"))
