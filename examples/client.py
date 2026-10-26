#!/usr/bin/env python3
"""Client examples for the DSV4.1-Flash-1017 UNCENSORED endpoint (OpenAI-compatible). Standard library only.

    python3 client.py [--url http://10.10.100.1:8888]

Each example is a small function; read them, copy what you need. With the official SDK the same requests are
`OpenAI(base_url=URL + "/v1", api_key="none").chat.completions.create(**body)`; SGLang-specific fields go in
`extra_body` (e.g. extra_body={"chat_template_kwargs": {"thinking": True}}).
"""
import argparse, base64, json, os, sys, urllib.request

ap = argparse.ArgumentParser(); ap.add_argument("--url", default="http://10.10.100.1:8888")
ap.add_argument("--image", help="path to a local image for the vision example")
ap.add_argument("--api-key", default=os.environ.get("DSV41_API_KEY", ""), help="if the server was started with API_KEY")
A = ap.parse_args()
MODEL = "DeepSeek-V4.1-Flash-1017-UNCENSORED"


def post(path, body, stream=False):
    h = {"Content-Type": "application/json", "User-Agent": "dsv41-client/1.0"}   # Python-urllib is blocked by Cloudflare BIC
    if A.api_key:
        h["Authorization"] = "Bearer " + A.api_key
    req = urllib.request.Request(A.url + path, json.dumps(body).encode(), h)
    resp = urllib.request.urlopen(req, timeout=3600)
    return resp if stream else json.loads(resp.read())


def chat(messages, **kw):
    body = {"model": MODEL, "messages": messages, "max_tokens": kw.pop("max_tokens", 512)}  # always send max_tokens
    body.update(kw)
    return post("/v1/chat/completions", body)


def ex_basic():
    r = chat([{"role": "system", "content": "You are concise."}, {"role": "user", "content": "Explain RDMA in one sentence."}],
             temperature=0.7, top_p=0.95, chat_template_kwargs={"thinking": False})
    print(r["choices"][0]["message"]["content"], "\nusage:", r["usage"])


def ex_thinking():
    # thinking on; reasoning_effort: "low" | "high" | "max" (publisher budgets 50/75/100; default 75)
    r = chat([{"role": "user", "content": "How many positive divisors does 720 have?"}], max_tokens=8000,
             chat_template_kwargs={"thinking": True}, reasoning_effort="high")
    m = r["choices"][0]["message"]
    print("reasoning:", (m.get("reasoning_content") or "")[:300], "...\nanswer:", m["content"])


def ex_stream():
    body = {"model": MODEL, "messages": [{"role": "user", "content": "Write a haiku about fibre optics."}], "max_tokens": 100,
            "stream": True, "stream_options": {"include_usage": True}, "chat_template_kwargs": {"thinking": False}}
    for line in post("/v1/chat/completions", body, stream=True):
        line = line.decode().strip()
        if not line.startswith("data:") or line == "data: [DONE]":
            continue
        j = json.loads(line[5:])
        for ch in j.get("choices", []):
            sys.stdout.write((ch.get("delta") or {}).get("content") or ""); sys.stdout.flush()
        if j.get("usage"):
            print("\nusage:", j["usage"])     # speed = usage.completion_tokens / elapsed (one SSE chunk can hold several tokens)


def ex_tools():
    tools = [{"type": "function", "function": {"name": "get_weather", "description": "Current weather for a city",
              "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}]
    msgs = [{"role": "user", "content": "What's the weather in Oslo?"}]
    r = chat(msgs, tools=tools, chat_template_kwargs={"thinking": False})
    call = r["choices"][0]["message"]["tool_calls"][0]
    print("tool call:", call["function"]["name"], call["function"]["arguments"])
    msgs += [{"role": "assistant", "content": None, "tool_calls": [call]},
             {"role": "tool", "tool_call_id": call["id"], "content": json.dumps({"temp_c": -7, "sky": "snow"})}]
    print("answer:", chat(msgs, tools=tools, chat_template_kwargs={"thinking": False})["choices"][0]["message"]["content"])


def ex_json():
    schema = {"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "integer"}}, "required": ["name", "age"]}
    r = chat([{"role": "user", "content": "Invent a person."}], chat_template_kwargs={"thinking": False},
             response_format={"type": "json_schema", "json_schema": {"name": "person", "schema": schema}})
    print(json.loads(r["choices"][0]["message"]["content"]))


def ex_vision():
    if not A.image:
        print("(pass --image path/to/file.png to run the vision example)"); return
    b64 = base64.b64encode(open(A.image, "rb").read()).decode()
    mime = "image/png" if A.image.lower().endswith(".png") else "image/jpeg"
    r = chat([{"role": "user", "content": [{"type": "text", "text": "Describe this image."},
                                            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]}],
             chat_template_kwargs={"thinking": False})
    print(r["choices"][0]["message"]["content"])


def ex_long_document():
    # Long context: up to 1,048,576 tokens per request. Keep the document as the FIRST, unchanged part of the message
    # and put the question after it: follow-up questions about the same document reuse the prefix cache
    # (usage.prompt_tokens_details.cached_tokens) and come back in seconds instead of minutes.
    doc = "\n".join(f"Line {i}: the value of key k{i} is {i * 7}." for i in range(20000))
    for q in ("What is the value of key k12345?", "What is the value of key k777?"):
        r = chat([{"role": "user", "content": doc + "\n\n" + q}], max_tokens=30, chat_template_kwargs={"thinking": False})
        print(q, "->", r["choices"][0]["message"]["content"].strip(), "| cached:", (r["usage"].get("prompt_tokens_details") or {}).get("cached_tokens"))


if __name__ == "__main__":
    for f in (ex_basic, ex_thinking, ex_stream, ex_tools, ex_json, ex_vision, ex_long_document):
        print(f"\n===== {f.__name__}"); f()
