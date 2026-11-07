# Running RGB MoCap on Linux

Upstream RGB MoCap is a Windows application (`install.cmd`, `RGBMoCap.cmd`, managed
`runtime*/python.exe` environments). This fork adds a Linux path. It uses the same pinned
package versions from `installer/runtime-lock.json`, in per-profile development venvs.
Windows keeps working unchanged: `mocap/runtime_registry.py` stays byte-identical to
upstream, so its hash still matches `registry_sha256` in the runtime lock. All Linux-only
logic lives in `mocap/posix_compat.py` and is selected with `sys.platform != 'win32'`.

Tested on Ubuntu 26.04 LTS (x86_64) with an NVIDIA RTX 4090 (CUDA 12.8 wheels) and GCC 15.2.

## Prerequisites

- An NVIDIA GPU and driver that support CUDA 12.8. The PyTorch wheels bring their own CUDA libraries.
- System `python3` (3.10 or newer, with the `venv` module). It is only used to bootstrap `uv`.
  uv then downloads the exact interpreters the lock pins (CPython 3.10.22 and 3.12.10).
- These system packages:

  ```sh
  sudo apt install ffmpeg cmake g++ libglu1-mesa
  ```

  `ffmpeg`/`ffprobe` decode and encode video. `cmake` and `g++` build the native motion-correction
  module. `libGLU.so.1` is needed by Blender and the EGL/OpenGL renderers.
- About 16 GB of disk for all five venvs (`.venv` and `.venv-exmr` are about 7.5 GB each) and 1.2 GB for
  Blender, plus space for model weights and projects (see the data layout below).

## Install from a clean clone

```sh
git clone https://github.com/a904guy/toyxyz_RGBMocap.git
cd toyxyz_RGBMocap
python3 tools/setup_linux.py --profiles shared exmr      # add flow hand htd for the other models
```

`tools/setup_linux.py` runs these steps in order. Pick a subset with `--steps a,b,...`. Every
step is idempotent: re-running it skips work that is already done and verified.

| Step | What it does |
|---|---|
| `prereqs` | Checks `ffmpeg`, `ffprobe`, `cmake`, `g++` and `libGLU.so.1`. Exits non-zero (before doing anything else) and prints the apt line if one is missing. |
| `venvs` | Creates `.bootstrap/` with `uv==0.13.1` (the same pin as `tools/runtime_support.py`) on first use. Then builds `.venv` (shared/gui), `.venv-exmr`, `.venv-flow`, `.venv-hand` and `.venv-htd` from the runtime lock. Pure-Python wheels are installed from the exact locked URL and SHA-256. Platform wheels use the Linux build of the same `name==version`. Torch's Linux CUDA wheels (`nvidia-*`, `triton`) are installed under a constraints file, so no locked pin moves. Each venv is checked with `posix_compat.activate()`: interpreter, Python version, pinned versions and import paths. |
| `native` | Builds `third_party/KimodoMotionCorrection` with `.venv/bin/python setup.py build_ext` (in `.setup/motion-correction-build/`). Copies `_motion_correction.cpython-310-x86_64-linux-gnu.so` into its `bin/`, then loads it through `mocap/correction.py`. |
| `blender` | Downloads `blender-4.5.0-linux-x64.tar.xz` into `.cache/downloads/`. Checks it against a SHA-256 pinned in the script **and** blender.org's published `.sha256` list. Extracts it into `runtime_character/`, checks `runtime_character/blender --version` = `Blender 4.5.0`, and writes `RGBMOCAP_RUNTIME.json`. |
| `opencc` | Repacks the OpenCC wheel for `mmd_tools` deterministically, as `tools/build_character_runtime.py` does. |
| `assets` | `tools/stage_assets.py --download`: the hash-verified auxiliary body data under `third_party/`. |

Model weights are not part of setup. Add them in the app the same way as on Windows (**Models**),
or put an existing `models/` tree in place (see below).

## Launch

```sh
./RGBMoCap.sh
```

The launcher is the counterpart of `RGBMoCap.cmd`. It clears `PYTHONHOME`/`PYTHONPATH` and
points `RGB_MOCAP_DATA`, `HF_HOME` and `TORCH_HOME` at the checkout. It sets
`RGBMOCAP_BLENDER=runtime_character/blender` and starts `app.py` with `.venv/bin/python`.
`QT_QPA_PLATFORM` defaults to `xcb` (XWayland), where PySide6's `QOpenGLWidget` is most reliable.
Override it if you need to, e.g. `QT_QPA_PLATFORM=offscreen` for headless runs. App options pass
through, e.g. `./RGBMoCap.sh --smoke-screenshot out.png --smoke-report out.json`.

Workers run as `.venv*/bin/python -X utf8 -m mocap.worker_progress <stage> ...`. This is the
Windows development layout with `bin/python` in place of `Scripts\python.exe`. The Windows
*managed* runtimes (`runtime*/python.exe`, `RGB_MOCAP_MANAGED=1`) do not exist on Linux and are
rejected with a clear error.

## Data layout

The app keeps its data under `RGB_MOCAP_DATA` (default: the checkout):

```
$RGB_MOCAP_DATA/models/     AI weights and registered body models (SMPL-X, MANO, ...)
$RGB_MOCAP_DATA/cache/      per-model caches (ExMR assets, worker job files)
$RGB_MOCAP_DATA/projects/   one folder per video project, including person takes and characters
```

These folders can be large. On the reference machine the root disk is nearly full, so they live on
a separate volume and are symlinked into the checkout:

```
toyxyz_RGBMocap/models   -> /xfs/Models/RGBMoCap/models
toyxyz_RGBMocap/cache    -> /xfs/Models/RGBMoCap/cache
toyxyz_RGBMocap/projects -> /xfs/Models/RGBMoCap/projects
```

`.gitignore` ignores the symlinks (`/models`, `/cache`, `/projects`, without the trailing slash
the directory patterns need). `mocap/paths.py` resolves `projects/` once, so paths derived from it
agree with the paths workers record.

To use a separate, empty data root (for example a clean verification run that must not reuse any
cache), point `RGB_MOCAP_DATA` at a folder that links the shared weights but has its own
projects:

```sh
mkdir -p /xfs/Models/RGBMoCap/verify/run1/projects
ln -s /xfs/Models/RGBMoCap/models /xfs/Models/RGBMoCap/verify/run1/models
RGB_MOCAP_DATA=/xfs/Models/RGBMoCap/verify/run1 .venv/bin/python app.py
```

## Not ported

- **PEAR webcam mode.** The live camera stream uses a Windows named mutex and shared memory
  (`mocap/pear/camera_stream.py`) and DirectShow device listing through `tools/bin/ffmpeg.exe -f dshow`
  (`mocap/pear_ui.py`). File-based processing is unaffected.
- **MAMMA and PromptHMR setup** (`Setup_MAMMA.cmd`, `Setup_PromptHMR.cmd`, `tools/setup_mamma.py`,
  `tools/setup_prompthmr.py`). They build Windows runtimes with MSVC.
- **Resource Safety: Windows commit headroom.** `processing_guard.commit_headroom()` reads the Windows
  commit (page-file) limit and already returns `None` on other platforms (upstream code), so
  `compute_policy.memory_pressure_message` skips that part. The RAM and VRAM checks still run.
- `install.cmd`, `update.cmd`, `launch_source.py` and the installer (`installer/`) are Windows-only.
  Update a Linux checkout with `git pull` on this fork, then re-run `python3 tools/setup_linux.py`.

## Linux patches

Every change to upstream files, and why. The new files are `mocap/posix_compat.py`, `tools/setup_linux.py`,
`RGBMoCap.sh`, this document and `tools/tests/test_posix_dispatch.py`.

| File | Change | Reason |
|---|---|---|
| `mocap/paths.py` | `worker_command` uses `posix_compat.command` when `sys.platform != 'win32'`; `PROJECTS` is resolved once | Dev venvs use `bin/python`. A symlinked `projects/` would otherwise give two spellings of the same project path. |
| `mocap/character_ui.py` | Character jobs use `posix_compat.command` on POSIX; cancel uses `posix_compat.kill_tree` | Same venv layout. `taskkill /T /F` does not exist. |
| `mocap/flow_hamer.py` | ExMR venv interpreter from `posix_compat.venv_python` on POSIX; peak RSS via `posix_compat.peak_rss` (computed before the f-string) | `.venv-exmr/bin/python`. `peak_wset` is Windows-only. |
| `mocap/flow_hamer_entry.py` | `posix_compat.activate` on POSIX | Validates the dev venv (the registry's `activate` expects `runtime_exmr/python.exe`). |
| `mocap/gui.py` | Cancel kills the whole worker tree (`kill_tree`) before `kill()` | Workers spawn children. On Windows this is `taskkill /T`. |
| `mocap/correction.py` | Also loads `_motion_correction*.so` | GCC build of the native module. |
| `mocap/worker.py` | Fingerprints include `*.so`; peak RSS via `peak_rss` | Cache identity of the native module. `peak_wset` is Windows-only. |
| `mocap/batch_worker.py` | `num_fds()` when `num_handles()` is unavailable | psutil has no handle count on POSIX. |
| `mocap/flow_backend.py`, `mocap/playback_check.py` | Peak RSS via `peak_rss`; `private` memory falls back to `rss` | `peak_wset`/`private` are Windows-only. |
| `mocap/exmr_backend.py`, `mocap/handflow_engine.py` | `PYOPENGL_PLATFORM=egl` on POSIX | Headless OpenGL for the mesh renderers. |
| `mocap/video.py`, `mocap/exmr_backend.py`, `mocap/mamma_*.py`, `mocap/prompthmr_*.py` | `creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)` instead of `0x08000000` | Non-zero `creationflags` raises `ValueError` on POSIX. |
| `mocap/retarget/runtime.py` | Also finds `runtime_character/blender` | The Linux Blender executable has no `.exe`. |
| `.gitignore` | `/.bootstrap/`, `/third_party/KimodoMotionCorrection/bin/*.so`, `/models`, `/cache`, `/projects` | Bootstrap venv, local build output and data-root symlinks. |

`mocap/runtime_registry.py`, `worker_boot.py`, `installer/` and `launch_source.py` are deliberately
unchanged. `tools/tests/test_posix_dispatch.py` checks that, with `sys.platform` patched to `win32`,
worker and character commands still resolve to `.venv/Scripts/python.exe`. Run the tests with:

```sh
.venv/bin/python -m unittest discover -s tools/tests
```
