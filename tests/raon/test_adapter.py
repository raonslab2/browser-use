"""Contract and real-browser lifecycle tests for the thin RAON boundary."""

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest
from pydantic import ValidationError

from browser_use.raon.contracts import RunInput, RunResult, safe_url, sanitize, workspace_output
from browser_use.raon.runner import cleanup_worker, run
from browser_use.raon.worker import output_model

WORKSPACE = Path(__file__).resolve().parents[2]


def config(**kwargs) -> RunInput:
	"""Create a valid bounded fixture run without real model inference."""
	return RunInput(task='fixture', allowed_domains=['127.0.0.1'], mode='fixture', **kwargs)


@pytest.mark.parametrize(
	'field,value',
	[
		('output', '/tmp/out'),
		('output', '../escape'),
		('run_id', '../duplicate'),
		('max_steps', 0),
		('timeout', 601),
		('allowed_domains', ['*']),
		('start_url', 'https://user:password@example.com'),
		('start_url', 'file:///etc/passwd'),
	],
)
def test_input_contract(field, value):
	payload = config().model_dump()
	payload[field] = value
	with pytest.raises(ValidationError):
		RunInput.model_validate(payload)


def test_symlink_escape(tmp_path):
	root = tmp_path / 'project'
	root.mkdir()
	(root / 'escape').symlink_to(tmp_path, target_is_directory=True)
	with pytest.raises(ValueError):
		workspace_output(root, 'escape/runs')
	with pytest.raises(ValueError):
		workspace_output(root, '.')


def test_secret_controls(monkeypatch):
	monkeypatch.setenv('OPENAI_API_KEY', 'sentinel-credential-value')
	value = sanitize(
		{
			'authorization': 'hidden',
			'answer': 'sentinel-credential-value Bearer abc token=xyz',
			'url': 'https://user:pass@example.com/private?token=abc#secret',
			'nested': [{'cookie': 'session-value'}],
		}
	)
	text = json.dumps(value)
	for forbidden in ('sentinel-credential-value', 'hidden', 'abc', 'xyz', 'session-value', 'user:pass', '/private'):
		assert forbidden not in text
	assert safe_url('https://example.com/private?token=abc') == 'https://example.com'


def test_structured_schema():
	model = output_model(config(output_schema={'title': 'str', 'items': 'str_list'}))
	assert model is not None
	assert model.model_validate({'title': 'Fixture', 'items': ['one']}).model_dump()['items'] == ['one']
	with pytest.raises(ValidationError):
		model.model_validate({'title': 'Fixture', 'items': 123})


def test_process_group_cleanup():
	# Exercise the hard fallback against a child which ignores graceful termination.
	process = subprocess.Popen(
		[
			sys.executable,
			'-c',
			'import signal,time,subprocess,sys; signal.signal(signal.SIGTERM,signal.SIG_IGN); '
			'subprocess.Popen([sys.executable,"-c","import time; time.sleep(100)"]); time.sleep(100)',
		],
		start_new_session=True,
	)
	time.sleep(0.2)
	assert cleanup_worker(process)
	assert process.poll() is not None


def test_duplicate_reservation(monkeypatch, tmp_path):
	cfg = config(run_id='reserved')
	(tmp_path / cfg.output / cfg.run_id).mkdir(parents=True)
	with pytest.raises(FileExistsError):
		run(cfg, tmp_path)


def test_missing_model_credential(monkeypatch):
	for name in ('BROWSER_USE_API_KEY', 'OPENAI_API_KEY', 'GOOGLE_API_KEY', 'ANTHROPIC_API_KEY'):
		monkeypatch.delenv(name, raising=False)
	cfg = RunInput(task='Read only', allowed_domains=['example.com'])
	result = run(cfg, WORKSPACE)
	assert result.status == 'REAL_BROWSER_AGENT_LLM_BLOCKED'
	assert result.failure_stage == 'model_configuration'
	assert result.browser_identity is None
	assert not (WORKSPACE / cfg.output / cfg.run_id / '.private').exists()


def test_screenshot_acknowledgement():
	cfg = config(capture_screenshot=True)
	result = run(cfg, WORKSPACE)
	assert result.status == 'FAILED'
	assert result.failure_stage == 'configuration'
	assert not (WORKSPACE / cfg.output / cfg.run_id / 'last.png').exists()


@pytest.mark.integration
def test_native_fixture_browser():
	if not os.environ.get('RAON_BROWSER_EXECUTABLE'):
		pytest.skip('set RAON_BROWSER_EXECUTABLE to a project-approved existing Chromium')
	cfg = config(capture_screenshot=True, controlled_page=True)
	result = run(cfg, WORKSPACE)
	assert result.status == 'COMPLETED', result.model_dump()
	assert result.model_execution == 'NONE_DETERMINISTIC'
	assert result.structured_result == {
		'title': 'RAON fixture',
		'items': ['Alpha', 'Bravo', 'Charlie'],
		'result': 'Action complete',
	}
	assert result.actions == ['navigate', 'click', 'extract_fixture']
	assert result.browser_identity and result.browser_identity.startswith('Chrome/')
	assert result.cleanup == 'PASS'
	root = WORKSPACE / cfg.output / cfg.run_id
	RunResult.model_validate_json((root / 'metadata.json').read_text())
	assert len(result.evidence) == 3
	for artifact in result.evidence:
		data = (root / artifact.path).read_bytes()
		assert len(data) == artifact.bytes
		assert hashlib.sha256(data).hexdigest() == artifact.sha256
	assert not (root / '.private').exists()
	assert_no_run_processes(root)


def assert_no_run_processes(root: Path):
	"""Inspect only process command lines referencing this isolated run directory."""
	for process in psutil.process_iter(['cmdline', 'status']):
		assert not (str(root) in ' '.join(process.info['cmdline'] or []) and process.info['status'] != psutil.STATUS_ZOMBIE)


@pytest.mark.integration
def test_native_cancel_cleanup():
	if not os.environ.get('RAON_BROWSER_EXECUTABLE'):
		pytest.skip('set RAON_BROWSER_EXECUTABLE')
	cfg = config()
	task_file = WORKSPACE / '.raon-runs' / f'{cfg.run_id}-task.json'
	task_file.parent.mkdir(exist_ok=True)
	task_file.write_text(cfg.model_dump_json())
	process = subprocess.Popen(
		[sys.executable, '-m', 'browser_use.raon', 'run', '--task-file', str(task_file)],
		cwd=WORKSPACE,
		stdout=subprocess.PIPE,
		stderr=subprocess.PIPE,
		text=True,
		start_new_session=True,
	)
	root = WORKSPACE / cfg.output / cfg.run_id
	saw_browser = False
	deadline = time.monotonic() + 30
	try:
		while time.monotonic() < deadline and process.poll() is None:
			for child in psutil.Process(process.pid).children(recursive=True):
				try:
					if any(argument.startswith('--user-data-dir=' + str(root / '.private')) for argument in child.cmdline()):
						saw_browser = True
						break
				except psutil.Error:
					pass
			if saw_browser:
				process.send_signal(signal.SIGTERM)
				break
			time.sleep(0.02)
		stdout, stderr = process.communicate(timeout=25)
		assert saw_browser, (stdout, stderr)
		assert json.loads(stdout)['status'] == 'CANCELLED'
		result = RunResult.model_validate_json((root / 'metadata.json').read_text())
		assert result.cleanup == 'PASS'
		assert not (root / '.private').exists()
		assert_no_run_processes(root)
	finally:
		if process.poll() is None:
			os.killpg(process.pid, signal.SIGKILL)
			process.wait()
		task_file.unlink(missing_ok=True)


def test_timeout_does_not_retry():
	cfg = config(timeout=1)
	result = run(cfg, WORKSPACE)
	assert result.status == 'FAILED'
	assert result.error_kind == 'TIMEOUT_ACTION_OUTCOME_UNKNOWN'
	assert result.cleanup == 'PASS'
	root = WORKSPACE / cfg.output / cfg.run_id
	assert not (root / '.private').exists()
	assert_no_run_processes(root)
	with pytest.raises(FileExistsError):
		run(cfg, WORKSPACE)


@pytest.mark.asyncio
async def test_agent_wrapper_with_synthetic_dependencies(monkeypatch, tmp_path):
	"""Synthetic wiring only: no model inference and no REAL_AGENT_E2E claim."""
	from types import SimpleNamespace
	from unittest.mock import AsyncMock

	import browser_use
	from browser_use.agent.views import ActionResult
	from browser_use.raon import worker
	from browser_use.tools.service import Tools

	native_action = Tools().registry.create_action_model().model_validate({'done': {'text': 'ok', 'success': True}})
	synthetic_output = output_model(config(output_schema={'title': 'str'}))
	assert synthetic_output is not None
	history = SimpleNamespace(
		history=[SimpleNamespace(model_output=SimpleNamespace(action=[native_action]), result=[ActionResult(is_done=True)])],
		urls=lambda: ['https://example.com/private?token=secret'],
		final_result=lambda: 'Bearer canary token=hidden',
		structured_output=synthetic_output.model_validate({'title': 'ok'}),
		is_successful=lambda: True,
	)
	model = SimpleNamespace(model='synthetic-test-model')
	observed = {}
	session = SimpleNamespace(
		session_id='fixture',
		cdp_client=SimpleNamespace(
			send=SimpleNamespace(Browser=SimpleNamespace(getVersion=AsyncMock(return_value={'product': 'synthetic-browser'})))
		),
	)
	browser = SimpleNamespace(start=AsyncMock(), get_or_create_cdp_session=AsyncMock(return_value=session), kill=AsyncMock())

	def browser_factory(**kwargs):
		observed['browser'] = kwargs
		return browser

	class SyntheticAgent:
		def __init__(self, **kwargs):
			observed['agent'] = kwargs

		async def run(self, **kwargs):
			observed['run'] = kwargs
			return history

	monkeypatch.setattr(browser_use, 'Agent', SyntheticAgent)
	monkeypatch.setattr(browser_use, 'BrowserSession', browser_factory)
	monkeypatch.setattr(worker, 'create_llm', lambda cfg: model)
	cfg = RunInput(
		task='public read only', allowed_domains=['example.com'], start_url='https://example.com', output_schema={'title': 'str'}
	)
	result = await worker.execute(cfg, tmp_path)
	assert result.status == 'COMPLETED'
	assert result.actions == ['done']
	assert result.structured_result == {'title': 'ok'}
	assert result.final_result is not None
	assert 'canary' not in result.final_result and 'hidden' not in result.final_result
	assert result.visited_urls == ['https://example.com']
	assert observed['agent']['browser_session'] is browser
	assert observed['agent']['llm'] is model
	assert observed['agent']['output_model_schema'] is not None
	assert observed['run'] == {'max_steps': cfg.max_steps}
	browser.kill.assert_awaited_once()


@pytest.mark.integration
def test_native_browser_timeout_cleanup():
	"""Force timeout after Chromium creation, without replaying the ambiguous run."""
	if not os.environ.get('RAON_BROWSER_EXECUTABLE'):
		pytest.skip('set RAON_BROWSER_EXECUTABLE')
	cfg = config(timeout=3)
	task_file = WORKSPACE / '.raon-runs' / f'{cfg.run_id}-task.json'
	task_file.write_text(cfg.model_dump_json())
	process = subprocess.Popen(
		[sys.executable, '-m', 'browser_use.raon', 'run', '--task-file', str(task_file)],
		cwd=WORKSPACE,
		stdout=subprocess.PIPE,
		stderr=subprocess.PIPE,
		text=True,
		start_new_session=True,
	)
	root = WORKSPACE / cfg.output / cfg.run_id
	saw_browser = False
	try:
		while process.poll() is None:
			for child in psutil.Process(process.pid).children(recursive=True):
				try:
					if any(argument.startswith('--user-data-dir=' + str(root / '.private')) for argument in child.cmdline()):
						saw_browser = True
				except psutil.Error:
					pass
			time.sleep(0.02)
		stdout, stderr = process.communicate(timeout=25)
		assert saw_browser, (stdout, stderr)
		result = RunResult.model_validate_json((root / 'metadata.json').read_text())
		assert result.status == 'FAILED'
		assert result.error_kind == 'TIMEOUT_ACTION_OUTCOME_UNKNOWN'
		assert result.cleanup == 'PASS'
		assert not (root / '.private').exists()
		assert_no_run_processes(root)
	finally:
		if process.poll() is None:
			os.killpg(process.pid, signal.SIGKILL)
			process.wait()
		task_file.unlink(missing_ok=True)
