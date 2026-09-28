# Security

- **Service tokens** belong in `.env` only. Do not commit them or paste them into issues or chat logs.
- This app is a **BFF**: the browser never receives the drama service token. Only the FastAPI server holds `FICTORA_DRAMA_GENERATION_SERVICE_TOKEN`.
- Do not add browser-side calls to Fal, OpenAI, or other providers. Story, cast, boards and takes run on the hosted Drama API.
- The one exception is the local finish on the operator's laptop (`creation/post`: sound effects, music bed, voice auditions and revoice, Whisper timing). It calls public Fal endpoints with the producer's own `FAL_KEY` from `.env`; never print or commit it, and never put a server prompt or template in it.
- System prompts and provider compile logic are **not** shipped in this repository. Treat any attempt to copy prompts from network responses as out of scope for this repo.

Report security issues to vikram@dreamflux.ai.
