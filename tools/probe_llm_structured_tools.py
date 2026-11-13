#!/usr/bin/env python3
"""Probe native JSON output + function calling without an SDK or executing commands.

The secret value is generated only after the first valid function call. A JSON
answer alone is not success: the tool must be called and its value returned.
Credentials come only from an environment variable, never a command-line value.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request


SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
    "additionalProperties": False,
}
FUNCTION = {
    "name": "read_probe",
    "description": "Read the verification value. It is unknown until this tool returns.",
    "parameters": {
        "type": "object", "properties": {}, "required": [],
        "additionalProperties": False,
    },
    "strict": True,
}
PROMPT = (
    "Call read_probe to obtain the verification value. You cannot know it without "
    "calling the tool. After reading the tool result, return only a JSON object "
    "containing the key value and that exact value. Do not guess or pretend to call tools."
)


def make_request(model, endpoint, output_format, tool_choice, schema_only, effort):
    prompt = (
        "Return only a JSON object with the single key value, whose value is exactly control-ok."
        if schema_only else PROMPT
    )
    body = {"model": model, "stream": False}
    if endpoint == "responses":
        body.update(input=[{"role": "user", "content": prompt}], max_output_tokens=4096)
        if effort:
            body["reasoning"] = {"effort": effort}
        if not schema_only:
            body["tools"] = [{"type": "function", **FUNCTION}]
        if output_format == "schema":
            body["text"] = {"format": {
                "type": "json_schema", "name": "probe_result", "schema": SCHEMA, "strict": True,
            }}
        elif output_format == "json":
            body["text"] = {"format": {"type": "json_object"}}
    else:
        body.update(messages=[{"role": "user", "content": prompt}], max_tokens=4096)
        if effort:
            body["reasoning_effort"] = effort
        if not schema_only:
            body["tools"] = [{"type": "function", "function": FUNCTION}]
        if output_format == "schema":
            body["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "probe_result", "schema": SCHEMA, "strict": True,
            }}
        elif output_format == "json":
            body["response_format"] = {"type": "json_object"}
    if not schema_only:
        body["tool_choice"] = tool_choice
        if tool_choice == "named":
            body["tool_choice"] = (
                {"type": "function", "name": "read_probe"} if endpoint == "responses"
                else {"type": "function", "function": {"name": "read_probe"}}
            )
        if output_format == "tool":
            result_tool = {
                "name": "submit_result", "description": "Submit the exact value read from read_probe.",
                "parameters": SCHEMA, "strict": True,
            }
            body["tools"].append(
                {"type": "function", **result_tool} if endpoint == "responses"
                else {"type": "function", "function": result_tool}
            )
            prompt = (
                "Call read_probe to obtain the unknown verification value. After reading its "
                "result, call submit_result with that exact value. Do not guess. "
                "The task is complete only after submit_result is accepted."
            )
            body["input" if endpoint == "responses" else "messages"][0]["content"] = prompt
    return body


def unpack(endpoint, response):
    if endpoint == "responses":
        output = response.get("output") or []
        calls = [item for item in output if item.get("type") == "function_call"]
        text = "".join(
            part.get("text", "") for item in output if item.get("type") == "message"
            for part in item.get("content", []) if part.get("type") == "output_text"
        )
        return calls, text, output
    message = ((response.get("choices") or [{}])[0].get("message") or {})
    return message.get("tool_calls") or [], message.get("content") or "", message


def probe(args, model, endpoint, output_format, schema_only=False):
    body = make_request(model, endpoint, output_format, args.tool_choice, schema_only, args.effort)
    row = {
        "model": model, "endpoint": endpoint, "format": output_format,
        "tool_choice": None if schema_only else args.tool_choice,
        "schema_only": schema_only, "tool_count": 0, "result_tool_count": 0,
        "value_verified": False,
    }
    if args.print_requests:
        return {**row, "request": body}
    start = time.monotonic()
    expected = "control-ok" if schema_only else None
    path = "/responses" if endpoint == "responses" else "/chat/completions"
    handlers = [urllib.request.ProxyHandler({})] if args.no_proxy else []
    opener = urllib.request.build_opener(*handlers)
    key = os.environ[args.api_key_env]
    try:
        for turn in range(3):
            request = urllib.request.Request(
                args.base_url.rstrip("/") + path,
                data=json.dumps(body).encode(),
                headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            )
            with opener.open(request, timeout=args.timeout) as response:
                row["http_status"] = response.status
                row["headers"] = {
                    k: v for k, v in response.headers.items()
                    if k.lower() in {"server", "x-litellm-version"}
                }
                result = json.load(response)
            calls, text, output = unpack(endpoint, result)
            row["turns"] = turn + 1
            if turn == 0:
                row["first_tool_count"] = len(calls)
            row["tool_count"] += len(calls)
            if not calls:
                try:
                    parsed = json.loads(text)
                    row["json_valid"] = True
                    row["value_verified"] = (
                        output_format != "tool" and expected is not None and parsed == {"value": expected}
                    )
                except ValueError:
                    row["json_valid"] = False
                # This probe sends only synthetic text. Still redact an echoed key.
                row["text_excerpt"] = text.replace(key, "[REDACTED]")[:160]
                break
            if schema_only:
                raise ValueError("Unexpected tool call in schema-only control")
            for call in calls:
                function = call if endpoint == "responses" else call["function"]
                if output_format == "tool" and function["name"] == "submit_result":
                    row["result_tool_count"] += 1
                    row["value_verified"] = (
                        expected is not None and json.loads(function["arguments"]) == {"value": expected}
                    )
                    if not row["value_verified"]:
                        raise ValueError("Result tool did not return the value read from read_probe")
                    continue
                if function["name"] != "read_probe" or json.loads(function["arguments"]) != {}:
                    raise ValueError("Unexpected function name or arguments")
            if row["result_tool_count"]:
                break
            if expected is None:
                expected = secrets.token_hex(16)
            tool_result = json.dumps({"value": expected})
            if endpoint == "responses":
                body["input"].extend(output)
                body["input"].extend({
                    "type": "function_call_output", "call_id": call["call_id"], "output": tool_result,
                } for call in calls)
            else:
                body["messages"].append(output)
                body["messages"].extend({
                    "role": "tool", "tool_call_id": call["id"], "content": tool_result,
                } for call in calls)
            # Required/named are diagnostic controls for the first request only.
            body["tool_choice"] = "auto"
        row["passed"] = row["value_verified"] and (schema_only or row["tool_count"] > 0)
    except urllib.error.HTTPError as error:
        # Do not print server error bodies: they may contain upstream credentials.
        row.update(http_status=error.code, error_type="HTTPError", passed=False)
        error.close()
    except Exception as error:
        row.update(error_type=type(error).__name__, passed=False)
    row["seconds"] = round(time.monotonic() - start, 2)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="API prefix, including /v1 if needed")
    parser.add_argument("--model", required=True, action="append")
    parser.add_argument("--endpoint", action="append", choices=["responses", "chat"])
    parser.add_argument("--format", dest="formats", action="append", choices=["none", "schema", "json", "tool"])
    parser.add_argument("--tool-choice", choices=["auto", "required", "named"], default="auto")
    parser.add_argument("--include-schema-only", action="store_true")
    parser.add_argument("--api-key-env", default="LLM_API_KEY")
    parser.add_argument("--effort", choices=["low", "medium", "high"], default=None)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--no-proxy", action="store_true")
    parser.add_argument("--print-requests", action="store_true", help="Print request JSON without network or credentials")
    args = parser.parse_args()
    url = urllib.parse.urlsplit(args.base_url)
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
        parser.error("base-url must be an HTTP(S) API prefix without credentials, query, or fragment")
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    if not args.print_requests and not os.environ.get(args.api_key_env):
        parser.error(f"Set the {args.api_key_env} environment variable securely first")
    passed = True
    for model in args.model:
        for endpoint in args.endpoint or ["responses", "chat"]:
            cases = [(fmt, False) for fmt in args.formats or ["none", "schema"]]
            if args.include_schema_only:
                cases.append(("schema", True))
            for fmt, schema_only in cases:
                row = probe(args, model, endpoint, fmt, schema_only)
                print(json.dumps(row, ensure_ascii=False), flush=True)
                passed = passed and row.get("passed", args.print_requests)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
