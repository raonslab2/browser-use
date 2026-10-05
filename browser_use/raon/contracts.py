"""Validated product contracts; never persist upstream history or model payloads."""

import os
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RunInput(BaseModel):
	"""Bounded one-PC run. Output paths are relative to the project workspace."""

	model_config = ConfigDict(extra='forbid')
	task: str = Field(min_length=1, max_length=16000)
	run_id: str = Field(default_factory=lambda: uuid4().hex, pattern=r'^[a-zA-Z0-9_-]{1,80}$')
	start_url: str | None = None
	allowed_domains: list[str] = Field(min_length=1, max_length=100)
	headless: bool = True
	max_steps: int = Field(default=10, ge=1, le=100)
	timeout: float = Field(default=120, ge=1, le=600)
	output: str = '.raon-runs'
	mode: Literal['agent', 'fixture'] = 'agent'
	provider: Literal['browser-use', 'openai', 'anthropic', 'google'] = 'browser-use'
	model: str | None = Field(default=None, max_length=120, pattern=r'^[a-zA-Z0-9_./:-]+$')
	# Flat JSON object schema, compiled to a Pydantic v2 model by the worker.
	output_schema: dict[str, Literal['str', 'int', 'bool', 'str_list']] | None = None
	capture_screenshot: bool = False
	controlled_page: bool = False

	@field_validator('output')
	@classmethod
	def relative_output(cls, value: str) -> str:
		path = Path(value)
		if path.is_absolute() or '..' in path.parts or not path.parts:
			raise ValueError('output must be a workspace-relative directory')
		return value

	@field_validator('allowed_domains')
	@classmethod
	def domain_patterns(cls, values: list[str]) -> list[str]:
		for value in values:
			if not re.fullmatch(r'(\*\.)?[a-zA-Z0-9.-]+(?::[0-9]+)?', value) or value == '*':
				raise ValueError('use explicit host names or *.host patterns')
		return values

	@field_validator('start_url')
	@classmethod
	def http_url(cls, value: str | None) -> str | None:
		if value is not None:
			url = urlsplit(value)
			if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
				raise ValueError('start_url must be HTTP(S), without credentials')
		return value


class EvidenceFile(BaseModel):
	"""Digest of a retained, relative evidence artifact."""

	path: str
	sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
	bytes: int = Field(ge=0)


class RunResult(BaseModel):
	"""Terminal product result, separate from Browser-Use's internal history."""

	run_id: str
	status: Literal['COMPLETED', 'FAILED', 'CANCELLED', 'REAL_BROWSER_AGENT_LLM_BLOCKED']
	mode: Literal['agent', 'fixture']
	model_execution: Literal['REAL', 'NONE_DETERMINISTIC', 'NOT_RUN']
	final_result: str | None = None
	structured_result: dict[str, Any] | None = None
	visited_urls: list[str] = Field(default_factory=list, max_length=100)
	actions: list[str] = Field(default_factory=list, max_length=100)
	evidence: list[EvidenceFile] = Field(default_factory=list, max_length=5)
	error_kind: str | None = None
	failure_stage: str | None = None
	upstream_source_revision: str = '7be96ed8bafa8dfe1eef228b59cf5c884b8b2431'
	source_revision: str
	adapter_revision: str
	browser_identity: str | None = None
	model_identity: str | None = None
	cleanup: Literal['PASS', 'FAILED'] = 'PASS'
	worker_exit_status: int | None = None


def safe_url(value: str) -> str:
	"""Retain origin only; omit userinfo, path, query and fragment."""
	try:
		url = urlsplit(value)
		if url.scheme in ('http', 'https') and url.hostname:
			return f'{url.scheme}://{url.hostname}' + (f':{url.port}' if url.port else '')
	except ValueError:
		pass
	return '[non-http-url]'


def sanitize(value: Any) -> Any:
	"""Basic credential controls, not a general PII detector. Use public fixtures only."""
	if isinstance(value, dict):
		return {
			key: '[REDACTED]' if re.search(r'password|secret|token|cookie|authorization|api.?key', key, re.I) else sanitize(item)
			for key, item in value.items()
		}
	if isinstance(value, list):
		return [sanitize(item) for item in value[:100]]
	if isinstance(value, str):
		for key, secret in os.environ.items():
			if re.search(r'KEY|TOKEN|SECRET|PASSWORD|COOKIE|AUTHORIZATION', key, re.I) and len(secret) >= 4:
				value = value.replace(secret, '[REDACTED]')
		value = re.sub(r'https?://[^\s<>"\']+', lambda match: safe_url(match.group()), value)
		value = re.sub(r'(?i)(bearer\s+)[^\s,;]+', r'\1[REDACTED]', value)
		value = re.sub(
			r'(?i)((?:password|token|secret|cookie|authorization|api[_-]?key)\s*[:=]\s*)[^\s,;]+', r'\1[REDACTED]', value
		)
		return value[:16000]
	return value


def workspace_output(workspace: Path, output: str) -> Path:
	"""Reject resolved symlink escapes, including pre-existing output directories."""
	root = workspace.resolve()
	path = (root / output).resolve()
	if path == root or not path.is_relative_to(root):
		raise ValueError('output must remain strictly inside the project workspace')
	return path
