"""Isolated task worker fixture. Synthetic silence; no cloud or style evidence."""

import time
from unittest.mock import Mock

from creator_dna.tts import make_provider
from creator_dna.tts_workflow import synthesize_stage
from test_tts import wav_bytes


def mock_cloud_tts(ctx, params):
    def request(method, url, *args, **kwargs):
        if method == "POST":
            time.sleep(params.get("synthetic_request_delay", 0))
            return {"output": {"audio": {"url": "https://synthetic.example/test.wav"}}, "request_id": "synthetic-only"}
        return wav_bytes()
    transport = Mock()
    transport.request.side_effect = request
    return {**synthesize_stage(ctx, params, make_provider(params["config"], transport)), "synthetic_only": True}
