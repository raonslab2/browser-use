"""One run per ID, isolated process lifecycle and minimal durable evidence."""

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import psutil

from browser_use.raon.contracts import EvidenceFile, RunInput, RunResult, workspace_output


def revision(workspace: Path) -> str:
	"""Return the checkout revision without exposing Git configuration."""
	try:
		return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=workspace, stderr=subprocess.DEVNULL, text=True).strip()
	except subprocess.CalledProcessError:
		return 'unknown'


def cleanup_worker(process: subprocess.Popen) -> bool:
	"""Stop only this worker's Linux session, including orphaned Chromium children."""
	try:
		os.killpg(process.pid, signal.SIGTERM)
	except ProcessLookupError:
		pass
	try:
		process.wait(timeout=12)
	except subprocess.TimeoutExpired:
		pass
	# Chromium may remain after worker exit; a new session ID scopes fallback cleanup.
	members = []
	for item in psutil.process_iter(['pid', 'status']):
		try:
			if os.getsid(item.pid) == process.pid and item.status() != psutil.STATUS_ZOMBIE:
				members.append(item)
				item.kill()
		except (ProcessLookupError, psutil.Error):
			pass
	_, alive = psutil.wait_procs(members, timeout=5)
	process.wait(timeout=5)
	return not any(item.is_running() and item.status() != psutil.STATUS_ZOMBIE for item in alive)


def run(config: RunInput, workspace: Path | None = None) -> RunResult:
	"""Run once; never retry ambiguous actions after timeout or interruption."""
	workspace = (workspace or Path.cwd()).resolve()
	output = workspace_output(workspace, config.output)
	output.mkdir(parents=True, exist_ok=True, mode=0o700)
	run_dir = output / config.run_id
	run_dir.mkdir(mode=0o700)  # Atomic reservation; existing run IDs are rejected.
	private = run_dir / '.private'
	private.mkdir(mode=0o700)
	(private / 'tmp').mkdir(mode=0o700)
	input_path = private / 'input.json'
	input_path.write_text(config.model_dump_json())
	input_path.chmod(0o600)
	result = RunResult(
		run_id=config.run_id,
		status='FAILED',
		mode=config.mode,
		model_execution='REAL' if config.mode == 'agent' else 'NONE_DETERMINISTIC',
		source_revision=revision(workspace),
		adapter_revision=revision(workspace),
	)
	env = dict(os.environ)
	env.update(
		ANONYMIZED_TELEMETRY='false',
		BROWSER_USE_CLOUD_SYNC='false',
		BROWSER_USE_SETUP_LOGGING='false',
		BROWSER_USE_LOGGING_LEVEL='critical',
		TMPDIR='/proc/self/cwd/tmp',
		BROWSER_USE_CONFIG_DIR=str(private / 'config'),
	)
	for name in ('BROWSER_USE_DEBUG_LOG_FILE', 'BROWSER_USE_INFO_LOG_FILE'):
		env.pop(name, None)
	process = None
	previous_handlers = {}

	def interrupt(signum, frame):
		raise KeyboardInterrupt

	try:
		for signum in (signal.SIGINT, signal.SIGTERM):
			previous_handlers[signum] = signal.signal(signum, interrupt)
		process = subprocess.Popen(
			[sys.executable, '-m', 'browser_use.raon.worker', str(input_path), str(run_dir)],
			cwd=private,
			env=env,
			start_new_session=True,
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL,
		)
		try:
			process.wait(timeout=config.timeout)
		except subprocess.TimeoutExpired:
			result.error_kind, result.failure_stage = 'TIMEOUT_ACTION_OUTCOME_UNKNOWN', 'execution'
		else:
			worker_result = run_dir / '.worker-result.json'
			if worker_result.is_file():
				result = RunResult.model_validate_json(worker_result.read_text())
			else:
				result.error_kind, result.failure_stage = 'WORKER_EXIT', 'execution'
	except KeyboardInterrupt:
		result.status, result.error_kind, result.failure_stage = 'CANCELLED', 'INTERRUPTED_ACTION_OUTCOME_UNKNOWN', 'execution'
	except Exception as error:
		result.error_kind, result.failure_stage = type(error).__name__, 'supervisor'
	finally:
		# Repeated interrupts must not interrupt cleanup itself.
		for signum in previous_handlers:
			signal.signal(signum, signal.SIG_IGN)
		if process:
			result.cleanup = 'PASS' if cleanup_worker(process) else 'FAILED'
			result.worker_exit_status = process.returncode
		for signum, handler in previous_handlers.items():
			signal.signal(signum, handler)
		shutil.rmtree(private)
		(run_dir / '.worker-result.json').unlink(missing_ok=True)
	if result.cleanup == 'FAILED':
		result.status, result.error_kind, result.failure_stage = 'FAILED', 'CLEANUP_FAILED', 'cleanup'
	# On interrupted runs, remove any worker output that was never validated.
	if result.status != 'COMPLETED':
		(run_dir / 'last.png').unlink(missing_ok=True)
	payload = {'final_result': result.final_result, 'structured_result': result.structured_result}
	(run_dir / 'result.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2))
	(run_dir / 'actions.json').write_text(json.dumps(result.actions))
	for name in ('result.json', 'actions.json', 'last.png'):
		path = run_dir / name
		if path.is_file():
			data = path.read_bytes()
			result.evidence.append(EvidenceFile(path=name, sha256=hashlib.sha256(data).hexdigest(), bytes=len(data)))
	(run_dir / 'metadata.json').write_text(result.model_dump_json(indent=2))
	return result
