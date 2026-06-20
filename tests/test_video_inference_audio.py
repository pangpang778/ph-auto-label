import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugins.video_inference import build_encode_command


def test_encode_command_preserves_original_audio_track():
    command = build_encode_command(
        video_path="input.mp4",
        width=1280,
        height=720,
        fps=30.0,
        output_path="output.mp4",
    )

    assert command.count("-i") == 2
    assert command[command.index("-i") + 1] == "pipe:0"
    assert "input.mp4" in command
    assert "-map" in command
    assert [command[i + 1] for i, value in enumerate(command) if value == "-map"] == ["0:v:0", "1:a:0"]
    assert "-c:a" in command
    assert command[command.index("-c:a") + 1] == "copy"
    assert "-shortest" in command
