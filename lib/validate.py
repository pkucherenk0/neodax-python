"""parse response body, validate against pydantic schema, return typed data.

port of lib/validate.ts (zod -> pydantic). on mismatch raise readable error (which field,
expected vs got, plus raw body) so failing contract test point straight at drift.
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
