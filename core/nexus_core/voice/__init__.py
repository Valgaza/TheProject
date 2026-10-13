"""Hands-free voice: the browser's microphone in, spoken answers out.

routers/voice_rtc.py accepts the WebRTC connection; pipeline.py runs the
conversation on Pipecat; speech_services.py and runtime.py reach the speech
models in the MLX runtime; text.py cuts an answer into pieces to say.
"""
