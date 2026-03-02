"""
Startup GPU / CUDA health check.

Call `check_gpu_at_startup(parent)` once after the main window is shown.
It will display a friendly QMessageBox warning if an NVIDIA GPU is detected
but the CUDA runtime libraries are not installed — and do nothing otherwise.

Keeping this check here (rather than buried in the transcription worker) means:
  - The user is informed immediately, before they try anything.
  - The whisper worker never needs to raise CUDA errors.
  - All platform / distro logic lives in one place.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import platform
import subprocess


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _detect_nvidia_gpu() -> tuple[bool, str]:
    """
    Ask nvidia-smi whether an NVIDIA GPU is physically present.
    Returns (has_gpu, "GPU Name, Driver X.Y.Z").
    Safe on every OS: returns (False, "") if nvidia-smi is not found.
    """
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            lines = [ln.strip() for ln in result.stdout.strip().splitlines() if ln.strip()]
            return bool(lines), lines[0] if lines else "Unknown NVIDIA GPU"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return False, ""


def _cuda_runtime_present() -> bool:
    """
    Check whether the CUDA runtime shared libraries are loadable using ctypes.

    This deliberately does NOT import torch or call torch.cuda so that
    PyTorch's C-level "libcublas.so not found" message is never triggered.
    """
    system = platform.system()

    if system == "Linux":
        return ctypes.util.find_library("cublas") is not None

    if system == "Windows":
        for name in ("cublas64_12", "cublas64_11", "cublas64_10"):
            if ctypes.util.find_library(name):
                return True
        return False

    # macOS (Boot Camp / eGPU edge case)
    return ctypes.util.find_library("cublas") is not None


def _build_warning_message() -> str:
    """Build a human-readable, distro-aware install-CUDA message."""
    # Read the CUDA version torch expects from the already-installed wheel.
    try:
        import torch  # noqa: PLC0415
        torch_cuda_ver = getattr(torch.version, "cuda", None) or "12.x"
    except Exception:
        torch_cuda_ver = "12.x"

    system = platform.system()

    if system == "Windows":
        return (
            f"An NVIDIA GPU was detected on this machine, but the CUDA "
            f"{torch_cuda_ver} runtime is not installed.\n\n"
            "GPU acceleration will be unavailable until you install the "
            "CUDA Toolkit:\n\n"
            "  https://developer.nvidia.com/cuda-downloads\n\n"
            "After installing, reboot and restart the application.\n\n"
            "The app will continue using the CPU in the meantime."
        )

    if system == "Linux":
        os_id = ""
        try:
            with open("/etc/os-release") as fh:
                for line in fh:
                    if line.startswith("ID=") or line.startswith("ID_LIKE="):
                        os_id += line.lower()
        except OSError:
            pass

        if any(d in os_id for d in ("arch", "manjaro", "endeavour", "garuda", "omarchy")):
            pm_cmd = "sudo pacman -S cuda"
        elif any(d in os_id for d in ("ubuntu", "debian", "pop", "mint", "elementary")):
            pm_cmd = "sudo apt install nvidia-cuda-toolkit"
        elif any(d in os_id for d in ("fedora", "rhel", "centos", "rocky", "alma")):
            pm_cmd = "sudo dnf install cuda"
        elif any(d in os_id for d in ("opensuse", "suse")):
            pm_cmd = "sudo zypper install cuda"
        else:
            pm_cmd = ""

        pkg_section = (
            f"Install via your package manager:\n\n    {pm_cmd}\n\n"
            if pm_cmd else ""
        )
        return (
            f"An NVIDIA GPU was detected on this machine, but the CUDA "
            f"{torch_cuda_ver} runtime libraries are not installed.\n\n"
            "GPU acceleration will be unavailable until CUDA is set up.\n\n"
            + pkg_section
            + "Or download the official installer:\n\n"
            "  https://developer.nvidia.com/cuda-downloads\n\n"
            "After installing, restart the application.\n\n"
            "The app will continue using the CPU in the meantime."
        )

    # Generic fallback
    return (
        f"An NVIDIA GPU was detected, but the CUDA {torch_cuda_ver} runtime "
        "is not installed.\n\n"
        "Install from:\n\n"
        "  https://developer.nvidia.com/cuda-downloads\n\n"
        "After installing, restart the application.\n\n"
        "The app will continue using the CPU in the meantime."
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_gpu_at_startup(parent=None) -> None:
    """
    Run the GPU / CUDA health check and, if needed, show a warning dialog.

    Call this once after the main window is shown.  It is a no-op on:
      - Machines with no NVIDIA GPU
      - Machines where CUDA is properly installed
      - Apple Silicon (MPS is handled transparently by PyTorch)

    Parameters
    ----------
    parent : QWidget | None
        Parent widget for the QMessageBox (can be the MainWindow).
    """
    system = platform.system()

    # Apple Silicon uses MPS — no CUDA needed, nothing to check.
    if system == "Darwin" and platform.machine() in ("arm64", "aarch64"):
        return

    has_nvidia, gpu_info = _detect_nvidia_gpu()
    if not has_nvidia:
        return  # No NVIDIA GPU → CPU is the right choice, no warning needed.

    if _cuda_runtime_present():
        return  # CUDA is installed → everything is fine.

    # NVIDIA GPU found but CUDA runtime is missing → warn the user.
    from PySide6.QtWidgets import QMessageBox  # noqa: PLC0415

    msg_box = QMessageBox(parent)
    msg_box.setWindowTitle("GPU Detected — CUDA Not Installed")
    msg_box.setIcon(QMessageBox.Icon.Warning)
    msg_box.setText(
        f"<b>NVIDIA GPU detected:</b> {gpu_info}<br><br>"
        "The CUDA runtime is <b>not installed</b> on this machine.<br>"
        "Transcription will run on the <b>CPU</b> (slower) until CUDA is set up."
    )
    msg_box.setInformativeText(_build_warning_message())
    msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
    msg_box.exec()
