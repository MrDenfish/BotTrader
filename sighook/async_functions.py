import asyncio
import signal
from typing import Optional


class AsyncFunctions:
    shutdown_in_progress = False
    shutdown_event: Optional[asyncio.Event] = None

    @classmethod
    def attach_shutdown_event(cls, event: asyncio.Event):
        """Attach the central shutdown_event (from main.py)."""
        cls.shutdown_event = event

    @classmethod
    async def shutdown(cls, loop, http_session=None):
        print("Initiating shutdown sequence...")
        tasks = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task(loop)]

        for task in tasks:
            task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)

        if http_session:
            try:
                await http_session.close()
            except Exception as e:
                print(f"Error closing the HTTP session: {e}")

        print("Shutdown complete.")

    @classmethod
    def signal_handler(cls, *args):
        if not cls.shutdown_in_progress:
            cls.shutdown_in_progress = True
            print("Shutdown signal received.")
            if cls.shutdown_event:
                cls.shutdown_event.set()



# Registering signal handlers
signal.signal(signal.SIGINT, AsyncFunctions.signal_handler)
signal.signal(signal.SIGTERM, AsyncFunctions.signal_handler)

