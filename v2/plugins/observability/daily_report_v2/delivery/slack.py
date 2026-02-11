"""Slack webhook delivery."""

from __future__ import annotations

import logging
import os

import aiohttp

logger = logging.getLogger(__name__)


async def send_slack(
    blocks: list[dict],
    webhook_url: str | None = None,
    webhook_url_env: str = "SLACK_WEBHOOK_URL",
) -> bool:
    """POST Slack Block Kit blocks to a webhook URL. Returns True on success."""
    url = webhook_url or os.environ.get(webhook_url_env, "")
    if not url:
        logger.debug("Slack webhook not configured (env: %s)", webhook_url_env)
        return False

    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.post(
                url,
                json={"blocks": blocks},
                timeout=aiohttp.ClientTimeout(total=15),
            )
            if resp.status == 200:
                logger.info("Report sent to Slack webhook")
                return True
            else:
                body = await resp.text()
                logger.warning("Slack webhook returned %d: %s", resp.status, body[:200])
                return False
    except Exception:
        logger.exception("Failed to send Slack webhook")
        return False
