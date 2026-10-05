# -*- coding: utf-8 -*-
"""导出 Agent SSE 事件契约的 JSON Schema 到 docs/sse-events.schema.json。"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.agent import events

import pydantic
models = events.AgentEvent.__args__
defs = {}
for m in models:
    d = m.model_json_schema()
    d.pop("title", None)
    defs[m.__name__] = d
out = {"$schema": "https://json-schema.org/draft/2020-12/schema",
       "oneOf": [{"$ref": f"#/$defs/{m.__name__}"} for m in models],
       "$defs": defs}
target = pathlib.Path(__file__).resolve().parent.parent.parent / "docs" / "sse-events.schema.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"written {target} ({len(models)} event types)")
