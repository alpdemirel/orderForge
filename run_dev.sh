#!/bin/bash
# Start both FastAPI and Vite dev servers for OrderForge UI

trap 'kill 0' EXIT

echo "Starting FastAPI backend on :8000..."
uvicorn api.server:app --reload --port 8000 &

echo "Starting Vite frontend on :5173..."
cd frontend && npm run dev &

wait
