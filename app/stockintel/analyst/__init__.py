"""Stock Intel analyst agent: LangGraph pure factory plus app-side wiring.

graph.py     the graph (no app, env, network or DB imports)
tools.py     database templates, guarded SQL and calc as LangChain tools over an injected query fn
evidence.py  evidence index, citation and number checks, ticker allow-list
schemas.py   structured outputs
openrouter.py chat_model factory (OpenRouter only, data_collection=deny enforced)
service.py   run an analysis in the app: pools, persist, spend cap
"""
