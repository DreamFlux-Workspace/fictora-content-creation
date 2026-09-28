"""Local post on the operator's laptop: voice change, sound effects, music, look, mix, captions, mark.

Hosted post-production is off on the Drama API, so a take is finished here with
ffmpeg and the producer's own Fal key. Nothing in this package holds a server
prompt or template; it reads the take's non-proprietary facts from the API
(``GET /v1/jobs/{take_job}/take-facts``) and the saved spine.
"""
