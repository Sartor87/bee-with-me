"""B22: the `[sh]` script tests must run Git Bash (or BWM_TEST_BASH), never the WSL launcher.

From PowerShell/cmd, `shutil.which('bash')` returns C:\\Windows\\system32\\bash.EXE (WSL), which can
not see C:/ paths and re-quotes `-c` arguments. `find_bash()` skips System32/WindowsApps and derives
Git Bash from git.exe instead.
"""

import shutil
from pathlib import Path

import pytest

from backend.tests import shells
from backend.tests.shells import find_bash

WSL_BASH = r'C:\Windows\system32\bash.EXE'
STORE_BASH = r'C:\Users\x\AppData\Local\Microsoft\WindowsApps\bash.exe'


def _fake_git_install(root: Path, *, bash_rel='bin/bash.exe') -> Path:
    git = root / 'Git' / 'cmd' / 'git.exe'
    git.parent.mkdir(parents=True)
    git.write_text('')
    bash = root / 'Git' / Path(bash_rel)
    bash.parent.mkdir(parents=True, exist_ok=True)
    bash.write_text('')
    return git


def _which(mapping):
    return lambda name, *a, **k: mapping.get(name)


@pytest.fixture()
def clean_env(monkeypatch, tmp_path):
    monkeypatch.delenv('BWM_TEST_BASH', raising=False)
    monkeypatch.setenv('ProgramFiles', str(tmp_path / 'no-program-files'))


@pytest.mark.Trait("Bug", "B22")
@pytest.mark.parametrize('bad', [WSL_BASH, STORE_BASH])
def test_never_returns_wsl_or_store_bash_and_derives_git_bash(monkeypatch, tmp_path, clean_env, bad):
    git = _fake_git_install(tmp_path)
    monkeypatch.setattr(shutil, 'which', _which({'bash': bad, 'git': str(git)}))
    got = find_bash(windows=True)
    assert got is not None
    assert 'system32' not in got.lower() and 'windowsapps' not in got.lower()
    assert Path(got) == tmp_path / 'Git' / 'bin' / 'bash.exe'


@pytest.mark.Trait("Bug", "B22")
def test_falls_back_to_usr_bin_bash_under_git_root(monkeypatch, tmp_path, clean_env):
    git = _fake_git_install(tmp_path, bash_rel='usr/bin/bash.exe')
    monkeypatch.setattr(shutil, 'which', _which({'bash': WSL_BASH, 'git': str(git)}))
    assert Path(find_bash(windows=True)) == tmp_path / 'Git' / 'usr' / 'bin' / 'bash.exe'


@pytest.mark.Trait("Bug", "B22")
def test_falls_back_to_program_files_git(monkeypatch, tmp_path):
    monkeypatch.delenv('BWM_TEST_BASH', raising=False)
    bash = tmp_path / 'Git' / 'bin' / 'bash.exe'
    bash.parent.mkdir(parents=True)
    bash.write_text('')
    monkeypatch.setenv('ProgramFiles', str(tmp_path))
    monkeypatch.setattr(shutil, 'which', _which({'bash': WSL_BASH}))
    assert Path(find_bash(windows=True)) == bash


@pytest.mark.Trait("Bug", "B22")
def test_returns_none_when_only_wsl_bash_exists(monkeypatch, clean_env):
    monkeypatch.setattr(shutil, 'which', _which({'bash': WSL_BASH}))
    assert find_bash(windows=True) is None


@pytest.mark.Trait("Bug", "B22")
def test_uses_path_bash_when_it_is_not_wsl(monkeypatch, clean_env):
    good = r'C:\Program Files\Git\usr\bin\bash.exe'
    monkeypatch.setattr(shutil, 'which', _which({'bash': good, 'git': None}))
    assert find_bash(windows=True) == good


@pytest.mark.Trait("Bug", "B22")
def test_env_override_wins(monkeypatch, tmp_path):
    git = _fake_git_install(tmp_path)
    monkeypatch.setattr(shutil, 'which', _which({'bash': WSL_BASH, 'git': str(git)}))
    monkeypatch.setenv('BWM_TEST_BASH', r'D:\tools\bash.exe')
    assert find_bash(windows=True) == r'D:\tools\bash.exe'
    assert find_bash(windows=False) == r'D:\tools\bash.exe'


@pytest.mark.Trait("Bug", "B22")
def test_posix_uses_which_bash(monkeypatch, clean_env):
    monkeypatch.setattr(shutil, 'which', _which({'bash': '/usr/bin/bash'}))
    assert find_bash(windows=False) == '/usr/bin/bash'


@pytest.mark.Trait("Bug", "B22")
def test_script_tests_do_not_call_which_bash_directly():
    here = Path(__file__).resolve().parent
    offenders = [p.name for p in here.glob('test_*.py')
                 if p.name != Path(__file__).name and "which('bash')" in p.read_text(encoding='utf-8')]
    assert offenders == []


@pytest.mark.Trait("Bug", "B22")
def test_module_bash_is_never_wsl():
    if shells.BASH is not None:
        assert 'system32' not in shells.BASH.lower() and 'windowsapps' not in shells.BASH.lower()
