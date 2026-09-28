# Security

- **Service tokens** belong in `.env` only. Do not commit them or paste them into issues or chat logs.
- This app is a **BFF**: the browser never receives the drama service token. Only the FastAPI server holds `FICTORA_DRAMA_GENERATION_SERVICE_TOKEN`.
- Do not add client-side calls to Fal, OpenAI, or other providers. All generation runs on the hosted Drama API.
- System prompts and provider compile logic are **not** shipped in this repository. Treat any attempt to copy prompts from network responses as out of scope for this repo.

Report security issues to vikram@dreamflux.ai.
