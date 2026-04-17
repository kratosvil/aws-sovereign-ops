import os
import boto3
import logging
from botocore.config import Config

logger = logging.getLogger(__name__)

# Phase constants — controls which tools Bedrock can call in each phase
INVESTIGATION_TOOLS = ["analyze_incident", "propose_fix"]
EXECUTION_TOOLS = ["execute_approved", "validate_fix", "rollback"]


class BedrockService:
    def __init__(self):
        self.client = boto3.client(
            "bedrock-runtime",
            region_name=os.environ["AWS_REGION"],
            config=Config(read_timeout=120, connect_timeout=10, retries={"max_attempts": 2}),
        )
        self.model_id = os.environ.get(
            "BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
        )

    def converse(self, messages: list, system: str, tools: list) -> dict:
        """
        Calls Bedrock converse API with tool support.
        Returns the full response dict from boto3.
        """
        logger.info("bedrock_converse model=%s tools=%s msgs=%d", self.model_id, [t["name"] for t in tools], len(messages))
        response = self.client.converse(
            modelId=self.model_id,
            system=[{"text": system}],
            messages=messages,
            toolConfig={"tools": [{"toolSpec": t} for t in tools]},
        )
        logger.info(
            "bedrock_response stop_reason=%s usage=%s",
            response["stopReason"],
            response.get("usage"),
        )
        return response

    def extract_tool_calls(self, response: dict) -> list:
        """
        Returns list of tool use blocks from a converse response.
        Each item: {"toolUseId": str, "name": str, "input": dict}
        """
        calls = []
        for block in response.get("output", {}).get("message", {}).get("content", []):
            if block.get("toolUse"):
                calls.append(block["toolUse"])
        return calls

    def extract_text(self, response: dict) -> str:
        """Returns concatenated text from a converse response."""
        parts = []
        for block in response.get("output", {}).get("message", {}).get("content", []):
            if block.get("text"):
                parts.append(block["text"])
        return "\n".join(parts)

    def build_tool_result_message(self, tool_use_id: str, result: dict) -> dict:
        """Wraps a tool result into the Bedrock message format."""
        return {
            "role": "user",
            "content": [
                {
                    "toolResult": {
                        "toolUseId": tool_use_id,
                        "content": [{"json": result}],
                    }
                }
            ],
        }
