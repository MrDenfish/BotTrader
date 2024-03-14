import random
import time


class AccessoryTools:
    def __init__(self, logmanager):
        self.base_delay = 5  # Start with a 5-second delay
        self.max_delay = 320  # Don't wait more than this
        self.max_retries = 5  # Default max retries
        self.log_manager = logmanager

    def retry_request(self, func, max_retries=5):
        """
        A retry mechanism using exponential backoff.
        """
        base_delay = 5  # Start with a 5-second delay
        max_delay = 320  # Don't wait more than this

        retries = 0
        while retries < max_retries:
            try:
                return func()  # Execute the function
            except Exception as e:
                if 'rate limit' not in str(e):
                    raise  # If the exception is not about rate limit, raise it

                wait_time = min(base_delay * (2 ** retries), max_delay)
                jitter = random.uniform(0.5, 1.5)
                sleep_time = wait_time * jitter

                self.log_manager.webhook_logger.warning(f"Rate limit hit. Retrying in {sleep_time:.2f} "
                                                        f"seconds...- retry_request")
                time.sleep(sleep_time)
                retries += 1

        self.log_manager.webhook_logger.error(f"Max retries ({max_retries}) reached. Giving up.- retry_request")
        # Maybe raise an exception here or return None
