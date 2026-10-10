"""Creator Voice sidecar assets; original media and V3.0-01 libraries stay read-only."""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .contracts import validate
from .storage import digest_file, now, read_json, validate_id, write_json
from .tts import TTSError, require_consent
from .tts_audio import metadata, tool, validate_reference


class VoiceAssets:
    def __init__(self, workspace, creator):
        self.workspace, self.creator = workspace, validate_id(creator)
        self.root = workspace.creator(creator) / "voice-assets"

    def reference(self, reference_id):
        data = read_json(self.root / "references" / (validate_id(reference_id) + ".json"))
        path = self.root / "references" / data["audio"]
        if path.parent.resolve() != (self.root / "references").resolve() or not path.is_file() or digest_file(path) != data["sha256"]:
            raise TTSError("ReferenceChanged")
        return data, path

    def add_reference(self, settings, *, source=None, library=None, candidate=None, consent=None):
        with self.workspace.lock(self.creator):
            reference_id = "ref-" + uuid.uuid4().hex
            folder = self.root / "references"
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / (reference_id + ".wav")
            if library:
                data = read_json(library)
                validate(data, "video-library")
                if data["creator_id"] != self.creator:
                    raise TTSError("CreatorMismatch")
                matches = [(s, c) for s in data["sources"] if s["status"] == "analyzed"
                           for c in s["candidates"] if c["candidate_id"] == candidate]
                if len(matches) != 1:
                    raise TTSError("InvalidCandidate")
                entry, clip = matches[0]
                source = Path(entry["path"])
                if digest_file(source) != entry["sha256"]:
                    raise TTSError("SourceChanged")
                provenance = {"library": str(Path(library).resolve()), "source_id": entry["source_id"],
                              "candidate_id": candidate, "start": clip["start"], "end": clip["end"]}
                cut = ["-ss", str(clip["start"]), "-t", str(clip["end"] - clip["start"])]
            else:
                source, provenance, cut = Path(source).resolve(), {}, []
            source_hash = digest_file(source)
            provenance.update(path=str(source), sha256=source_hash)
            try:
                tool([settings["ffmpeg"], "-v", "error", "-nostdin", "-n", "-i", str(source), *cut,
                      "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(target)])
                if digest_file(source) != source_hash:
                    raise TTSError("SourceChanged")
                info = {"schema_version": "3.0-03", "reference_id": reference_id, "creator_id": self.creator,
                        "created_at": now(), "audio": target.name, "sha256": digest_file(target),
                        "source": provenance, "metadata": metadata(target, settings), "consent": consent}
                write_json(folder / (reference_id + ".json"), info)
                return info
            except BaseException:
                target.unlink(missing_ok=True)
                raise

    def voices(self):
        return [self.get(p.stem) for p in sorted(self.root.glob("voice-*.json"))]

    def get(self, asset_id):
        data = read_json(self.root / (validate_id(asset_id) + ".json"))
        validate(data, "voice-asset")
        if data["creator_id"] != self.creator:
            raise TTSError("CreatorMismatch")
        return data

    def save(self, data):
        validate(data, "voice-asset")
        write_json(self.root / (validate_id(data["asset_id"]) + ".json"), data)

    def usable(self, asset_id, provider):
        asset = self.get(asset_id)
        if asset["provider"] != provider.name or asset["scope"] != provider.scope:
            raise TTSError("ProviderOrAccountMismatch")
        if provider.settings["model"] not in asset["models"]:
            raise TTSError("VoiceModelMismatch")
        if asset["status"] not in {"ready", "pending_activation"} or not asset["voice_id"]:
            raise TTSError("VoiceNotReady")
        if asset.get("activation_deadline") and not asset.get("last_used_at") and now() > asset["activation_deadline"]:
            raise TTSError("VoiceActivationExpired")
        require_consent(asset["consent"], provider.name, provider.scope)
        return asset

    def create(self, reference_id, provider, name):
        # Lock covers deduplication AND remote mutation across different tasks.
        with self.workspace.lock(self.creator):
            reference, path = self.reference(reference_id)
            require_consent(reference["consent"], provider.name, provider.scope)
            validate_reference(path, provider.settings, provider.name)
            key = hashlib.sha256((reference["sha256"] + provider.scope + provider.settings["model"]).encode()).hexdigest()
            asset_id = "voice-" + key[:32]
            asset_path = self.root / (asset_id + ".json")
            if asset_path.exists():
                existing = self.get(asset_id)
                if existing["status"] in {"ready", "pending_activation"}:
                    return self.usable(asset_id, provider)
                raise TTSError("VoiceCreationNeedsReview")
            provider.validate_voice_name(name)
            provider.check_credentials()
            asset = {"schema_version": "3.0-03", "asset_id": asset_id, "creator_id": self.creator,
                     "provider": provider.name, "scope": provider.scope, "voice_id": None,
                     "models": [provider.settings["model"]], "created_at": now(), "source": {
                         "reference_id": reference_id, "sha256": reference["sha256"]},
                     "status": "outcome_unknown", "consent": reference["consent"], "last_used_at": None}
            self.save(asset)  # durable BEFORE a possibly billable upload/create
            try:
                result = provider.create_voice(path, name, reference["consent"])
                if not result.get("voice_id"):
                    raise TTSError("InvalidVoiceResponse")
                asset.update(result)
                if provider.name == "minimax":
                    asset["activation_deadline"] = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
                self.save(asset)
                return asset
            except BaseException as exc:
                asset.update(status="failed" if isinstance(exc, TTSError) and exc.rejected else "outcome_unknown",
                             voice_id=None, error=getattr(exc, "code", "ProviderError"))
                self.save(asset)
                raise

    def import_voice(self, provider, voice_id, consent):
        require_consent(consent, provider.name, provider.scope)
        with self.workspace.lock(self.creator):
            found = None
            for page in range(100):
                data = provider.list_voices(page)
                for entry in data.get("voice_list", []):
                    if entry.get("voice", entry.get("voice_id")) == voice_id:
                        found = entry
                        break
                if found or provider.name == "minimax" or (page+1)*100 >= data.get("total_count", 0):
                    break
            if not found:
                raise TTSError("VoiceNotFound")
            model = found.get("target_model") if provider.name == "qwen" else provider.settings["model"]
            if model != provider.settings["model"]:
                raise TTSError("VoiceModelMismatch")
            key = hashlib.sha256((provider.scope + voice_id).encode()).hexdigest()[:32]
            asset_id = "voice-" + key
            path = self.root / (asset_id + ".json")
            if path.exists():
                return self.usable(asset_id, provider)
            asset = {"schema_version": "3.0-03", "asset_id": asset_id, "creator_id": self.creator,
                     "provider": provider.name, "scope": provider.scope, "voice_id": voice_id,
                     "models": [model], "created_at": now(), "source": {"kind": "existing_cloud_voice"},
                     "status": "ready", "consent": consent, "last_used_at": None}
            self.save(asset)
            return asset

    def mark_used(self, asset_id):
        with self.workspace.lock(self.creator):
            asset = self.get(asset_id)
            if asset["status"] not in {"ready", "pending_activation"}:
                raise TTSError("VoiceNotReady")
            asset.update(status="ready", last_used_at=now())
            self.save(asset)

    def delete(self, asset_id, provider):
        with self.workspace.lock(self.creator):
            asset = self.get(asset_id)
            if asset["scope"] != provider.scope or asset["provider"] != provider.name or not asset["voice_id"]:
                raise TTSError("ProviderOrAccountMismatch")
            provider.delete_voice(asset["voice_id"])
            asset["status"] = "deleted"
            self.save(asset)
            return asset
