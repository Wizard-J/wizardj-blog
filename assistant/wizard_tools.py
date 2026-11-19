"""Read tools and private draft proposals for the Old Wizard."""
from dataclasses import dataclass
import json
from http.client import HTTPException

from draft_tool import draft_content
from model_client import ModelError, RESOURCE, request, response_settings
from web_search import web_search

NAMESPACE = "blog"


@dataclass(frozen=True)
class DraftPreview:
    title: str
    body: str


@dataclass(frozen=True)
class TagPreview:
    query: str
    limit: int
    categories: str


@dataclass(frozen=True)
class SearchUpdate:
    data: dict


def definition(name, description, fields):
    return {
        "type": "function", "name": name, "description": description, "strict": True,
        "parameters": {
            "type": "object", "properties": {key: {"type": "string"} for key in fields},
            "required": fields, "additionalProperties": False,
        },
    }


TOOLS = [
    definition("listArticles", "列出最近发布的 Hugo 文章。", []),
    definition("getArticle", "按查询结果给出的文章 ID 或同站文章链接读取正文。", ["id"]),
    definition("searchSiteArticles", "在已发布文章中搜索标题、标签或正文，返回出处。", ["query"]),
    definition("webSearch", "通过公网搜索外部资料，返回有链接的来源。查询词只包含可公开的信息，勿发送站长或会话的私有数据。", ["query"]),
    definition("autoTagArticles", "仅在站长当前明确要求批量打标时生成待确认预览。query 留空选近期，limit 为 1 至 10 的数字字符串，categories 为站长指定的分类，未指定则留空。", ["query", "limit", "categories"]),
    definition("createArticle", "仅在站长当前明确要求撰写文章草稿时，生成待确认的 Markdown 草稿预览。", ["title", "body"]),
]
ALLOWED = {tool["name"]: tuple(tool["parameters"]["required"]) for tool in TOOLS}


def execute_read(catalog, name, arguments):
    if name == "getArticle":
        entry = catalog.get(arguments["id"])
        return entry if entry is not None else {"error": "未找到已发布文章"}
    matches = catalog.list(arguments.get("query", ""))
    return {"articles": matches[:10]}


def consume(payload, headers):
    output_items = {}
    with request(RESOURCE + "/responses", json.dumps(payload).encode(), headers) as response:
        for line in response:
            if not line.startswith(b"data:"):
                continue
            raw = line[5:].strip()
            if raw == b"[DONE]":
                break
            event = json.loads(raw)
            kind = event.get("type")
            if kind == "response.output_text.delta":
                yield event.get("delta", "")
            elif kind == "response.output_item.done":
                output_items[event["output_index"]] = event["item"]
            elif kind == "response.completed":
                result = event.get("response", {})
                if result.get("status") != "completed" or not isinstance(result.get("output"), list):
                    raise ModelError("未收到完整的工具调用结果")
                yield result["output"] or [output_items[index] for index in sorted(output_items)]
                return
            elif kind == "error" and not event.get("code") and not event.get("message"):
                continue
            elif kind in {"response.failed", "response.incomplete", "error"}:
                failure = event.get("response", {}).get("error") or event
                if failure.get("code") in {"insufficient_quota", "credit_balance_exhausted"}:
                    raise ModelError("OpenAI API 项目额度不可用，请核对所属组织的余额和额度状态")
                if failure.get("code") in {"subscription_sharing_usage_limit_exceeded", "subscription_sharing_usage_unavailable"}:
                    raise ModelError("ChatGPT 套餐额度当前不可用")
                raise ModelError("ChatGPT 未完成此次工具请求")
    raise ModelError("ChatGPT 响应中断，未执行本轮工具")


def validated_calls(output):
    calls = []
    seen = set()
    for item in output:
        if item.get("type") != "function_call":
            continue
        name = item.get("name")
        call_id = item.get("call_id")
        raw = item.get("arguments")
        if (item.get("namespace") != NAMESPACE or name not in ALLOWED or not isinstance(call_id, str)
                or not call_id or call_id in seen or not isinstance(raw, str) or len(raw) > 100_000):
            raise ModelError("模型请求了无效或未授权的工具")
        args = json.loads(raw)
        if not isinstance(args, dict) or set(args) != set(ALLOWED[name]):
            raise ModelError("工具参数不符合要求")
        for key, value in args.items():
            maximum = {"id": 500, "query": 140, "title": 120, "body": 20_000, "limit": 2, "categories": 160}[key]
            optional = name == "autoTagArticles" and key in {"query", "categories"}
            if not isinstance(value, str) or (not optional and not value.strip()) or len(value) > maximum:
                raise ModelError("工具参数不符合要求")
        seen.add(call_id)
        calls.append((name, call_id, args))
    if len(calls) > 6:
        raise ModelError("单轮工具调用过多")
    return calls


def wizard_stream(messages, catalog):
    model, bearer = response_settings()
    headers = {**bearer, "Content-Type": "application/json", "Accept": "text/event-stream"}
    instructions = "\n\n".join(m["content"] for m in messages if m["role"] in {"system", "developer"})
    inputs = [m.copy() for m in messages if m["role"] in {"user", "assistant"}]
    payload = {
        "model": model, "input": inputs, "instructions": instructions,
        "store": False, "stream": True, "include": ["reasoning.encrypted_content"],
        "tools": [{"type": "namespace", "name": NAMESPACE, "description": "Hugo 站内文章、公网搜索和待确认草稿", "tools": TOOLS}],
    }
    total_chars = 0
    web_calls = 0
    try:
        for _ in range(4):
            output = None
            had_text = False
            for item in consume(payload, headers):
                if isinstance(item, str):
                    if not item:
                        continue
                    had_text |= bool(item.strip())
                    total_chars += len(item)
                    if total_chars > 18_000:
                        raise ModelError("模型输出超过长度限制")
                    yield item
                else:
                    output = item
            if output is None:
                raise ModelError("未收到工具响应")
            calls = validated_calls(output)
            if not calls:
                if not had_text:
                    for item in output:
                        for block in item.get("content", []):
                            if block.get("type") == "output_text" and block.get("text"):
                                text = block["text"]
                                total_chars += len(text)
                                if total_chars > 18_000:
                                    raise ModelError("模型输出超过长度限制")
                                had_text |= bool(text.strip())
                                yield text
                if not had_text:
                    raise ModelError("模型没有返回文本")
                return

            tags = [args for name, _, args in calls if name == "autoTagArticles"]
            if tags:
                if len(tags) != 1 or len(calls) != 1 or tags[0]["limit"] not in {str(i) for i in range(1, 11)}:
                    raise ModelError("请单独生成 1 至 10 篇文章的打标预览")
                args = tags[0]
                yield TagPreview(args["query"].strip(), int(args["limit"]), args["categories"].strip())
                return

            drafts = [args for name, _, args in calls if name == "createArticle"]
            if drafts:
                if len(drafts) != 1 or len(calls) != 1:
                    raise ModelError("请在完成查询后单独生成一篇草稿")
                title, body = draft_content(json.dumps(drafts[0]))
                yield DraftPreview(title, body)
                return

            inputs.extend(output)
            for name, call_id, args in calls:
                if name == "webSearch":
                    web_calls += 1
                    query = args["query"]
                    if web_calls <= 2:
                        yield SearchUpdate({"state": "running", "query": query})
                        result = web_search(query)
                        yield SearchUpdate({**result, "state": "finished", "query": query})
                    else:
                        result = {"error": "本次对话已达到联网搜索次数上限"}
                else:
                    result = execute_read(catalog, name, args)
                inputs.append({
                    "type": "function_call_output", "call_id": call_id,
                    "output": json.dumps(result, ensure_ascii=False),
                })
    except (HTTPException, TimeoutError, ConnectionError, ValueError, KeyError, TypeError):
        raise ModelError("博客工具响应异常，请稍后重试") from None
    raise ModelError("本次查询达到工具调用轮数上限")
