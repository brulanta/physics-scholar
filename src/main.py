from fastapi import FastAPI
from src.config import ROOT

app = FastAPI(
  title="physicsScholar",
  version="0.1.0"
)

@app.get("/health")
def health():
  return {"status":"ok","version":"0.1.0"}