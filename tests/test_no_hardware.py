"""Static boundary check: the Studio must not touch hardware, DDS or motion.

The Studio is an L4 operator application. It talks to the system gateways over
HTTP/WS only: it joins no DDS domain, opens no device, publishes no vendor
topic, calls no vendor SDK and never executes motion itself.

The scan covers Python, YAML, shell, XML and config files outside the
documentation, evidence, CI and test directories. Documentation may name
forbidden devices and topics when explaining the boundary; code and configs may
not use them.

This file itself contains the forbidden tokens, so the tests directory is
excluded from the scan.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXCLUDED_DIRS = {
    ".git",
    "tests",
    "docs",
    "evidence",
    ".github",
    "__pycache__",
    ".pytest_cache",
    ".venv",
}
TEXT_SUFFIXES = {
    ".py",
    ".yaml",
    ".yml",
    ".sh",
    ".xml",
    ".xacro",
    ".cfg",
    ".toml",
    ".json",
    ".txt",
}
FORBIDDEN = (
    "/dev/",
    "serial.Serial",
    "pyserial",
    "socketcan",
    "can0",
    "pigpio",
    "RPi.GPIO",
    "gpiod",
    "cv2.VideoCapture",
    "picamera",
    "/ros_robot_controller",
    "ros_robot_controller_sdk",
    "set_motor_speeds",
    "MotorsSpeedControl",
    "controller/mecanum",
    "ExecuteMotion",
    "execute_motion",
    "/move_action",
    "/cmd_vel",
    "/joint_states",
    "rclpy",
    "ros2 run",
    "ros2 launch",
    "10.42.0.218",
    "paramiko",
)
ARTIFACT_SUFFIXES = {
    ".pt",
    ".onnx",
    ".engine",
    ".bag",
    ".mcap",
    ".db3",
    ".mp4",
    ".avi",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".env",
}


def _scanned_files():
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        parts = path.relative_to(ROOT).parts[:-1]
        if any(part in EXCLUDED_DIRS for part in parts):
            continue
        if path.suffix not in TEXT_SUFFIXES:
            continue
        yield path


def test_no_hardware_dds_or_motion_tokens():
    problems = []
    for path in _scanned_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for token in FORBIDDEN:
            if token in text:
                problems.append(f"{path.relative_to(ROOT)}: {token}")
    assert not problems, "forbidden hardware/DDS/motion tokens found:\n" + "\n".join(
        problems
    )


def test_no_model_weights_session_data_or_recordings():
    found = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        if ".git" in path.relative_to(ROOT).parts:
            continue
        if path.suffix in ARTIFACT_SUFFIXES:
            found.append(str(path.relative_to(ROOT)))
    assert not found, (
        "model weights, session data or recordings found:\n" + "\n".join(found)
    )


def test_no_dataset_model_or_session_store_directories():
    for name in ("datasets", "models", "data"):
        directory = ROOT / name
        if directory.is_dir():
            files = [p for p in directory.rglob("*") if p.is_file()]
            assert not files, f"{name}/ must stay outside the repository"


def test_gitignore_covers_data_weights_and_sessions():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in (
        "datasets/",
        "models/",
        "data/",
        "*.pt",
        "*.onnx",
        "*.engine",
        "*.bag",
        "*.mcap",
        "*.db",
        "*.sqlite",
        "*.env",
    ):
        assert pattern in ignored, f".gitignore missing {pattern}"
