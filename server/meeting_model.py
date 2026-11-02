import json
import os

from server.runpod_jobs import RunpodJobClient

ENDPOINT = "moonshot-kimi"
MODEL = "kimi-k3"


def inline_schema(schema):
    definitions = schema.get("$defs", {})

    def inline(value):
        if isinstance(value, list):
            return [inline(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            return inline(definitions[value["$ref"].split("/")[-1]])
        if "anyOf" in value and len(value["anyOf"]) == 1:
            return inline(value["anyOf"][0])
        return {
            key: inline(item)
            for key, item in value.items()
            if key not in ("$defs", "pattern", "title")
        }

    return inline(schema)


class KimiModel:
    def __init__(self, transport=None):
        self.key = os.environ.get("RUNPOD_API_KEY", "")
        self.transport = transport

    def ready(self):
        return bool(self.key)

    async def complete(self, instructions, payload, schema, name, max_tokens=2048):
        jobs = RunpodJobClient(
            ENDPOINT,
            self.key,
            transport=self.transport,
            timeout=55,
            execution_timeout_ms=45000,
            ttl_ms=90000,
        )
        response = await jobs.run(
            {
                "openai_route": "/v1/chat/completions",
                "openai_input": {
                    "model": MODEL,
                    "messages": [
                        {"role": "system", "content": instructions},
                        {
                            "role": "user",
                            "content": json.dumps(
                                payload, ensure_ascii=True, separators=(",", ":")
                            ),
                        },
                    ],
                    "max_tokens": max_tokens,
                    "stream": False,
                    "reasoning_effort": "low",
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": name,
                            "strict": True,
                            "schema": inline_schema(schema),
                        },
                    },
                },
            }
        )
        body = response.get("output")
        if isinstance(body, list) and len(body) == 1:
            body = body[0]
        if not isinstance(body, dict) or body.get("model") != MODEL:
            raise ValueError("Unexpected model response or identity.")
        choices = body.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError("Expected one model completion.")
        choice = choices[0]
        if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
            raise TypeError("Invalid model completion.")
        if choice.get("finish_reason") != "stop":
            raise ValueError("Model output incomplete.")
        content = choice.get("message", {}).get("content")
        if not isinstance(content, str) or len(content) > 30000:
            raise ValueError("Invalid model output.")
        usage = body.get("usage") or {}
        if not isinstance(usage, dict):
            raise TypeError("Invalid usage metadata.")
        details = usage.get("completion_tokens_details") or {}
        if not isinstance(details, dict):
            raise TypeError("Invalid reasoning usage metadata.")
        return content, {
            "total_tokens": max(0, int(usage.get("total_tokens", 0))),
            "round_trip_ms": response["round_trip_ms"],
            "queue_delay_ms": response.get("delayTime"),
            "provider_execution_ms": response.get("executionTime"),
            "completion_tokens": usage.get("completion_tokens"),
            "reasoning_tokens": details.get("reasoning_tokens"),
        }
