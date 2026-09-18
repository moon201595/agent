"""⑦ 정리는 실행 판정·증거 저장 이후이며 다른 작업의 자원을 지우지 않는다."""
import json
import subprocess

import pytest

import docker_runner as dr


@pytest.mark.parametrize('mode', ['success', 'build_fail', 'build_timeout', 'run_exception'])
def test_owned_builder_is_removed_on_every_exit(monkeypatch, tmp_path, mode):
    """전용 빌더 삭제를 빼거나 전역 prune으로 바꾸면 실패한다."""
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'probe.py').write_text('x=1')
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        if cmd[:3] == ['docker', 'buildx', 'build']:
            if mode == 'build_timeout':
                raise subprocess.TimeoutExpired(cmd, 900)
            return subprocess.CompletedProcess(cmd, int(mode == 'build_fail'), '', '')
        return subprocess.CompletedProcess(cmd, 0, '', '')

    def execute(*a, **kw):
        if mode == 'run_exception':
            raise ValueError('bad docker wait result')
        return dr.RunResult(True, 0, 'proof', '', False, False, 1.0)

    monkeypatch.setattr(dr.subprocess, 'run', run)
    monkeypatch.setattr(dr, '_run_container', execute)
    if mode == 'run_exception':
        with pytest.raises(ValueError):
            dr.run_repo_in_docker(repo)
    else:
        out = dr.run_repo_in_docker(repo)
        assert out['success'] is (mode == 'success')
        assert out['cleanup_errors'] == []
    create = next(cmd for cmd, _ in calls if cmd[:3] == ['docker', 'buildx', 'create'])
    name = create[create.index('--name') + 1]
    build, args = next((cmd, kw) for cmd, kw in calls if cmd[:3] == ['docker', 'buildx', 'build'])
    assert build[build.index('--builder') + 1] == name
    assert '--load' in build and args['cwd'] == repo
    assert ['docker', 'image', 'rm', name] in [cmd for cmd, _ in calls]
    assert ['docker', 'buildx', 'rm', '--force', '--timeout', '120s', name] in [cmd for cmd, _ in calls]
    assert not any('prune' in cmd or '--keep-state' in cmd for cmd, _ in calls)
    assert not list(tmp_path.glob('Dockerfile.*'))


def test_cleanup_failure_is_reported_without_faking_run_failure(monkeypatch, tmp_path):
    """빌더 제거 실패를 무시하거나 재현 성공을 실패로 덮으면 실패한다."""
    monkeypatch.setattr(dr, '_run_repo_in_docker', lambda *a: {'success': True})
    def run(cmd, **kw):
        fail = cmd[:3] == ['docker', 'buildx', 'rm']
        return subprocess.CompletedProcess(cmd, int(fail), '', 'busy' if fail else '')
    monkeypatch.setattr(dr.subprocess, 'run', run)
    with pytest.warns(RuntimeWarning, match='busy'):
        out = dr.run_repo_in_docker(tmp_path)
    assert out['success'] and len(out['cleanup_errors']) == 1


@pytest.mark.parametrize('db_fails', [False, True])
@pytest.mark.parametrize('success', [False, True])
def test_clone_removed_only_after_evidence_and_db_save(monkeypatch, tmp_path, db_fails, success):
    """저장 전 삭제·성공 clone 영구 보관·실패를 성공으로 기록하는 변경을 잡는다."""
    monkeypatch.setattr(dr.server, 'REPRO_DIR', tmp_path)
    candidate = {'url': 'https://github.com/a/b', 'source': 'paper', 'confidence': 'high'}
    monkeypatch.setattr(dr.code_finder, 'find_repo_candidates', lambda aid: {})
    monkeypatch.setattr(dr, '_rank_candidates', lambda found: [candidate])
    repo = tmp_path / '1234.56789' / 'clone'
    repo.mkdir(parents=True)
    (repo / 'dataset.bin').write_bytes(b'generated data')
    outside = tmp_path / 'shared-dataset.bin'
    outside.write_bytes(b'original')
    (repo / 'linked-data').symlink_to(outside)
    monkeypatch.setattr(dr, '_clone', lambda *a: (repo, ''))
    outcome = {'success': success, 'stage': 'run', 'attempts': [
        {'exit_code': 0 if success else 1, 'stdout': 'evidence', 'network_enabled': False}]}
    monkeypatch.setattr(dr, 'run_repo_in_docker', lambda *a: outcome)
    saved = []
    def save(*a, **kw):
        assert repo.exists()
        payload = json.loads(dr.Path(a[10]).read_text())
        assert payload['attempts'][0]['stdout'] == 'evidence'
        assert a[4] is success and a[11] == ''
        if db_fails:
            raise RuntimeError('database unavailable')
        saved.append(a)
    monkeypatch.setattr(dr.server, 'save_repro_result', save)
    if db_fails:
        with pytest.raises(RuntimeError):
            dr.reproduce('1234.56789')
        assert repo.exists()
    else:
        out = dr.reproduce('1234.56789')
        assert out['success'] is success and saved
        assert not repo.exists()
    assert outside.read_bytes() == b'original'


def test_container_removed_when_wait_output_is_invalid(monkeypatch):
    """docker wait 해석 예외가 컨테이너를 남기지 않아야 한다."""
    calls = []
    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, 'invalid', '')
    monkeypatch.setattr(dr.subprocess, 'run', run)
    with pytest.raises(ValueError):
        dr._run_container('image', 'true', 1, False)
    name = calls[0][calls[0].index('--name') + 1]
    assert calls[-1] == ['docker', 'rm', '-f', name]


def test_partial_clone_removed_on_timeout(monkeypatch, tmp_path):
    """clone 실패 시 다운로드 도중 남은 데이터도 이번 경로에서만 제거한다."""
    monkeypatch.setattr(dr, '_github_repo_size_kb', lambda _: None)
    monkeypatch.setattr(dr, '_git_supports_blob_filter', lambda: False)
    def run(cmd, **kw):
        dest = dr.Path(cmd[-1])
        dest.mkdir()
        (dest / 'partial').write_bytes(b'data')
        raise subprocess.TimeoutExpired(cmd, 120)
    monkeypatch.setattr(dr.subprocess, 'run', run)
    assert dr._clone('https://github.com/a/b', tmp_path) == (None, 'clone_timeout')
    assert list(tmp_path.iterdir()) == []
