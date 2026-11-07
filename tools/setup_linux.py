"""Linux setup: the counterpart of install.cmd / tools/setup_dev.py.

Run it from a clean clone with the system interpreter:

    python3 tools/setup_linux.py [--profiles shared exmr ...] [--steps prereqs,venvs,native,blender,opencc,assets]

Steps (all by default, in this order; each one is idempotent and skips work already done):

  prereqs  ffmpeg, ffprobe, cmake, g++ and libGLU.so.1 must be present (apt: ffmpeg cmake g++ libglu1-mesa).
  venvs    Per-profile development venvs (.venv, .venv-exmr, ...) from installer/runtime-lock.json.
           Pins are identical: pure-Python wheels are installed from the exact locked URL and SHA-256;
           Windows platform wheels are replaced by the Linux build of the same name==version (PyPI /
           PyTorch cu128). Torch's Linux-only CUDA wheels (nvidia-*, triton) are added under a
           constraints file so no locked pin can move. uv comes from .bootstrap (uv==0.13.1, the same
           pin as tools/runtime_support.py), created on first use.
  native   Builds third_party/KimodoMotionCorrection with GCC and installs the .so into its bin/.
  blender  Blender 4.5.0 Linux x64 into runtime_character/ (SHA-256 pinned here and cross-checked
           against blender.org's .sha256 list).
  opencc   The mmd_tools OpenCC wheel, repacked deterministically as tools/build_character_runtime.py does.
  assets   tools/stage_assets.py --download (hash-verified auxiliary body data).
"""
from pathlib import Path
import argparse, ctypes.util, hashlib, json, os, shutil, subprocess, sys, tarfile, urllib.request, zipfile

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = {'shared': '.venv', 'flow': '.venv-flow', 'hand': '.venv-hand', 'htd': '.venv-htd', 'exmr': '.venv-exmr'}
WINDOWS_ONLY = {'win32-setctime', 'pywin32', 'pywin32-ctypes', 'pyreadline3', 'wmi', 'comtypes'}
TORCH_INDEX = 'https://download.pytorch.org/whl/cu128'
UV_PIN = 'uv==0.13.1'  # tools/runtime_support.py:find_uv
STEPS = ('prereqs', 'venvs', 'native', 'blender', 'opencc', 'assets')
RECEIPTS = ROOT / '.setup/linux'

BLENDER = dict(
    version='4.5.0',
    url='https://download.blender.org/release/Blender4.5/blender-4.5.0-linux-x64.tar.xz',
    sums='https://download.blender.org/release/Blender4.5/blender-4.5.0.sha256',
    bytes=376170648,
    sha256='1188b95cc12321c770b631939f7c25a096910b6f884a990bf9c0f62d52b38aec',
)
OPENCC = 'opencc_python_reimplemented-0.1.7-py2.py3-none-any.whl'
MOTION_CORRECTION = ROOT / 'third_party/KimodoMotionCorrection'


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''): h.update(b)
    return h.hexdigest()


def run(cmd, **kw):
    print('+', ' '.join(map(str, cmd)), flush=True)
    subprocess.run(list(map(str, cmd)), check=True, **kw)


def receipt(name):
    try: return json.loads((RECEIPTS / (name + '.json')).read_text(encoding='utf8'))
    except (OSError, ValueError): return None


def write_receipt(name, value):
    RECEIPTS.mkdir(parents=True, exist_ok=True)
    tmp = RECEIPTS / (name + '.json.part'); tmp.write_text(json.dumps(value, indent=2), encoding='utf8'); tmp.replace(RECEIPTS / (name + '.json'))


def skip(step, why): print(f'SKIP {step}: {why}', flush=True)


# --------------------------------------------------------------------------- prereqs
def prereqs(args):
    missing = [t for t in ('ffmpeg', 'ffprobe', 'cmake', 'g++') if not shutil.which(t)]
    glu = ctypes.util.find_library('GLU')
    if not glu:
        try: glu = 'libGLU.so.1' if 'libGLU.so.1' in subprocess.run(['ldconfig', '-p'], capture_output=True, text=True).stdout else None
        except OSError: glu = None
    if not glu: missing.append('libGLU.so.1')
    if missing:
        print('Missing prerequisites: ' + ', '.join(missing), file=sys.stderr)
        print('Install them with:  sudo apt install ffmpeg cmake g++ libglu1-mesa', file=sys.stderr)
        raise SystemExit(2)
    print('OK prereqs: ffmpeg, ffprobe, cmake, g++, ' + glu, flush=True)


# --------------------------------------------------------------------------- venvs
def find_uv(explicit=None):
    if explicit: return Path(explicit)
    uv = ROOT / '.bootstrap/bin/uv'
    if not uv.is_file():
        run([sys.executable, '-m', 'venv', ROOT / '.bootstrap'])
        run([ROOT / '.bootstrap/bin/python', '-m', 'pip', 'install', '--disable-pip-version-check', UV_PIN])
    return uv


def repack(source, wheel):
    """Deterministic wheel repack, byte-for-byte the scheme of runtime_support.prepared_lock()
    and build_character_runtime.stage(): sorted entries, 1980 timestamps, 0644, deflate 9."""
    wheel = Path(wheel); wheel.parent.mkdir(parents=True, exist_ok=True)
    tmp = wheel.with_name(wheel.name + '.part')
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(Path(source).rglob('*')):
            if not p.is_file() or p.name == 'SOURCE_WHEEL.json': continue
            info = zipfile.ZipInfo(p.relative_to(source).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED; info.external_attr = 0o644 << 16
            z.writestr(info, p.read_bytes())
    tmp.replace(wheel)
    return wheel


def fetch(p, cache):
    target = cache / p['filename']
    if not target.is_file() or sha(target) != p['sha256']:
        cache.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix('.part')
        urllib.request.urlretrieve(p['url'], tmp)
        tmp.replace(target)
    if sha(target) != p['sha256']: raise RuntimeError('Hash mismatch: ' + p['filename'])
    return target


def venvs(args):
    lock = json.loads((ROOT / 'installer/runtime-lock.json').read_text(encoding='utf8'))
    keys = list(dict.fromkeys((['shared'] if any(k in args.profiles for k in ('flow', 'hand', 'htd')) else []) + args.profiles))
    work = ROOT / 'build/dependencies'; cache = ROOT / '.cache/wheels'
    build_reqs = sha(ROOT / 'requirements-build.txt')
    uv = None; skipped = []
    for g in [g for g in lock['groups'] if g['key'] in keys]:
        target = ROOT / FOLDERS[g['key']]
        py = target / 'bin/python'
        identity = dict(group=hashlib.sha256(json.dumps(g, sort_keys=True).encode()).hexdigest(),
                        build=build_reqs if g['key'] == 'shared' else None)
        if py.is_file() and receipt('venv-' + g['key']) == identity:
            skip('venvs', g['key'] + ' (' + FOLDERS[g['key']] + ') matches its receipt'); continue
        uv = uv or find_uv(args.uv)
        if not py.is_file(): run([uv, 'venv', target, '--python', g['python'], '--seed'])
        files, specs, pins = [], [], []
        for p in g['packages']:
            name, ver = p['name'], p['version']
            if name.lower() in WINDOWS_ONLY: skipped.append((g['key'], name, ver)); continue
            pins.append(f'{name}=={ver}')
            if p.get('bundled'):
                name_ = Path(p['bundled']).name
                files.append(repack(ROOT / 'installer/runtime_sources' / Path(name_).stem, work / name_))
            elif p['filename'].endswith('-none-any.whl'): files.append(fetch(p, cache))
            else: specs.append(f'{name}=={ver}')
        constraints = work / f'constraints-{g["key"]}.txt'
        constraints.parent.mkdir(parents=True, exist_ok=True)
        constraints.write_text('\n'.join(pins) + '\n')
        common = ['--python', py, '--no-deps', '--extra-index-url', TORCH_INDEX, '--index-strategy', 'unsafe-best-match']
        if files: run([uv, 'pip', 'install', *common, *files])
        if specs: run([uv, 'pip', 'install', *common, '--only-binary', ':all:', *specs])
        torch_pins = [s for s in specs if s.split('==')[0] in ('torch', 'torchvision')]
        if torch_pins:
            # Linux torch wheels split CUDA libraries into nvidia-*/triton packages.
            run([uv, 'pip', 'install', '--python', py, '--extra-index-url', TORCH_INDEX,
                 '--index-strategy', 'unsafe-best-match', '-c', constraints, *torch_pins])
        if g.get('base'):
            base_site = next((ROOT / FOLDERS[g['base']] / 'lib').glob('python3.*/site-packages'))
            own_site = next((target / 'lib').glob('python3.*/site-packages'))
            (own_site / 'shared_runtime.pth').write_text('import sys; sys.path.append(' + ascii(str(base_site)) + ')\n', encoding='ascii')
        if g['key'] == 'shared':
            reqs = [l for l in (ROOT / 'requirements-build.txt').read_text().splitlines()
                    if l and not l.startswith('#') and not l.lower().startswith('pyinstaller')]
            run([uv, 'pip', 'install', '--python', py, '--no-deps', '--only-binary', ':all:', *reqs])
        verify_venv(g['key'])
        write_receipt('venv-' + g['key'], identity)
        print('READY', g['key'], flush=True)
    for s in skipped: print('SKIPPED (Windows-only):', *s)


def verify_venv(key):
    """posix_compat.activate: interpreter, Python version, pinned versions and import paths."""
    py = ROOT / FOLDERS[key] / 'bin/python'
    code = 'import sys;sys.path.insert(0,sys.argv[1]);from mocap.posix_compat import activate;print("VERIFIED",activate(sys.argv[1],sys.argv[2])["packages"])'
    run([py, '-I', '-c', code, ROOT, key])


# --------------------------------------------------------------------------- native
def native_identity():
    files = [MOTION_CORRECTION / 'setup.py', MOTION_CORRECTION / 'CMakeLists.txt', *sorted((MOTION_CORRECTION / 'src').rglob('*'))]
    h = hashlib.sha256()
    for p in files:
        if p.is_file(): h.update(p.relative_to(MOTION_CORRECTION).as_posix().encode() + b'\0' + p.read_bytes() + b'\0')
    return dict(sources=h.hexdigest(), python=str(ROOT / '.venv/bin/python'))


def verify_native():
    code = 'import sys;sys.path.insert(0,sys.argv[1]);from mocap.correction import backend;m=backend();print("VERIFIED",m.__file__)'
    run([ROOT / '.venv/bin/python', '-I', '-c', code, ROOT])


def native(args):
    py = ROOT / '.venv/bin/python'
    if not py.is_file(): raise SystemExit('native: the shared .venv is required (run the venvs step with --profiles shared).')
    out = MOTION_CORRECTION / 'bin'
    built = sorted(out.glob('_motion_correction*.so'))
    identity = native_identity()
    if built and receipt('native') == dict(identity, so=sha(built[0])):
        skip('native', built[0].name + ' matches its sources'); verify_native(); return
    work = ROOT / '.setup/motion-correction-build'
    shutil.rmtree(work, ignore_errors=True)
    log = work / 'build.log'; work.mkdir(parents=True)
    cmd = [py, 'setup.py', 'build_ext', '--build-temp', work / 'temp', '--build-lib', work / 'lib']
    print('+', ' '.join(map(str, cmd)), '>', log, flush=True)
    with open(log, 'w') as stream:
        code = subprocess.run(list(map(str, cmd)), cwd=MOTION_CORRECTION, stdout=stream, stderr=subprocess.STDOUT).returncode
    if code:
        print(log.read_text(errors='replace')[-6000:], file=sys.stderr)
        raise RuntimeError(f'Motion Correction build failed ({code}); full log: {log}')
    found = sorted((work / 'lib').rglob('_motion_correction*.so'))
    if len(found) != 1: raise RuntimeError('Expected one _motion_correction*.so in ' + str(work / 'lib') + ', found ' + str(found))
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob('_motion_correction*.so'): old.unlink()
    target = out / found[0].name; shutil.copy2(found[0], target)
    verify_native()
    write_receipt('native', dict(identity, so=sha(target)))
    print('READY native', target.relative_to(ROOT), flush=True)


# --------------------------------------------------------------------------- blender
def blender_version(executable):
    try:
        r = subprocess.run([str(executable), '--factory-startup', '--version'], capture_output=True, text=True, timeout=120)
        return r.stdout.splitlines()[0].strip() if r.returncode == 0 and r.stdout else None
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return None


def blender(args):
    target = ROOT / 'runtime_character'; executable = target / 'blender'
    expected = 'Blender ' + BLENDER['version']
    if blender_version(executable) != expected:
        archive = download_blender()
        staging = ROOT / 'runtime_character.partial'
        shutil.rmtree(staging, ignore_errors=True); staging.mkdir()
        print('Extracting', archive.name, '->', target.name, flush=True)
        with tarfile.open(archive, 'r:xz') as tar:
            members = []
            for m in tar.getmembers():
                parts = Path(m.name).parts
                if len(parts) < 2: continue  # the top-level blender-4.5.0-linux-x64/ folder
                m.name = str(Path(*parts[1:]))
                if m.islnk(): m.linkname = str(Path(*Path(m.linkname).parts[1:]))  # hard links name archive paths
                members.append(m)
            tar.extractall(staging, members=members, filter='tar')
        if target.exists(): shutil.rmtree(target)
        staging.replace(target)
    else:
        skip('blender', expected + ' already in runtime_character/')
    version = blender_version(executable)
    if version != expected: raise RuntimeError(f'runtime_character/blender reports {version!r}, expected {expected!r}')
    if not (target / '4.5/python').is_dir() or not (target / 'license').is_dir():
        raise RuntimeError('Incomplete Blender distribution (4.5/python or license/ missing)')
    # Same record as tools/build_character_runtime.py:stage(), plus the archive identity.
    record = dict(family='Blender 4.5 LTS', executable_sha256=sha(executable), source='https://www.blender.org/download/lts/4-5/',
                  licenses='license/', archive=BLENDER['url'], archive_sha256=BLENDER['sha256'])
    (target / 'RGBMOCAP_RUNTIME.json').write_text(json.dumps(record, indent=2), encoding='utf8')
    print('VERIFIED', version, flush=True)


def download_blender():
    cache = ROOT / '.cache/downloads'; cache.mkdir(parents=True, exist_ok=True)
    archive = cache / BLENDER['url'].rsplit('/', 1)[1]
    # Two independent checks: the hash pinned in this script, and blender.org's published list.
    with urllib.request.urlopen(BLENDER['sums'], timeout=60) as r: sums = r.read().decode()
    published = {line.split()[1]: line.split()[0] for line in sums.splitlines() if len(line.split()) == 2}
    if published.get(archive.name) != BLENDER['sha256']:
        raise RuntimeError(f'blender.org lists {published.get(archive.name)} for {archive.name}; pinned {BLENDER["sha256"]}')
    if not (archive.is_file() and archive.stat().st_size == BLENDER['bytes'] and sha(archive) == BLENDER['sha256']):
        tmp = archive.with_name(archive.name + '.part')
        print('Downloading', BLENDER['url'], flush=True)
        with urllib.request.urlopen(BLENDER['url'], timeout=60) as r, open(tmp, 'wb') as f: shutil.copyfileobj(r, f, 1 << 20)
        tmp.replace(archive)
    else:
        skip('blender download', archive.name + ' already verified in .cache/downloads/')
    size, digest = archive.stat().st_size, sha(archive)
    if size != BLENDER['bytes'] or digest != BLENDER['sha256']:
        raise RuntimeError(f'{archive.name}: {size} bytes, sha256 {digest}; expected {BLENDER["bytes"]} / {BLENDER["sha256"]}')
    print('VERIFIED', archive.name, digest, flush=True)
    return archive


# --------------------------------------------------------------------------- opencc / assets
def opencc(args):
    source = ROOT / 'installer/runtime_sources' / Path(OPENCC).stem
    if not source.is_dir(): raise FileNotFoundError('Missing OpenCC source archive contents: ' + str(source))
    wheel = ROOT / 'third_party/character_import/mmd_tools/wheels' / OPENCC
    fresh = repack(source, ROOT / 'build/opencc' / OPENCC)
    if wheel.is_file() and sha(wheel) == sha(fresh): skip('opencc', 'wheel already staged')
    else: wheel.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(fresh, wheel)
    with zipfile.ZipFile(wheel) as z:
        bad = z.testzip(); names = z.namelist()
    if bad or not any(n.startswith('opencc/') for n in names): raise RuntimeError('Bad OpenCC wheel ' + str(wheel))
    print('VERIFIED', wheel.relative_to(ROOT), sha(wheel), flush=True)


def assets(args):
    run([sys.executable, ROOT / 'tools/stage_assets.py', '--download'], cwd=ROOT)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--profiles', nargs='+', default=['shared', 'exmr'], choices=list(FOLDERS))
    ap.add_argument('--steps', default=','.join(STEPS), help='comma-separated subset of: ' + ','.join(STEPS))
    ap.add_argument('--uv', type=Path, help='use this uv instead of .bootstrap/bin/uv')
    args = ap.parse_args()
    if sys.platform != 'linux': raise SystemExit('Use install.cmd / tools/setup_dev.py on Windows.')
    steps = [s.strip() for s in args.steps.split(',') if s.strip()]
    unknown = [s for s in steps if s not in STEPS]
    if unknown: ap.error('unknown steps: ' + ', '.join(unknown))
    for step in STEPS:  # fixed order, whatever order was given
        if step in steps:
            print(f'== {step}', flush=True)
            globals()[step](args)
    print('DONE', ','.join(s for s in STEPS if s in steps), flush=True)


if __name__ == '__main__':
    main()
