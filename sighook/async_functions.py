import asyncio
import signal


class AsyncFunctions:
    shutdown_event = asyncio.Event()

    @classmethod
    async def shutdown(cls, loop, http_session=None):
        # Cancel all running tasks
        tasks = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task(loop)]
        [task.cancel() for task in tasks]

        await asyncio.gather(*tasks, return_exceptions=True)

        # Close database connections
        # if database_manager:
        #     await database_manager.close()

        # Gracefully close the aiohttp session
        if http_session:
            try:
                await http_session.close()
            except Exception as e:
                print(f"Error closing the HTTP session: {e}")

        loop.stop()

    @classmethod
    def signal_handler(cls, *args):
        print("Shutdown signal received")
        cls.shutdown_event.set()


# Registering signal handlers
signal.signal(signal.SIGINT, AsyncFunctions.signal_handler)
signal.signal(signal.SIGTERM, AsyncFunctions.signal_handler)

