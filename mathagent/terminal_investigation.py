# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Terminal controls for the same durable conversation and worker host as the web app."""
import copy

from .gui import store
from .gui.hub import Busy, Hub
from .gui.server import folder_lock
from .investigation import list_records, load_record


PHASE_WORDS = {'frame': 'planning', 'planned': 'planned', 'approval': 'planned', 'running': 'started', 'done': 'finished',
               'clarification': 'needs your answer', 'refused': 'no plan'}


def _pause_causes(root, chat_id, state):
    """What stopped the run. The terminal shows neither the worker cards nor the chat notices."""
    slots = (state.get('investigation') or {}).get('tasks', {}).values()
    causes = [slot['reason'] for slot in slots if slot['status'] == 'interrupted' and slot.get('reason')]
    last = store.load_chat(root, chat_id)['transcript'][-1]
    if last.get('role') == 'notice':
        causes.append(last['content'])
    return list(dict.fromkeys(causes))


def command(name, argument, args, ui):
    if name == '/investigations':
        records = list_records(args.workspace)
        for record in records:
            ui.say(f"{record['id']}  {PHASE_WORDS.get(record['phase'], record['phase'])}  "
                   f"{(record.get('target') or {}).get('statement') or record.get('query', '')}")
        if not records:
            ui.say('No saved investigations.')
        return 'listed'
    if not argument:
        raise ValueError('Supply a question.' if name == '/investigate' else 'Supply an ID from /investigations.')
    record = load_record(args.workspace, argument) if name != '/investigate' else None
    if record and name == '/investigate-report' and record.get('report'):
        ui.say(record['report'])
        return 'complete'

    # Both interfaces use the same on-disk chat checkpoint. Use the existing
    # workspace writer lock when the terminal starts, resumes, or stops work.
    with folder_lock(args.workspace):
        options = copy.copy(args)
        options.terminal_investigation = True
        options.gui_auto_titles = False
        options.assistant_concurrency = 1
        hub = Hub(options)
        try:
            if record:
                chat_id = record['chat_id']
                state = store.load_chat(hub.root, chat_id).get('assistant_state') or {}
                if (state.get('investigation') or {}).get('id') != argument:
                    raise ValueError('This investigation is no longer the current turn of its conversation.')
                if name == '/investigate-report':
                    ui.say(hub.stop_investigation(chat_id)['answer'])
                    return 'complete'
                task = hub.resume_assistant(chat_id)
            else:
                chat_id = store.create_chat(hub.root, 'free', not args.no_think, args.model, args.online)['id']
                files = list(dict.fromkeys([*args.proof_file, *args.research_file]))
                task = hub.route(chat_id, argument, files, required_mode='investigate')
            try:
                while not task.ready.wait(0.2):
                    for approval in hub.snapshot()['approvals']:
                        hub.decide(approval['id'], ui.approve(approval['preview']))
                        # Wait until the worker has removed this answered prompt.
                        while not task.ready.wait(0.05) and any(
                                item['id'] == approval['id'] for item in hub.snapshot()['approvals']):
                            pass
            except KeyboardInterrupt:
                if hub.active(task.id):
                    hub.pause(task.id)
                task.ready.wait()
            state = store.load_chat(hub.root, chat_id).get('assistant_state') or {}
            if task.error:
                raise ValueError(task.error)
            if state.get('answer'):
                ui.say(state['answer'])
            record_id = (state.get('investigation') or {}).get('id')
            if record_id:
                ui.say(f'Investigation {record_id}')
            if task.state == 'paused' and record_id:
                for cause in _pause_causes(hub.root, chat_id, state):
                    ui.say(cause)
                ui.say(f'/investigate-resume {record_id} or /investigate-report {record_id}')
            if (state.get('investigation') or {}).get('phase') in ('failed', 'refused'):
                return 'failed'
            return task.result_status or task.state
        except (Busy, store.NotFound) as exc:
            raise ValueError(str(exc)) from exc
        finally:
            hub.shutdown()
