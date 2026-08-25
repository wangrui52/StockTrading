from fastapi import FastAPI

app = FastAPI(title="A 股交易辅助决策 API", version="0.1.0")


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "stock-trading-backend",
        "api_version": "v1",
    }
