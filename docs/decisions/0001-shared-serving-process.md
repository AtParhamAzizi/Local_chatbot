# 0001: Shared serving process with explicit initialization

Date recorded: 2026-11-08

Status: Implemented; recorded from the current code

## Context and decision

The user and administrator apps share text models, controller state, in-memory
admission limits and retention. [main.py](../../backend/main.py),
[application.py](../../backend/application.py) and
[bootstrap.py](../../backend/bootstrap.py) separate app assembly from runtime
initialization. Importing the factories does not start services. The supported
launcher validates settings, checks the already-migrated schema, restores
controller state, warms text models and starts retention before serving.

[serving.py](../../backend/serving.py) uses two threaded Flask listeners for
explicit development and one Gunicorn gthread worker with eight threads for
production. The listening socket port chooses the app. Models load inside the
worker after fork; there is no preload or automatic reload. Native Windows is
development-only. [speech.py](../../backend/speech.py) can lazily start a private
TTS subprocess with its own interpreter, separate from public serving sockets.

## Alternatives and consequences

Separate web workers/apps would duplicate text-model memory and split controller,
limit and cleanup state. Supporting that topology requires explicit shared state
and model-serving coordination; raising Gunicorn's worker count alone does not
provide it. One worker also limits concurrency: each open voice WebSocket occupies
a thread. A text-model startup failure prevents both services from binding.
The optional TTS subprocess isolates incompatible speech dependencies while
adding its own lifecycle and cache preparation requirements.

Factory tests can assemble apps with adapters without running a deployment.
Production must use the explicit serving bootstrap rather than an arbitrary
`flask run` or factory-only WSGI command. The [operations guide](../operations.md)
describes the supported launch and acceptance sequence.

## Verification and reconsideration

[test_application_factories.py](../../backend/tests/test_application_factories.py)
checks factory/startup boundaries and runtime policy;
[test_speech.py](../../backend/tests/test_speech.py) exercises speech HTTP behavior
with doubles. These do not establish production throughput or real speech/model
capacity. Revisit this decision when scaling requires additional serving workers
and shared external coordination has been designed and tested.
