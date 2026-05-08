"""Mistral Workflows worker — registers the GenAIUseCaseWorkflow with the
Mistral runtime so it can be invoked from Le Chat (once published) or via
the Workflows API.

Run locally:

    uv run python -m scripts.run_worker

The worker polls the Mistral runtime for task assignments. As long as it's
running, your workflow is invokable. Stop with Ctrl-C.

For Le Chat publication, see docs/publish_le_chat.md — this script is the
prerequisite worker that has to be running (or deployed) for the published
assistant to actually execute work.
"""

from __future__ import annotations

import logging
import sys

from mistralai.workflows import run_worker

from src.config import settings
from src.workflow import GenAIUseCaseWorkflow


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("run_worker")

    if not settings.mistral_api_key:
        log.error("MISTRAL_API_KEY is required — set it in .env or your shell")
        return 1

    log.info(
        "starting Mistral Workflows worker for %r (tier=%s)",
        GenAIUseCaseWorkflow.__name__,
        settings.tier.value,
    )
    # Blocking call — polls indefinitely. The Mistral runtime hands tasks
    # to this worker when the assistant is invoked from Le Chat or the API.
    run_worker(
        workflows=[GenAIUseCaseWorkflow],
        detach=False,
        api_key=settings.mistral_api_key,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
