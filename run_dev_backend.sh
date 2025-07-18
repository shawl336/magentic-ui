#!/bin/bash -e


source .venv/bin/activate && python -m magentic_ui.backend.cli --reload --run-without-docker --host 127.0.0.1 --port 8081 --config config.yaml