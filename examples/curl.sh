#!/usr/bin/env bash
# curl examples. URL defaults to the fabric address of the head; on the LAN use http://<head LAN address>:8888
URL=${URL:-http://10.10.100.1:8888}
M=DeepSeek-V4.1-Flash-1017-UNCENSORED

# plain chat (thinking off, always send max_tokens)
curl -s $URL/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "model":"'$M'","max_tokens":256,"temperature":0.7,
  "messages":[{"role":"user","content":"Explain sliding-window attention in two sentences."}],
  "chat_template_kwargs":{"thinking":false}}' | python3 -m json.tool

# thinking + effort (low | high | max)
curl -s $URL/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "model":"'$M'","max_tokens":8000,"reasoning_effort":"max",
  "messages":[{"role":"user","content":"Is 1001 prime?"}],
  "chat_template_kwargs":{"thinking":true}}' | python3 -c 'import json,sys; m=json.load(sys.stdin)["choices"][0]["message"]; print(m["content"])'

# streaming with usage
curl -sN $URL/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "model":"'$M'","max_tokens":100,"stream":true,"stream_options":{"include_usage":true},
  "messages":[{"role":"user","content":"Count to ten."}],"chat_template_kwargs":{"thinking":false}}'

# Anthropic Messages API and OpenAI Responses API are served too
curl -s $URL/v1/messages -H 'Content-Type: application/json' -H 'anthropic-version: 2023-06-01' \
  -d '{"model":"'$M'","max_tokens":64,"messages":[{"role":"user","content":"Hi"}]}'
curl -s $URL/v1/responses -H 'Content-Type: application/json' -d '{"model":"'$M'","input":"Hi","max_output_tokens":64}'

# health / load / metrics
curl -s $URL/v1/models; curl -s $URL/v1/loads | head -c 400; echo; curl -s $URL/metrics | grep -E '^sglang:(kv_evictable_tokens|swa_available_tokens|cache_hit_rate)'
