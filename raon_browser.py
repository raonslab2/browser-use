"""Secure product bootstrap before importing Browser-Use's dotenv-loading package."""

import os


def configure_environment() -> None:
	"""Disable automatic dotenv discovery and telemetry before any upstream import."""
	os.environ['PYTHON_DOTENV_DISABLED'] = '1'
	os.environ['ANONYMIZED_TELEMETRY'] = 'false'
	os.environ['BROWSER_USE_CLOUD_SYNC'] = 'false'
	os.environ['BROWSER_USE_SETUP_LOGGING'] = 'false'


def main() -> int:
	"""Preserve explicit process credentials; never discover credentials from files."""
	configure_environment()
	from browser_use.raon.__main__ import main as run_cli

	return run_cli()


if __name__ == '__main__':
	raise SystemExit(main())
