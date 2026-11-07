"""Linux/POSIX counterparts of Windows-only helpers.

``mocap/runtime_registry.py`` is hash-pinned by ``installer/runtime-lock.json``
and must stay byte-identical to upstream, so the POSIX development-venv layout
(``bin/python``, ``lib/python3.X/site-packages``) lives here. Callers dispatch
on ``sys.platform != 'win32'``; Windows always uses the registry unchanged.
"""
from __future__ import annotations

import os
from pathlib import Path


def venv_python(folder) -> Path:
    """Interpreter of a POSIX virtual environment."""
    return Path(folder) / 'bin/python'


def venv_site(folder) -> Path:
    """site-packages of a POSIX virtual environment (lib/python3.X/site-packages)."""
    folder = Path(folder)
    found = sorted((folder / 'lib').glob('python3.*/site-packages'))
    return found[0] if found else folder / 'lib/site-packages'


def command(root, stage, args=(), *, frozen=False):
    """Development-venv branch of ``runtime_registry.command`` for POSIX."""
    from .runtime_registry import PROFILES, profile_for
    root = Path(root); key = profile_for(stage, args); spec = PROFILES[key]
    if frozen or os.environ.get('RGB_MOCAP_MANAGED') == '1':
        raise RuntimeError('Managed (installer) runtimes are Windows-only. Use the development venvs from tools/setup_linux.py.')
    python = venv_python(root / spec['dev'])
    if not python.is_file():
        raise RuntimeError(key + ' development runtime is missing. Run: python3 tools/setup_linux.py --profiles ' + key)
    return [str(python), '-X', 'utf8', '-m', 'mocap.worker_progress', stage, *map(str, args)]


def activate(root, key, base_root=None):
    """Development-venv version of ``runtime_registry.activate`` for POSIX.

    Same checks as upstream: the running interpreter is the profile's venv, the
    Python version and pinned package versions match, and every pinned package
    imports from the profile's own overlay or from its base environment.
    """
    import importlib.metadata as metadata
    import importlib.util
    import sys
    from .runtime_registry import PROFILES
    root = Path(root).resolve(); spec = PROFILES[key]
    base = PROFILES[spec['base']] if spec['base'] else spec
    base_root = Path(base_root).resolve() if base_root else root
    python = venv_python(root / spec['dev'])
    # Venv interpreters are symlinks to a shared CPython; compare the venv path itself.
    if Path(sys.executable).absolute() != python.absolute():
        raise RuntimeError('Wrong interpreter for ' + key + ': ' + sys.executable + ' (expected ' + str(python) + ')')
    if '.'.join(map(str, sys.version_info[:3])) != spec['python']:
        raise RuntimeError('Unexpected Python version for ' + key)
    site = venv_site(base_root / base['dev'])
    overlay = venv_site(root / spec['dev']) if spec['base'] else site
    if not overlay.is_dir(): raise RuntimeError(key + ' runtime is missing. Run tools/setup_linux.py to repair it.')
    if spec['base'] and str(overlay) not in sys.path: sys.path.insert(0, str(overlay))
    own = {d.metadata['Name'].lower().replace('_', '-') for d in metadata.distributions(path=[str(overlay)])}
    paths = {}
    for name, version in spec['packages'].items():
        dist = metadata.distribution(name); module = importlib.util.find_spec(name)
        if dist.version != version or module is None or not module.origin:
            raise RuntimeError('Incompatible ' + name + ' in ' + key + '. Run tools/setup_linux.py to repair it.')
        actual = Path(module.origin).resolve(); expected = overlay if name in own else site
        if not actual.is_relative_to(expected.resolve()):
            raise RuntimeError('Unexpected ' + name + ' import path in ' + key + ': ' + str(actual))
        paths[name] = str(actual)
    return dict(profile=key, python=str(python), packages=spec['packages'], imports=paths)


def kill_tree(pid: int) -> None:
    """Kill a process and all its descendants, children first (taskkill /T /F)."""
    import psutil
    try:
        parent = psutil.Process(pid)
        procs = parent.children(recursive=True) + [parent]
    except psutil.Error:
        return
    for p in procs:
        try: p.kill()
        except psutil.Error: pass
    psutil.wait_procs(procs, timeout=3)


def peak_rss(process=None) -> int:
    """Peak resident memory in bytes: Windows peak working set, else ru_maxrss."""
    import psutil
    info = (process or psutil.Process()).memory_info()
    peak = getattr(info, 'peak_wset', None)
    if peak is not None:
        return peak
    if process is None or process.pid == psutil.Process().pid:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024  # KiB on Linux
    return info.rss  # no peak available for another process on POSIX
