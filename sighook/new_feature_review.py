import aiohttp
import asyncio


"""This feature may be utilized at a later date to imporve sending webhooks, since AIOHTTP.CLientSession has been removed."""


class WebhookSender:
    def __init__(self):
        self.session = None

    async def start_session(self):
        if self.session:
            await self.session.close()
        self.session = aiohttp.ClientSession()

    async def send_webhook(self, url, payload):
        if not self.session:
            await self.start_session()
        try:
            async with self.session.post(url, json=payload) as response:
                return await response.text()
        except Exception as e:
            print(f"Failed to send webhook: {e}")
            # Handle exceptions, possibly reinitialize the session

    async def run(self):
        while True:
            # logic to decide when to send a webhook
            await self.send_webhook("http://example.com/webhook", {"data": "value"})
            await asyncio.sleep(10)  # Example delay

    async def close(self):
        if self.session:
            await self.session.close()

async def main():
    sender = WebhookSender()
    try:
        await sender.run()
    finally:
        await sender.close()

if __name__ == "__main__":
    asyncio.run(main())
