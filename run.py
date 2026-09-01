"""Entry point: python run.py  ->  http://127.0.0.1:8420"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("clipmind.server:app", host="127.0.0.1", port=8420, log_level="warning")
