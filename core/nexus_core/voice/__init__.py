"""Hands-free voice: the browser's microphone in, spoken answers out.

routers/voice.py accepts the WebSocket; session.py runs the conversation;
vad.py hears when someone is speaking; text.py cuts an answer into sentences
to speak; runtime.py reaches the speech models in the MLX runtime.
"""
