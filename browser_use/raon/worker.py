"""Private worker reusing native Browser-Use APIs; no persistent raw traces."""

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

from aiohttp import web
from pydantic import BaseModel, create_model

from browser_use.raon.contracts import RunInput, RunResult, safe_url, sanitize
from browser_use.raon.runner import revision

FIXTURE = """<!doctype html><html><head><title>RAON fixture</title></head><body>
<h1>RAON fixture</h1><ul><li>Alpha</li><li>Bravo</li><li>Charlie</li></ul>
<button id="reveal" onclick="document.getElementById('result').textContent='Action complete'">Reveal</button>
<p id="result">Ready</p></body></html>"""


class FixtureResult(BaseModel):
	"""Controlled fixture extraction after a real native click action."""

	title: str
	items: list[str]
	result: str


def output_model(config: RunInput) -> type[BaseModel] | None:
	"""Compile the deliberately small CLI schema into a native Pydantic v2 model."""
	if config.output_schema is None:
		return None
	types = {'str': str, 'int': int, 'bool': bool, 'str_list': list[str]}
	fields: dict[str, Any] = {key: (types[value], ...) for key, value in config.output_schema.items()}
	return create_model('RaonOutput', **fields)


def create_llm(config: RunInput):
	"""Use only explicit environment credentials; development providers are unrelated."""
	from browser_use import ChatAnthropic, ChatBrowserUse, ChatGoogle, ChatOpenAI

	adapters = {
		'browser-use': ('BROWSER_USE_API_KEY', ChatBrowserUse),
		'openai': ('OPENAI_API_KEY', ChatOpenAI),
		'anthropic': ('ANTHROPIC_API_KEY', ChatAnthropic),
		'google': ('GOOGLE_API_KEY', ChatGoogle),
	}
	key, adapter = adapters[config.provider]
	if not os.environ.get(key):
		return None
	if config.provider != 'browser-use' and config.model is None:
		raise ValueError('an explicit model name is required for this provider')
	return adapter(**({'model': config.model} if config.model else {}))


async def execute(config: RunInput, run_dir: Path) -> RunResult:
	"""Execute native Tools for fixture mode or the unmodified Agent loop for real mode."""
	from browser_use import Agent, BrowserSession, Tools

	result = RunResult(
		run_id=config.run_id,
		status='FAILED',
		mode=config.mode,
		model_execution='REAL' if config.mode == 'agent' else 'NONE_DETERMINISTIC',
		source_revision=revision(Path.cwd()),
		adapter_revision=revision(Path.cwd()),
	)
	browser = None
	server = None
	agent = None
	stage = 'configuration'
	try:
		if config.capture_screenshot and not config.controlled_page:
			raise ValueError('screenshot retention requires controlled_page acknowledgement')
		llm = create_llm(config) if config.mode == 'agent' else None
		if config.mode == 'agent' and llm is None:
			result.status = 'REAL_BROWSER_AGENT_LLM_BLOCKED'
			result.model_execution = 'NOT_RUN'
			result.error_kind, result.failure_stage = 'MISSING_MODEL_CREDENTIAL', 'model_configuration'
			return result
		private = run_dir / '.private'
		domains = config.allowed_domains
		url = config.start_url
		if config.mode == 'fixture':
			app = web.Application()

			async def fixture_page(request):
				return web.Response(text=FIXTURE, content_type='text/html')

			app.router.add_get('/', fixture_page)
			server = web.AppRunner(app, access_log=None)
			await server.setup()
			site = web.TCPSite(server, '127.0.0.1', 0)
			await site.start()
			port = server.addresses[0][1]
			url = f'http://127.0.0.1:{port}/'
			domains = ['127.0.0.1']
		stage = 'browser_start'
		browser = BrowserSession(
			headless=config.headless,
			chromium_sandbox=os.environ.get('RAON_DISABLE_SANDBOX') != '1',
			allowed_domains=domains,
			keep_alive=True,
			executable_path=os.environ.get('RAON_BROWSER_EXECUTABLE'),
			user_data_dir=str(private / 'profile'),
			downloads_path=str(private / 'downloads'),
			accept_downloads=False,
			auto_download_pdfs=False,
			enable_default_extensions=False,
		)
		await browser.start()
		session = await browser.get_or_create_cdp_session()
		version = await session.cdp_client.send.Browser.getVersion()
		result.browser_identity = version['product']
		stage = 'execution'
		if config.mode == 'fixture':
			tools = Tools()
			action_model = tools.registry.create_action_model()
			navigation = await tools.act(
				action_model.model_validate({'navigate': {'url': url, 'new_tab': False}}), browser_session=browser
			)
			if navigation.error:
				raise RuntimeError('fixture navigation failed')
			result.actions.append('navigate')
			result.visited_urls.append(safe_url(await browser.get_current_page_url()))
			state = await browser.get_browser_state_summary(include_screenshot=False)
			assert state.dom_state is not None
			index = next(index for index, node in state.dom_state.selector_map.items() if node.attributes.get('id') == 'reveal')
			clicked = await tools.act(action_model.model_validate({'click': {'index': index}}), browser_session=browser)
			if clicked.error:
				raise RuntimeError('fixture click failed')
			result.actions.append('click')
			session = await browser.get_or_create_cdp_session()
			extracted = await session.cdp_client.send.Runtime.evaluate(
				params={
					'expression': "JSON.stringify({title:document.title,items:[...document.querySelectorAll('li')].map(x=>x.textContent),result:document.querySelector('#result').textContent})",
					'returnByValue': True,
				},
				session_id=session.session_id,
			)
			extracted_json = extracted['result'].get('value')
			assert isinstance(extracted_json, str)
			value = FixtureResult.model_validate_json(extracted_json)
			if value.result != 'Action complete' or value.items != ['Alpha', 'Bravo', 'Charlie']:
				raise RuntimeError('fixture extraction mismatch')
			result.actions.append('extract_fixture')
			result.structured_result = value.model_dump()
			result.final_result = value.result
		else:
			assert llm is not None
			result.model_identity = f'{config.provider}/{llm.model}'
			agent = Agent(
				task=config.task,
				llm=llm,
				browser_session=browser,
				initial_actions=[{'navigate': {'url': url, 'new_tab': False}}] if url else None,
				output_model_schema=output_model(config),
				enable_signal_handler=False,
				file_system_path=str(private / 'files'),
				save_conversation_path=None,
				generate_gif=False,
				use_judge=False,
				calculate_cost=False,
				directly_open_url=False,
				max_actions_per_step=3,
				step_timeout=min(120, int(config.timeout)),
				llm_timeout=min(90, int(config.timeout)),
			)
			history = await agent.run(max_steps=config.max_steps)
			# Pair native actions with results: exclude proposed but unexecuted actions.
			result.actions = [
				next(iter(action.model_dump(exclude_none=True)))
				for step in history.history
				if step.model_output
				for action, action_result in zip(step.model_output.action, step.result)
				if action_result
			][:100]
			result.visited_urls = list(dict.fromkeys(safe_url(item) for item in history.urls() if item))[:100]
			result.final_result = sanitize(history.final_result())
			if history.structured_output is not None:
				result.structured_result = sanitize(history.structured_output.model_dump())
			if not history.is_successful():
				result.error_kind, result.failure_stage = 'AGENT_UNSUCCESSFUL', 'execution'
				return result
		stage = 'evidence'
		if config.capture_screenshot:
			await browser.take_screenshot(path=str(run_dir / 'last.png'))
		result.status = 'COMPLETED'
	except asyncio.CancelledError:
		if agent:
			agent.stop()
		result.status, result.error_kind, result.failure_stage = 'CANCELLED', 'INTERRUPTED', stage
	except Exception as error:
		# Never store exception messages: browser/model errors can contain input secrets.
		result.error_kind, result.failure_stage = type(error).__name__, stage
	finally:
		if browser:
			try:
				await asyncio.wait_for(browser.kill(), timeout=10)
			except Exception:
				result.cleanup = 'FAILED'
		if server:
			await server.cleanup()
	return result


async def main() -> None:
	"""SIGTERM cancels native work before cleanup; the supervisor enforces a hard cap."""
	logging.disable(logging.CRITICAL)
	config = RunInput.model_validate_json(await asyncio.to_thread(Path(sys.argv[1]).read_text))
	run_dir = Path(sys.argv[2])
	task = asyncio.create_task(execute(config, run_dir))
	loop = asyncio.get_running_loop()
	loop.add_signal_handler(signal.SIGTERM, task.cancel)
	loop.add_signal_handler(signal.SIGINT, task.cancel)
	result = await task
	(run_dir / '.worker-result.json').write_text(result.model_dump_json())


if __name__ == '__main__':
	asyncio.run(main())
