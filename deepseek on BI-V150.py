from typing import List, Union, Generator, Iterator
import requests


class Pipeline:
    def __init__(self):
        self.name = "deepseek(external)"
        self.external_url = f"http://100.84.87.212:5000/chat"
        pass

    async def on_startup(self):
        pass

    async def on_shutdown(self):
        pass

    def pipe(
        self, user_message: str, model_id: str, messages: List[dict], body: dict
    ) -> Union[str, Generator, Iterator]:
        headers = {
            "Content-Type": "application/json"
        }
        payload = {
            "messages":messages,
            "max_tokens": 5000,
            "stream": True,
        }
        try:
            r = requests.post(
                url=self.external_url,
                json=payload,
                headers=headers,
                stream=True,
            )

            r.raise_for_status()

            return r.iter_lines()

        except Exception as e:
            return f"Error: {e}"
