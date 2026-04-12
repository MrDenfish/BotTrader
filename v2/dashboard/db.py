"""Database connection and async bridge for the Streamlit dashboard.

Streamlit runs synchronously, but the report collectors are async (asyncpg).
This module provides:
  - A module-level asyncio event loop shared by all async operations
  - run_async() to bridge sync Streamlit code to async collectors
  - get_pool() to create/cache an asyncpg connection pool
"""

import asyncio
import os

import asyncpg
import streamlit as st

# Module-level event loop — shared across all async calls so the asyncpg
# pool (which is bound to a specific loop) stays valid.
# Also set as the current loop for this thread so that libraries calling
# asyncio.get_event_loop() (e.g. asyncpg.create_pool) find it.
_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)


def run_async(coro):
    """Run an async coroutine from synchronous Streamlit code.

    Uses a module-level event loop so asyncpg connections remain valid
    across calls within the same Streamlit process.
    """
    asyncio.set_event_loop(_loop)
    return _loop.run_until_complete(coro)


@st.cache_resource
def get_pool() -> asyncpg.Pool:
    """Create a shared asyncpg connection pool (cached per Streamlit process).

    Pool size is kept small (1-3) since the dashboard is single-user
    and all queries run sequentially through run_async().
    """
    return run_async(
        asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    )
