"""A synchronous OpenAI-compatible tool loop; deliberately no agent framework."""
import json
import re
import time
from pathlib import Path

from openai import APIConnectionError, APIStatusError, RateLimitError
from pydantic import ValidationError
from .tools import TOOLS, REGISTRY

SYSTEM = ('Ты справочная по базе Ростова. Выполняй запрос через инструменты, не угадывай данные. '
          'Координаты и id следующего шага бери из наблюдений. Ошибки инструмента — наблюдения: '
          'исправь аргументы или повтори временный сбой, но не сообщай об успехе без данных. '
          'После получения данных дай короткий ответ с нужными числами, адресами или часами. '
          'При отсутствии данных честно сообщи об ограничении.')


def redact(value):
    return re.sub(r'sk-(?:or-v1-)?[A-Za-z0-9_-]{20,}', '[REDACTED]', str(value))


class Journal:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('x', encoding='utf-8')

    def write(self, event, **fields):
        record = {'event': event, **fields}
        self.file.write(redact(json.dumps(record, ensure_ascii=False)) + '\n')
        self.file.flush()

    def close(self):
        self.file.close()


def run_agent(client, model, question, variant, journal, pricing, *, max_steps=6, diagnostic=None):
    schemas = [tool.schema(variant) for tool in TOOLS]
    instruction = SYSTEM
    if diagnostic == 'retry_loop':
        instruction += ' В диагностике при retryable=true повторяй тот же вызов до получения данных.'
    messages = [{'role': 'system', 'content': instruction}, {'role': 'user', 'content': question}]
    journal.write('start', model=model, question=question, variant=variant,
                  max_steps=max_steps, diagnostic=diagnostic, system=instruction, tools=schemas)
    result = {'final': '', 'status': 'max_steps', 'calls': [], 'input_tokens': 0,
              'output_tokens': 0, 'cost_usd': 0.0, 'cost_sources': [], 'steps': 0}
    for step in range(1, max_steps + 1):
        result['steps'] = step
        response = None
        for attempt in range(1, 4):
            journal.write('request', step=step, attempt=attempt, messages=messages)
            try:
                response = client.chat.completions.create(
                    model=model, messages=messages, tools=schemas, reasoning_effort='none',
                    max_completion_tokens=1800, temperature=0.2, parallel_tool_calls=False)
                break
            except (APIConnectionError, RateLimitError) as exc:
                journal.write('api_error', step=step, attempt=attempt, error_type=type(exc).__name__, error=redact(exc))
                if attempt < 3:
                    time.sleep(attempt)
            except APIStatusError as exc:
                journal.write('api_error', step=step, attempt=attempt, status=exc.status_code, error_type=type(exc).__name__, error=redact(exc))
                if exc.status_code in (401, 403, 402, 404):
                    raise RuntimeError(f'Provider rejected request: HTTP {exc.status_code}') from None
                if exc.status_code < 500 or attempt == 3:
                    break
                time.sleep(attempt)
        if response is None:
            result['status'] = 'api_error'
            break
        if response.usage is None:
            raise RuntimeError('Provider did not return usage; cannot compute assignment metrics')
        usage = response.usage.model_dump(mode='json')
        in_tokens, out_tokens = usage['prompt_tokens'], usage['completion_tokens']
        if usage.get('cost') is not None:
            cost, source = float(usage['cost']), 'provider_usage.cost'
        else:
            cost, source = in_tokens * float(pricing['prompt']) + out_tokens * float(pricing['completion']), 'catalog_estimate_no_cache_discount'
        result['input_tokens'] += in_tokens
        result['output_tokens'] += out_tokens
        result['cost_usd'] += cost
        result['cost_sources'].append(source)
        message = response.choices[0].message
        # Reasoning is disabled; only protocol fields are replayed to avoid provider extensions.
        assistant = {'role': 'assistant', 'content': message.content}
        if message.tool_calls:
            assistant['tool_calls'] = [c.model_dump(include={'id','type','function'}, mode='json') for c in message.tool_calls]
        journal.write('response', step=step, response_id=response.id, actual_model=response.model,
                      usage=usage, cost_usd=cost, cost_source=source, message=assistant)
        messages.append(assistant)
        if not message.tool_calls:
            result['final'] = message.content or ''
            result['status'] = 'completed'
            break
        for call in message.tool_calls:
            requested = call.function.arguments
            name = call.function.name
            record = {'step': step, 'name': name, 'requested_arguments': requested, 'ok': False}
            try:
                if name not in REGISTRY:
                    raise ValueError(f'Unknown tool: {name}')
                executed = requested
                if diagnostic == 'invalid_arguments':
                    executed = json.dumps({'kind': 'аптека', 'only_24_7': False})
                if diagnostic == 'database_unavailable':
                    raise OSError('Controlled diagnostic: read-only city database unavailable')
                if diagnostic == 'retry_loop':
                    observation = {'error': 'Controlled diagnostic: retry required', 'retryable': True}
                    record['arguments'] = json.loads(executed)
                    record['error_type'] = 'RetryRequired'
                else:
                    args, observation = REGISTRY[name].invoke(executed)
                    record.update(arguments=args, ok=True)
                record['executed_arguments'] = executed
            except Exception as exc:
                # Tool failures never kill the model loop. Validation errors are shortened.
                error = exc.errors(include_url=False, include_input=False) if isinstance(exc, ValidationError) else redact(exc)
                observation = {'error_type': type(exc).__name__, 'error': error,
                               'retryable': isinstance(exc, OSError), 'hint': 'Исправьте вызов или сообщите, что данных нет'}
                record.update(error_type=type(exc).__name__, executed_arguments=locals().get('executed', requested))
            record['observation'] = observation
            result['calls'].append(record)
            journal.write('observation', **record)
            messages.append({'role': 'tool', 'tool_call_id': call.id,
                             'content': json.dumps(observation, ensure_ascii=False)})
    journal.write('finish', **result)
    return result
