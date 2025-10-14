#!/bin/bash -e


#source .venv/bin/activate && python -m magentic_ui.backend.cli --reload --run-without-docker --host 0.0.0.0 --port 8081 --config config.yaml
source .venv/bin/activate && python -m magentic_ui.backend.cli --host 0.0.0.0 --port 8081 --config config.yaml
