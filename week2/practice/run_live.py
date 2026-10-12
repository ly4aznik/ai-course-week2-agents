"""Запускает ноутбук с настоящей моделью; ключ получает через stdin или окружение."""
import json
import os
import re
import argparse
import sys
from pathlib import Path

import nbformat
from nbclient import NotebookClient
from jupyter_client import KernelManager

parser = argparse.ArgumentParser()
parser.add_argument('--tasks', type=int, nargs='*')
args = parser.parse_args()
sections = {1: [7,8,9], 2: [11,12,13], 3: [15,16,17], 4: [19,20,21], 5: [23,24,25], 6: [27,28,29], 7: [31,32,33], 8: [35,36,37], 9: [39,40,41], 10: [43,44,45]}
selected = None if not args.tasks else {1,3} | {i for t in args.tasks for i in sections[t]}
path = Path(__file__).with_name('week02_agents.ipynb')
key = os.getenv('OPENROUTER_API_KEY')
if not key:
    print('WAITING_FOR_API_KEY', flush=True)
    key = __import__("getpass").getpass("API key: ").strip()
if not key:
    raise SystemExit('API key not provided')
os.environ['OPENROUTER_API_KEY'] = key
os.environ['OPENROUTER_MODEL'] = 'openai/gpt-6-luna'
os.environ['LAB_SERVICE'] = 'openrouter'
os.environ['PYTHONIOENCODING'] = 'utf-8'
nb = nbformat.read(path, as_version=4)


def save():
    # Не сохраняем секрет даже если библиотека неожиданно выведет его в ошибке.
    safe = json.dumps(nb, ensure_ascii=False, indent=1).replace(key, '[REDACTED]')
    path.write_text(safe + '\n', encoding='utf-8')


class ProgressClient(NotebookClient):
    async def async_execute_cell(self, cell, cell_index, execution_count=None, store_history=True):
        if selected is not None and cell_index not in selected:
            return cell
        if cell.cell_type == 'code':
            print(f'START cell {cell_index}: {cell.source.splitlines()[0]}', flush=True)
        result = await super().async_execute_cell(cell, cell_index, execution_count, store_history)
        save()
        if cell.cell_type == 'code':
            errors = [o for o in cell.get('outputs', []) if o.output_type == 'error']
            print(f'END cell {cell_index}: ' + (','.join(o.ename for o in errors) or 'OK'), flush=True)
            if any(o.ename in ('AuthenticationError', 'PermissionDeniedError', 'APIConnectionError', 'NotFoundError') for o in errors):
                raise RuntimeError('API unavailable: ' + errors[0].ename)
        return result


km = KernelManager(kernel_name='python3')
km.kernel_spec.argv = [sys.executable, '-m', 'ipykernel_launcher', '-f', '{connection_file}']
client = ProgressClient(nb, km=km, timeout=240, allow_errors=True,
                        resources={'metadata': {'path': str(path.parent)}})
try:
    client.execute()
finally:
    save()
    os.environ.pop('OPENROUTER_API_KEY', None)
    os.environ.pop('OPENAI_API_KEY', None)

checks = [9, 13, 17, 21, 25, 29, 33, 37, 41, 45]
results = {}
for task, i in enumerate(checks, 1):
    outputs = nb.cells[i].get('outputs', [])
    results[str(task)] = {'passed': any('ЗАДАЧА ЗАЧТЕНА' in o.get('text', '') for o in outputs),
                          'errors': [o.get('ename') for o in outputs if o.output_type == 'error']}
path.with_name('live_results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
print('RESULTS', json.dumps(results, ensure_ascii=False), flush=True)
