import asyncio
import signal


class AsyncFunctions:
    shutdown_event = asyncio.Event()
    shutdown_in_progress = False

    @classmethod
    async def shutdown(cls, loop, http_session=None):
        print("Initiating shutdown sequence...")
        tasks = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task(loop)]

        # Cancel all pending tasks
        for task in tasks:
            task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)  # Ensures cancellation completes

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
            cls.shutdown_event.set()


# Registering signal handlers
signal.signal(signal.SIGINT, AsyncFunctions.signal_handler)
signal.signal(signal.SIGTERM, AsyncFunctions.signal_handler)

