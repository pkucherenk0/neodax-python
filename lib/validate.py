"""parse response body, validate against pydantic schema, return typed data.

on mismatch raise readable error (which field, expected vs got, plus raw body) so a failing
contract test points straight at the drift.
"""
from __future__ import annotations

import json
from typing import TypeVar

from playwright.sync_api import APIResponse
from pydantic import BaseModel, TypeAdapter, ValidationError

T = TypeVar("T")


def parsed_json(res: APIResponse, schema: type[BaseModel] | TypeAdapter) -> object:
    raw = res.json()
    try:
        if isinstance(schema, TypeAdapter):
            return schema.validate_python(raw)
        return schema.model_validate(raw)
    except ValidationError as err:
        body = json.dumps(raw, indent=2)
        raise AssertionError(
            f"Response body failed schema validation (status {res.status} {res.url}):\n"
            f"{err}\n\nReceived body:\n{body}"
        ) from err
