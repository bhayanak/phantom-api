"""Record & replay: proxy real APIs, store responses, and replay them offline."""

from phantom_api.recorder.proxy import create_proxy_app
from phantom_api.recorder.replayer import build_replay_spec, create_replay_app
from phantom_api.recorder.storage import Interaction, RecordingStorage

__all__ = [
    "Interaction",
    "RecordingStorage",
    "build_replay_spec",
    "create_proxy_app",
    "create_replay_app",
]
