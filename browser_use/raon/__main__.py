"""Repeatable canonical Request entrypoint; no AgentOpt runtime dependencies."""

import argparse
import json
from pathlib import Path

from browser_use.raon.contracts import RunInput
from browser_use.raon.runner import run


def main() -> int:
	"""Run a validated task file and print only terminal status and evidence location."""
	parser = argparse.ArgumentParser()
	parser.add_argument('command', choices=['run'])
	parser.add_argument('--task-file', required=True, type=Path)
	parser.add_argument('--output', help='Workspace-relative evidence directory')
	args = parser.parse_args()
	payload = json.loads(args.task_file.read_text())
	if args.output is not None:
		payload['output'] = args.output
	try:
		config = RunInput.model_validate(payload)
		result = run(config)
	except (ValueError, OSError) as error:
		print(json.dumps({'status': 'FAILED', 'error_kind': type(error).__name__, 'failure_stage': 'input_or_reservation'}))
		return 2
	print(
		json.dumps(
			{
				'run_id': result.run_id,
				'status': result.status,
				'metadata': f'{config.output}/{result.run_id}/metadata.json',
				'error_kind': result.error_kind,
			}
		)
	)
	return 0 if result.status == 'COMPLETED' else 2


if __name__ == '__main__':
	# This package's parent imports dotenv before nested __main__ can configure it.
	print(json.dumps({'status': 'FAILED', 'error_kind': 'SAFE_BOOTSTRAP_REQUIRED', 'entrypoint': 'python -m raon_browser'}))
	raise SystemExit(2)
