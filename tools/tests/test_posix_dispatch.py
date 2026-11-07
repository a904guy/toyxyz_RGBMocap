"""Worker-command dispatch: Windows keeps the hash-pinned registry, POSIX uses posix_compat.

Run: .venv/bin/python -m unittest discover -s tools/tests
"""
from pathlib import Path
import json, os, subprocess, sys, tempfile, unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from mocap import paths  # noqa: E402

VENVS = {'shared': '.venv', 'exmr': '.venv-exmr', 'flow': '.venv-flow', 'hand': '.venv-hand', 'htd': '.venv-htd'}


def fake_root(tmp):
    """A checkout with both Windows and POSIX dev-venv interpreters present (as empty files)."""
    root = Path(tmp)
    for folder in set(VENVS.values()):
        for rel in ('Scripts/python.exe', 'bin/python'):
            (root / folder / rel).parent.mkdir(parents=True, exist_ok=True); (root / folder / rel).touch()
    return root


class FakeProcess:
    started = []
    MergedChannels = 1; FailedToStart = 0
    def __init__(self, *a): pass
    def __getattr__(self, name): return mock.MagicMock()
    def start(self, program, args): FakeProcess.started.append([program, *args])


def character_command(root, platform, job):
    """Run the real CharacterWorkspace.start_character_job on a stub and return the launched command."""
    from mocap import character_ui
    stub = mock.MagicMock(); stub.character_root = root
    FakeProcess.started = []
    with mock.patch.object(sys, 'platform', platform), mock.patch.object(character_ui, 'ROOT', root), \
         mock.patch.object(character_ui, 'QProcess', FakeProcess), mock.patch('mocap.retarget.runtime.blender', lambda: 'blender'):
        character_ui.CharacterWorkspace.start_character_job(stub, job)
    stub.status.setText.assert_not_called()  # no error path taken
    return FakeProcess.started[-1]


class DispatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = fake_root(self.tmp.name)
        (self.root / 'characters').mkdir()
        take = self.root / 'take'; take.mkdir()
        (take / 'project.json').write_text(json.dumps(dict(pipeline=dict(model='exmr'))), encoding='utf-8')
        self.take = take
        env = mock.patch.dict(os.environ); env.start(); self.addCleanup(env.stop)
        os.environ.pop('RGB_MOCAP_MANAGED', None)

    def tearDown(self): self.tmp.cleanup()

    def worker(self, platform, stage, *args):
        with mock.patch.object(sys, 'platform', platform), mock.patch.object(paths, 'ROOT', self.root):
            return paths.worker_command(stage, *args)

    def test_worker_command_windows_uses_registry(self):
        cmd = self.worker('win32', 'track', '--project', str(self.take))
        self.assertEqual(cmd[0], str(self.root / '.venv/Scripts/python.exe'))
        self.assertEqual(cmd[1:6], ['-X', 'utf8', '-m', 'mocap.worker_progress', 'track'])
        self.assertEqual(self.worker('win32', 'exmr', 'x')[0], str(self.root / '.venv-exmr/Scripts/python.exe'))

    def test_worker_command_linux_uses_posix_venv(self):
        cmd = self.worker('linux', 'track', '--project', str(self.take))
        self.assertEqual(cmd[0], str(self.root / '.venv/bin/python'))
        self.assertEqual(cmd[1:6], ['-X', 'utf8', '-m', 'mocap.worker_progress', 'track'])
        for stage, folder in [('exmr', '.venv-exmr'), ('flow_body', '.venv-flow'), ('handflow', '.venv-hand'), ('refine', '.venv-htd')]:
            self.assertEqual(self.worker('linux', stage)[0], str(self.root / folder / 'bin/python'), stage)

    def test_windows_and_linux_commands_differ_only_in_interpreter(self):
        win = self.worker('win32', 'people', '--project', str(self.take))
        lin = self.worker('linux', 'people', '--project', str(self.take))
        self.assertEqual(win[1:], lin[1:])

    def test_posix_managed_mode_is_rejected(self):
        os.environ['RGB_MOCAP_MANAGED'] = '1'
        with self.assertRaises(RuntimeError): self.worker('linux', 'track')

    def test_character_command(self):
        for job in (dict(mode='import'), dict(mode='retarget', takes=[dict(project=str(self.take))])):
            win = character_command(self.root, 'win32', job)
            lin = character_command(self.root, 'linux', job)
            self.assertEqual(win[0], str(self.root / '.venv/Scripts/python.exe'), job['mode'])
            self.assertEqual(lin[0], str(self.root / '.venv/bin/python'), job['mode'])
            self.assertEqual(win[1:5], ['-X', 'utf8', '-m', 'mocap.worker_progress'])
            self.assertEqual(lin[5], 'character')
            self.assertEqual('--project' in lin, job['mode'] == 'retarget')


class RealVenvActivateTest(unittest.TestCase):
    """posix_compat.activate validates each real development venv from inside it."""

    @unittest.skipIf(sys.platform == 'win32', 'POSIX venv layout')
    def test_activate_each_profile(self):
        code = ('import json,sys;sys.path.insert(0,sys.argv[1]);from mocap.posix_compat import activate;'
                'print(json.dumps(activate(sys.argv[1],sys.argv[2])))')
        for key, folder in VENVS.items():
            python = ROOT / folder / 'bin/python'
            if not python.is_file(): self.skipTest(f'{folder} not built'); continue
            with self.subTest(profile=key):
                out = subprocess.run([str(python), '-I', '-c', code, str(ROOT), key], capture_output=True, text=True, timeout=300)
                self.assertEqual(out.returncode, 0, out.stderr[-2000:])
                info = json.loads(out.stdout.strip().splitlines()[-1])
                self.assertEqual(info['profile'], key)
                self.assertEqual(info['python'], str(python))
                self.assertEqual(set(info['imports']), set(__import__('mocap.runtime_registry', fromlist=['PROFILES']).PROFILES[key]['packages']))

    @unittest.skipIf(sys.platform == 'win32', 'POSIX venv layout')
    def test_activate_rejects_wrong_interpreter(self):
        from mocap.posix_compat import activate
        with self.assertRaisesRegex(RuntimeError, 'Wrong interpreter'): activate(ROOT, 'exmr')


if __name__ == '__main__':
    unittest.main()
